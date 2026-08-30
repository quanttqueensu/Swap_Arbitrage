from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta
from typing import Any, Literal


InstrumentKind = Literal["swap_future", "treasury_future"]


class ContractSelectionError(RuntimeError):
    """Raised when no contract can be safely bound for a maturity leg."""


def _parse_yyyymm(value: object) -> date | None:
    text = str(value or "").strip()
    if len(text) != 6 or not text.isdigit():
        return None
    try:
        return datetime.strptime(text, "%Y%m").date()
    except ValueError:
        return None


def _parse_expiry(value: object) -> date | None:
    text = str(value or "").strip()
    if not text.isdigit():
        return None
    if len(text) == 8:
        try:
            return datetime.strptime(text, "%Y%m%d").date()
        except ValueError:
            return None
    if len(text) == 6:
        month_start = _parse_yyyymm(text)
        if month_start is None:
            return None
        last_day = calendar.monthrange(month_start.year, month_start.month)[1]
        return date(month_start.year, month_start.month, last_day)
    return None


def _contract_id(detail: Any) -> int | None:
    contract = getattr(detail, "contract", None)
    value = getattr(contract, "conId", None)
    return value if type(value) is int and value > 0 else None


def _eligible_expiry(detail: Any, threshold: date) -> date | None:
    contract = getattr(detail, "contract", None)
    expiry = _parse_expiry(getattr(contract, "lastTradeDateOrContractMonth", ""))
    return expiry if expiry is not None and expiry > threshold else None


def _eris_vintage(detail: Any) -> date | None:
    value = getattr(detail, "contractMonth", "")
    if not value:
        contract = getattr(detail, "contract", None)
        value = getattr(contract, "contractMonth", "")
    return _parse_yyyymm(value)


def select_contract(
    details: list[Any],
    *,
    kind: InstrumentKind,
    as_of: date,
    min_days_to_expiry: int,
    held_con_id: int | None = None,
) -> Any:
    if kind not in {"swap_future", "treasury_future"}:
        raise ContractSelectionError(f"Unsupported instrument kind: {kind!r}.")
    if type(as_of) is not date:
        raise ContractSelectionError("as_of must be a date.")
    if type(min_days_to_expiry) is not int or min_days_to_expiry <= 0:
        raise ContractSelectionError("min_days_to_expiry must be a positive integer.")
    if held_con_id is not None and (type(held_con_id) is not int or held_con_id <= 0):
        raise ContractSelectionError("held_con_id must be a positive integer when provided.")

    threshold = as_of + timedelta(days=min_days_to_expiry)
    candidates: list[tuple[object, Any]] = []

    for detail in details:
        con_id = _contract_id(detail)
        expiry = _eligible_expiry(detail, threshold)
        if con_id is None or expiry is None:
            continue

        if kind == "swap_future":
            vintage = _eris_vintage(detail)
            if vintage is None or vintage > as_of:
                continue
            sort_key: object = (-vintage.toordinal(), expiry.toordinal(), con_id)
        else:
            sort_key = (expiry.toordinal(), con_id)

        if held_con_id == con_id:
            return getattr(detail, "contract")

        candidates.append((sort_key, getattr(detail, "contract")))

    if not candidates:
        raise ContractSelectionError(
            f"No eligible {kind} contract satisfies the minimum expiry policy."
        )

    candidates.sort(key=lambda item: item[0])
    return candidates[0][1]

from decimal import Decimal, InvalidOperation

from .models import BoundContract


def _positive_tick(value: object) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ContractSelectionError("Contract minimum tick is invalid.") from exc
    if not parsed.is_finite() or parsed <= 0:
        raise ContractSelectionError("Contract minimum tick must be positive.")
    return parsed


def _default_future_factory(**kwargs: object) -> Any:
    try:
        from ib_insync import Future
    except ImportError as exc:
        raise ContractSelectionError("ib_insync is required for IBKR contract binding.") from exc
    return Future(**kwargs)


