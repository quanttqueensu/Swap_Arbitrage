from __future__ import annotations

import argparse
from decimal import Decimal, InvalidOperation
from pathlib import Path
from tempfile import TemporaryDirectory

from config import DATA_DIR

from .assumptions import NAIVE_ASSUMPTIONS, NaiveAssumptions
from .historical import run_historical_backtest


def _decimal(text: str) -> Decimal:
    try:
        value = Decimal(text)
    except InvalidOperation as error:
        raise argparse.ArgumentTypeError("must be a decimal") from error
    if not value.is_finite() or value < 0:
        raise argparse.ArgumentTypeError("must be a finite nonnegative decimal")
    return value


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the canonical swap-arbitrage backtest.")
    parser.add_argument("--run-id", default="historical-backtest")
    parser.add_argument("--start", default="auto")
    parser.add_argument("--end", default="auto")
    parser.add_argument("--initial-equity", type=_decimal, default=Decimal("1000000"))
    parser.add_argument("--output-root", type=Path, default=DATA_DIR / "results" / "backtests")
    parser.add_argument("--refresh-signals", action="store_true")
    parser.add_argument("--bid-ask-half-spread-points", type=_decimal, default=NAIVE_ASSUMPTIONS.bid_ask_half_spread_points)
    parser.add_argument("--commission-usd-per-contract", type=_decimal, default=NAIVE_ASSUMPTIONS.commission_usd_per_contract)
    parser.add_argument("--slippage-points", type=_decimal, default=NAIVE_ASSUMPTIONS.slippage_points)
    parser.add_argument("--financing-usd-per-contract-day", type=_decimal, default=NAIVE_ASSUMPTIONS.financing_usd_per_contract_day)
    parser.add_argument("--roll-usd-per-contract", type=_decimal, default=NAIVE_ASSUMPTIONS.roll_usd_per_contract)
    parser.add_argument("--self-check", action="store_true")
    return parser.parse_args(argv)


def self_check() -> None:
    from unittest.mock import patch

    from .historical import _self_check_frame

    zero_costs = NaiveAssumptions(*([Decimal("0")] * 5))
    with TemporaryDirectory() as directory:
        with patch("backtesting.historical._load_historical_frame", return_value=_self_check_frame()):
            result, run_dir = run_historical_backtest(
                "self-check", Path(directory), assumptions=zero_costs
            )
        if len(list(run_dir.iterdir())) != 8:
            raise RuntimeError("self-check did not write eight canonical files")
        if result.daily[3].gross_pnl_usd != Decimal("220.0"):
            raise RuntimeError("self-check P&L reconciliation failed")
    print("[OK] backtesting self-check passed")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.self_check:
        self_check()
        return 0
    assumptions = NaiveAssumptions(
        args.bid_ask_half_spread_points,
        args.commission_usd_per_contract,
        args.slippage_points,
        args.financing_usd_per_contract_day,
        args.roll_usd_per_contract,
    )
    result, run_dir = run_historical_backtest(
        args.run_id,
        args.output_root,
        args.start,
        args.end,
        args.refresh_signals,
        assumptions,
        args.initial_equity,
    )
    print(run_dir)
    for key, value in result.summary:
        print(f"{key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
