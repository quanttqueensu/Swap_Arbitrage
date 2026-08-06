# P33 Position Sizing and Risk Signals Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert approved causal signals into capacity-scaled immutable target positions and fail-closed immutable risk decisions.

**Architecture:** `strategy.position_sizing` contains four small Decimal scale functions and one target-position orchestrator that reuses the P31 hedge equations. `strategy.risk_signals` contains one explicit keyword-only risk evaluator; it records numeric evidence with existing P30 `NamedValue` and `RiskDecision` records. No file, clock, pandas, broker, network, or order path enters either module.

**Tech Stack:** Python 3.12 standard library (`collections.abc`, `datetime`, `decimal`), existing `strategy.models`, existing `strategy.spread`, and `unittest`.

## Global Constraints

- Frozen sizing/risk configuration version: `p33.position-sizing-risk.v1`.
- Preserve the existing `p10.strategy-equations.v1` economic strategy version.
- Use exact finite `Decimal` inputs and exact built-in integer/boolean/text types; reject booleans as integers.
- Use local Decimal precision 50 for arithmetic and preserve the complete caller context.
- Volatility uses exactly 63 strictly increasing prior aware-UTC timestamped positive realized-volatility values, all earlier than the decision timestamp.
- Scales remain in `[0, 1]`; low volatility never increases risk above the base target.
- Contract and gross-DV01 capacity limits scale risk; every other safety failure blocks.
- Residual DV01 at exactly 5% is allowed; values above 5% are blocked.
- Functions are pure and may not import file APIs, clocks, pandas, IBKR, brokers, network clients, or order modules.
- Do not modify the legacy `risk_pipeline.py`, `MarketSnapshot`, canonical schemas, cost models, portfolio ranking, or backtests.
- P33 stops at MG5 and does not approve MG5 or begin P34.
- Apply `ponytail:ponytail` at full intensity to every implementation and review step: reuse P30/P31 code, use the standard library, add no speculative abstraction, and keep the diff as small as the safety requirements permit.
- If subagents are selected for execution or review, every subagent uses `gpt-5.6-terra` with high reasoning effort.

---

### Task 1: Causal scale primitives

**Files:**
- Create: `strategy/position_sizing.py`
- Create: `docs/tests/test_position_sizing_and_risk.py`

**Interfaces:**
- Consumes: an exact aware-UTC decision timestamp; exact `Decimal` volatility and z-score values; non-string sequences of exact `(datetime, Decimal)` pairs; exact integer quantities and displayed sizes.
- Produces: `SIZING_RISK_VERSION`, `VOLATILITY_LOOKBACK`, `MAX_RESIDUAL_FRACTION`, `volatility_scale`, `signal_strength_scale`, `liquidity_scale`, and `scaled_target_dv01`.

- [ ] **Step 1: Write failing scale tests**

Create `docs/tests/test_position_sizing_and_risk.py` with imports and literal boundary cases:

```python
from datetime import datetime, timedelta, timezone
from decimal import Decimal, getcontext, setcontext
import unittest

from strategy.position_sizing import (
    SIZING_RISK_VERSION,
    liquidity_scale,
    scaled_target_dv01,
    signal_strength_scale,
    volatility_scale,
)


D = Decimal
DECISION = datetime(2026, 1, 5, 21, 0, tzinfo=timezone.utc)


def prior_vols(value=D("0.8")):
    return tuple(
        (DECISION - timedelta(days=63 - index), value)
        for index in range(63)
    )


class ScaleTests(unittest.TestCase):
    def test_frozen_version_and_hand_examples(self):
        prior = prior_vols()
        self.assertEqual(SIZING_RISK_VERSION, "p33.position-sizing-risk.v1")
        self.assertEqual(volatility_scale(DECISION, D("1"), prior), D("0.8"))
        self.assertEqual(volatility_scale(DECISION, D("0.5"), prior), D("1"))
        self.assertEqual(signal_strength_scale(D("0")), D("0"))
        self.assertEqual(signal_strength_scale(D("1")), D("0.5"))
        self.assertEqual(signal_strength_scale(D("2")), D("1"))
        self.assertEqual(signal_strength_scale(D("-3")), D("1"))
        self.assertEqual(liquidity_scale(10, -4, 5, 4), D("0.5"))
        self.assertEqual(
            scaled_target_dv01(D("3000"), D("0.8"), D("0.5"), D("0.5")),
            D("600"),
        )

    def test_volatility_requires_exact_causal_window(self):
        valid = tuple(
            (DECISION - timedelta(days=63 - index), D(index + 1))
            for index in range(63)
        )
        self.assertIsNotNone(volatility_scale(DECISION, D("64"), valid))
        for invalid in (valid[:-1], valid + (D("64"),), "not-a-sequence"):
            self.assertIsNone(volatility_scale(DECISION, D("64"), invalid))
        future = valid[:-1] + ((DECISION, D("63")),)
        reversed_pair = valid[:30] + (valid[31], valid[30]) + valid[32:]
        naive = valid[:-1] + ((datetime(2026, 1, 4, 21, 0), D("63")),)
        for invalid in (future, reversed_pair, naive):
            self.assertIsNone(volatility_scale(DECISION, D("64"), invalid))
        for invalid in (D("0"), D("-1"), D("NaN"), 1, True):
            self.assertIsNone(volatility_scale(DECISION, invalid, valid))

    def test_zero_interior_and_full_scale_boundaries(self):
        self.assertEqual(liquidity_scale(10, -4, 0, 4), D("0"))
        self.assertEqual(liquidity_scale(10, -4, 10, 4), D("1"))
        for invalid in ((0, -4, 1, 1), (10, 0, 1, 1), (10, -4, -1, 1)):
            self.assertIsNone(liquidity_scale(*invalid))
        self.assertIsNone(scaled_target_dv01(D("3000"), D("1.1"), D("1"), D("1")))

    def test_public_scales_preserve_complete_decimal_context(self):
        original = getcontext().copy()
        try:
            context = getcontext()
            context.prec = 2
            context.rounding = "ROUND_DOWN"
            before = context.copy()
            self.assertEqual(
                volatility_scale(DECISION, D("3"), prior_vols(D("2"))),
                D("0.66666666666666666666666666666666666666666666666667"),
            )
            after = getcontext()
            self.assertEqual(after.prec, before.prec)
            self.assertEqual(after.rounding, before.rounding)
            self.assertEqual(after.traps, before.traps)
            self.assertEqual(after.flags, before.flags)
        finally:
            setcontext(original)
```

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```powershell
& '.\.venv\Scripts\python.exe' -m unittest docs.tests.test_position_sizing_and_risk -v
```

Expected: exit `1` because `strategy.position_sizing` does not exist.

- [ ] **Step 3: Implement the four minimal scale functions**

Create `strategy/position_sizing.py` with these public signatures and validation rules:

```python
from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from decimal import Decimal, localcontext


SIZING_RISK_VERSION = "p33.position-sizing-risk.v1"
VOLATILITY_LOOKBACK = 63
MAX_RESIDUAL_FRACTION = Decimal("0.05")


def _decimal(value: object, *, positive: bool = False, nonnegative: bool = False) -> Decimal | None:
    if type(value) is not Decimal or not value.is_finite():
        return None
    if positive and value <= 0:
        return None
    if nonnegative and value < 0:
        return None
    return value


def _scale(value: object) -> Decimal | None:
    decimal = _decimal(value, nonnegative=True)
    return decimal if decimal is not None and decimal <= 1 else None


def _utc(value: object) -> datetime | None:
    return value if type(value) is datetime and value.utcoffset() == timedelta(0) else None


def volatility_scale(
    decision_time_utc: object,
    current_realized_vol: object,
    prior_realized_vols: object,
) -> Decimal | None:
    decision_time = _utc(decision_time_utc)
    current = _decimal(current_realized_vol, positive=True)
    if (
        decision_time is None
        or current is None
        or not isinstance(prior_realized_vols, Sequence)
        or isinstance(prior_realized_vols, str)
        or len(prior_realized_vols) != VOLATILITY_LOOKBACK
    ):
        return None
    timestamps = []
    values = []
    for item in prior_realized_vols:
        if type(item) is not tuple or len(item) != 2:
            return None
        timestamp, value = item
        timestamp = _utc(timestamp)
        value = _decimal(value, positive=True)
        if timestamp is None or value is None or timestamp >= decision_time:
            return None
        timestamps.append(timestamp)
        values.append(value)
    if any(left >= right for left, right in zip(timestamps, timestamps[1:])):
        return None
    median = sorted(values)[VOLATILITY_LOOKBACK // 2]
    with localcontext() as context:
        context.prec = 50
        return min(Decimal("1"), median / current)


def signal_strength_scale(z_score: object) -> Decimal | None:
    z = _decimal(z_score)
    if z is None:
        return None
    with localcontext() as context:
        context.prec = 50
        return min(Decimal("1"), z.copy_abs() / Decimal("2"))


def liquidity_scale(
    swap_quantity: object,
    treasury_quantity: object,
    swap_available_contracts: object,
    treasury_available_contracts: object,
) -> Decimal | None:
    if (
        type(swap_quantity) is not int
        or type(treasury_quantity) is not int
        or not swap_quantity
        or not treasury_quantity
        or type(swap_available_contracts) is not int
        or type(treasury_available_contracts) is not int
        or swap_available_contracts < 0
        or treasury_available_contracts < 0
    ):
        return None
    with localcontext() as context:
        context.prec = 50
        return min(
            Decimal("1"),
            Decimal(swap_available_contracts) / Decimal(abs(swap_quantity)),
            Decimal(treasury_available_contracts) / Decimal(abs(treasury_quantity)),
        )


def scaled_target_dv01(
    base_target: object,
    volatility: object,
    strength: object,
    liquidity: object,
) -> Decimal | None:
    base = _decimal(base_target, positive=True)
    scales = (_scale(volatility), _scale(strength), _scale(liquidity))
    if base is None or any(scale is None for scale in scales):
        return None
    with localcontext() as context:
        context.prec = 50
        return base * scales[0] * scales[1] * scales[2]
```

- [ ] **Step 4: Run focused tests and verify GREEN**

Run the Task 1 focused command again. Expected: all `ScaleTests` pass.

- [ ] **Step 5: Commit the scale primitives**

```powershell
git add strategy/position_sizing.py
git add -f docs/tests/test_position_sizing_and_risk.py
git commit -m "feat: add P33 causal sizing scales"
```

---

### Task 2: Capacity-scaled target positions

**Files:**
- Modify: `strategy/position_sizing.py`
- Modify: `docs/tests/test_position_sizing_and_risk.py`

**Interfaces:**
- Consumes: Task 1 scales; `TradeDirection`; P31 `dv01_hedge_quantities`, `residual_dv01_usd_per_bp`, and `residual_fraction`; P30 `TargetPosition`.
- Produces: `build_target_position(...) -> TargetPosition | None` with the exact keyword-only signature below.

- [ ] **Step 1: Add failing target-position tests**

Append tests that call the wished-for API directly:

