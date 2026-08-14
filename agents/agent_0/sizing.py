from __future__ import annotations

import math

import pandas as pd

from . import config
from .contracts import allowed_instruments
from .models import AgentInstrument, SizingCap


def _load_sizing_frame() -> pd.DataFrame | None:
    if not config.MAIN_SIZING_FILE.exists():
        return None

    df = pd.read_csv(config.MAIN_SIZING_FILE)

    if df.empty:
        return None

    return df


def _max_abs_value(df: pd.DataFrame, column: str) -> float | None:
    if column not in df.columns:
        return None

    values = pd.to_numeric(df[column], errors="coerce").dropna()

    if values.empty:
        return None

    return float(values.abs().max())


def _main_quantity_from_frame(
    instrument: AgentInstrument,
    df: pd.DataFrame | None,
) -> tuple[int, str]:
    if df is None:
        return 0, "missing_sizing_file"

    maturity_key = instrument.maturity_key

    if instrument.kind == "treasury_future":
        column = f"treasury_futures_contracts_rounded_{maturity_key}"
        value = _max_abs_value(df, column)

        if value is None:
            return 0, f"missing_column:{column}"

        return int(value), f"{config.MAIN_SIZE_CAP_MODE}:{column}"

    direct_columns = [
        f"swap_futures_contracts_rounded_{maturity_key}",
        f"eris_swap_futures_contracts_rounded_{maturity_key}",
    ]

    for column in direct_columns:
        value = _max_abs_value(df, column)

        if value is not None:
            return int(value), f"{config.MAIN_SIZE_CAP_MODE}:{column}"

    notional_column = f"swap_notional_{maturity_key}"
    notional = _max_abs_value(df, notional_column)

    if notional is None:
        return 0, f"missing_column:{notional_column}"

    estimated_contracts = math.floor(
        abs(notional) / config.SWAP_NOTIONAL_PER_FUTURE_CONTRACT
    )

    return (
        int(estimated_contracts),
        f"{config.MAIN_SIZE_CAP_MODE}:{notional_column}/notional_estimate",
    )


def _agent_quantity_cap(main_quantity: int) -> int:
    if main_quantity <= 0:
        return 0

    capped = math.floor(main_quantity * config.MAX_ORDER_SIZE_FRACTION)

    if capped <= 0:
        return config.MIN_ORDER_QTY

    return int(capped)


def load_sizing_caps() -> dict[str, SizingCap]:
    df = _load_sizing_frame()
    caps: dict[str, SizingCap] = {}

    for instrument in allowed_instruments():
        main_quantity, source = _main_quantity_from_frame(instrument, df)
        max_agent_quantity = _agent_quantity_cap(main_quantity)

        # Paper-trading fallback:
        # if no usable sizing data exists, allow 1 contract.
        if max_agent_quantity <= 0:
            max_agent_quantity = 1
            source = f"{source}|paper_fallback"

        caps[instrument.symbol] = SizingCap(
            instrument=instrument,
            main_quantity=main_quantity,
            max_agent_quantity=max_agent_quantity,
            source=source,
        )

    return caps
