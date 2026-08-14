from __future__ import annotations

from datetime import datetime, time, timezone
from decimal import Decimal

import pandas as pd

from backtesting.engine import ReplayEvent
from config import (
    ERIS_DOLLARS_PER_POINT,
    MATURITIES,
    RISK_DATA_FILE,
    TREASURY_FUTURES_DOLLARS_PER_POINT,
)
from risk_pipeline import (
    build_risk_data,
    load_cme_swap_data,
    load_treasury_futures_data,
    merge_cme_dv01,
    merge_treasury_futures_data,
)
from signal_pipeline import clean_maturity
from strategy import ContractMetadata, InstrumentObservation, MarketSnapshot


UTC = timezone.utc


def _decimal(value: object) -> Decimal:
    return Decimal(str(value))


def _load_historical_frame(refresh_signals: bool = False) -> pd.DataFrame:
    if refresh_signals:
        risk = build_risk_data(refresh_signals=True, save=False)
    else:
        if not RISK_DATA_FILE.exists():
            raise FileNotFoundError(
                f"Missing {RISK_DATA_FILE}. Run `python risk_pipeline.py` first."
            )
        risk = pd.read_csv(RISK_DATA_FILE)
    merged = merge_cme_dv01(risk, load_cme_swap_data(), include_tickers=True)
    merged = merge_treasury_futures_data(
        merged, load_treasury_futures_data(), include_market_data=True
    )
    output = merged.copy()
    output["date"] = pd.to_datetime(output["date"], errors="coerce").dt.normalize()
    if output["date"].isna().any() or output["date"].duplicated().any():
        raise RuntimeError("historical data contains an invalid or duplicate date")
    return output.sort_values("date").reset_index(drop=True)


def _events_from_frame(frame: pd.DataFrame) -> tuple[ReplayEvent, ...]:
    if "date" not in frame:
        raise RuntimeError("historical data must contain date")
    rows = frame.copy()
    rows["date"] = pd.to_datetime(rows["date"], errors="coerce").dt.normalize()
    if rows.empty or rows["date"].isna().any() or rows["date"].duplicated().any():
        raise RuntimeError("historical data contains an invalid or duplicate date")
    rows = rows.sort_values("date").reset_index(drop=True)
    events = []
    for _, row in rows.iterrows():
        timestamp = datetime.combine(row["date"].date(), time(21), tzinfo=UTC)
        instruments = []
        contracts = []
        multipliers = []
        seen: dict[str, tuple[Decimal, Decimal, str, Decimal]] = {}
        for maturity in MATURITIES:
            m = clean_maturity(maturity)
            for leg, multiplier in (
                ("swap", ERIS_DOLLARS_PER_POINT),
                ("treasury", TREASURY_FUTURES_DOLLARS_PER_POINT[maturity]),
            ):
                quantity_value = pd.to_numeric(
                    row.get(f"{leg}_futures_contracts_rounded_{m}", 0), errors="coerce"
                )
                if pd.isna(quantity_value) or not float(quantity_value).is_integer():
                    raise RuntimeError(f"{maturity} {leg} requires an integer contract quantity")
                quantity = int(quantity_value)
                ticker_value = row.get(f"{leg}_ticker_{m}", "")
                ticker = "" if pd.isna(ticker_value) else str(ticker_value).strip()
                price = pd.to_numeric(row.get(f"{leg}_price_{m}"), errors="coerce")
                dv01 = pd.to_numeric(
                    row.get(f"{leg}_dv01_per_contract_{m}"), errors="coerce"
                )
                if quantity and (not ticker or not price > 0 or not dv01 > 0):
                    raise RuntimeError(
                        f"Nonzero {maturity} {leg} contracts require positive price/DV01 and ticker"
                    )
                if not ticker or not price > 0 or not dv01 > 0:
                    continue
                candidate = (_decimal(price), _decimal(dv01), maturity, _decimal(multiplier))
                if ticker in seen:
                    if seen[ticker] != candidate:
                        raise RuntimeError(f"conflicting duplicate instrument: {ticker}")
                    continue
                seen[ticker] = candidate
                instruments.append(
                    InstrumentObservation(
                        ticker, candidate[0], "historical_master", timestamp, timestamp
                    )
                )
                contracts.append(ContractMetadata(ticker, maturity, candidate[1], -1))
                multipliers.append((ticker, candidate[3]))
        events.append(
            ReplayEvent(
                MarketSnapshot(timestamp, (), tuple(instruments), tuple(contracts)),
                tuple(multipliers),
            )
        )
    return tuple(events)
