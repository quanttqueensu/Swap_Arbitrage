from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Protocol

from .models import DailyTarget, MaturityTarget


class TargetValidationError(RuntimeError):
    """Raised when the latest strategy row cannot be accepted for execution."""


REQUIRED_FIELDS = (
    "date",
    "risk_allowed",
    "risk_block_reason",
    "swap_futures_contracts_rounded_2y",
    "treasury_futures_contracts_rounded_2y",
    "swap_futures_contracts_rounded_5y",
    "treasury_futures_contracts_rounded_5y",
)

EXECUTION_FIELDS = (
    "risk_allowed",
    "risk_block_reason",
    "swap_futures_contracts_rounded_2y",
    "treasury_futures_contracts_rounded_2y",
    "swap_futures_contracts_rounded_5y",
    "treasury_futures_contracts_rounded_5y",
)


def _parse_date(value: object) -> date:
    try:
        return date.fromisoformat(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise TargetValidationError(f"Invalid target date: {value!r}.") from exc


def _parse_integer(value: object, field: str) -> int:
    raw = str(value).strip()
    try:
        parsed = Decimal(raw)
    except (InvalidOperation, ValueError) as exc:
        raise TargetValidationError(f"{field} must be an integer contract quantity.") from exc

    if not parsed.is_finite() or parsed != parsed.to_integral_value():
        raise TargetValidationError(f"{field} must be an integer contract quantity.")

    return int(parsed)


def _business_day_age(as_of: date, current: date) -> int:
    if as_of > current:
        raise TargetValidationError("Target date is in the future.")

    age = 0
    cursor = as_of
    while cursor < current:
        cursor += timedelta(days=1)
        if cursor.weekday() < 5:
            age += 1
    return age


def _latest_row(path: Path) -> tuple[dict[str, str], date]:
    if not path.exists():
        raise TargetValidationError(f"Target source does not exist: {path}")

    try:
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            fieldnames = tuple(reader.fieldnames or ())
            missing = [field for field in REQUIRED_FIELDS if field not in fieldnames]
            if missing:
                raise TargetValidationError(
                    f"Target source is missing required columns: {missing}."
                )

            rows = list(reader)
    except OSError as exc:
        raise TargetValidationError(f"Could not read target source: {path}") from exc

    if not rows:
        raise TargetValidationError("Target source has no data rows.")

    dated_rows = [(_parse_date(row["date"]), row) for row in rows]
    row_date, row = max(dated_rows, key=lambda item: item[0])
    return row, row_date


def _target_version(row_date: date, canonical_values: dict[str, object]) -> str:
    payload = json.dumps(
        canonical_values,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()
    return f"{row_date.isoformat()}:{digest}"


def load_daily_target(
    path: Path,
    *,
    now: datetime,
    max_age_business_days: int,
) -> DailyTarget:
    if now.utcoffset() is None:
        raise TargetValidationError("Current time must include a timezone.")
    if type(max_age_business_days) is not int or max_age_business_days <= 0:
        raise TargetValidationError("max_age_business_days must be a positive integer.")

    row, row_date = _latest_row(path)

    risk_allowed = _parse_integer(row["risk_allowed"], "risk_allowed")
    reason = str(row.get("risk_block_reason", "") or "").strip()
    if risk_allowed != 1:
        raise TargetValidationError("Latest target has risk_allowed != 1.")
    if reason and reason.lower() != "nan":
        raise TargetValidationError(
            f"Latest target has non-empty risk_block_reason: {reason}."
        )

    quantities = {
        field: _parse_integer(row[field], field)
        for field in EXECUTION_FIELDS
        if field not in {"risk_allowed", "risk_block_reason"}
    }

    current_date = now.date()
    age = _business_day_age(row_date, current_date)
    if age > max_age_business_days:
        raise TargetValidationError(
            f"Latest target is stale: age={age} business day(s), "
            f"limit={max_age_business_days}."
        )

    canonical = {
        "risk_allowed": risk_allowed,
        "risk_block_reason": "",
        **quantities,
    }
    version = _target_version(row_date, canonical)

    return DailyTarget(
        as_of=row_date,
        version=version,
        age_business_days=age,
        target_2y=MaturityTarget(
            swap_qty=quantities["swap_futures_contracts_rounded_2y"],
            treasury_qty=quantities["treasury_futures_contracts_rounded_2y"],
        ),
        target_5y=MaturityTarget(
            swap_qty=quantities["swap_futures_contracts_rounded_5y"],
            treasury_qty=quantities["treasury_futures_contracts_rounded_5y"],
        ),
    )


class TargetProvider(Protocol):
    def load_target(self, now: datetime) -> DailyTarget: ...


def _apply_refreshed_contracts(runner: Any, refreshed: Any) -> None:
    return None


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
class LiveSignalTargetProvider:
    runner: Any
    refresher: Any

    def current_bindings(self) -> dict[str, Any]:
        refreshed = getattr(self.refresher, "_last_result", None)
        bindings = getattr(refreshed, "bindings", {})
        return dict(bindings) if isinstance(bindings, dict) else {}

    def load_target(self, now: datetime) -> DailyTarget:
        try:
            refreshed = self.refresher.refresh(now)
            generated_at = datetime.now(timezone.utc)
            result = self.runner.run_once(generated_at, risk_inputs=refreshed.risk_inputs)
        except TargetValidationError:
            raise
        except Exception as exc:
            raise TargetValidationError(f"Automatic live-data refresh failed: {exc}") from exc

        target = result.target
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
        observed_at = result.observation_time_utc
        age = _business_day_age(observed_at.date(), generated_at.date())
        max_age = getattr(self.refresher.agent_config, "max_target_age_business_days")
        if age > max_age:
            raise TargetValidationError(
                f"Latest aligned daily signal is stale: age={age} business day(s), limit={max_age}."
            )
        payload = json.dumps(
            {
                "date": observed_at.date().isoformat(),
                "strategy_version": target.strategy_version,
                "quantities": quantities,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        version = f"{observed_at.date().isoformat()}:{hashlib.sha256(payload).hexdigest()}"
        return DailyTarget(
            as_of=observed_at.date(),
            version=version,
            age_business_days=age,
            target_2y=MaturityTarget(
                swap_qty=quantities["2Y"]["swap"],
                treasury_qty=quantities["2Y"]["treasury"],
            ),
            target_5y=MaturityTarget(
                swap_qty=quantities["5Y"]["swap"],
                treasury_qty=quantities["5Y"]["treasury"],
            ),
        )
