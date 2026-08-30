from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
from typing import Any

from data_pipeline.live_data_pipeline.eris_reference_data import (
    CsvErisReferenceProvider,
)
from data_pipeline.live_data_pipeline.auto_refresh import Agent1DataRefresher
from data_pipeline.live_data_pipeline.live_market_source import IbkrLiveMarketSource
from data_pipeline.live_data_pipeline.shadow_runner import ShadowLiveSignalRunner
from strategy.live_target import MaturityRiskInputs

from .target_provider import LiveSignalTargetProvider, ShadowLiveTargetProvider


class ShadowConfigError(RuntimeError):
    pass


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LIVE_DATA_DIR = PROJECT_ROOT / "data" / "live_signal"
DEFAULT_BASELINE_PATH = DEFAULT_LIVE_DATA_DIR / "baseline.csv"
DEFAULT_REFERENCE_PATH = DEFAULT_LIVE_DATA_DIR / "eris_reference.csv"
DEFAULT_CONTRACT_RISK_PATH = DEFAULT_LIVE_DATA_DIR / "contract_risk.csv"


@dataclass(frozen=True)
class ShadowSettings:
    baseline_path: Path
    eris_reference_path: Path
    risk_inputs: dict[str, MaturityRiskInputs]
    quote_max_age_seconds: int
    reference_max_age_seconds: int


def _positive_int(value: object, name: str) -> int:
    if type(value) is not int or value <= 0:
        raise ShadowConfigError(f"{name} must be a positive integer")
    return value


def _decimal(value: object, name: str) -> Decimal:
    if isinstance(value, bool):
        raise ShadowConfigError(f"{name} must be a finite number")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ShadowConfigError(f"{name} must be a finite number") from exc
    if not result.is_finite():
        raise ShadowConfigError(f"{name} must be a finite number")
    return result


def _path(base: Path, value: object, name: str) -> Path:
    if type(value) is not str or not value.strip():
        raise ShadowConfigError(f"{name} must be a non-empty path")
    candidate = Path(value.strip())
    return candidate if candidate.is_absolute() else base / candidate


def load_shadow_settings(path: Path, agent_config: object) -> ShadowSettings:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ShadowConfigError(f"Could not load shadow configuration: {path}") from exc
    if not isinstance(raw, dict):
        raise ShadowConfigError("Shadow configuration must be a JSON object")

    risks = raw.get("risk_inputs")
    if not isinstance(risks, dict) or set(risks) != {"2Y", "5Y"}:
        raise ShadowConfigError("risk_inputs must contain exactly 2Y and 5Y")

    parsed_risks = {}
    for maturity in ("2Y", "5Y"):
        values = risks[maturity]
        if not isinstance(values, dict):
            raise ShadowConfigError(f"risk_inputs.{maturity} must be an object")
        try:
            parsed_risks[maturity] = MaturityRiskInputs(
                base_target_dv01=_decimal(
                    values["base_target_dv01"],
                    f"risk_inputs.{maturity}.base_target_dv01",
                ),
                vol_scale=_decimal(
                    values["vol_scale"], f"risk_inputs.{maturity}.vol_scale"
                ),
                swap_dv01_per_contract=_decimal(
                    values["swap_dv01_per_contract"],
                    f"risk_inputs.{maturity}.swap_dv01_per_contract",
                ),
                treasury_dv01_per_contract=_decimal(
                    values["treasury_dv01_per_contract"],
                    f"risk_inputs.{maturity}.treasury_dv01_per_contract",
                ),
                max_swap_contracts=getattr(
                    agent_config, f"max_{maturity.lower()}_swap_contracts"
                ),
                max_treasury_contracts=getattr(
                    agent_config, f"max_{maturity.lower()}_treasury_contracts"
                ),
            )
        except KeyError as exc:
            raise ShadowConfigError(
                f"risk_inputs.{maturity} is missing {exc.args[0]}"
            ) from exc
        except (TypeError, ValueError) as exc:
            raise ShadowConfigError(f"Invalid risk_inputs.{maturity}: {exc}") from exc

    base = path.resolve().parent
    try:
        baseline = _path(base, raw["baseline_path"], "baseline_path")
        reference = _path(
            base, raw["eris_reference_path"], "eris_reference_path"
        )
    except KeyError as exc:
        raise ShadowConfigError(f"Shadow configuration is missing {exc.args[0]}") from exc

    return ShadowSettings(
        baseline_path=baseline,
        eris_reference_path=reference,
        risk_inputs=parsed_risks,
        quote_max_age_seconds=_positive_int(
            raw.get("quote_max_age_seconds", 30), "quote_max_age_seconds"
        ),
        reference_max_age_seconds=_positive_int(
            raw.get("reference_max_age_seconds", 86400),
            "reference_max_age_seconds",
        ),
    )


