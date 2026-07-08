from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

    from agents.agent_0 import config
    from agents.agent_0.broker import (
        connect,
        disconnect,
        load_allowed_positions,
        submit_order,
    )
    from agents.agent_0.contracts import (
        qualify_existing_future_for_order,
        resolve_front_future,
    )
    from agents.agent_0.orders import build_order, trade_order_id, trade_status
    from agents.agent_0.random_policy import RandomPolicy
    from agents.agent_0.risk_limits import RiskLimitError, validate_decision
    from agents.agent_0.sizing import load_sizing_caps
    from agents.agent_0.state import AgentState, append_order_log
else:
    from . import config
    from .broker import connect, disconnect, load_allowed_positions, submit_order
    from .contracts import qualify_existing_future_for_order, resolve_front_future
    from .orders import build_order, trade_order_id, trade_status
    from .random_policy import RandomPolicy
    from .risk_limits import RiskLimitError, validate_decision
    from .sizing import load_sizing_caps
    from .state import AgentState, append_order_log


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _log_decision(
    *,
    account_id: str,
    state: AgentState,
    action: str,
    symbol: str = "",
    side: str = "",
    quantity: int = 0,
    dry_run: bool = False,
    order_id: str = "",
    status: str = "",
    reason: str = "",
) -> None:
    append_order_log(
        {
            "timestamp": _timestamp(),
            "agent": config.AGENT_NAME,
            "account": account_id,
            "action": action,
            "symbol": symbol,
            "side": side,
            "quantity": quantity,
            "order_type": config.DEFAULT_ORDER_TYPE,
            "dry_run": dry_run,
            "order_id": order_id,
            "status": status,
            "reason": reason,
            "trades_today": state.trades_today,
        }
    )


def _contract_for_decision(ib: Any, decision, positions):
    assert decision.instrument is not None

    if decision.action == "flatten":
        for position in positions:
            if position.instrument.symbol == decision.instrument.symbol:
                return qualify_existing_future_for_order(
                    ib,
                    position.contract,
                    decision.instrument,
                )

        raise RuntimeError(
            f"No existing contract found for flattening {decision.instrument.symbol}."
        )

    return resolve_front_future(ib, decision.instrument)


def _counts_against_daily_cap(status: str) -> bool:
    return status not in {"Cancelled", "Inactive"}


def execute_once(
    dry_run: bool = config.DEFAULT_DRY_RUN,
    account_id: str | None = None,
    allow_skip: bool = True,
) -> str:
    config.ensure_agent_directories()

    account_id = config.get_agent_account_id(account_id)
    config.assert_paper_only_settings(account_id)

    state = AgentState.load()
    state.save()

    ib = None

    try:
        ib = connect(account_id)
        positions = load_allowed_positions(ib, account_id)
        sizing_caps = load_sizing_caps()

        decision = RandomPolicy().choose(
            sizing_caps=sizing_caps,
            positions=positions,
            allow_skip=allow_skip,
        )

        try:
            validate_decision(
                decision=decision,
                state=state,
                sizing_caps=sizing_caps,
                positions=positions,
            )
        except RiskLimitError as error:
            symbol = decision.instrument.symbol if decision.instrument else ""
            side = decision.side or ""
            print(f"[BLOCKED] {error}")
            _log_decision(
                account_id=account_id,
                state=state,
                action=decision.action,
                symbol=symbol,
                side=side,
                quantity=decision.quantity,
                dry_run=dry_run,
                status="blocked",
                reason=str(error),
            )
            return "blocked"

        if decision.action == "skip":
            print(f"[SKIP] {decision.reason}")
            _log_decision(
                account_id=account_id,
                state=state,
                action=decision.action,
                dry_run=dry_run,
                status="skipped",
                reason=decision.reason,
            )
            return "skipped"

        assert decision.instrument is not None
        assert decision.side is not None

        contract = _contract_for_decision(ib, decision, positions)
        order = build_order(account_id, decision)

        if dry_run:
            print(
                "[DRY RUN] "
                f"{decision.action} {decision.side} {decision.quantity} "
                f"{decision.instrument.symbol} for {account_id}"
            )
            _log_decision(
                account_id=account_id,
                state=state,
                action=decision.action,
                symbol=decision.instrument.symbol,
                side=decision.side,
                quantity=decision.quantity,
                dry_run=True,
                status="dry_run",
                reason=decision.reason,
            )
            return "dry_run"

        print(
            "[PAPER ORDER] "
            f"{decision.action} {decision.side} {decision.quantity} "
            f"{decision.instrument.symbol} for {account_id}"
        )

        trade = submit_order(
            ib=ib,
            account_id=account_id,
            contract=contract,
            order=order,
        )

        status = trade_status(trade)

        if _counts_against_daily_cap(status):
            state.record_submitted_trade()
            state.save()

        _log_decision(
            account_id=account_id,
            state=state,
            action=decision.action,
            symbol=decision.instrument.symbol,
            side=decision.side,
            quantity=decision.quantity,
            dry_run=False,
            order_id=trade_order_id(trade),
            status=status,
            reason=decision.reason,
        )

        return "submitted"

    finally:
        disconnect(ib)


