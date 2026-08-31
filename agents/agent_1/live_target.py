from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

from data_pipeline.live_data_pipeline.auto_refresh import Agent1DataRefresher
from data_pipeline.live_data_pipeline.eris_reference_data import CsvErisReferenceProvider
from data_pipeline.live_data_pipeline.live_market_source import IbkrLiveMarketSource
from data_pipeline.live_data_pipeline.live_signal_runner import LiveSignalRunner
from strategy.live_target import MaturityRiskInputs

from .contracts import resolve_strategy_bindings
from .targets import LiveSignalTargetProvider


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LIVE_DATA_DIR = PROJECT_ROOT / "data" / "live_signal"
DEFAULT_BASELINE_PATH = DEFAULT_LIVE_DATA_DIR / "baseline.csv"
DEFAULT_REFERENCE_PATH = DEFAULT_LIVE_DATA_DIR / "eris_reference.csv"
DEFAULT_CONTRACT_RISK_PATH = DEFAULT_LIVE_DATA_DIR / "contract_risk.csv"


def _bootstrap_risk_inputs(agent_config: object) -> dict[str, MaturityRiskInputs]:
    # Replaced by exact refreshed ERIS DV01 values before each live target.
    return {
        "2Y": MaturityRiskInputs(
            base_target_dv01=Decimal("3000"),
            vol_scale=Decimal("1"),
            swap_dv01_per_contract=Decimal("20"),
            treasury_dv01_per_contract=Decimal("40"),
            max_swap_contracts=getattr(agent_config, "max_2y_swap_contracts"),
            max_treasury_contracts=getattr(agent_config, "max_2y_treasury_contracts"),
        ),
        "5Y": MaturityRiskInputs(
            base_target_dv01=Decimal("3000"),
            vol_scale=Decimal("1"),
            swap_dv01_per_contract=Decimal("50"),
            treasury_dv01_per_contract=Decimal("50"),
            max_swap_contracts=getattr(agent_config, "max_5y_swap_contracts"),
            max_treasury_contracts=getattr(agent_config, "max_5y_treasury_contracts"),
        ),
    }


def build_live_target_provider(
    *,
    ib: Any,
    agent_config: object,
    audit_path: Path,
    state_path: Path,
    contract_risk_path: Path = DEFAULT_CONTRACT_RISK_PATH,
    held_contracts: dict[str, int] | None = None,
) -> LiveSignalTargetProvider:
    runner = LiveSignalRunner(
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
        risk_inputs=_bootstrap_risk_inputs(agent_config),
        audit_path=audit_path,
        state_path=state_path,
        quote_max_age_seconds=int(getattr(agent_config, "max_quote_age_seconds")),
        max_gross_dv01=getattr(agent_config, "max_gross_dv01"),
        max_net_dv01=getattr(agent_config, "max_net_dv01"),
    )
    return LiveSignalTargetProvider(
        runner=runner,
        refresher=Agent1DataRefresher(
            ib=ib,
            agent_config=agent_config,
            baseline_path=DEFAULT_BASELINE_PATH,
            reference_path=DEFAULT_REFERENCE_PATH,
            contract_risk_path=contract_risk_path,
            binding_resolver=resolve_strategy_bindings,
            held_contracts=held_contracts,
        ),
    )
