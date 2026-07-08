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

    for fmt in ("%Y%m%d", "%Y%m"):
        try:
            return datetime.strptime(raw_value, fmt).date()
        except ValueError:
            pass

    return None


def pick_front_contract(details: list[Any]) -> Any | None:
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

    if not candidates:
        return None

    candidates.sort(key=lambda item: item[0])
    return candidates[0][1]


def resolve_front_future(ib: Any, instrument: AgentInstrument) -> Any:
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

            front = pick_front_contract(details)

            if front is None:
                time.sleep(0.25)
                continue

            qualified = ib.qualifyContracts(front)

            if not qualified:
                time.sleep(0.25)
                continue

            return qualified[0]

        except Exception:
            time.sleep(0.25)

    raise RuntimeError(
        f"Could not resolve front contract for {instrument.symbol} "
        f"({instrument.kind}, {instrument.maturity})."
    )


def qualify_existing_future_for_order(
    ib: Any,
    contract: Any,
    instrument: AgentInstrument,
) -> Any:
    for exchange in dict.fromkeys([instrument.exchange, *config.IBKR_EXCHANGES_TO_TRY]):
        if not exchange:
            continue

        contract.exchange = exchange
        qualified = ib.qualifyContracts(contract)

        if qualified:
            return qualified[0]

    raise RuntimeError(
        f"Could not qualify existing {instrument.symbol} position contract "
        "with an order exchange."
    )
