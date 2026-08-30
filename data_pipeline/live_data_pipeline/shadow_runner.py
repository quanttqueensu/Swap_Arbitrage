from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
from typing import Any, Callable

from strategy.eris_pricing import (
    ErisParRateQuote,
    ErisReference,
    PriceQuote,
    convert_eris_quote,
)
from strategy.live_signal import (
    HistoricalModelState,
    LIVE_SIGNAL_STRATEGY_VERSION,
    LiveSignalResult,
    TreasuryYieldQuote,
    evaluate_live_signal,
)
from strategy.live_target import (
    DEFAULT_MAX_GROSS_DV01,
    DEFAULT_MAX_NET_DV01,
    LiveTarget,
    MaturityRiskInputs,
    build_live_target,
)

from .live_market_source import ContractRequest
from .live_market_source import MarketDataError
from .eris_reference_data import ErisReferenceError
from .model_state import load_model_state, load_signal_state, save_signal_state
from .shadow_store import append_rows


MATURITY_SYMBOLS = {
    "2Y": {"eris": "YIT", "treasury_yield": "2YY"},
    "5Y": {"eris": "YIW", "treasury_yield": "5YY"},
}


@dataclass(frozen=True)
class ShadowCycleResult:
    timestamp_utc: datetime
    signals: dict[str, LiveSignalResult]
    hypothetical_target: LiveTarget
    executable_target_changed: bool = False


def _stable_hash(parts: list[str]) -> str:
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def _quote_hash(quotes: dict[str, PriceQuote | TreasuryYieldQuote | None]) -> str:
    parts = []
    for symbol in sorted(quotes):
        quote = quotes[symbol]
        if quote is None:
            parts.append(f"{symbol}:missing")
            continue
        values = {
            key: value
            for key, value in vars(quote).items()
        }
        parts.append(
            f"{symbol}:" + json.dumps(
                {
                    key: value.isoformat() if isinstance(value, datetime) else str(value)
                    for key, value in sorted(values.items())
                },
                sort_keys=True,
            )
        )
    return _stable_hash(parts)


def _blocked_signal(
    *,
    maturity: str,
    prior_state: int,
    reasons: list[str],
    market_snapshot_hash: str,
    model: HistoricalModelState | None = None,
    eris_par: ErisParRateQuote | None = None,
    treasury: TreasuryYieldQuote | None = None,
) -> LiveSignalResult:
    mid_spread = None
    lower = None
    upper = None
    if eris_par is not None and treasury is not None:
        mid_spread = eris_par.mid_par_rate_bps - treasury.mid_bps
        lower = eris_par.ask_par_rate_bps - treasury.ask_bps
        upper = eris_par.bid_par_rate_bps - treasury.bid_bps

    snapshot_id = _stable_hash(
        [
            LIVE_SIGNAL_STRATEGY_VERSION,
            maturity,
            market_snapshot_hash,
            str(prior_state),
            "|".join(reasons),
        ]
    )
    return LiveSignalResult(
        maturity=maturity,
        strategy_version=LIVE_SIGNAL_STRATEGY_VERSION,
        snapshot_id=snapshot_id,
        mid_spread_bps=mid_spread,
        spread_bid_side_bps=lower,
        spread_ask_side_bps=upper,
        historical_mean_bps=model.mean_bps if model is not None else None,
        historical_std_bps=model.std_bps if model is not None else None,
        z_score=None,
        prior_state=prior_state,
        state=0,
        blocked=True,
        reason_codes=tuple(dict.fromkeys(reasons)),
    )


