from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal, localcontext
import hashlib
import json

from strategy import (
    MarketSnapshot,
    OrderIntent,
    OrderSide,
    PaperPosition,
    RiskDecision,
    SignalDecision,
    WorkingOrder,
    to_csv_row,
)

from .assumptions import NAIVE_ASSUMPTIONS, NaiveAssumptions


@dataclass(frozen=True, slots=True)
class ReplayEvent:
    snapshot: MarketSnapshot
    multipliers_usd_per_point: tuple[tuple[str, Decimal], ...]
    fill_limits: tuple[tuple[str, int], ...] = ()

    def __post_init__(self) -> None:
        if type(self.snapshot) is not MarketSnapshot:
            raise TypeError("snapshot must be a MarketSnapshot")
        multiplier_ids = [instrument_id for instrument_id, _ in self.multipliers_usd_per_point]
        limit_ids = [instrument_id for instrument_id, _ in self.fill_limits]
        instrument_ids = [item.instrument_id for item in self.snapshot.instruments]
        contract_ids = [item.instrument_id for item in self.snapshot.contracts]
        if (
            len(multiplier_ids) != len(set(multiplier_ids))
            or len(limit_ids) != len(set(limit_ids))
            or len(instrument_ids) != len(set(instrument_ids))
            or len(contract_ids) != len(set(contract_ids))
            or any(
                type(instrument_id) is not str
                or not instrument_id.strip()
                or type(value) is not Decimal
                or not value.is_finite()
                or value <= 0
                for instrument_id, value in self.multipliers_usd_per_point
            )
            or any(
                type(instrument_id) is not str
                or not instrument_id.strip()
                or type(value) is not int
                or value < 0
                for instrument_id, value in self.fill_limits
            )
        ):
            raise ValueError("event multipliers and fill limits must be unique and valid")


@dataclass(frozen=True, slots=True)
class StrategyResult:
    decisions: tuple[SignalDecision, ...] = ()
    risk_decisions: tuple[tuple[str, RiskDecision], ...] = ()
    intents: tuple[OrderIntent, ...] = ()

    def __post_init__(self) -> None:
        if (
            any(type(value) is not SignalDecision for value in self.decisions)
            or any(
                type(maturity) is not str
                or not maturity.strip()
                or type(decision) is not RiskDecision
                for maturity, decision in self.risk_decisions
            )
            or any(type(value) is not OrderIntent for value in self.intents)
        ):
            raise TypeError("strategy results must contain Phase 4 records")


@dataclass(frozen=True, slots=True)
class DailyRecord:
    observation_date: str
    gross_pnl_usd: Decimal
    transaction_cost_usd: Decimal
    financing_cost_usd: Decimal
    net_pnl_usd: Decimal
    equity_usd: Decimal
    drawdown_usd: Decimal
    drawdown_pct: Decimal
    gross_dv01_usd_per_bp: Decimal
    net_dv01_usd_per_bp: Decimal


@dataclass(frozen=True, slots=True)
class FillRecord:
    order_id: str
    decision_id: str
    fill_time_utc: datetime
    instrument_id: str
    side: str
    requested_quantity_contracts: int
    filled_quantity_contracts: int
    remaining_quantity_contracts: int
    status: str
    mid_price_points: Decimal
    execution_price_points: Decimal
    transaction_cost_usd: Decimal
    roll_cost_usd: Decimal


@dataclass(frozen=True, slots=True)
class TradeRecord:
    trade_id: str
    decision_id: str
    maturity: str
    direction: int
    opened_at_utc: datetime
    closed_at_utc: datetime | None
    gross_pnl_usd: Decimal
    cost_usd: Decimal
    net_pnl_usd: Decimal


@dataclass(frozen=True, slots=True)
class PositionRecord:
    timestamp_utc: datetime
    instrument_id: str
    quantity_contracts: int
    mark_price_points: Decimal
    multiplier_usd_per_point: Decimal
    dv01_usd_per_bp: Decimal
    average_cost_points: Decimal
    realized_pnl_usd: Decimal


