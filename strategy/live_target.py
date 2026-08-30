from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal, localcontext

from .live_signal import LIVE_SIGNAL_STRATEGY_VERSION, LiveSignalResult
from .models import TradeDirection
from .spread import dv01_hedge_quantities


DEFAULT_MIN_TARGET_DV01 = Decimal("100")
DEFAULT_MAX_GROSS_DV01 = Decimal("10000")
DEFAULT_MAX_NET_DV01 = Decimal("250")
MIN_VOL_SCALE = Decimal("0.25")
MAX_VOL_SCALE = Decimal("1.00")


def _require_decimal(
    name: str,
    value: object,
    *,
    positive: bool = False,
    nonnegative: bool = False,
) -> Decimal:
    if type(value) is not Decimal or not value.is_finite():
        raise TypeError(f"{name} must be a finite Decimal")
    if positive and value <= 0:
        raise ValueError(f"{name} must be positive")
    if nonnegative and value < 0:
        raise ValueError(f"{name} must be nonnegative")
    return value


def _clip_quantity(quantity: int, cap: int) -> tuple[int, bool]:
    if type(cap) is not int or cap < 0:
        raise ValueError("contract cap must be a nonnegative integer")
    if cap == 0 or abs(quantity) <= cap:
        return quantity, False
    return (cap if quantity > 0 else -cap), True


@dataclass(frozen=True)
class MaturityRiskInputs:
    base_target_dv01: Decimal
    vol_scale: Decimal
    swap_dv01_per_contract: Decimal
    treasury_dv01_per_contract: Decimal
    max_swap_contracts: int = 0
    max_treasury_contracts: int = 0

    def __post_init__(self) -> None:
        _require_decimal("base_target_dv01", self.base_target_dv01, nonnegative=True)
        vol = _require_decimal("vol_scale", self.vol_scale, nonnegative=True)
        if vol < MIN_VOL_SCALE or vol > MAX_VOL_SCALE:
            raise ValueError("vol_scale is outside configured live sizing bounds")
        _require_decimal(
            "swap_dv01_per_contract", self.swap_dv01_per_contract, positive=True
        )
        _require_decimal(
            "treasury_dv01_per_contract",
            self.treasury_dv01_per_contract,
            positive=True,
        )
        if type(self.max_swap_contracts) is not int or self.max_swap_contracts < 0:
            raise ValueError("max_swap_contracts must be nonnegative")
        if (
            type(self.max_treasury_contracts) is not int
            or self.max_treasury_contracts < 0
        ):
            raise ValueError("max_treasury_contracts must be nonnegative")


@dataclass(frozen=True)
class MaturityTarget:
    maturity: str
    state: int
    z_score: Decimal | None
    signal_strength_scale: Decimal
    vol_scale: Decimal
    target_dv01: Decimal
    swap_quantity: int
    treasury_quantity: int
    signed_swap_dv01: Decimal
    signed_treasury_dv01: Decimal
    residual_dv01: Decimal
    swap_contract_cap_hit: bool
    treasury_contract_cap_hit: bool
    blocked: bool
    reason_codes: tuple[str, ...]


@dataclass(frozen=True)
class LiveTarget:
    strategy_version: str
    snapshot_ids: tuple[str, ...]
    maturities: dict[str, MaturityTarget]
    gross_target_dv01: Decimal
    net_rate_dv01: Decimal
    dv01_cap_scale: Decimal
    blocked: bool
    reason_codes: tuple[str, ...]


def _signal_strength(signal: LiveSignalResult) -> Decimal:
    if signal.z_score is None or signal.state == 0:
        return Decimal("0")
    return min(abs(signal.z_score) / Decimal("2"), Decimal("1"))


