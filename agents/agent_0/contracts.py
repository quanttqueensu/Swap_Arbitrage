from __future__ import annotations

import time
from datetime import date, datetime, timedelta
from typing import Any

from . import config
from .models import AgentInstrument


def allowed_instruments() -> list[AgentInstrument]:
    instruments: list[AgentInstrument] = []

    for maturity, symbol in config.ALLOWED_SWAP_FUTURES.items():
        instruments.append(
            AgentInstrument(
                maturity=maturity,
                symbol=symbol,
                kind="swap_future",
            )
        )

    for maturity, symbol in config.ALLOWED_TREASURY_FUTURES.items():
        instruments.append(
            AgentInstrument(
                maturity=maturity,
                symbol=symbol,
                kind="treasury_future",
            )
        )

    return instruments


def instrument_by_symbol() -> dict[str, AgentInstrument]:
    return {instrument.symbol: instrument for instrument in allowed_instruments()}


def get_instrument(symbol: str) -> AgentInstrument | None:
    return instrument_by_symbol().get(symbol)


def parse_contract_month(value: str) -> date | None:
    if not value:
        return None

    raw_value = str(value).strip()

    if not raw_value.isdigit():
        return None

    if len(raw_value) == 6:
        fmt = "%Y%m"
    elif len(raw_value) == 8:
        fmt = "%Y%m%d"
    else:
        return None

    try:
        return datetime.strptime(raw_value, fmt).date()
    except ValueError:
        return None


def pick_front_contracts(details: list[Any], count: int) -> list[Any]:
    today = date.today()
    earliest_expiry = today + timedelta(days=config.MIN_DAYS_TO_EXPIRY)
    candidates = []

    for item in details:
        contract = item.contract
        expiry = parse_contract_month(
            getattr(contract, "lastTradeDateOrContractMonth", "")
        )

        if expiry is None or expiry <= earliest_expiry:
            continue

        candidates.append((expiry, contract))

    candidates.sort(key=lambda item: item[0])
    return [contract for _, contract in candidates[:count]]

def pick_eris_contracts(
    details: list[Any],
    count: int,
    as_of: date | None = None,
) -> list[Any]:
    if count <= 0:
        return []
    if as_of is None:
        as_of = date.today()

    candidates = []

    for item in details:
        contract_month = parse_contract_month(
            getattr(item, "contractMonth", "")
        )

        if contract_month is None:
            continue

        # Do not use forward-starting Eris vintages.
        if contract_month > as_of:
            continue

        candidates.append(
            (contract_month, item.contract)
        )

    # Newest effective vintage first.
    candidates.sort(
        key=lambda item: item[0],
        reverse=True,
    )

    return [
        contract
        for _, contract in candidates[:count]
    ]


def resolve_futures(
    ib: Any,
    instrument: AgentInstrument,
    count: int = config.CONTRACTS_PER_SYMBOL,
) -> list[Any]:
    try:
        from ib_insync import Future
    except ImportError as exc:
        raise ImportError(
            "Missing dependency: ib_insync. Install it with:\n\n"
            "pip install ib_insync\n"
        ) from exc

    for exchange in config.IBKR_EXCHANGES_TO_TRY:
        contract = Future(
            symbol=instrument.symbol,
            exchange=exchange,
            currency=instrument.currency,
        )

        try:
            details = ib.reqContractDetails(contract)

            if not details:
                time.sleep(0.25)
                continue

            if instrument.kind == "swap_future":
                fronts = pick_eris_contracts(details, count)
            else:
                fronts = pick_front_contracts(details, count)

            if len(fronts) < count:
                time.sleep(0.25)
                continue

            qualified = ib.qualifyContracts(*fronts)

            if len(qualified) < count:
                time.sleep(0.25)
                continue

            return list(qualified)

        except Exception:
            time.sleep(0.25)

    raise RuntimeError(
        f"Could not resolve {count} contracts for {instrument.symbol} "
        f"({instrument.kind}, {instrument.maturity})."
    )