@dataclass(frozen=True, slots=True)
class BacktestResult:
    run_id: str
    manifest: tuple[tuple[str, str], ...]
    daily: tuple[DailyRecord, ...]
    decisions: tuple[SignalDecision, ...]
    orders: tuple[OrderIntent, ...]
    fills: tuple[FillRecord, ...]
    trades: tuple[TradeRecord, ...]
    positions: tuple[PositionRecord, ...]
    summary: tuple[tuple[str, str], ...]


@dataclass(slots=True)
class _PendingOrder:
    order_id: str
    intent: OrderIntent
    remaining: int
    created_at_utc: datetime
    is_roll: bool


@dataclass(slots=True)
class _OpenTrade:
    trade_id: str
    decision_id: str
    maturity: str
    direction: int
    opened_at_utc: datetime
    gross_pnl_usd: Decimal = Decimal("0")
    cost_usd: Decimal = Decimal("0")


def _maps(event: ReplayEvent):
    marks = {item.instrument_id: item.price_points for item in event.snapshot.instruments}
    multipliers = dict(event.multipliers_usd_per_point)
    contracts = {item.instrument_id: item for item in event.snapshot.contracts}
    return marks, multipliers, contracts


def _validate_events(events: object) -> tuple[ReplayEvent, ...]:
    if isinstance(events, str) or not isinstance(events, Sequence) or not events:
        raise ValueError("events must be a nonempty sequence")
    result = tuple(events)
    if any(type(event) is not ReplayEvent for event in result):
        raise TypeError("events must contain ReplayEvent values")
    times = [event.snapshot.decision_time_utc for event in result]
    dates = [timestamp.date() for timestamp in times]
    if (
        times != sorted(times)
        or len(times) != len(set(times))
        or len(dates) != len(set(dates))
    ):
        raise ValueError("events must have unique increasing decision times and dates")
    return result


