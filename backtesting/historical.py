from __future__ import annotations

from dataclasses import replace
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pandas as pd

from backtesting.assumptions import NAIVE_ASSUMPTIONS, NaiveAssumptions
from backtesting.engine import BacktestResult, ReplayEvent, StrategyResult, run_backtest
from backtesting.reports import write_results
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
from strategy import (
    ContractMetadata,
    FlattenUrgency,
    InstrumentObservation,
    MarketSnapshot,
    OrderIntent,
    OrderSide,
    OrderType,
    PositionState,
    RiskDecision,
    SignalDecision,
    TimeInForce,
    TradeDirection,
)


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


def _self_check_frame() -> pd.DataFrame:
    return pd.DataFrame({
        "date": pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05", "2024-01-08"]),
        "risk_allowed": [1, 1, 1, 1, 1],
        "risk_block_reason": ["", "", "", "", ""],
        "proxy_position_2y": [0, 1, 1, 0, 0],
        "swap_futures_contracts_rounded_2y": [0, 2, 2, 0, 0],
        "treasury_futures_contracts_rounded_2y": [0, -1, -1, 0, 0],
        "swap_ticker_2y": ["YITH24"] * 5,
        "treasury_ticker_2y": ["ZTH24"] * 5,
        "swap_price_2y": [100.0, 100.0, 100.0, 100.11, 100.11],
        "treasury_price_2y": [102.0, 102.0, 101.99, 101.99, 101.99],
        "swap_dv01_per_contract_2y": [19.0] * 5,
        "treasury_dv01_per_contract_2y": [38.0] * 5,
    })


def _events_from_frame(frame: pd.DataFrame) -> tuple[ReplayEvent, ...]:
    if "date" not in frame:
        raise RuntimeError("historical data must contain date")
    rows = frame.copy()
    rows["date"] = pd.to_datetime(rows["date"], errors="coerce").dt.normalize()
    if rows.empty or rows["date"].isna().any() or rows["date"].duplicated().any():
        raise RuntimeError("historical data contains an invalid or duplicate date")
    rows = rows.sort_values("date").reset_index(drop=True)
    events = []
    last_observed: dict[tuple[str, str], tuple[str, tuple[Decimal, Decimal, str, Decimal]]] = {}
    carry: dict[int, list[tuple[str, tuple[Decimal, Decimal, str, Decimal]]]] = {}
    for index, row in rows.iterrows():
        timestamp = datetime.combine(row["date"].date(), time(21), tzinfo=UTC)
        instruments = []
        contracts = []
        multipliers = []
        seen: dict[str, tuple[Decimal, Decimal, str, Decimal]] = {}

        def add_instrument(
            ticker: str,
            candidate: tuple[Decimal, Decimal, str, Decimal],
            source: str,
        ) -> None:
            if ticker in seen:
                if seen[ticker] != candidate:
                    raise RuntimeError(f"conflicting duplicate instrument: {ticker}")
                return
            seen[ticker] = candidate
            instruments.append(InstrumentObservation(ticker, candidate[0], source, timestamp, timestamp))
            contracts.append(ContractMetadata(ticker, candidate[2], candidate[1], -1))
            multipliers.append((ticker, candidate[3]))

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
                key = (maturity, leg)
                previous = last_observed.get(key)
                if previous and previous[0] != ticker:
                    for carry_index in (index, index + 1):
                        carry.setdefault(carry_index, []).append(previous)
                add_instrument(ticker, candidate, "historical_master")
                last_observed[key] = (ticker, candidate)
        for ticker, candidate in carry.get(index, []):
            add_instrument(ticker, candidate, "historical_roll_zero_return_proxy")
        events.append(
            ReplayEvent(
                MarketSnapshot(timestamp, (), tuple(instruments), tuple(contracts)),
                tuple(multipliers),
            )
        )
    return tuple(events)


def _signed_delta(side: OrderSide, quantity: int) -> int:
    return quantity if side is OrderSide.BUY else -quantity


def _side_and_quantity(delta: int) -> tuple[OrderSide, int]:
    return (OrderSide.BUY, delta) if delta > 0 else (OrderSide.SELL, -delta)


def _position_state(quantity: int) -> PositionState:
    return PositionState.TRADITIONAL if quantity > 0 else (
        PositionState.REVERSE if quantity < 0 else PositionState.FLAT
    )


def _direction(quantity: int) -> TradeDirection:
    return TradeDirection.TRADITIONAL if quantity > 0 else (
        TradeDirection.REVERSE if quantity < 0 else TradeDirection.FLAT
    )


