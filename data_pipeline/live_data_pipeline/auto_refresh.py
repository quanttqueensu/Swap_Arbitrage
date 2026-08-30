from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from io import StringIO
import os
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import pandas as pd

from config import (
    ERIS_PUBLIC_BASE_URL,
    POSITION_SIZE_BY_MATURITY,
    TREASURY_FUTURES_HEDGE_RATIOS,
)
from data_pipeline.historical_data.historical_data_builder import (
    get_eris_public_swap_data,
)
from strategy.live_signal import LIVE_SIGNAL_STRATEGY_VERSION
from strategy.live_target import MaturityRiskInputs
from risk_pipeline import vol_scale_from_series


MATURITY_YIELD_SYMBOLS = {"2Y": "2YY", "5Y": "5YY"}
MIN_BASELINE_ROWS = 252


class AutoRefreshError(RuntimeError):
    pass


@dataclass(frozen=True)
class RefreshResult:
    risk_inputs: dict[str, MaturityRiskInputs]
    reference_date: date
    baseline_rows: dict[str, int]
    bindings: dict[str, Any]


def _previous_business_day(value: date) -> date:
    cursor = value - timedelta(days=1)
    while cursor.weekday() >= 5:
        cursor -= timedelta(days=1)
    return cursor


def _business_day_age(observed: date, current: date) -> int:
    if observed > current:
        raise AutoRefreshError("ERIS reference date is in the future")
    age = 0
    cursor = observed
    while cursor < current:
        cursor += timedelta(days=1)
        if cursor.weekday() < 5:
            age += 1
    return age


