from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from .audit import PaperAuditError, record_paper_audit
from .broker import (
    cancel_agent1_orders,
    connect_paper,
    disconnect,
    request_delayed_market_data,
)
from .config import load_config
from .cycle import RuntimeCache, market_is_open, status_cycle
from .engine import Agent1Engine, polling_loop
from .live_target import (
    DEFAULT_CONTRACT_RISK_PATH,
    build_live_target_provider,
)
from .risk import AccountRiskError, ContractRiskError
from .state import load_state


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TARGET_PATH = PROJECT_ROOT / "data" / "raw_data" / "risk_data.csv"
DEFAULT_STATE_PATH = PROJECT_ROOT / "data" / "paper" / "agent_1" / "state.json"
DEFAULT_LIVE_SIGNAL_STATE_PATH = (
    PROJECT_ROOT / "data" / "paper" / "agent_1" / "live_signal_state.json"
)
DEFAULT_STOP_PATH = PROJECT_ROOT / "data" / "paper" / "agent_1" / "STOP"


def _default_run_id(now: datetime) -> str:
    return "session-" + now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _default_contract_risk_path(now: datetime) -> Path:
    return PROJECT_ROOT / "data" / "contract_risk" / f"contract_risk_{now.year}.csv"


def _refresh_delayed_contract_risk(
    now: datetime | None = None,
    *,
    swap_path: Path | None = None,
    treasury_path: Path | None = None,
    output_path: Path | None = None,
    eris_reference: Any | None = None,
) -> Path:
    import pandas as pd

    from config import CME_SWAP_DATA_FILE, TREASURY_FUTURES_DATA_FILE
    from data_pipeline.historical_data.canonicalize import canonicalize_futures
    from data_pipeline.historical_data.historical_data_builder import (
        read_eris_settlement_file,
    )
    from data_pipeline.live_data_pipeline.auto_refresh import _atomic_csv

    observed_at = now or datetime.now(timezone.utc)
    if observed_at.utcoffset() is None:
        raise ValueError("Contract-risk refresh time must include a timezone.")
    year = observed_at.astimezone(timezone.utc).year
    result = canonicalize_futures(
        swap_path or CME_SWAP_DATA_FILE,
        treasury_path or TREASURY_FUTURES_DATA_FILE,
    )
    rows = result.risk_by_year.get(year)
    if not rows:
        raise RuntimeError(f"No canonical contract-risk rows were available for {year}.")

    swap_source = pd.read_csv(swap_path or CME_SWAP_DATA_FILE, usecols=["date"])
    reference_date = pd.to_datetime(swap_source["date"], errors="coerce").max()
    if pd.isna(reference_date):
        raise RuntimeError("No dated Eris settlement rows were available for contract risk.")
    reference = (
        eris_reference
        if eris_reference is not None
        else read_eris_settlement_file(reference_date)
    )
    required = {"ExchangeSymbol (EX005)", "EffectiveYearMonth", "DV01"}
    if reference.empty or not required.issubset(reference.columns):
        raise RuntimeError("The current Eris settlement cache cannot build contract risk.")
    current = reference[reference["ExchangeSymbol (EX005)"].isin(("YIT", "YIW"))].copy()
    current["vintage"] = (
        current["EffectiveYearMonth"].astype(str).str.replace(".0", "", regex=False)
    )
    current["dv01"] = pd.to_numeric(current["DV01"], errors="coerce")
    current = current[current["vintage"].str.fullmatch(r"\d{6}") & current["dv01"].gt(0)]
    if current.empty or current.duplicated(["ExchangeSymbol (EX005)", "vintage"]).any():
        raise RuntimeError("The current Eris settlement cache has invalid contract risk.")
    rows.extend(
        {
            "observation_date": reference_date.date().isoformat(),
            "instrument_id": f"ERIS-{row['ExchangeSymbol (EX005)']}-{row['vintage']}",
            "dv01_usd_per_bp": str(row["dv01"]),
            "rate_sensitivity_sign": "-1",
            "dv01_method": "eris_settlement_dv01",
        }
        for _, row in current.iterrows()
    )
    rows = list(
        {
            (row["observation_date"], row["instrument_id"]): row
            for row in rows
        }.values()
    )
    destination = output_path or _default_contract_risk_path(observed_at)
    _atomic_csv(
        pd.DataFrame(rows).sort_values(["observation_date", "instrument_id"]),
        destination,
    )
    print(f"[RISK] Refreshed {destination}")
    return destination


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