```python
from strategy import TradeDirection
from strategy.position_sizing import build_target_position


def target_kwargs(**overrides):
    values = dict(
        maturity="2Y",
        swap_instrument_id="YITH27",
        treasury_instrument_id="ZTH27",
        direction=TradeDirection.TRADITIONAL,
        base_target_dv01_usd_per_bp=D("1000"),
        decision_time_utc=DECISION,
        current_realized_vol=D("1"),
        prior_realized_vols=prior_vols(D("1")),
        z_score=D("2"),
        swap_available_contracts=100,
        treasury_available_contracts=100,
        swap_dv01_usd_per_bp=D("100"),
        treasury_dv01_usd_per_bp=D("950"),
        current_swap_quantity_contracts=0,
        current_treasury_quantity_contracts=0,
        max_swap_contracts=0,
        max_treasury_contracts=0,
        available_gross_dv01_usd_per_bp=D("10000"),
        expected_cost_usd=D("0"),
    )
    values.update(overrides)
    return values


class TargetPositionTests(unittest.TestCase):
    def test_hand_checked_traditional_basket(self):
        target = build_target_position(**target_kwargs())
        self.assertIsNotNone(target)
        self.assertEqual(target.swap_quantity_contracts, 10)
        self.assertEqual(target.treasury_quantity_contracts, -1)
        self.assertEqual(target.target_dv01_usd_per_bp, D("1000"))
        self.assertEqual(target.gross_dv01_usd_per_bp, D("1950"))
        self.assertEqual(target.residual_net_dv01_usd_per_bp, D("-50"))
        self.assertEqual(target.expected_turnover_contracts, 11)
        self.assertEqual(target.cap_diagnostic, "within_capacity")

    def test_capacity_limits_scale_instead_of_overallocating(self):
        uncapped = build_target_position(**target_kwargs(base_target_dv01_usd_per_bp=D("3000")))
        capped = build_target_position(**target_kwargs(
            base_target_dv01_usd_per_bp=D("3000"), max_swap_contracts=10,
        ))
        self.assertIsNotNone(uncapped)
        self.assertIsNotNone(capped)
        self.assertLessEqual(abs(capped.swap_quantity_contracts), 10)
        self.assertLess(capped.gross_dv01_usd_per_bp, uncapped.gross_dv01_usd_per_bp)
        self.assertEqual(capped.cap_diagnostic, "scaled_to_capacity")

    def test_tighter_capacity_never_increases_risk(self):
        gross_values = []
        for capacity in (D("5000"), D("3000"), D("2000"), D("1000")):
            target = build_target_position(**target_kwargs(
                base_target_dv01_usd_per_bp=D("3000"),
                available_gross_dv01_usd_per_bp=capacity,
            ))
            gross_values.append(D("0") if target is None else target.gross_dv01_usd_per_bp)
        self.assertEqual(gross_values, sorted(gross_values, reverse=True))

    def test_residual_boundary_and_zero_risk_fail_closed(self):
        self.assertIsNotNone(build_target_position(**target_kwargs()))
        self.assertIsNone(build_target_position(**target_kwargs(
            treasury_dv01_usd_per_bp=D("949.9"),
        )))
        self.assertIsNone(build_target_position(**target_kwargs(swap_available_contracts=0)))
        self.assertIsNone(build_target_position(**target_kwargs(
            available_gross_dv01_usd_per_bp=D("0"),
        )))

    def test_turnover_uses_current_and_target_quantities(self):
        target = build_target_position(**target_kwargs(
            current_swap_quantity_contracts=4,
            current_treasury_quantity_contracts=-1,
        ))
        self.assertEqual(target.expected_turnover_contracts, 6)
```

- [ ] **Step 2: Run the focused test and verify RED**

Run the Task 1 focused command. Expected: import failure for
`build_target_position`.

- [ ] **Step 3: Implement the target orchestrator minimally**

Add imports from `.models` and `.spread`, a private bounded basket selector,
and this exact public signature:

```python
def build_target_position(
    *,
    maturity: object,
    swap_instrument_id: object,
    treasury_instrument_id: object,
    direction: object,
    base_target_dv01_usd_per_bp: object,
    decision_time_utc: object,
    current_realized_vol: object,
    prior_realized_vols: object,
    z_score: object,
    swap_available_contracts: object,
    treasury_available_contracts: object,
    swap_dv01_usd_per_bp: object,
    treasury_dv01_usd_per_bp: object,
    current_swap_quantity_contracts: object,
    current_treasury_quantity_contracts: object,
    max_swap_contracts: object,
    max_treasury_contracts: object,
    available_gross_dv01_usd_per_bp: object,
    expected_cost_usd: object,
) -> TargetPosition | None:
```

Implement the approved sequence without new records or abstractions:

```python
vol = volatility_scale(decision_time_utc, current_realized_vol, prior_realized_vols)
strength = signal_strength_scale(z_score)
pre_liquidity_target = scaled_target_dv01(
    base_target_dv01_usd_per_bp, vol, strength, Decimal("1")
)
provisional = dv01_hedge_quantities(
    direction, pre_liquidity_target, swap_dv01, treasury_dv01
)
liquidity = liquidity_scale(*provisional, swap_available_contracts, treasury_available_contracts)
liquid_target = scaled_target_dv01(
    base_target_dv01_usd_per_bp, vol, strength, liquidity
)
bounded = _bounded_target(
    direction=direction,
    liquid_target=liquid_target,
    swap_dv01=swap_dv01,
    treasury_dv01=treasury_dv01,
    swap_available_contracts=swap_available_contracts,
    treasury_available_contracts=treasury_available_contracts,
    max_swap_contracts=max_swap_contracts,
    max_treasury_contracts=max_treasury_contracts,
    available_gross=available_gross,
)
```

`_bounded_target` validates exact nonnegative integer caps and displayed sizes
and nonnegative available gross DV01. Under local precision 50, calculate the
largest swap magnitude that does not exceed `liquid_target / swap_dv01` using
`ROUND_FLOOR`, then reduce it by displayed swap size, a nonzero configured swap
cap, and the swap-only gross upper bound. Search from that magnitude down to
one. For each candidate target `Decimal(magnitude) * swap_dv01`, reuse
`dv01_hedge_quantities`, then reject the candidate when either leg is zero,
displayed Treasury size or a configured contract cap is exceeded, gross DV01
exceeds available capacity, or
`residual_fraction(...) > MAX_RESIDUAL_FRACTION`. Return the first valid
`(target, swap_quantity, treasury_quantity, gross, residual)` tuple or `None`.
This descending bounded search is the minimal monotonic solution: tighter
capacity can never select a larger basket.

Construct `TargetPosition` directly from the bounded tuple and calculate
turnover as:

```python
abs(swap_quantity - current_swap_quantity_contracts) + abs(
    treasury_quantity - current_treasury_quantity_contracts
)
```

Use `rounding_diagnostic="minimum_residual"` and either
`cap_diagnostic="within_capacity"` or
`cap_diagnostic="scaled_to_capacity"`. The latter applies when the selected
target is below the unrestricted whole-swap target solely because a displayed
size, configured contract cap, or gross-DV01 capacity bound reduced it.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run the Task 1 focused command. Expected: all scale and target tests pass.

- [ ] **Step 5: Commit target construction**

```powershell
git add strategy/position_sizing.py
git add -f docs/tests/test_position_sizing_and_risk.py
git commit -m "feat: add P33 capacity-scaled targets"
```

---

### Task 3: Fail-closed risk signals

**Files:**
- Create: `strategy/risk_signals.py`
- Modify: `docs/tests/test_position_sizing_and_risk.py`

**Interfaces:**
- Consumes: explicit operational booleans, integer order counters, Decimal limits and measurements, and P30 `FlattenUrgency`, `NamedValue`, and `RiskDecision`.
- Produces: `evaluate_risk(...) -> RiskDecision | None` with stable reason precedence and exact evidence tuples.

- [ ] **Step 1: Write failing risk-decision tests**

Append the risk imports, helper, and test class to
`docs/tests/test_position_sizing_and_risk.py`:

```python
from decimal import Decimal
import unittest

from strategy import FlattenUrgency
from strategy.risk_signals import evaluate_risk


D = Decimal


def risk_kwargs(**overrides):
    values = dict(
        capacity_scale=D("1"),
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
        residual_fraction=D("0.05"),
        max_residual_fraction=D("0.05"),
        portfolio_gross_dv01_usd_per_bp=D("1950"),
        max_portfolio_gross_dv01_usd_per_bp=D("5000"),
        portfolio_net_dv01_usd_per_bp=D("-250"),
        max_portfolio_net_dv01_usd_per_bp=D("250"),
        orders_submitted=0,
        max_orders=5,
        working_orders=0,
        max_working_orders=5,
        session_pnl_usd=D("0"),
        max_session_loss_usd=D("1000"),
        drawdown_usd=D("0"),
        max_drawdown_usd=D("1500"),
    )
    values.update(overrides)
    return values


class RiskSignalTests(unittest.TestCase):
    def test_allowed_capacity_scale_and_evidence(self):
        decision = evaluate_risk(**risk_kwargs(capacity_scale=D("0.5")))
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.scale, D("0.5"))
        self.assertEqual(decision.reason_codes, ("capacity_scaled",))
        self.assertFalse(decision.flatten_requested)
        self.assertEqual(decision.urgency, FlattenUrgency.NONE)
        self.assertEqual(decision.limits[0].name, "max_residual_fraction")
        self.assertEqual(decision.measured_values[0].name, "capacity_scale")

    def test_hard_failure_blocks_and_flattens_existing_exposure(self):
        flat = evaluate_risk(**risk_kwargs(data_fresh=False))
        exposed = evaluate_risk(**risk_kwargs(data_fresh=False, has_open_position=True))
        self.assertEqual(flat.reason_codes, ("stale_market_data",))
        self.assertFalse(flat.flatten_requested)
        self.assertEqual(exposed.scale, D("0"))
        self.assertTrue(exposed.flatten_requested)
        self.assertEqual(exposed.urgency, FlattenUrgency.EMERGENCY)

    def test_explicit_flatten_precedence(self):
        emergency = evaluate_risk(**risk_kwargs(
            emergency_flatten=True, scheduled_flatten=True, data_fresh=False,
            has_open_position=True,
        ))
        scheduled = evaluate_risk(**risk_kwargs(
            scheduled_flatten=True, data_fresh=False, has_open_position=True,
        ))
        self.assertEqual(emergency.reason_codes, ("emergency_flatten",))
        self.assertEqual(emergency.urgency, FlattenUrgency.EMERGENCY)
        self.assertEqual(scheduled.reason_codes, ("scheduled_flatten",))
        self.assertEqual(scheduled.urgency, FlattenUrgency.SCHEDULED)

    def test_all_hard_failures_have_stable_ordered_reasons(self):
        decision = evaluate_risk(**risk_kwargs(
            data_fresh=False,
            bid_ask_valid=False,
            market_fields_valid=False,
            broker_connected=False,
            reconciled=False,
            roll_allowed=False,
            session_pnl_usd=D("-1000"),
            drawdown_usd=D("1500"),
            margin_reserve_ok=False,
            residual_fraction=D("0.0501"),
            portfolio_net_dv01_usd_per_bp=D("250.1"),
            orders_submitted=5,
            working_orders=5,
        ))
        self.assertEqual(decision.reason_codes, (
            "stale_market_data",
            "invalid_bid_ask",
            "missing_or_nonpositive_market_field",
            "broker_disconnected",
            "reconciliation_mismatch",
            "roll_restricted",
            "session_loss_limit",
            "drawdown_limit",
            "margin_reserve_failure",
            "residual_dv01_limit",
            "portfolio_net_dv01_limit",
            "order_rate_limit",
            "working_order_limit",
        ))

    def test_boundaries_and_malformed_inputs_fail_closed(self):
        self.assertTrue(evaluate_risk(**risk_kwargs()).allowed)
        self.assertFalse(evaluate_risk(**risk_kwargs(orders_submitted=5)).allowed)
        self.assertFalse(evaluate_risk(**risk_kwargs(session_pnl_usd=D("-1000"))).allowed)
        self.assertIsNone(evaluate_risk(**risk_kwargs(capacity_scale=D("1.1"))))
        self.assertIsNone(evaluate_risk(**risk_kwargs(data_fresh=1)))
        self.assertIsNone(evaluate_risk(**risk_kwargs(max_orders=True)))
        self.assertIsNone(evaluate_risk(**risk_kwargs(
            portfolio_gross_dv01_usd_per_bp=D("5000.1"),
        )))
```

