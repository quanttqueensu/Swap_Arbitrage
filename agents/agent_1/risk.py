from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation, localcontext
from pathlib import Path
from typing import Any, Callable

from .models import BoundContract


class AccountRiskError(RuntimeError):
    """Raised when IBKR account-level paper risk cannot be normalized."""


@dataclass(frozen=True)
class DrawdownState:
    peak_pnl_usd: Decimal
    drawdown_usd: Decimal


def _finite_decimal(value: object, label: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise AccountRiskError(f"Invalid {label}.") from exc
    if not parsed.is_finite():
        raise AccountRiskError(f"Invalid {label}.")
    return parsed


def collect_session_pnl(
    ib: Any,
    account_id: str,
    *,
    wait_seconds: float = 0.25,
) -> Decimal:
    if not str(account_id).upper().startswith("DU"):
        raise AccountRiskError("Session P&L requires a DU paper account.")
    subscribed = False
    try:
        pnl = ib.reqPnL(account_id, "")
        subscribed = True
        ib.sleep(wait_seconds)
        return _finite_decimal(getattr(pnl, "dailyPnL", None), "IBKR daily P&L")
    except AccountRiskError:
        raise
    except Exception as exc:
        raise AccountRiskError("Could not collect IBKR daily P&L.") from exc
    finally:
        if subscribed:
            try:
                ib.cancelPnL(account_id, "")
            except Exception:
                pass


def update_drawdown(previous_peak_pnl_usd: Decimal, session_pnl_usd: Decimal) -> DrawdownState:
    peak = _finite_decimal(previous_peak_pnl_usd, "session peak P&L")
    current = _finite_decimal(session_pnl_usd, "session P&L")
    new_peak = max(peak, current)
    drawdown = new_peak - current
    if drawdown < 0:
        raise AccountRiskError("Calculated drawdown cannot be negative.")
    return DrawdownState(new_peak, drawdown)


class ContractRiskError(RuntimeError):
    """Raised when execution-time contract risk cannot be validated."""


@dataclass(frozen=True)
class ContractRisk:
    con_id: int
    risk_id: str
    observation_date: date
    dv01_usd_per_bp: Decimal
    rate_sensitivity_sign: int
    method: str


@dataclass(frozen=True)
class PortfolioDV01:
    gross: Decimal
    net: Decimal
    residual_fraction: Decimal


REQUIRED_COLUMNS = (
    "observation_date",
    "instrument_id",
    "dv01_usd_per_bp",
    "rate_sensitivity_sign",
    "dv01_method",
)


def _positive_decimal(value: object, label: str) -> Decimal:
    try:
        parsed = Decimal(str(value).strip())
    except (InvalidOperation, ValueError) as exc:
        raise ContractRiskError(f"Invalid {label} in contract-risk data.") from exc
    if not parsed.is_finite() or parsed <= 0:
        raise ContractRiskError(f"Invalid {label} in contract-risk data.")
    return parsed


def load_contract_risks(
    path: Path,
    *,
    as_of: date,
    bindings: dict[str, BoundContract],
) -> dict[int, ContractRisk]:
    if not path.exists():
        raise ContractRiskError(f"Contract-risk source does not exist: {path}")
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            missing = [name for name in REQUIRED_COLUMNS if name not in (reader.fieldnames or [])]
            if missing:
                raise ContractRiskError(f"Contract-risk source is missing columns: {missing}.")
            rows = list(reader)
    except OSError as exc:
        raise ContractRiskError("Could not read contract-risk source.") from exc

    by_id: dict[str, list[tuple[date, dict[str, str]]]] = {}
    for row in rows:
        try:
            row_date = date.fromisoformat(str(row["observation_date"]).strip())
        except ValueError as exc:
            raise ContractRiskError("Invalid observation_date in contract-risk source.") from exc
        if row_date > as_of:
            continue
        risk_id = str(row["instrument_id"]).strip()
        by_id.setdefault(risk_id, []).append((row_date, row))

    output: dict[int, ContractRisk] = {}
    for binding in bindings.values():
        candidates = by_id.get(binding.risk_id, [])
        if not candidates:
            raise ContractRiskError(
                f"Contract-risk data is missing bound instrument {binding.risk_id!r}."
            )
        observation_date, row = max(candidates, key=lambda item: item[0])
        try:
            sign = int(str(row["rate_sensitivity_sign"]).strip())
        except ValueError as exc:
            raise ContractRiskError("Invalid rate_sensitivity_sign in contract-risk data.") from exc
        if sign not in {-1, 1}:
            raise ContractRiskError("rate_sensitivity_sign must be -1 or 1.")
        method = str(row["dv01_method"]).strip()
        if not method:
            raise ContractRiskError("Contract-risk method must be non-empty.")
        output[binding.con_id] = ContractRisk(
            con_id=binding.con_id,
            risk_id=binding.risk_id,
            observation_date=observation_date,
            dv01_usd_per_bp=_positive_decimal(row["dv01_usd_per_bp"], "dv01_usd_per_bp"),
            rate_sensitivity_sign=sign,
            method=method,
        )
    return output


def calculate_portfolio_dv01(
    positions: dict[int, int],
    risks: dict[int, ContractRisk],
) -> PortfolioDV01:
    gross = Decimal("0")
    net = Decimal("0")
    for con_id, quantity in positions.items():
        if type(quantity) is not int:
            raise ContractRiskError("Portfolio position quantity must be an integer.")
        if quantity == 0:
            continue
        risk = risks.get(con_id)
        if risk is None:
            raise ContractRiskError(f"Missing contract risk for conId {con_id}.")
        exposure = Decimal(quantity) * risk.dv01_usd_per_bp
        gross += exposure.copy_abs()
        net += exposure * Decimal(risk.rate_sensitivity_sign)

    if gross == 0:
        residual = Decimal("0")
    else:
        with localcontext() as context:
            context.prec = 50
            residual = net.copy_abs() / gross
    return PortfolioDV01(gross=gross, net=net, residual_fraction=residual)


@dataclass(frozen=True)
class RuntimeRiskState:
    capacity_scale: Decimal
    has_open_position: bool
    emergency_flatten: bool
    scheduled_flatten: bool
    data_fresh: bool
    bid_ask_valid: bool
    market_fields_valid: bool
    broker_connected: bool
    reconciled: bool
    roll_allowed: bool
    margin_reserve_ok: bool
    residual_fraction: Decimal
    portfolio_gross_dv01_usd_per_bp: Decimal
    portfolio_net_dv01_usd_per_bp: Decimal
    orders_submitted: int
    working_orders: int
    session_pnl_usd: Decimal
    drawdown_usd: Decimal

    @classmethod
    def safe_defaults(cls) -> "RuntimeRiskState":
        return cls(
            capacity_scale=Decimal("1"),
            has_open_position=False,
            emergency_flatten=False,
            scheduled_flatten=False,
            data_fresh=True,
            bid_ask_valid=True,
            market_fields_valid=True,
            broker_connected=True,
            reconciled=True,
            roll_allowed=True,
            margin_reserve_ok=True,
            residual_fraction=Decimal("0"),
            portfolio_gross_dv01_usd_per_bp=Decimal("0"),
            portfolio_net_dv01_usd_per_bp=Decimal("0"),
            orders_submitted=0,
            working_orders=0,
            session_pnl_usd=Decimal("0"),
            drawdown_usd=Decimal("0"),
        )


def _default_evaluator() -> Callable[..., Any] | None:
    try:
        from strategy.risk_signals import evaluate_risk
    except Exception:
        return None
    return evaluate_risk


def evaluate_runtime_risk(
    state: RuntimeRiskState,
    config: object,
    *,
    evaluator: Callable[..., Any] | None = None,
) -> Any | None:
    if type(state) is not RuntimeRiskState:
        return None

    active_evaluator = evaluator or _default_evaluator()
    if active_evaluator is None:
        return None

    try:
        return active_evaluator(
            capacity_scale=state.capacity_scale,
            has_open_position=state.has_open_position,
            emergency_flatten=state.emergency_flatten,
            scheduled_flatten=state.scheduled_flatten,
            data_fresh=state.data_fresh,
            bid_ask_valid=state.bid_ask_valid,
            market_fields_valid=state.market_fields_valid,
            broker_connected=state.broker_connected,
            reconciled=state.reconciled,
            roll_allowed=state.roll_allowed,
            margin_reserve_ok=state.margin_reserve_ok,
            residual_fraction=state.residual_fraction,
            max_residual_fraction=getattr(config, "max_residual_dv01_fraction"),
            portfolio_gross_dv01_usd_per_bp=state.portfolio_gross_dv01_usd_per_bp,
            max_portfolio_gross_dv01_usd_per_bp=getattr(config, "max_gross_dv01"),
            portfolio_net_dv01_usd_per_bp=state.portfolio_net_dv01_usd_per_bp,
            max_portfolio_net_dv01_usd_per_bp=getattr(config, "max_net_dv01"),
            orders_submitted=state.orders_submitted,
            max_orders=getattr(config, "max_order_groups_per_session"),
            working_orders=state.working_orders,
            max_working_orders=getattr(config, "max_working_order_groups"),
            session_pnl_usd=state.session_pnl_usd,
            max_session_loss_usd=getattr(config, "max_session_loss_usd"),
            drawdown_usd=state.drawdown_usd,
            max_drawdown_usd=getattr(config, "max_drawdown_usd"),
        )
    except (AttributeError, TypeError, ValueError):
        return None
