from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from .broker import connect_paper, disconnect
from .broker_scope import cancel_agent1_orders
from .config import load_config
from .market_hours import market_is_open
from .paper_audit import PaperAuditError, record_paper_audit
from .runtime import status_cycle
from .service import once_cycle, polling_loop
from .state import load_state


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TARGET_PATH = PROJECT_ROOT / "data" / "raw_data" / "risk_data.csv"
DEFAULT_STATE_PATH = PROJECT_ROOT / "data" / "paper" / "agent_1" / "state.json"


def _default_run_id(now: datetime) -> str:
    return "session-" + now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _default_contract_risk_path(now: datetime) -> Path:
    return PROJECT_ROOT / "data" / "contract_risk" / f"contract_risk_{now.year}.csv"


def _load_evaluator() -> Any:
    try:
        from strategy.risk_signals import evaluate_risk
    except ImportError as exc:
        raise RuntimeError(
            "Agent 1 requires the repository strategy.risk_signals.evaluate_risk function."
        ) from exc
    return evaluate_risk


def _create_store(run_id: str) -> Any:
    try:
        from data_pipeline.live_data_pipeline.paper_store import PaperEventStore
    except ImportError as exc:
        raise RuntimeError(
            "Agent 1 execution requires the repository PaperEventStore."
        ) from exc
    return PaperEventStore(PROJECT_ROOT, "agent_1", run_id)




def _record_audit_or_cancel(
    *,
    ib: Any,
    store: Any,
    config: object,
    result: Any,
    observed_at: datetime,
    audit_recorder: Any = record_paper_audit,
    canceller: Any = cancel_agent1_orders,
) -> dict[str, int]:
    """Persist post-cycle broker truth, cancelling only Agent 1 on failure."""
    try:
        return audit_recorder(
            ib,
            store,
            account_id=getattr(config, "account"),
            bindings=result.status.bindings,
            submitted_order_ids=result.execution.state.submitted_order_ids,
            observed_at=observed_at,
        )
    except PaperAuditError:
        canceller(
            ib,
            account_id=getattr(config, "account"),
            client_id=getattr(config, "client_id"),
        )
        raise

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Strategy-driven IBKR paper supervisor.")
    parser.add_argument(
        "command",
        choices=("run", "once", "status", "stop-and-flatten"),
        help="Operator command.",
    )
    parser.add_argument("--target", type=Path, default=DEFAULT_TARGET_PATH)
    parser.add_argument("--contract-risk", type=Path, default=None)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE_PATH)
    parser.add_argument("--run-id", default=None)
    return parser


def _render_status(result: Any) -> str:
    target = result.target
    lines = [f"action={result.plan.action}"]
    if target is None:
        lines.append(f"target=INVALID ({result.target_error or 'unavailable'})")
    else:
        lines.append(
            f"target={target.as_of.isoformat()} age_business_days={target.age_business_days} "
            f"version={target.version}"
        )
    for maturity in ("2Y", "5Y"):
        swap = result.bindings[f"{maturity}:swap"]
        treasury = result.bindings[f"{maturity}:treasury"]
        swap_state = result.snapshot.position_state(swap.con_id)
        treasury_state = result.snapshot.position_state(treasury.con_id)
        lines.append(
            f"{maturity}: {swap.local_symbol} confirmed={swap_state.confirmed_qty} "
            f"working={swap_state.working_qty}; {treasury.local_symbol} "
            f"confirmed={treasury_state.confirmed_qty} working={treasury_state.working_qty}"
        )
    lines.append("risk=" + "|".join(result.plan.reason_codes or ("none",)))
    lines.append(f"margin_reserve_ok={result.margin_reserve_ok}")
    lines.append(f"reconciled={result.recovery.reconciled}")
    return "\n".join(lines)


def _is_flat(result: Any) -> bool:
    tracked = {binding.con_id for binding in result.status.bindings.values()}
    return (
        all(result.status.snapshot.positions.get(con_id, 0) == 0 for con_id in tracked)
        and not result.status.snapshot.working_orders
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = load_config()
    evaluator = _load_evaluator()
    now = datetime.now(timezone.utc)
    contract_risk_path = args.contract_risk or _default_contract_risk_path(now)
    run_id = args.run_id or _default_run_id(now)
    decision_log_path = (
        PROJECT_ROOT / "data" / "paper" / "agent_1" / run_id / "agent1_decisions.csv"
    )

    ib = connect_paper(config)
    try:
        if args.command == "status":
            state = load_state(args.state)
            result = status_cycle(
                ib=ib,
                config=config,
                target_path=args.target,
                contract_risk_path=contract_risk_path,
                state=state,
                now=now,
                evaluator=evaluator,
                min_days_to_expiry=config.min_days_to_expiry,
            )
            print(_render_status(result))
            return 0

        store = _create_store(run_id)
        if args.command == "once":
            result = once_cycle(
                ib=ib, config=config, target_path=args.target,
                contract_risk_path=contract_risk_path, state_path=args.state,
                now=now, evaluator=evaluator, decision_log_path=decision_log_path,
                store=store,
            )
            _record_audit_or_cancel(
                ib=ib, store=store, config=config, result=result,
                observed_at=datetime.now(timezone.utc),
            )
            print(_render_status(result.status))
            return 0

        if args.command == "run":
            def cycle(cycle_now: datetime) -> None:
                result = once_cycle(
                    ib=ib, config=config, target_path=args.target,
                    contract_risk_path=contract_risk_path, state_path=args.state,
                    now=cycle_now, evaluator=evaluator,
                    decision_log_path=decision_log_path, store=store,
                )
                _record_audit_or_cancel(
                    ib=ib, store=store, config=config, result=result,
                    observed_at=datetime.now(timezone.utc),
                )
                print(_render_status(result.status), flush=True)

            polling_loop(config=config, cycle=cycle, sleep_fn=ib.sleep)
            return 0

        # stop-and-flatten: one or more broker-truth cycles. Working flatten
        # groups are allowed to remain until their lifecycle timeout.
        while True:
            cycle_now = datetime.now(timezone.utc)
            result = once_cycle(
                ib=ib, config=config, target_path=args.target,
                contract_risk_path=contract_risk_path, state_path=args.state,
                now=cycle_now, evaluator=evaluator, stop_requested=True,
                decision_log_path=decision_log_path, store=store,
            )
            _record_audit_or_cancel(
                ib=ib, store=store, config=config, result=result,
                observed_at=datetime.now(timezone.utc),
            )
            print(_render_status(result.status), flush=True)
            if _is_flat(result):
                return 0
            if not market_is_open(config, cycle_now):
                print("stop-and-flatten remains non-flat; market window is closed.")
                return 2
            ib.sleep(config.poll_interval_seconds)
    finally:
        disconnect(ib)


if __name__ == "__main__":
    raise SystemExit(main())
