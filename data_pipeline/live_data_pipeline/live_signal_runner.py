from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
from typing import Any, Callable

from strategy.live_signal import (
    DailySpreadObservation,
    HistoricalModelState,
    LIVE_SIGNAL_STRATEGY_VERSION,
    LiveSignalResult,
    evaluate_daily_signal,
)
from strategy.live_target import (
    DEFAULT_MAX_GROSS_DV01,
    DEFAULT_MAX_NET_DV01,
    LiveTarget,
    MaturityRiskInputs,
    build_live_target,
)

from .live_signal_store import append_rows
from .model_state import (
    load_daily_observation,
    load_model_state,
    load_signal_state,
    save_signal_state,
)


MATURITY_FRED_SERIES = {"2Y": "DGS2", "5Y": "DGS5"}


@dataclass(frozen=True)
class LiveSignalCycleResult:
    timestamp_utc: datetime
    observation_time_utc: datetime
    signals: dict[str, LiveSignalResult]
    target: LiveTarget


def _stable_hash(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _blocked_signal(
    maturity: str,
    prior_state: int,
    reasons: list[str],
    observation: DailySpreadObservation | None,
    model: HistoricalModelState | None,
) -> LiveSignalResult:
    return LiveSignalResult(
        maturity=maturity,
        strategy_version=LIVE_SIGNAL_STRATEGY_VERSION,
        snapshot_id=_stable_hash(
            [LIVE_SIGNAL_STRATEGY_VERSION, maturity, prior_state, reasons, observation, model]
        ),
        mid_spread_bps=observation.spread_bps if observation else None,
        spread_bid_side_bps=None,
        spread_ask_side_bps=None,
        historical_mean_bps=model.mean_bps if model else None,
        historical_std_bps=model.std_bps if model else None,
        z_score=None,
        prior_state=prior_state,
        state=0,
        blocked=True,
        reason_codes=tuple(dict.fromkeys(reasons)),
    )


class LiveSignalRunner:
    def __init__(
        self,
        *,
        model_state_path: Path,
        risk_inputs: dict[str, MaturityRiskInputs],
        audit_path: Path,
        state_path: Path,
        model_state_loader: Callable[[Path, str, datetime], HistoricalModelState] = load_model_state,
        observation_loader: Callable[[Path, str, datetime], DailySpreadObservation] = load_daily_observation,
        max_gross_dv01: Decimal = DEFAULT_MAX_GROSS_DV01,
        max_net_dv01: Decimal = DEFAULT_MAX_NET_DV01,
    ) -> None:
        if set(risk_inputs) != set(MATURITY_FRED_SERIES):
            raise ValueError("risk_inputs must contain exactly 2Y and 5Y")
        self.model_state_path = Path(model_state_path)
        self.model_state_loader = model_state_loader
        self.observation_loader = observation_loader
        self.risk_inputs = risk_inputs
        self.audit_path = Path(audit_path)
        self.state_path = Path(state_path)
        self.max_gross_dv01 = max_gross_dv01
        self.max_net_dv01 = max_net_dv01

    def run_once(
        self,
        now: datetime,
        *,
        risk_inputs: dict[str, MaturityRiskInputs] | None = None,
    ) -> LiveSignalCycleResult:
        if now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        now_utc = now.astimezone(timezone.utc)
        prior_state = load_signal_state(self.state_path)
        observations: dict[str, DailySpreadObservation | None] = {}
        models: dict[str, HistoricalModelState | None] = {}
        errors: dict[str, list[str]] = {maturity: [] for maturity in MATURITY_FRED_SERIES}

        for maturity in MATURITY_FRED_SERIES:
            try:
                observations[maturity] = self.observation_loader(
                    self.model_state_path, maturity, now_utc
                )
            except Exception:
                observations[maturity] = None
                errors[maturity].append("missing_daily_observation")
            try:
                models[maturity] = self.model_state_loader(
                    self.model_state_path, maturity, now_utc
                )
            except Exception:
                models[maturity] = None
                errors[maturity].append("missing_historical_model_state")

        dates = {
            observation.observed_at.astimezone(timezone.utc).date()
            for observation in observations.values()
            if observation is not None and observation.observed_at.utcoffset() is not None
        }
        if len(dates) > 1:
            for maturity in errors:
                errors[maturity].append("misaligned_observation_dates")

        data_hash = _stable_hash(observations)
        signals: dict[str, LiveSignalResult] = {}
        for maturity in MATURITY_FRED_SERIES:
            observation = observations[maturity]
            model = models[maturity]
            if not errors[maturity] and observation is not None and model is not None:
                signals[maturity] = evaluate_daily_signal(
                    observation=observation,
                    model=model,
                    prior_state=prior_state.get(maturity, 0),
                )
            else:
                signals[maturity] = _blocked_signal(
                    maturity,
                    prior_state.get(maturity, 0),
                    errors[maturity] or ["blocked_signal"],
                    observation,
                    model,
                )

        target = build_live_target(
            signals=signals,
            risk_inputs=risk_inputs or self.risk_inputs,
            max_gross_dv01=self.max_gross_dv01,
            max_net_dv01=self.max_net_dv01,
        )
        append_rows(
            self.audit_path,
            [
                self._audit_row(
                    now_utc,
                    maturity,
                    observations[maturity],
                    signals[maturity],
                    target,
                    data_hash,
                )
                for maturity in MATURITY_FRED_SERIES
            ],
        )
        save_signal_state(
            self.state_path,
            {maturity: signal.state for maturity, signal in signals.items()},
        )

        observation_times = [
            observation.observed_at.astimezone(timezone.utc)
            for observation in observations.values()
            if observation is not None and observation.observed_at.utcoffset() is not None
        ]
        observation_time = min(observation_times) if observation_times else now_utc
        return LiveSignalCycleResult(
            timestamp_utc=now_utc,
            observation_time_utc=observation_time,
            signals=signals,
            target=target,
        )

    @staticmethod
    def _audit_row(
        generated_at: datetime,
        maturity: str,
        observation: DailySpreadObservation | None,
        signal: LiveSignalResult,
        target: LiveTarget,
        data_hash: str,
    ) -> dict[str, Any]:
        maturity_target = target.maturities[maturity]
        reasons = tuple(
            dict.fromkeys((*signal.reason_codes, *maturity_target.reason_codes))
        )
        return {
            "generated_at_utc": generated_at,
            "observation_time_utc": getattr(observation, "observed_at", None),
            "strategy_version": LIVE_SIGNAL_STRATEGY_VERSION,
            "snapshot_id": signal.snapshot_id,
            "data_snapshot_hash": data_hash,
            "maturity": maturity,
            "eris_symbol": "YIT" if maturity == "2Y" else "YIW",
            "fred_series": MATURITY_FRED_SERIES[maturity],
            "eris_rate_bps": getattr(observation, "eris_rate_bps", None),
            "treasury_rate_bps": getattr(observation, "treasury_rate_bps", None),
            "spread_bps": signal.mid_spread_bps,
            "historical_mean_bps": signal.historical_mean_bps,
            "historical_std_bps": signal.historical_std_bps,
            "z_score": signal.z_score,
            "prior_state": signal.prior_state,
            "resulting_state": signal.state,
            "target_swap_quantity": maturity_target.swap_quantity,
            "target_treasury_quantity": maturity_target.treasury_quantity,
            "blocked": signal.blocked or maturity_target.blocked,
            "reason_codes": reasons,
        }