def _historical_strategy(run_id: str, frame: pd.DataFrame, assumptions: NaiveAssumptions):
    rows = {
        datetime.combine(row["date"].date(), time(21), tzinfo=UTC): row
        for _, row in frame.iterrows()
    }
    instrument_legs: dict[str, tuple[str, str]] = {}

    def record_instrument_legs(row: pd.Series) -> None:
        for maturity in MATURITIES:
            m = clean_maturity(maturity)
            for leg in ("swap", "treasury"):
                value = row.get(f"{leg}_ticker_{m}", "")
                ticker = "" if pd.isna(value) else str(value).strip()
                if ticker:
                    instrument_legs[ticker] = (maturity, leg)

    def strategy(snapshot: MarketSnapshot) -> StrategyResult:
        row = rows[snapshot.decision_time_utc]
        record_instrument_legs(row)
        current = {item.instrument_id: item.quantity_contracts for item in snapshot.paper_positions}
        marks = {item.instrument_id: item.price_points for item in snapshot.instruments}
        blocked = row.get("risk_allowed", 1) != 1
        reasons = tuple(
            item for item in str(row.get("risk_block_reason", "")).split("|") if item
        ) or ("upstream_risk_block",)
        has_exposure = any(current.values())
        decisions = []
        risk_decisions = []
        intents = []
        for maturity in MATURITIES:
            m = clean_maturity(maturity)
            desired: dict[str, tuple[str, int]] = {}
            for leg in ("swap", "treasury"):
                ticker_value = row.get(f"{leg}_ticker_{m}", "")
                ticker = "" if pd.isna(ticker_value) else str(ticker_value).strip()
                quantity = int(row.get(f"{leg}_futures_contracts_rounded_{m}", 0))
                desired[leg] = (ticker, 0 if blocked else quantity)

            if blocked:
                risk = RiskDecision(
                    has_exposure,
                    Decimal("0"),
                    (*reasons, "flatten_only") if has_exposure else reasons,
                    has_exposure,
                    FlattenUrgency.SCHEDULED if has_exposure else FlattenUrgency.NONE,
                    (),
                    (),
                )
            else:
                risk = RiskDecision(True, Decimal("1"), ("within_limits",), False, FlattenUrgency.NONE, (), ())
            risk_decisions.append((maturity, risk))

            retiring: list[tuple[str, int]] = []
            openings: list[tuple[str, int]] = []
            rolled = False
            for leg in ("swap", "treasury"):
                ticker, target = desired[leg]
                held = [
                    (instrument_id, quantity)
                    for instrument_id, quantity in current.items()
                    if instrument_legs.get(instrument_id) == (maturity, leg) and instrument_id != ticker
                ]
                rolled = rolled or bool(held and ticker)
                retiring.extend((instrument_id, -quantity) for instrument_id, quantity in held)
                if ticker:
                    delta = target - current.get(ticker, 0)
                    if delta:
                        openings.append((ticker, delta))
            deltas = retiring + openings
            if not deltas:
                continue

            swap_ticker, swap_target = desired["swap"]
            held_swap = sum(
                quantity
                for instrument_id, quantity in current.items()
                if instrument_legs.get(instrument_id) == (maturity, "swap")
            )
            decision_id = f"historical-{row['date'].date().isoformat()}-{m.lower()}"
            decisions.append(SignalDecision(
                decision_id,
                maturity,
                snapshot.decision_time_utc,
                _position_state(held_swap),
                _position_state(swap_target),
                _direction(swap_target),
                "risk_flatten" if blocked else ("contract_roll" if rolled else "historical_target_change"),
                (),
                "historical.adapter.v1",
                "p40.naive.v1",
            ))
            for instrument_id, delta in deltas:
                side, quantity = _side_and_quantity(delta)
                if not quantity or instrument_id not in marks:
                    continue
                if blocked and abs(current.get(instrument_id, 0) + _signed_delta(side, quantity)) > abs(current.get(instrument_id, 0)):
                    continue
                intents.append(OrderIntent(
                    run_id,
                    "backtest",
                    "historical_adapter",
                    decision_id,
                    instrument_id,
                    side,
                    quantity,
                    OrderType.MARKET,
                    TimeInForce.DAY,
                    snapshot.decision_time_utc,
                    snapshot.decision_time_utc,
                    snapshot.decision_time_utc + timedelta(days=7),
                    marks[instrument_id],
                    assumptions.slippage_points,
                    True,
                ))
        return StrategyResult(tuple(decisions), tuple(risk_decisions), tuple(intents))

    return strategy


def _upsert(items: tuple[tuple[str, str], ...], key: str, value: str) -> tuple[tuple[str, str], ...]:
    names = [name for name, _ in items]
    if len(names) != len(set(names)):
        raise ValueError("manifest contains duplicate keys")
    output = [(name, value if name == key else old) for name, old in items]
    if key not in names:
        output.append((key, value))
    return tuple(output)


def run_historical_backtest(
    run_id: str,
    output_root: Path,
    start: str = "auto",
    end: str = "auto",
    refresh_signals: bool = False,
    assumptions: NaiveAssumptions = NAIVE_ASSUMPTIONS,
    initial_equity_usd: Decimal = Decimal("1000000"),
) -> tuple[BacktestResult, Path]:
    if not isinstance(output_root, Path):
        raise TypeError("output_root must be a Path")
    frame = _load_historical_frame(refresh_signals).copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.normalize()
    if frame.empty or frame["date"].isna().any():
        raise RuntimeError("historical data contains an invalid date")
    try:
        start_date = frame["date"].min().date() if start == "auto" else pd.Timestamp(start).date()
        end_date = frame["date"].max().date() if end == "auto" else pd.Timestamp(end).date()
    except (TypeError, ValueError):
        raise RuntimeError(f"Invalid backtest window: {start} to {end}") from None
    if start_date > end_date:
        raise RuntimeError(f"Backtest start is after end: {start_date} > {end_date}")
    selected = frame[frame["date"].dt.date.between(start_date, end_date)].reset_index(drop=True)
    if selected.empty:
        raise RuntimeError(f"No rows found from {start} to {end}.")
    result = run_backtest(
        run_id,
        _events_from_frame(selected),
        _historical_strategy(run_id, selected, assumptions),
        assumptions,
        initial_equity_usd,
    )
    risk_allowed = selected["risk_allowed"] if "risk_allowed" in selected else pd.Series(1, index=selected.index)
    blocked_days = int(risk_allowed.ne(1).sum())
    result = replace(
        result,
        manifest=_upsert(result.manifest, "risk_blocked_days", str(blocked_days)) + (
            ("historical_input_mode", "legacy_signal_risk_adapter"),
            ("historical_roll_mark_policy", "last_pre_roll_mark_zero_return"),
        ),
        summary=_upsert(result.summary, "risk_blocked_days", str(blocked_days)),
    )
    return result, write_results(result, output_root)