def _pre_target(
    maturity: str,
    signal: LiveSignalResult,
    risk: MaturityRiskInputs,
    min_target_dv01: Decimal,
) -> MaturityTarget:
    if signal.strategy_version != LIVE_SIGNAL_STRATEGY_VERSION:
        return MaturityTarget(
            maturity=maturity,
            state=0,
            z_score=signal.z_score,
            signal_strength_scale=Decimal("0"),
            vol_scale=risk.vol_scale,
            target_dv01=Decimal("0"),
            swap_quantity=0,
            treasury_quantity=0,
            signed_swap_dv01=Decimal("0"),
            signed_treasury_dv01=Decimal("0"),
            residual_dv01=Decimal("0"),
            swap_contract_cap_hit=False,
            treasury_contract_cap_hit=False,
            blocked=True,
            reason_codes=("strategy_version_mismatch",),
        )

    if signal.blocked or signal.state == 0 or signal.z_score is None:
        return MaturityTarget(
            maturity=maturity,
            state=0 if signal.blocked else signal.state,
            z_score=signal.z_score,
            signal_strength_scale=Decimal("0"),
            vol_scale=risk.vol_scale,
            target_dv01=Decimal("0"),
            swap_quantity=0,
            treasury_quantity=0,
            signed_swap_dv01=Decimal("0"),
            signed_treasury_dv01=Decimal("0"),
            residual_dv01=Decimal("0"),
            swap_contract_cap_hit=False,
            treasury_contract_cap_hit=False,
            blocked=signal.blocked,
            reason_codes=signal.reason_codes if signal.blocked else ("flat_signal",),
        )

    strength = _signal_strength(signal)
    target = risk.base_target_dv01 * risk.vol_scale * strength
    if target < min_target_dv01:
        target = Decimal("0")

    return MaturityTarget(
        maturity=maturity,
        state=signal.state,
        z_score=signal.z_score,
        signal_strength_scale=strength,
        vol_scale=risk.vol_scale,
        target_dv01=target,
        swap_quantity=0,
        treasury_quantity=0,
        signed_swap_dv01=Decimal("0"),
        signed_treasury_dv01=Decimal("0"),
        residual_dv01=Decimal("0"),
        swap_contract_cap_hit=False,
        treasury_contract_cap_hit=False,
        blocked=False,
        reason_codes=("within_sizing_model",),
    )


def _size_contracts(target: MaturityTarget, risk: MaturityRiskInputs) -> MaturityTarget:
    if target.blocked or target.target_dv01 <= 0 or target.state == 0:
        return target

    swap_quantity, treasury_quantity = dv01_hedge_quantities(
        TradeDirection(target.state),
        target.target_dv01,
        risk.swap_dv01_per_contract,
        risk.treasury_dv01_per_contract,
    )
    swap_quantity, swap_cap_hit = _clip_quantity(
        swap_quantity, risk.max_swap_contracts
    )
    signed_swap = Decimal(swap_quantity) * risk.swap_dv01_per_contract

    if swap_cap_hit:
        _, treasury_quantity = dv01_hedge_quantities(
            TradeDirection(target.state),
            signed_swap.copy_abs(),
            risk.swap_dv01_per_contract,
            risk.treasury_dv01_per_contract,
        )
    treasury_quantity, treasury_cap_hit = _clip_quantity(
        treasury_quantity, risk.max_treasury_contracts
    )
    signed_treasury = (
        Decimal(treasury_quantity) * risk.treasury_dv01_per_contract
    )
    residual = signed_swap + signed_treasury

    return replace(
        target,
        swap_quantity=swap_quantity,
        treasury_quantity=treasury_quantity,
        signed_swap_dv01=signed_swap,
        signed_treasury_dv01=signed_treasury,
        residual_dv01=residual,
        swap_contract_cap_hit=swap_cap_hit,
        treasury_contract_cap_hit=treasury_cap_hit,
    )


def _zero_all(
    targets: dict[str, MaturityTarget], reason: str
) -> dict[str, MaturityTarget]:
    output = {}
    for maturity, target in targets.items():
        output[maturity] = replace(
            target,
            target_dv01=Decimal("0"),
            swap_quantity=0,
            treasury_quantity=0,
            signed_swap_dv01=Decimal("0"),
            signed_treasury_dv01=Decimal("0"),
            residual_dv01=Decimal("0"),
            blocked=True,
            reason_codes=tuple(dict.fromkeys((*target.reason_codes, reason))),
        )
    return output