def _atomic_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="") as handle:
            frame.to_csv(handle, index=False, lineterminator="\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _download_csv(url: str, *, timeout_seconds: int = 30) -> pd.DataFrame:
    request = Request(
        url,
        headers={"User-Agent": "Swap-Arb-Agent1/1.0", "Accept": "text/csv,*/*"},
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            text = response.read().decode("utf-8", errors="replace")
    except (HTTPError, URLError, TimeoutError) as exc:
        raise AutoRefreshError(f"Could not download ERIS data: {url}") from exc
    try:
        return pd.read_csv(StringIO(text))
    except Exception as exc:
        raise AutoRefreshError(f"Could not parse ERIS data: {url}") from exc


def load_latest_eris_reference_frame(
    as_of: datetime,
    *,
    downloader: Callable[[str], pd.DataFrame] = _download_csv,
    lookback_days: int = 10,
) -> tuple[pd.DataFrame, date]:
    if as_of.utcoffset() is None:
        raise ValueError("as_of must be timezone-aware")
    current = as_of.astimezone(timezone.utc).date()
    after_settlement = as_of.astimezone(timezone.utc).time() >= time(20)
    for offset in range(lookback_days + 1):
        candidate = current - timedelta(days=offset)
        ymd = candidate.strftime("%Y%m%d")
        top_day = f"Eris_Instruments_{ymd}_Prices_TopDay_PAI_Rate.csv"
        settlement = f"Eris_Instruments_{ymd}_Settles.csv"
        names = (
            (settlement, top_day)
            if offset > 0 or after_settlement
            else (top_day, settlement)
        )
        for name in names:
            try:
                frame = downloader(f"{ERIS_PUBLIC_BASE_URL}/{name}")
            except AutoRefreshError:
                continue
            if frame.empty or "EvaluationDate" not in frame.columns:
                continue
            dates = pd.to_datetime(frame["EvaluationDate"], errors="coerce")
            valid = dates.dropna()
            if valid.empty:
                continue
            observed = valid.max().date()
            if _business_day_age(observed, current) > 1:
                continue
            return frame, observed
    raise AutoRefreshError("No current ERIS reference file was available")


def build_reference_frame(
    source: pd.DataFrame,
    bindings: dict[str, Any],
    *,
    observed_at: datetime,
) -> pd.DataFrame:
    required = {
        "Symbol",
        "ExchangeSymbol (EX005)",
        "Coupon (%)",
        "PastFxdFltPmts (B)",
        "ErisPAI (C)",
        "PV01",
        "DV01",
        "EffectiveDate",
        "MaturityDate",
        "EffectiveYearMonth",
    }
    missing = sorted(required.difference(source.columns))
    if missing:
        raise AutoRefreshError(f"ERIS reference file is missing columns: {missing}")

    rows = []
    for maturity in ("2Y", "5Y"):
        binding = bindings[f"{maturity}:swap"]
        matches = source[source["Symbol"].astype(str).eq(binding.local_symbol)]
        if len(matches) != 1:
            vintage = str(binding.risk_id).rsplit("-", 1)[-1]
            matches = source[
                source["ExchangeSymbol (EX005)"].astype(str).eq(binding.symbol)
                & source["EffectiveYearMonth"].astype(str).str.replace(".0", "", regex=False).eq(vintage)
            ]
        if len(matches) != 1:
            raise AutoRefreshError(
                f"ERIS reference did not uniquely match {binding.local_symbol}"
            )
        row = matches.iloc[0]
        try:
            fixed = Decimal(str(row["Coupon (%)"])) / Decimal("100")
            b_value = Decimal(str(row["PastFxdFltPmts (B)"]))
            c_value = Decimal(str(row["ErisPAI (C)"]))
            pv01 = Decimal(str(row["PV01"]))
            dv01 = Decimal(str(row["DV01"]))
        except Exception as exc:
            raise AutoRefreshError(
                f"Invalid ERIS reference values for {binding.local_symbol}"
            ) from exc
        if not all(value.is_finite() for value in (fixed, b_value, c_value, pv01, dv01)):
            raise AutoRefreshError(f"Non-finite ERIS reference for {binding.local_symbol}")
        if pv01 <= 0 or dv01 <= 0:
            raise AutoRefreshError(f"Non-positive ERIS risk for {binding.local_symbol}")
        if str(row["ExchangeSymbol (EX005)"]).strip() != binding.symbol:
            raise AutoRefreshError(f"ERIS root mismatch for {binding.local_symbol}")
        rows.append(
            {
                "contract_id": str(binding.con_id),
                "symbol": binding.symbol,
                "local_symbol": str(row["Symbol"]).strip(),
                "fixed_rate_decimal": str(fixed),
                "b_price_points": str(b_value),
                "c_price_points": str(c_value),
                "pv01_usd_per_bp": str(pv01),
                "dv01_usd_per_bp": str(dv01),
                "effective_date": str(row["EffectiveDate"]),
                "maturity_date": str(row["MaturityDate"]),
                "observed_at": observed_at.astimezone(timezone.utc).isoformat(),
            }
        )
    return pd.DataFrame(rows)


def build_baseline_frame(
    eris_history: pd.DataFrame,
    yield_history: dict[str, pd.DataFrame],
) -> pd.DataFrame:
    rows: list[pd.DataFrame] = []
    for maturity in ("2Y", "5Y"):
        rate_column = f"eris_swap_{maturity.lower()}_equivalent_par_rate_bps"
        if "date" not in eris_history or rate_column not in eris_history:
            raise AutoRefreshError(f"ERIS history is missing {rate_column}")
        yields = yield_history.get(maturity)
        if yields is None or not {"date", "yield_percent"}.issubset(yields.columns):
            raise AutoRefreshError(f"Yield history is missing for {maturity}")
        eris = pd.DataFrame(
            {
                "date": pd.to_datetime(eris_history["date"], errors="coerce").dt.normalize(),
                "eris_rate_bps": pd.to_numeric(
                    eris_history[rate_column], errors="coerce"
                ),
            }
        )
        treasury = pd.DataFrame(
            {
                "date": pd.to_datetime(yields["date"], errors="coerce").dt.normalize(),
                "yield_percent": pd.to_numeric(yields["yield_percent"], errors="coerce"),
            }
        )
        merged = eris.merge(treasury, on="date", how="inner").dropna()
        merged = merged.drop_duplicates("date").sort_values("date").tail(252)
        if len(merged) < MIN_BASELINE_ROWS:
            raise AutoRefreshError(
                f"Only {len(merged)} aligned historical observations for {maturity}"
            )
        timestamp = merged["date"].dt.tz_localize("UTC") + pd.Timedelta(hours=21)
        rows.append(
            pd.DataFrame(
                {
                    "timestamp_utc": timestamp.map(lambda value: value.isoformat()),
                    "maturity": maturity,
                    "strategy_version": LIVE_SIGNAL_STRATEGY_VERSION,
                    "spread_bps": merged["eris_rate_bps"] - merged["yield_percent"] * 100,
                }
            )
        )
    return pd.concat(rows, ignore_index=True).sort_values(
        ["timestamp_utc", "maturity"]
    )


class Agent1DataRefresher:
    def __init__(
        self,
        *,
        ib: Any,
        agent_config: object,
        baseline_path: Path,
        reference_path: Path,
        contract_risk_path: Path,
        binding_resolver: Callable[..., dict[str, Any]],
        current_reference_loader: Callable[[datetime], tuple[pd.DataFrame, date]] = load_latest_eris_reference_frame,
        eris_history_loader: Callable[..., pd.DataFrame] = get_eris_public_swap_data,
        yield_history_loader: Callable[[str, datetime], pd.DataFrame] | None = None,
        held_contracts: dict[str, int] | None = None,
    ) -> None:
        self.ib = ib
        self.agent_config = agent_config
        self.baseline_path = Path(baseline_path)
        self.reference_path = Path(reference_path)
        self.contract_risk_path = Path(contract_risk_path)
        self.binding_resolver = binding_resolver
        self.current_reference_loader = current_reference_loader
        self.eris_history_loader = eris_history_loader
        self.yield_history_loader = yield_history_loader or self._load_yield_history
        self.held_contracts = dict(held_contracts or {})
        self._last_date: date | None = None
        self._last_result: RefreshResult | None = None

    def refresh(self, now: datetime) -> RefreshResult:
        if now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        current_date = now.astimezone(timezone.utc).date()
        if self._last_date == current_date and self._last_result is not None:
            return self._last_result

        bindings = self.binding_resolver(
            self.ib,
            as_of=current_date,
            min_days_to_expiry=getattr(self.agent_config, "min_days_to_expiry"),
            held_contracts=self.held_contracts,
        )
        source, reference_date = self.current_reference_loader(now)
        source_time = datetime.combine(reference_date, time(21), tzinfo=timezone.utc)
        if reference_date == current_date and source_time > now.astimezone(timezone.utc):
            source_time = now.astimezone(timezone.utc)
        reference = build_reference_frame(source, bindings, observed_at=source_time)
        _atomic_csv(reference, self.reference_path)

        self._write_contract_risk(bindings, reference, reference_date)

        baseline_end = _previous_business_day(current_date)
        baseline_start = baseline_end - timedelta(days=730)
        eris_history = self.eris_history_loader(
            baseline_start.isoformat(), baseline_end.isoformat()
        )
        yields = {
            maturity: self.yield_history_loader(symbol, now)
            for maturity, symbol in MATURITY_YIELD_SYMBOLS.items()
        }
        baseline = build_baseline_frame(eris_history, yields)
        _atomic_csv(baseline, self.baseline_path)
        risk_inputs = self._risk_inputs(reference, baseline)
        counts = {
            maturity: int((baseline["maturity"] == maturity).sum())
            for maturity in MATURITY_YIELD_SYMBOLS
        }
        result = RefreshResult(risk_inputs, reference_date, counts, bindings)
        self._last_date = current_date
        self._last_result = result
        return result

    def _load_yield_history(self, symbol: str, now: datetime) -> pd.DataFrame:
        try:
            from ib_insync import ContFuture
        except ImportError as exc:
            raise AutoRefreshError("ib_insync is required for yield history") from exc

        last_error: Exception | None = None
        for exchange in ("CBOT", "ECBOT"):
            contract = ContFuture(symbol=symbol, exchange=exchange, currency="USD")
            try:
                qualified = list(self.ib.qualifyContracts(contract))
                if len(qualified) == 1:
                    contract = qualified[0]
                bars = list(
                    self.ib.reqHistoricalData(
                        contract,
                        endDateTime=now.astimezone(timezone.utc),
                        durationStr="2 Y",
                        barSizeSetting="1 day",
                        whatToShow="TRADES",
                        useRTH=False,
                        formatDate=2,
                        keepUpToDate=False,
                    )
                )
                frame = pd.DataFrame(
                    {
                        "date": [getattr(bar, "date", None) for bar in bars],
                        "yield_percent": [getattr(bar, "close", None) for bar in bars],
                    }
                )
                frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.tz_localize(None)
                frame["yield_percent"] = pd.to_numeric(
                    frame["yield_percent"], errors="coerce"
                )
                frame = frame.dropna().drop_duplicates("date").sort_values("date")
                if len(frame) >= MIN_BASELINE_ROWS:
                    return frame
            except Exception as exc:
                last_error = exc
        raise AutoRefreshError(
            f"IBKR did not return sufficient continuous history for {symbol}"
        ) from last_error

    def _risk_inputs(
        self,
        reference: pd.DataFrame,
        baseline: pd.DataFrame,
    ) -> dict[str, MaturityRiskInputs]:
        output = {}
        for maturity in ("2Y", "5Y"):
            symbol = "YIT" if maturity == "2Y" else "YIW"
            row = reference[reference["symbol"].eq(symbol)].iloc[0]
            swap_dv01 = Decimal(str(row["dv01_usd_per_bp"]))
            treasury_dv01 = swap_dv01 * Decimal(
                str(TREASURY_FUTURES_HEDGE_RATIOS[maturity])
            )
            spread_history = pd.to_numeric(
                baseline.loc[baseline["maturity"].eq(maturity), "spread_bps"],
                errors="coerce",
            )
            scales = vol_scale_from_series(spread_history)
            vol_scale = Decimal(str(scales.iloc[-1]))
            output[maturity] = MaturityRiskInputs(
                base_target_dv01=Decimal(str(POSITION_SIZE_BY_MATURITY[maturity])),
                vol_scale=vol_scale,
                swap_dv01_per_contract=swap_dv01,
                treasury_dv01_per_contract=treasury_dv01,
                max_swap_contracts=getattr(
                    self.agent_config, f"max_{maturity.lower()}_swap_contracts"
                ),
                max_treasury_contracts=getattr(
                    self.agent_config, f"max_{maturity.lower()}_treasury_contracts"
                ),
            )
        return output

    def _write_contract_risk(
        self,
        bindings: dict[str, Any],
        reference: pd.DataFrame,
        observation_date: date,
    ) -> None:
        rows = []
        for maturity in ("2Y", "5Y"):
            swap = bindings[f"{maturity}:swap"]
            treasury = bindings[f"{maturity}:treasury"]
            ref = reference[reference["symbol"].eq(swap.symbol)].iloc[0]
            swap_dv01 = Decimal(str(ref["dv01_usd_per_bp"]))
            treasury_dv01 = swap_dv01 * Decimal(
                str(TREASURY_FUTURES_HEDGE_RATIOS[maturity])
            )
            rows.extend(
                (
                    {
                        "observation_date": observation_date.isoformat(),
                        "instrument_id": swap.risk_id,
                        "dv01_usd_per_bp": str(swap_dv01),
                        "rate_sensitivity_sign": -1,
                        "dv01_method": "eris_settlement_dv01",
                    },
                    {
                        "observation_date": observation_date.isoformat(),
                        "instrument_id": treasury.risk_id,
                        "dv01_usd_per_bp": str(treasury_dv01),
                        "rate_sensitivity_sign": -1,
                        "dv01_method": "cme_fixed_ics_ratio_proxy",
                    },
                )
            )
        _atomic_csv(pd.DataFrame(rows), self.contract_risk_path)