- [ ] **Step 2: Run the focused risk test and verify RED**

Run:

```powershell
& '.\.venv\Scripts\python.exe' -m unittest docs.tests.test_position_sizing_and_risk -v
```

Expected: exit `1` because `strategy.risk_signals` does not exist.

- [ ] **Step 3: Implement one explicit evaluator**

Create `strategy/risk_signals.py` with this signature and no additional public
configuration types:

```python
def evaluate_risk(
    *,
    capacity_scale: object,
    has_open_position: object,
    emergency_flatten: object,
    scheduled_flatten: object,
    data_fresh: object,
    bid_ask_valid: object,
    market_fields_valid: object,
    broker_connected: object,
    reconciled: object,
    roll_allowed: object,
    margin_reserve_ok: object,
    residual_fraction: object,
    max_residual_fraction: object,
    portfolio_gross_dv01_usd_per_bp: object,
    max_portfolio_gross_dv01_usd_per_bp: object,
    portfolio_net_dv01_usd_per_bp: object,
    max_portfolio_net_dv01_usd_per_bp: object,
    orders_submitted: object,
    max_orders: object,
    working_orders: object,
    max_working_orders: object,
    session_pnl_usd: object,
    max_session_loss_usd: object,
    drawdown_usd: object,
    max_drawdown_usd: object,
) -> RiskDecision | None:
```

Validate all booleans with `type(value) is bool`, counters with exact
nonnegative integers, the capacity scale in `[0, 1]`, signed session P&L and
signed portfolio net DV01 as finite Decimals, and gross DV01, residual,
drawdown, and numeric limits as finite nonnegative Decimals. Require positive
session-loss and drawdown limits. Post-scale portfolio gross DV01 above its
declared maximum is an inconsistent caller state and returns `None`; Task 2
must scale it before risk evaluation. Return `None` for every malformed input.

Return explicit emergency or scheduled decisions before evaluating other
conditions. Otherwise append the exact reason codes from the test in the same
order. Use these inclusive/exclusive comparisons:

```python
loss_breached = -session_pnl_usd >= max_session_loss_usd
drawdown_breached = drawdown_usd >= max_drawdown_usd
residual_breached = residual_fraction > max_residual_fraction
net_breached = portfolio_net_dv01_usd_per_bp.copy_abs() > max_portfolio_net_dv01_usd_per_bp
order_rate_breached = orders_submitted >= max_orders
working_order_breached = working_orders >= max_working_orders
```

For any reasons, return `allowed=False`, `scale=Decimal("0")`, and request an
emergency flatten only when `has_open_position` is true. With no reasons,
return `allowed=True`, the validated capacity scale, reason
`capacity_scaled` when below 1 or `within_limits` at 1, no flatten, and
`FlattenUrgency.NONE`.

Construct stable `limits` in this order:

```text
max_residual_fraction, max_portfolio_gross_dv01, max_portfolio_net_dv01,
max_orders, max_working_orders, max_session_loss, max_drawdown
```

Construct stable `measured_values` in this order:

```text
capacity_scale, portfolio_gross_dv01, residual_fraction, portfolio_net_dv01,
orders_submitted, working_orders, session_pnl, drawdown
```

Convert exact integer counters to `Decimal` only when building `NamedValue`.

- [ ] **Step 4: Run focused risk tests and verify GREEN**

Run the Task 3 focused command. Expected: all `RiskSignalTests` pass.

- [ ] **Step 5: Commit risk signals**

```powershell
git add strategy/risk_signals.py
git add -f docs/tests/test_position_sizing_and_risk.py
git commit -m "feat: add P33 fail-closed risk signals"
```

---

### Task 4: Public API, equation record, and P33 evidence

**Files:**
- Modify: `strategy/__init__.py`
- Modify: `docs/research/strategy-equations.md`
- Modify: `docs/tests/test_position_sizing_and_risk.py`
- Create: `docs/verification/P33.md`

**Interfaces:**
- Consumes: all public Task 1–3 functions and constants.
- Produces: stable imports from `strategy`; authoritative P33 equations and hand examples; reproducible verification evidence.