def build_shadow_provider(
    *,
    ib: Any,
    config_path: Path,
    agent_config: object,
    audit_path: Path,
    state_path: Path,
) -> ShadowLiveTargetProvider:
    settings = load_shadow_settings(config_path, agent_config)
    runner = ShadowLiveSignalRunner(
        market_source=IbkrLiveMarketSource(
            ib,
            quote_max_age_seconds=settings.quote_max_age_seconds,
            min_days_to_expiry=getattr(agent_config, "min_days_to_expiry"),
        ),
        reference_provider=CsvErisReferenceProvider(
            settings.eris_reference_path,
            max_age_seconds=settings.reference_max_age_seconds,
        ),
        model_state_path=settings.baseline_path,
        risk_inputs=settings.risk_inputs,
        audit_path=audit_path,
        state_path=state_path,
        quote_max_age_seconds=settings.quote_max_age_seconds,
        max_gross_dv01=getattr(agent_config, "max_gross_dv01"),
        max_net_dv01=getattr(agent_config, "max_net_dv01"),
    )
    return ShadowLiveTargetProvider(runner)


def _bootstrap_risk_inputs(agent_config: object) -> dict[str, MaturityRiskInputs]:
    # Replaced by exact refreshed ERIS DV01 values before every live observation.
    return {
        "2Y": MaturityRiskInputs(
            base_target_dv01=Decimal("3000"),
            vol_scale=Decimal("1"),
            swap_dv01_per_contract=Decimal("20"),
            treasury_dv01_per_contract=Decimal("40"),
            max_swap_contracts=getattr(agent_config, "max_2y_swap_contracts"),
            max_treasury_contracts=getattr(
                agent_config, "max_2y_treasury_contracts"
            ),
        ),
        "5Y": MaturityRiskInputs(
            base_target_dv01=Decimal("3000"),
            vol_scale=Decimal("1"),
            swap_dv01_per_contract=Decimal("50"),
            treasury_dv01_per_contract=Decimal("50"),
            max_swap_contracts=getattr(agent_config, "max_5y_swap_contracts"),
            max_treasury_contracts=getattr(
                agent_config, "max_5y_treasury_contracts"
            ),
        ),
    }


def build_auto_live_provider(
    *,
    ib: Any,
    agent_config: object,
    audit_path: Path,
    state_path: Path,
    contract_risk_path: Path = DEFAULT_CONTRACT_RISK_PATH,
    executable: bool = True,
    held_contracts: dict[str, int] | None = None,
) -> LiveSignalTargetProvider | ShadowLiveTargetProvider:
    risk_inputs = _bootstrap_risk_inputs(agent_config)
    runner = ShadowLiveSignalRunner(
        market_source=IbkrLiveMarketSource(
            ib,
            quote_max_age_seconds=int(getattr(agent_config, "max_quote_age_seconds")),
            min_days_to_expiry=getattr(agent_config, "min_days_to_expiry"),
        ),
        reference_provider=CsvErisReferenceProvider(
            DEFAULT_REFERENCE_PATH,
            max_age_seconds=4 * 24 * 60 * 60,
        ),
        model_state_path=DEFAULT_BASELINE_PATH,
        risk_inputs=risk_inputs,
        audit_path=audit_path,
        state_path=state_path,
        quote_max_age_seconds=int(getattr(agent_config, "max_quote_age_seconds")),
        max_gross_dv01=getattr(agent_config, "max_gross_dv01"),
        max_net_dv01=getattr(agent_config, "max_net_dv01"),
    )
    refresher = Agent1DataRefresher(
        ib=ib,
        agent_config=agent_config,
        baseline_path=DEFAULT_BASELINE_PATH,
        reference_path=DEFAULT_REFERENCE_PATH,
        contract_risk_path=contract_risk_path,
        held_contracts=held_contracts,
    )
    if executable:
        return LiveSignalTargetProvider(runner=runner, refresher=refresher)
    return ShadowLiveTargetProvider(runner=runner, refresher=refresher)


def render_shadow_result(result: Any) -> str:
    lines = [
        "mode=shadow-only",
        f"executable_target_changed={result.executable_target_changed}",
    ]
    for maturity in ("2Y", "5Y"):
        signal = result.signals[maturity]
        target = result.hypothetical_target.maturities[maturity]
        lines.append(
            f"{maturity}: state={signal.state} blocked={target.blocked} "
            f"swap={target.swap_quantity} treasury={target.treasury_quantity} "
            f"reasons={'|'.join(target.reason_codes)}"
        )
    return "\n".join(lines)
