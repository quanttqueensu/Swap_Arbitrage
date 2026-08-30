from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from pathlib import Path
from typing import Any, Protocol

from .models import DailyTarget
from .models import MaturityTarget
from .target_loader import TargetValidationError, load_daily_target


class TargetProvider(Protocol):
    def load_target(self, now: datetime) -> DailyTarget: ...


def _apply_refreshed_contracts(runner: Any, refreshed: Any) -> None:
    market_source = getattr(runner, "market_source", None)
    setter = getattr(market_source, "set_preferred_contracts", None)
    bindings = getattr(refreshed, "bindings", {})
    if not callable(setter) or not isinstance(bindings, dict):
        return
    setter(
        {
            binding.symbol: binding.con_id
            for key, binding in bindings.items()
            if str(key).endswith(":swap")
        }
    )


@dataclass(frozen=True)
class DailyCsvTargetProvider:
    path: Path
    max_age_business_days: int

    def load_target(self, now: datetime) -> DailyTarget:
        return load_daily_target(
            self.path,
            now=now,
            max_age_business_days=self.max_age_business_days,
        )


@dataclass(frozen=True)
class ShadowLiveTargetProvider:
    runner: Any
    refresher: Any | None = None

    def observe(self, now: datetime) -> Any:
        if self.refresher is None:
            return self.runner.run_once(now)
        refreshed = self.refresher.refresh(now)
        _apply_refreshed_contracts(self.runner, refreshed)
        return self.runner.run_once(now, risk_inputs=refreshed.risk_inputs)


@dataclass(frozen=True)
class LiveSignalTargetProvider:
    runner: Any
    refresher: Any

    def load_target(self, now: datetime) -> DailyTarget:
        try:
            refreshed = self.refresher.refresh(now)
            _apply_refreshed_contracts(self.runner, refreshed)
            result = self.runner.run_once(now, risk_inputs=refreshed.risk_inputs)
        except TargetValidationError:
            raise
        except Exception as exc:
            raise TargetValidationError(f"Automatic live-data refresh failed: {exc}") from exc

        target = result.hypothetical_target
        blocked = [
            maturity
            for maturity, maturity_target in target.maturities.items()
            if maturity_target.blocked
        ]
        if target.blocked or blocked:
            reasons = list(target.reason_codes)
            for maturity in blocked:
                reasons.extend(target.maturities[maturity].reason_codes)
            raise TargetValidationError(
                "Live signal target is blocked: " + "|".join(dict.fromkeys(reasons))
            )

        quantities = {
            maturity: {
                "swap": target.maturities[maturity].swap_quantity,
                "treasury": target.maturities[maturity].treasury_quantity,
            }
            for maturity in ("2Y", "5Y")
        }
        payload = json.dumps(
            {
                "date": now.date().isoformat(),
                "strategy_version": target.strategy_version,
                "quantities": quantities,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        version = f"{now.date().isoformat()}:{hashlib.sha256(payload).hexdigest()}"
        return DailyTarget(
            as_of=now.date(),
            version=version,
            age_business_days=0,
            target_2y=MaturityTarget(
                swap_qty=quantities["2Y"]["swap"],
                treasury_qty=quantities["2Y"]["treasury"],
            ),
            target_5y=MaturityTarget(
                swap_qty=quantities["5Y"]["swap"],
                treasury_qty=quantities["5Y"]["treasury"],
            ),
        )