def run_batch(
    dry_run: bool,
    account_id: str | None = None,
    max_trades: int | None = None,
    pause_seconds: float = 1.0,
) -> None:
    account_id = config.get_agent_account_id(account_id)
    state = AgentState.load()
    remaining = max(config.MAX_TRADES_PER_DAY - state.trades_today, 0)
    target = remaining if max_trades is None else min(max_trades, remaining)

    if target <= 0:
        print(f"[BATCH] Daily trade cap already reached: {state.trades_today}/{config.MAX_TRADES_PER_DAY}")
        return

    print(f"[BATCH] Running {target} immediate Agent 0 trade attempt(s)")

    for index in range(target):
        outcome = execute_once(
            dry_run=dry_run,
            account_id=account_id,
            allow_skip=False,
        )

        if outcome in {"blocked", "skipped"}:
            print(f"[BATCH] Stopping after outcome={outcome}")
            return

        if index < target - 1:
            time.sleep(pause_seconds)


def run_loop(
    dry_run: bool,
    interval_seconds: int,
    account_id: str | None = None,
) -> None:
    while True:
        execute_once(dry_run=dry_run, account_id=account_id)
        time.sleep(interval_seconds)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run Agent 0, the paper-only random trading agent."
    )
    parser.add_argument(
        "--loop",
        action="store_true",
        help="Run continuously instead of making one decision.",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=config.LOOP_INTERVAL_SECONDS,
        help="Seconds between loop decisions.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Resolve and log decisions without submitting paper orders.",
    )
    parser.add_argument(
        "--account",
        help="IBKR paper account ID, e.g. DU1234567. Overrides AGENT0_IBKR_ACCOUNT.",
    )
    parser.add_argument(
        "--batch",
        action="store_true",
        help="Use the remaining daily trade slots immediately.",
    )
    parser.add_argument(
        "--batch-trades",
        type=int,
        help="Max trades to attempt in this batch. Defaults to remaining daily cap.",
    )
    parser.add_argument(
        "--batch-pause",
        type=float,
        default=1.0,
        help="Seconds to pause between batch orders.",
    )
    parser.add_argument(
        "--print-settings",
        action="store_true",
        help="Print Agent 0 settings and exit.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if args.print_settings:
        print(json.dumps(config.settings_summary(), indent=2, sort_keys=True))
        return

    dry_run = bool(args.dry_run or config.DEFAULT_DRY_RUN)

    if args.batch:
        run_batch(
            dry_run=dry_run,
            account_id=args.account,
            max_trades=args.batch_trades,
            pause_seconds=args.batch_pause,
        )
        return

    if args.loop:
        run_loop(
            dry_run=dry_run,
            interval_seconds=args.interval,
            account_id=args.account,
        )
        return

    execute_once(dry_run=dry_run, account_id=args.account)


if __name__ == "__main__":
    main()