class ShadowLiveSignalRunner:
    def __init__(
        self,
        *,
        market_source: Any,
        reference_provider: Any,
        model_state_path: Path,
        risk_inputs: dict[str, MaturityRiskInputs],
        audit_path: Path,
        state_path: Path,
        model_state_loader: Callable[[Path, str, datetime], HistoricalModelState] = load_model_state,
        quote_max_age_seconds: int = 30,
        max_gross_dv01: Decimal = DEFAULT_MAX_GROSS_DV01,
        max_net_dv01: Decimal = DEFAULT_MAX_NET_DV01,
    ) -> None:
        if set(risk_inputs) != set(MATURITY_SYMBOLS):
            raise ValueError("risk_inputs must contain exactly 2Y and 5Y")
        if type(quote_max_age_seconds) is not int or quote_max_age_seconds <= 0:
            raise ValueError("quote_max_age_seconds must be a positive integer")
        self.market_source = market_source
        self.reference_provider = reference_provider
        self.model_state_path = Path(model_state_path)
        self.model_state_loader = model_state_loader
        self.risk_inputs = risk_inputs
        self.audit_path = Path(audit_path)
        self.state_path = Path(state_path)
        self.quote_max_age_seconds = quote_max_age_seconds
        self.max_gross_dv01 = max_gross_dv01
        self.max_net_dv01 = max_net_dv01

    def _get_quote(
        self,
        request: ContractRequest,
        now: datetime,
    ) -> tuple[PriceQuote | TreasuryYieldQuote | None, str | None]:
        try:
            result = self.market_source.snapshot([request], now=now)
            quote = result.get(request.symbol)
            if quote is None:
                return None, "missing_market_quote"
            return quote, None
        except MarketDataError as exc:
            return None, exc.reason_code
        except Exception:
            return None, "market_data_error"

    def run_once(
        self,
        now: datetime,
        *,
        risk_inputs: dict[str, MaturityRiskInputs] | None = None,
    ) -> ShadowCycleResult:
        if now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        now_utc = now.astimezone(timezone.utc)
        prior_state = load_signal_state(self.state_path)

        quotes: dict[str, PriceQuote | TreasuryYieldQuote | None] = {}
        quote_errors: dict[str, str] = {}
        for maturity, symbols in MATURITY_SYMBOLS.items():
            for kind in ("eris", "treasury_yield"):
                symbol = symbols[kind]
                quote, error = self._get_quote(
                    ContractRequest(symbol=symbol, kind=kind),
                    now_utc,
                )
                quotes[symbol] = quote
                if error:
                    quote_errors[symbol] = error

        market_snapshot_hash = _quote_hash(quotes)
        signals: dict[str, LiveSignalResult] = {}
        context: dict[str, dict[str, Any]] = {}

        for maturity, symbols in MATURITY_SYMBOLS.items():
            eris_quote = quotes.get(symbols["eris"])
            treasury_quote = quotes.get(symbols["treasury_yield"])
            reasons: list[str] = []
            reference: ErisReference | None = None
            eris_par: ErisParRateQuote | None = None
            model: HistoricalModelState | None = None

            if not isinstance(eris_quote, PriceQuote):
                reasons.append(
                    quote_errors.get(symbols["eris"], "missing_market_quote")
                )
            if not isinstance(treasury_quote, TreasuryYieldQuote):
                reasons.append(
                    quote_errors.get(
                        symbols["treasury_yield"], "missing_market_quote"
                    )
                )

            if isinstance(eris_quote, PriceQuote):
                try:
                    reference = self.reference_provider.get(
                        eris_quote.contract_id,
                        eris_quote.symbol,
                        now_utc,
                    )
                    eris_par = convert_eris_quote(eris_quote, reference)
                except ErisReferenceError as exc:
                    reasons.append(exc.reason_code)
                except Exception:
                    reasons.append("invalid_eris_reference")

            try:
                model = self.model_state_loader(
                    self.model_state_path,
                    maturity,
                    now_utc,
                )
            except Exception:
                reasons.append("missing_historical_model_state")

            if (
                not reasons
                and eris_par is not None
                and isinstance(treasury_quote, TreasuryYieldQuote)
                and model is not None
            ):
                signal = evaluate_live_signal(
                    maturity=maturity,
                    eris=eris_par,
                    treasury=treasury_quote,
                    model=model,
                    prior_state=prior_state.get(maturity, 0),
                    now=now_utc,
                    max_quote_age_seconds=self.quote_max_age_seconds,
                )
            else:
                signal = _blocked_signal(
                    maturity=maturity,
                    prior_state=prior_state.get(maturity, 0),
                    reasons=reasons or ["blocked_signal"],
                    market_snapshot_hash=market_snapshot_hash,
                    model=model,
                    eris_par=eris_par,
                    treasury=treasury_quote if isinstance(treasury_quote, TreasuryYieldQuote) else None,
                )

            signals[maturity] = signal
            context[maturity] = {
                "eris_quote": eris_quote,
                "treasury_quote": treasury_quote,
                "reference": reference,
                "eris_par": eris_par,
                "model": model,
            }

        target = build_live_target(
            signals=signals,
            risk_inputs=risk_inputs or self.risk_inputs,
            max_gross_dv01=self.max_gross_dv01,
            max_net_dv01=self.max_net_dv01,
        )
        rows = [
            self._audit_row(
                now_utc,
                maturity,
                signals[maturity],
                target,
                market_snapshot_hash,
                context[maturity],
            )
            for maturity in MATURITY_SYMBOLS
        ]
        append_rows(self.audit_path, rows)

        save_signal_state(
            self.state_path,
            {maturity: signal.state for maturity, signal in signals.items()},
        )

        return ShadowCycleResult(
            timestamp_utc=now_utc,
            signals=signals,
            hypothetical_target=target,
            executable_target_changed=False,
        )

    @staticmethod
    def _audit_row(
        now: datetime,
        maturity: str,
        signal: LiveSignalResult,
        target: LiveTarget,
        market_snapshot_hash: str,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        eris_quote = context["eris_quote"]
        treasury_quote = context["treasury_quote"]
        reference = context["reference"]
        eris_par = context["eris_par"]
        model = context["model"]
        maturity_target = target.maturities[maturity]
        reason_codes = tuple(
            dict.fromkeys((*signal.reason_codes, *maturity_target.reason_codes))
        )

        eris_mid = None
        if isinstance(eris_quote, PriceQuote):
            eris_mid = (eris_quote.bid + eris_quote.ask) / Decimal("2")

        return {
            "timestamp_utc": now,
            "strategy_version": LIVE_SIGNAL_STRATEGY_VERSION,
            "snapshot_id": signal.snapshot_id,
            "market_snapshot_hash": market_snapshot_hash,
            "maturity": maturity,
            "eris_symbol": getattr(eris_quote, "symbol", MATURITY_SYMBOLS[maturity]["eris"]),
            "eris_contract_id": getattr(eris_quote, "contract_id", ""),
            "treasury_yield_symbol": getattr(
                treasury_quote,
                "symbol",
                MATURITY_SYMBOLS[maturity]["treasury_yield"],
            ),
            "treasury_yield_contract_id": getattr(treasury_quote, "contract_id", ""),
            "eris_bid": getattr(eris_quote, "bid", None),
            "eris_ask": getattr(eris_quote, "ask", None),
            "eris_mid": eris_mid,
            "eris_fixed_coupon_decimal": getattr(reference, "fixed_rate_decimal", None),
            "eris_b_price_points": getattr(reference, "b_price_points", None),
            "eris_c_price_points": getattr(reference, "c_price_points", None),
            "eris_pv01_usd_per_bp": getattr(reference, "pv01_usd_per_bp", None),
            "eris_par_bid_bps": getattr(eris_par, "bid_par_rate_bps", None),
            "eris_par_ask_bps": getattr(eris_par, "ask_par_rate_bps", None),
            "eris_par_mid_bps": getattr(eris_par, "mid_par_rate_bps", None),
            "treasury_yield_bid_bps": getattr(treasury_quote, "bid_bps", None),
            "treasury_yield_ask_bps": getattr(treasury_quote, "ask_bps", None),
            "treasury_yield_mid_bps": getattr(treasury_quote, "mid_bps", None),
            "live_spread_bid_side_bps": signal.spread_bid_side_bps,
            "live_spread_ask_side_bps": signal.spread_ask_side_bps,
            "live_spread_mid_bps": signal.mid_spread_bps,
            "historical_mean_bps": getattr(model, "mean_bps", None),
            "historical_std_bps": getattr(model, "std_bps", None),
            "z_score": signal.z_score,
            "prior_state": signal.prior_state,
            "resulting_state": signal.state,
            "hypothetical_swap_quantity": maturity_target.swap_quantity,
            "hypothetical_treasury_quantity": maturity_target.treasury_quantity,
            "blocked": signal.blocked or maturity_target.blocked,
            "reason_codes": reason_codes,
        }