def _input_sha256(events: tuple[ReplayEvent, ...]) -> str:
    payload = [
        {
            "snapshot": {
                "decision_time_utc": event.snapshot.decision_time_utc.isoformat(),
                "rates": [to_csv_row(item) for item in event.snapshot.rates],
                "instruments": [to_csv_row(item) for item in event.snapshot.instruments],
                "contracts": [to_csv_row(item) for item in event.snapshot.contracts],
            },
            "multipliers": [
                [instrument_id, format(value, "f")]
                for instrument_id, value in event.multipliers_usd_per_point
            ],
            "fill_limits": [list(item) for item in event.fill_limits],
        }
        for event in events
    ]
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def run_backtest(
    run_id: str,
    events: object,
    strategy: Callable[[MarketSnapshot], StrategyResult],
    assumptions: NaiveAssumptions = NAIVE_ASSUMPTIONS,
    initial_equity_usd: Decimal = Decimal("1000000"),
    start_date: date | None = None,
    end_date: date | None = None,
) -> BacktestResult:
    if type(run_id) is not str or not run_id.strip():
        raise ValueError("run_id must be nonblank")
    if not callable(strategy):
        raise TypeError("strategy must be callable")
    if type(assumptions) is not NaiveAssumptions:
        raise TypeError("assumptions must be NaiveAssumptions")
    if type(initial_equity_usd) is not Decimal or not initial_equity_usd.is_finite() or initial_equity_usd <= 0:
        raise ValueError("initial equity must be a positive finite Decimal")
    replay = _validate_events(events)
    if start_date is not None or end_date is not None:
        start = start_date or replay[0].snapshot.decision_time_utc.date()
        end = end_date or replay[-1].snapshot.decision_time_utc.date()
        if type(start) is not date or type(end) is not date or start > end:
            raise ValueError("date window must be ordered dates")
        replay = tuple(event for event in replay if start <= event.snapshot.decision_time_utc.date() <= end)
        if not replay:
            raise ValueError("date window contains no events")

    positions: dict[str, int] = {}
    pending: list[_PendingOrder] = []
    daily_rows: list[DailyRecord] = []
    decisions: list[SignalDecision] = []
    orders: list[OrderIntent] = []
    fills: list[FillRecord] = []
    trades: list[TradeRecord] = []
    position_rows: list[PositionRecord] = []
    average_costs: dict[str, Decimal] = {}
    realized_pnl: dict[str, Decimal] = {}
    decision_signals: dict[str, SignalDecision] = {}
    open_trades: dict[str, _OpenTrade] = {}
    instrument_trades: dict[str, str] = {}
    decision_open_trades: dict[str, str] = {}
    next_trade_number = 1
    equity = initial_equity_usd
    peak = equity
    previous_time: datetime | None = None
    previous_marks: dict[str, Decimal] = {}
    previous_multipliers: dict[str, Decimal] = {}
    missing_input_count = 0
    missing_input_locations: set[tuple[str, str, str]] = set()
    risk_blocked_days = 0

    for event in replay:
        timestamp = event.snapshot.decision_time_utc
        marks, multipliers, contracts = _maps(event)
        limits = dict(event.fill_limits)
        event_cost = Decimal("0")
        gross_pnl = Decimal("0")
        financing_cost = Decimal("0")
        event_missing: set[str] = set()
        observation_date = timestamp.date().isoformat()
        if previous_time is not None:
            elapsed_days = Decimal((timestamp.date() - previous_time.date()).days)
            for instrument_id, quantity in positions.items():
                if instrument_id in marks and instrument_id in previous_marks:
                    multiplier = multipliers.get(instrument_id, previous_multipliers.get(instrument_id))
                    if multiplier is not None:
                        leg_pnl = (
                            Decimal(quantity)
                            * multiplier
                            * (marks[instrument_id] - previous_marks[instrument_id])
                        )
                        gross_pnl += leg_pnl
                        trade_id = instrument_trades.get(instrument_id)
                        if trade_id in open_trades:
                            open_trades[trade_id].gross_pnl_usd += leg_pnl
                    else:
                        event_missing.add(instrument_id)
                        if instrument_id not in multipliers:
                            missing_input_locations.add((observation_date, instrument_id, "current_multiplier"))
                        if instrument_id not in previous_multipliers:
                            missing_input_locations.add((observation_date, instrument_id, "previous_multiplier"))
                else:
                    event_missing.add(instrument_id)
                    field = "current_mark" if instrument_id not in marks else "previous_mark"
                    missing_input_locations.add((observation_date, instrument_id, field))
                leg_financing = (
                    Decimal(abs(quantity))
                    * assumptions.financing_usd_per_contract_day
                    * elapsed_days
                )
                financing_cost += leg_financing
                trade_id = instrument_trades.get(instrument_id)
                if trade_id in open_trades:
                    open_trades[trade_id].cost_usd += leg_financing
        still_pending: list[_PendingOrder] = []
        for queued in pending:
            intent_value = queued.intent
            if timestamp <= queued.created_at_utc or timestamp < intent_value.activate_at_utc:
                still_pending.append(queued)
                continue
            if timestamp > intent_value.expires_at_utc:
                mid = marks.get(intent_value.instrument_id, intent_value.reference_price_points)
                fills.append(FillRecord(
                    queued.order_id,
                    intent_value.decision_id,
                    timestamp,
                    intent_value.instrument_id,
                    intent_value.side.value,
                    intent_value.quantity_contracts,
                    0,
                    queued.remaining,
                    "expired",
                    mid,
                    mid,
                    Decimal("0"),
                    Decimal("0"),
                ))
                continue
            if intent_value.instrument_id not in marks or intent_value.instrument_id not in multipliers:
                event_missing.add(intent_value.instrument_id)
                if intent_value.instrument_id not in marks:
                    missing_input_locations.add((observation_date, intent_value.instrument_id, "execution_mark"))
                if intent_value.instrument_id not in multipliers:
                    missing_input_locations.add((observation_date, intent_value.instrument_id, "execution_multiplier"))
                still_pending.append(queued)
                continue
            available = limits.get(intent_value.instrument_id, queued.remaining)
            if available == 0:
                filled = 0
                status = "rejected"
                remaining = queued.remaining
            else:
                filled = min(queued.remaining, available)
                remaining = queued.remaining - filled
                status = "filled" if remaining == 0 else "partial"
            mid = marks[intent_value.instrument_id]
            multiplier = multipliers[intent_value.instrument_id]
            slippage = min(assumptions.slippage_points, intent_value.max_slippage_price_points)
            concession = assumptions.bid_ask_half_spread_points + slippage
            direction = Decimal("1") if intent_value.side is OrderSide.BUY else Decimal("-1")
            execution = mid + direction * concession
            roll_cost = assumptions.roll_usd_per_contract * filled if queued.is_roll else Decimal("0")
            transaction_cost = Decimal(filled) * (
                concession * multiplier + assumptions.commission_usd_per_contract
            ) + roll_cost
            fill = FillRecord(
                queued.order_id,
                intent_value.decision_id,
                timestamp,
                intent_value.instrument_id,
                intent_value.side.value,
                intent_value.quantity_contracts,
                filled,
                remaining,
                status,
                mid,
                execution,
                transaction_cost,
                roll_cost,
            )
            fills.append(fill)
            event_cost += transaction_cost
            if filled:
                signed = filled if intent_value.side is OrderSide.BUY else -filled
                instrument_id = intent_value.instrument_id
                old_quantity = positions.get(instrument_id, 0)
                old_average = average_costs.get(instrument_id, execution)
                closing_quantity = (
                    min(abs(old_quantity), filled)
                    if old_quantity and old_quantity * signed < 0
                    else 0
                )
                opening_quantity = filled - closing_quantity
                unit_cost = transaction_cost / Decimal(filled)
                signal = decision_signals[intent_value.decision_id]
                old_trade_id = instrument_trades.get(instrument_id)
                if closing_quantity and old_trade_id in open_trades:
                    open_trades[old_trade_id].cost_usd += Decimal(closing_quantity) * unit_cost

                opening_trade_id = None
                if opening_quantity:
                    if old_quantity and old_quantity * signed > 0 and old_trade_id in open_trades:
                        opening_trade_id = old_trade_id
                    elif signal.new_state.value:
                        if signal.new_state == signal.prior_state:
                            opening_trade_id = next(
                                (
                                    trade_id
                                    for trade_id, trade in open_trades.items()
                                    if trade.maturity == signal.maturity
                                    and trade.direction == signal.direction.value
                                ),
                                None,
                            )
                        else:
                            opening_trade_id = decision_open_trades.get(signal.decision_id)
                            if opening_trade_id not in open_trades:
                                opening_trade_id = None
                        if opening_trade_id is None:
                            opening_trade_id = f"trade-{next_trade_number}"
                            next_trade_number += 1
                            open_trades[opening_trade_id] = _OpenTrade(
                                opening_trade_id,
                                signal.decision_id,
                                signal.maturity,
                                signal.direction.value,
                                timestamp,
                            )
                            decision_open_trades[signal.decision_id] = opening_trade_id
                        open_trades[opening_trade_id].cost_usd += (
                            Decimal(opening_quantity) * unit_cost
                        )

                if closing_quantity:
                    realized_pnl[instrument_id] = realized_pnl.get(instrument_id, Decimal("0")) + (
                        Decimal(closing_quantity)
                        * (execution - old_average)
                        * multiplier
                        * (Decimal("1") if old_quantity > 0 else Decimal("-1"))
                    )
                new_quantity = old_quantity + signed
                if not old_quantity or old_quantity * signed > 0:
                    average_costs[instrument_id] = (
                        Decimal(abs(old_quantity)) * old_average
                        + Decimal(filled) * execution
                    ) / Decimal(abs(new_quantity))
                elif not new_quantity:
                    average_costs.pop(instrument_id, None)
                elif old_quantity * new_quantity < 0:
                    average_costs[instrument_id] = execution
                positions[instrument_id] = new_quantity
                if not new_quantity:
                    del positions[instrument_id]
                    instrument_trades.pop(instrument_id, None)
                elif old_quantity and old_quantity * new_quantity > 0 and old_trade_id:
                    instrument_trades[instrument_id] = old_trade_id
                elif opening_trade_id:
                    instrument_trades[instrument_id] = opening_trade_id
                if intent_value.instrument_id in limits:
                    limits[intent_value.instrument_id] -= filled
            if status == "partial":
                queued.remaining = remaining
                still_pending.append(queued)
        pending = still_pending

        assigned_trade_ids = set(instrument_trades.values())
        for trade_id, active in tuple(open_trades.items()):
            if trade_id not in assigned_trade_ids:
                trades.append(TradeRecord(
                    active.trade_id,
                    active.decision_id,
                    active.maturity,
                    active.direction,
                    active.opened_at_utc,
                    timestamp,
                    active.gross_pnl_usd,
                    active.cost_usd,
                    active.gross_pnl_usd - active.cost_usd,
                ))
                del open_trades[trade_id]

        snapshot = replace(
            event.snapshot,
            paper_positions=tuple(
                PaperPosition(instrument_id, quantity)
                for instrument_id, quantity in sorted(positions.items())
            ),
            working_orders=tuple(
                WorkingOrder(item.order_id, item.intent.instrument_id, item.intent.side, item.remaining)
                for item in pending
            ),
        )
        result = strategy(snapshot)
        if type(result) is not StrategyResult:
            raise TypeError("strategy must return StrategyResult")
        if any(item.decision_time_utc != timestamp for item in result.decisions):
            raise ValueError("strategy decision time must match the replay event")
        decision_by_id = {item.decision_id: item for item in result.decisions}
        if len(decision_by_id) != len(result.decisions):
            raise ValueError("decision IDs must be unique within an event")
        decision_signals.update(decision_by_id)
        risk_by_maturity = dict(result.risk_decisions)
        if len(risk_by_maturity) != len(result.risk_decisions):
            raise ValueError("risk decisions must have unique maturities")
        if any(not value.allowed for value in risk_by_maturity.values()):
            risk_blocked_days += 1
        for index, intent_value in enumerate(result.intents, 1):
            signal = decision_by_id.get(intent_value.decision_id)
            if (
                signal is None
                or intent_value.run_id != run_id
                or intent_value.earliest_submission_utc < timestamp
                or signal.maturity in risk_by_maturity and not risk_by_maturity[signal.maturity].allowed
            ):
                raise ValueError("intent must match an allowed current decision and run")
            order_id = f"order-{len(orders) + 1}"
            pending.append(_PendingOrder(
                order_id,
                intent_value,
                intent_value.quantity_contracts,
                timestamp,
                "roll" in signal.reason_code,
            ))
            orders.append(intent_value)
        decisions.extend(result.decisions)

        with localcontext() as context:
            context.prec = 50
            net_pnl = gross_pnl - event_cost - financing_cost
            equity += net_pnl
            peak = max(peak, equity)
            drawdown = peak - equity
            drawdown_pct = drawdown / peak if peak else Decimal("0")
            gross_dv01 = Decimal("0")
            net_dv01 = Decimal("0")
            for instrument_id, quantity in positions.items():
                contract = contracts.get(instrument_id)
                if contract is None:
                    event_missing.add(instrument_id)
                    missing_input_locations.add((observation_date, instrument_id, "contract_metadata"))
                    continue
                exposure = Decimal(quantity) * contract.dv01_usd_per_bp * contract.rate_sensitivity_sign
                gross_dv01 += exposure.copy_abs()
                net_dv01 += exposure
            daily_rows.append(DailyRecord(
                timestamp.date().isoformat(),
                gross_pnl,
                event_cost,
                financing_cost,
                net_pnl,
                equity,
                drawdown,
                drawdown_pct,
                gross_dv01,
                net_dv01,
            ))
        for instrument_id, quantity in sorted(positions.items()):
            if instrument_id not in marks:
                event_missing.add(instrument_id)
                missing_input_locations.add((observation_date, instrument_id, "current_mark"))
            if instrument_id not in multipliers:
                event_missing.add(instrument_id)
                missing_input_locations.add((observation_date, instrument_id, "current_multiplier"))
            if instrument_id not in contracts:
                event_missing.add(instrument_id)
                missing_input_locations.add((observation_date, instrument_id, "contract_metadata"))
            if instrument_id in marks and instrument_id in multipliers and instrument_id in contracts:
                position_rows.append(PositionRecord(
                    timestamp,
                    instrument_id,
                    quantity,
                    marks[instrument_id],
                    multipliers[instrument_id],
                    contracts[instrument_id].dv01_usd_per_bp,
                    average_costs[instrument_id],
                    realized_pnl.get(instrument_id, Decimal("0")),
                ))

        missing_input_count += len(event_missing)
        previous_time = timestamp
        previous_marks = marks
        previous_multipliers = multipliers

    for active in sorted(open_trades.values(), key=lambda item: (item.opened_at_utc, item.trade_id)):
        trades.append(TradeRecord(
            active.trade_id,
            active.decision_id,
            active.maturity,
            active.direction,
            active.opened_at_utc,
            None,
            active.gross_pnl_usd,
            active.cost_usd,
            active.gross_pnl_usd - active.cost_usd,
        ))

    total_transaction_cost = sum((row.transaction_cost_usd for row in daily_rows), Decimal("0"))
    total_financing_cost = sum((row.financing_cost_usd for row in daily_rows), Decimal("0"))
    max_drawdown = max((row.drawdown_usd for row in daily_rows), default=Decimal("0"))
    max_gross_dv01 = max((row.gross_dv01_usd_per_bp for row in daily_rows), default=Decimal("0"))
    strategy_versions = sorted({item.strategy_version for item in decisions})
    configuration_versions = sorted({item.configuration_version for item in decisions})
    summary = (
        ("start_date", daily_rows[0].observation_date),
        ("end_date", daily_rows[-1].observation_date),
        ("initial_equity_usd", format(initial_equity_usd, "f")),
        ("ending_equity_usd", format(equity, "f")),
        ("max_drawdown_usd", format(max_drawdown, "f")),
        ("max_gross_dv01_usd_per_bp", format(max_gross_dv01, "f")),
        ("turnover_contracts", str(sum(fill.filled_quantity_contracts for fill in fills))),
        ("transaction_cost_usd", format(total_transaction_cost, "f")),
        ("financing_cost_usd", format(total_financing_cost, "f")),
        ("risk_blocked_days", str(risk_blocked_days)),
        ("missing_input_count", str(missing_input_count)),
    )
    manifest = (
        ("run_id", run_id),
        ("schema_version", "p40.backtest.v1"),
        ("mode", "naive"),
        ("maturity_scope", "synthetic_fixture"),
        ("evidence_class", "synthetic_mechanics_only"),
        ("missing_input_locations", ";".join(":".join(item) for item in sorted(missing_input_locations))),
        ("window_policy", "start_flat"),
        ("input_sha256", _input_sha256(replay)),
        ("coverage_start_date", daily_rows[0].observation_date),
        ("coverage_end_date", daily_rows[-1].observation_date),
        ("daily_row_count", str(len(daily_rows))),
        ("decision_row_count", str(len(decisions))),
        ("order_row_count", str(len(orders))),
        ("fill_row_count", str(len(fills))),
        ("trade_row_count", str(len(trades))),
        ("position_row_count", str(len(position_rows))),
        ("summary_row_count", "1"),
        ("strategy_version", ",".join(strategy_versions)),
        ("configuration_version", ",".join(configuration_versions)),
        ("bid_ask_half_spread_points", format(assumptions.bid_ask_half_spread_points, "f")),
        ("commission_usd_per_contract", format(assumptions.commission_usd_per_contract, "f")),
        ("slippage_points", format(assumptions.slippage_points, "f")),
        ("financing_usd_per_contract_day", format(assumptions.financing_usd_per_contract_day, "f")),
        ("roll_usd_per_contract", format(assumptions.roll_usd_per_contract, "f")),
    )
    return BacktestResult(
        run_id,
        manifest,
        tuple(daily_rows),
        tuple(decisions),
        tuple(orders),
        tuple(fills),
        tuple(trades),
        tuple(position_rows),
        summary,
    )