def resolve_binding(
    ib: Any,
    *,
    maturity: str,
    leg: str,
    symbol: str,
    kind: InstrumentKind,
    as_of: date,
    min_days_to_expiry: int,
    held_con_id: int | None = None,
    exchanges: tuple[str, ...] = ("CBOT", "ECBOT"),
    future_factory: Any | None = None,
) -> BoundContract:
    if maturity not in {"2Y", "5Y"}:
        raise ContractSelectionError("maturity must be 2Y or 5Y.")
    if leg not in {"swap", "treasury"}:
        raise ContractSelectionError("leg must be swap or treasury.")
    factory = future_factory or _default_future_factory
    last_error: Exception | None = None

    for exchange in exchanges:
        try:
            probe = factory(symbol=symbol, exchange=exchange, currency="USD")
            details = list(ib.reqContractDetails(probe))
            if not details:
                continue
            selected = select_contract(
                details,
                kind=kind,
                as_of=as_of,
                min_days_to_expiry=min_days_to_expiry,
                held_con_id=held_con_id,
            )
            selected_id = getattr(selected, "conId", None)
            detail = next(
                (item for item in details if getattr(getattr(item, "contract", None), "conId", None) == selected_id),
                None,
            )
            if detail is None:
                raise ContractSelectionError("Selected contract details disappeared during binding.")
            tick = _positive_tick(getattr(detail, "minTick", None))
            qualified = list(ib.qualifyContracts(selected))
            if len(qualified) != 1:
                raise ContractSelectionError("IBKR did not uniquely qualify the selected contract.")
            broker_contract = qualified[0]
            con_id = getattr(broker_contract, "conId", None)
            if type(con_id) is not int or con_id <= 0:
                raise ContractSelectionError("Qualified contract has invalid conId.")
            local_symbol = str(getattr(broker_contract, "localSymbol", "") or "").strip()
            if not local_symbol:
                raise ContractSelectionError("Qualified contract has no localSymbol.")

            if kind == "swap_future":
                vintage = _eris_vintage(detail)
                if vintage is None:
                    raise ContractSelectionError("Eris contract has no valid vintage.")
                risk_id = f"ERIS-{symbol}-{vintage:%Y%m}"
            else:
                risk_id = f"YAHOO-CONTINUOUS-{symbol}"

            return BoundContract(
                maturity=maturity,
                leg=leg,
                con_id=con_id,
                symbol=symbol,
                local_symbol=local_symbol,
                min_tick=tick,
                risk_id=risk_id,
                broker_contract=broker_contract,
            )
        except ContractSelectionError as exc:
            last_error = exc
            continue
        except Exception as exc:
            last_error = exc
            continue

    if isinstance(last_error, ContractSelectionError):
        raise last_error
    raise ContractSelectionError(
        f"Could not resolve a qualified {symbol} contract for Agent 1."
    ) from last_error


STRATEGY_LEGS = (
    ("2Y", "swap", "YIT", "swap_future"),
    ("2Y", "treasury", "ZT", "treasury_future"),
    ("5Y", "swap", "YIW", "swap_future"),
    ("5Y", "treasury", "ZF", "treasury_future"),
)


def resolve_strategy_bindings(
    ib: Any,
    *,
    as_of: date,
    min_days_to_expiry: int,
    held_contracts: dict[str, int] | None = None,
    resolver: Any = resolve_binding,
) -> dict[str, BoundContract]:
    held_contracts = held_contracts or {}
    output: dict[str, BoundContract] = {}
    for maturity, leg, symbol, kind in STRATEGY_LEGS:
        key = f"{maturity}:{leg}"
        output[key] = resolver(
            ib,
            maturity=maturity,
            leg=leg,
            symbol=symbol,
            kind=kind,
            as_of=as_of,
            min_days_to_expiry=min_days_to_expiry,
            held_con_id=held_contracts.get(key),
        )
    return output