def build_live_target(
    *,
    signals: dict[str, LiveSignalResult],
    risk_inputs: dict[str, MaturityRiskInputs],
    min_target_dv01: Decimal = DEFAULT_MIN_TARGET_DV01,
    max_gross_dv01: Decimal = DEFAULT_MAX_GROSS_DV01,
    max_net_dv01: Decimal = DEFAULT_MAX_NET_DV01,
) -> LiveTarget:
    minimum = _require_decimal(
        "min_target_dv01", min_target_dv01, nonnegative=True
    )
    max_gross = _require_decimal(
        "max_gross_dv01", max_gross_dv01, positive=True
    )
    max_net = _require_decimal("max_net_dv01", max_net_dv01, nonnegative=True)

    if set(signals) != set(risk_inputs):
        raise ValueError("signals and risk_inputs must contain identical maturities")

    targets = {
        maturity: _pre_target(
            maturity, signals[maturity], risk_inputs[maturity], minimum
        )
        for maturity in sorted(signals)
    }

    gross_before = sum(
        (target.target_dv01.copy_abs() for target in targets.values()),
        Decimal("0"),
    )
    cap_scale = Decimal("1")
    if gross_before > max_gross:
        with localcontext() as context:
            context.prec = 28
            cap_scale = max_gross / gross_before
        targets = {
            maturity: replace(
                target,
                target_dv01=target.target_dv01 * cap_scale,
            )
            for maturity, target in targets.items()
        }

    sized = {
        maturity: _size_contracts(target, risk_inputs[maturity])
        for maturity, target in targets.items()
    }

    for _ in range(8):
        actual_gross = sum(
            (
                target.signed_swap_dv01.copy_abs()
                + target.signed_treasury_dv01.copy_abs()
                for target in sized.values()
            ),
            Decimal("0"),
        )
        if actual_gross <= max_gross:
            break
        with localcontext() as context:
            context.prec = 28
            actual_scale = max_gross / actual_gross
        cap_scale *= actual_scale
        targets = {
            maturity: replace(
                target,
                target_dv01=target.target_dv01 * actual_scale,
            )
            for maturity, target in targets.items()
        }
        sized = {
            maturity: _size_contracts(target, risk_inputs[maturity])
            for maturity, target in targets.items()
        }
    actual_gross = sum(
        (
            target.signed_swap_dv01.copy_abs()
            + target.signed_treasury_dv01.copy_abs()
            for target in sized.values()
        ),
        Decimal("0"),
    )

    net_dv01 = sum(
        (
            target.signed_swap_dv01 + target.signed_treasury_dv01
            for target in sized.values()
        ),
        Decimal("0"),
    )

    reasons: list[str] = []
    if actual_gross > max_gross:
        reasons.append("portfolio_gross_dv01_limit")
        sized = _zero_all(sized, "portfolio_gross_dv01_limit")
        actual_gross = Decimal("0")
        net_dv01 = Decimal("0")
    elif abs(net_dv01) > max_net:
        reasons.append("portfolio_net_dv01_limit")
        sized = _zero_all(sized, "portfolio_net_dv01_limit")
        net_dv01 = Decimal("0")

    gross_after = actual_gross
    if not reasons:
        blocked_maturities = [
            maturity for maturity, target in sized.items() if target.blocked
        ]
        if blocked_maturities:
            reasons.extend(f"{maturity}:blocked" for maturity in blocked_maturities)

    return LiveTarget(
        strategy_version=LIVE_SIGNAL_STRATEGY_VERSION,
        snapshot_ids=tuple(signals[m].snapshot_id for m in sorted(signals)),
        maturities=sized,
        gross_target_dv01=gross_after,
        net_rate_dv01=net_dv01,
        dv01_cap_scale=cap_scale,
        blocked=bool(reasons),
        reason_codes=tuple(reasons) if reasons else ("within_limits",),
    )