def _refresh_delayed_target(target_path: Path) -> None:
    if target_path.resolve() != DEFAULT_TARGET_PATH.resolve():
        return

    from risk_pipeline import build_risk_data

    print(f"[TARGET] Refreshing {DEFAULT_TARGET_PATH}")
    build_risk_data(
        refresh_signals=True,
        pull_interest_rates=True,
        pull_eris=True,
        save=True,
    )
    _refresh_delayed_contract_risk()


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
            snapshot=getattr(result.status, "snapshot", None),
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
        choices=(
            "run",
            "once",
            "status",
            "delayed-status",
            "delayed-run",
            "delayed-once",
            "stop-and-flatten",
        ),
        help="Operator command.",
    )
    parser.add_argument("--target", type=Path, default=DEFAULT_TARGET_PATH)
    parser.add_argument("--contract-risk", type=Path, default=None)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE_PATH)
    parser.add_argument("--run-id", default=None)
    parser.add_argument(
        "--stop-file",
        type=Path,
        default=DEFAULT_STOP_PATH,
        help=(
            "Persistent operator stop-state file. When present, Agent 1 cancels "
            "its working orders and targets zero exposure."
        ),
    )
    return parser


def _request_stop(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(exist_ok=True)


def _stop_requested(path: Path) -> bool:
    return path.exists()


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


def _render_unavailable_status(reason: Exception, *, risk_code: str) -> str:
    return "\n".join(
        (
            "action=hold",
            f"target=INVALID ({reason})",
            f"risk={risk_code}",
            "margin_reserve_ok=False",
            "reconciled=False",
        )
    )


def _is_flat(result: Any) -> bool:
    tracked = {binding.con_id for binding in result.status.bindings.values()}
    return (
        all(result.status.snapshot.positions.get(con_id, 0) == 0 for con_id in tracked)
        and not result.status.snapshot.working_orders
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    delayed_execution = args.command in {"delayed-run", "delayed-once"}
    config = load_config()
    evaluator = _load_evaluator()

    if delayed_execution:
        _refresh_delayed_target(args.target)

    now = datetime.now(timezone.utc)
    use_live_target = not delayed_execution
    contract_risk_path = args.contract_risk or (
        DEFAULT_CONTRACT_RISK_PATH if use_live_target else _default_contract_risk_path(now)
    )
    run_id = args.run_id or _default_run_id(now)
    decision_log_path = (
        PROJECT_ROOT / "data" / "paper" / "agent_1" / run_id / "agent1_decisions.csv"
    )

    if args.command == "stop-and-flatten":
        _request_stop(args.stop_file)

    ib = connect_paper(config)
    try:
        if args.command in {
            "delayed-status",
            "delayed-run",
            "delayed-once",
            "stop-and-flatten",
        }:
            request_delayed_market_data(ib)
        target_provider = None
        if use_live_target:
            live_dir = PROJECT_ROOT / "data" / "paper" / "agent_1" / run_id
            target_provider = build_live_target_provider(
                ib=ib,
                agent_config=config,
                audit_path=live_dir / "live_signals.csv",
                state_path=DEFAULT_LIVE_SIGNAL_STATE_PATH,
                contract_risk_path=contract_risk_path,
                held_contracts=load_state(args.state).bound_contracts,
            )

        runtime_cache = RuntimeCache()

        if args.command in {"status", "delayed-status"}:
            state = load_state(args.state)
            try:
                result = status_cycle(
                    ib=ib,
                    config=config,
                    target_path=args.target,
                    contract_risk_path=contract_risk_path,
                    state=state,
                    now=now,
                    evaluator=evaluator,
                    min_days_to_expiry=config.min_days_to_expiry,
                    stop_requested=_stop_requested(args.stop_file),
                    target_provider=target_provider,
                    runtime_cache=runtime_cache,
                )
            except (ContractRiskError, AccountRiskError) as exc:
                risk_code = (
                    "contract-risk-unavailable"
                    if isinstance(exc, ContractRiskError)
                    else "account-risk-unavailable"
                )
                print(_render_unavailable_status(exc, risk_code=risk_code))
                return 2
            if args.command == "delayed-status":
                print("market_data=delayed-requested (diagnostic-only; no orders submitted)")
            print(_render_status(result))
            return 0

        store = _create_store(run_id)
        engine = Agent1Engine(
            ib=ib,
            config=config,
            target_path=args.target,
            contract_risk_path=contract_risk_path,
            state_path=args.state,
            evaluator=evaluator,
            decision_log_path=decision_log_path,
            store=store,
            target_provider=target_provider,
            runtime_cache=runtime_cache,
        )
        if delayed_execution:
            print("market_data=delayed-requested (legacy target; paper execution enabled)")

        if args.command in {"once", "delayed-once"}:
            result = engine.cycle(now, stop_requested=_stop_requested(args.stop_file))
            _record_audit_or_cancel(
                ib=ib, store=store, config=config, result=result,
                observed_at=datetime.now(timezone.utc),
            )
            print(_render_status(result.status))
            return 0

        if args.command in {"run", "delayed-run"}:
            def cycle(cycle_now: datetime) -> None:
                result = engine.cycle(
                    cycle_now,
                    stop_requested=_stop_requested(args.stop_file),
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
            result = engine.cycle(cycle_now, stop_requested=True)
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