- [ ] **Step 1: Add failing public-export and import-boundary tests**

Add assertions that import these names from `strategy`:

```python
from strategy import (
    MAX_RESIDUAL_FRACTION,
    SIZING_RISK_VERSION,
    VOLATILITY_LOOKBACK,
    build_target_position,
    evaluate_risk,
    liquidity_scale,
    scaled_target_dv01,
    signal_strength_scale,
    volatility_scale,
)
```

Add a behavior test that launches `sys.executable -S -c` in a fresh process,
imports `strategy.position_sizing` and `strategy.risk_signals`, and asserts
that `sys.modules` contains none of `pandas`, `ib_insync`, `requests`,
`urllib3`, `socket`, `pathlib`, `agents.agent_0.broker`, or
`agents.agent_0.orders`. Assert the subprocess exits zero and prints an empty
loaded-forbidden list. Do not inspect or grep production source text. Direct
review verifies that the two P33 modules do not use wall-clock or file APIs;
the reused P30 models legitimately import `datetime` for immutable records.

- [ ] **Step 2: Run both focused modules and verify RED**

Run:

```powershell
& '.\.venv\Scripts\python.exe' -m unittest docs.tests.test_position_sizing_and_risk -v
```

Expected: public imports fail because `strategy/__init__.py` does not export
the P33 API.

- [ ] **Step 3: Export the P33 API and document the frozen equations**

Add direct imports from `.position_sizing` and `.risk_signals` to
`strategy/__init__.py`, and append the nine names to `__all__` in the same
order shown in Step 1.

Add a `P33 position sizing and risk` section to
`docs/research/strategy-equations.md` containing the exact four formulas,
63-row causal window, capacity order, 5% residual rule, hard-failure list,
reason precedence, version `p33.position-sizing-risk.v1`, and the hand-worked
examples from the approved design. State that these formulas are a new P33
configuration version and do not reinterpret `p10.strategy-equations.v1`.

- [ ] **Step 4: Run focused and full verification**

Run all commands and retain their literal exit codes and test counts:

```powershell
$python = '.\.venv\Scripts\python.exe'
& $python -m unittest docs.tests.test_position_sizing_and_risk -v
& $python -m unittest discover -s docs/tests -v
& $python -m unittest discover -s agents/agent_0/tests -v
& $python -m compileall -q strategy docs/tests agents/agent_0
& $python -m data_pipeline.historical_data.historical_data_builder --self-check
& $python signal_pipeline.py --self-check
& $python risk_pipeline.py --self-check
& $python backtest_engine.py --self-check
git diff --check
```

Expected: every command exits `0`; both focused modules pass; the full existing
suites remain green; compileall is quiet; four self-checks report success; and
`git diff --check` is silent.

- [ ] **Step 5: Write the verification record**

Create `docs/verification/P33.md` with these sections and only fresh evidence:

```text
# P33 position-sizing and risk verification
## Scope and versions
## Scale boundary evidence
## Hand-checked hedge and capacity evidence
## Risk precedence and flatten evidence
## Causality and Decimal-context evidence
## TDD evidence
## Full verification matrix
## Known limitations and MG5 status
```

Record the RED failure for each task, the corresponding GREEN command, literal
hand-example inputs and outputs, final test counts, self-check results, and
`git diff --check`. Explicitly state that bid/ask sizes and operational flags
are caller-supplied causal inputs, P33 does not estimate costs, no external
connection occurred, MG5 remains unapproved, and P34 did not begin.

- [ ] **Step 6: Self-review and commit P33 integration**

Check the implementation against every P33 prompt item, scan the new plan and
verification record for placeholders, confirm signatures and names match
across modules/tests/docs, and inspect the final diff for unrelated changes.

```powershell
git add strategy/__init__.py
git add -f docs/research/strategy-equations.md docs/tests/test_position_sizing_and_risk.py docs/verification/P33.md
git commit -m "feat: complete P33 sizing and risk core"
```
