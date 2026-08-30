from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation, localcontext
from pathlib import Path

from .models import BoundContract


class ContractRiskError(RuntimeError):
    """Raised when execution-time contract risk cannot be validated."""


@dataclass(frozen=True)
class ContractRisk:
    con_id: int
    risk_id: str
    observation_date: date
    dv01_usd_per_bp: Decimal
    rate_sensitivity_sign: int
    method: str


@dataclass(frozen=True)
class PortfolioDV01:
    gross: Decimal
    net: Decimal
    residual_fraction: Decimal


REQUIRED_COLUMNS = (
    "observation_date",
    "instrument_id",
    "dv01_usd_per_bp",
    "rate_sensitivity_sign",
    "dv01_method",
)


def _positive_decimal(value: object, label: str) -> Decimal:
    try:
        parsed = Decimal(str(value).strip())
    except (InvalidOperation, ValueError) as exc:
        raise ContractRiskError(f"Invalid {label} in contract-risk data.") from exc
    if not parsed.is_finite() or parsed <= 0:
        raise ContractRiskError(f"Invalid {label} in contract-risk data.")
    return parsed


def load_contract_risks(
    path: Path,
    *,
    as_of: date,
    bindings: dict[str, BoundContract],
) -> dict[int, ContractRisk]:
    if not path.exists():
        raise ContractRiskError(f"Contract-risk source does not exist: {path}")
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            missing = [name for name in REQUIRED_COLUMNS if name not in (reader.fieldnames or [])]
            if missing:
                raise ContractRiskError(f"Contract-risk source is missing columns: {missing}.")
            rows = list(reader)
    except OSError as exc:
        raise ContractRiskError("Could not read contract-risk source.") from exc

    by_id: dict[str, list[tuple[date, dict[str, str]]]] = {}
    for row in rows:
        try:
            row_date = date.fromisoformat(str(row["observation_date"]).strip())
        except ValueError as exc:
            raise ContractRiskError("Invalid observation_date in contract-risk source.") from exc
        if row_date > as_of:
            continue
        risk_id = str(row["instrument_id"]).strip()
        by_id.setdefault(risk_id, []).append((row_date, row))

    output: dict[int, ContractRisk] = {}
    for binding in bindings.values():
        candidates = by_id.get(binding.risk_id, [])
        if not candidates:
            raise ContractRiskError(
                f"Contract-risk data is missing bound instrument {binding.risk_id!r}."
            )
        observation_date, row = max(candidates, key=lambda item: item[0])
        try:
            sign = int(str(row["rate_sensitivity_sign"]).strip())
        except ValueError as exc:
            raise ContractRiskError("Invalid rate_sensitivity_sign in contract-risk data.") from exc
        if sign not in {-1, 1}:
            raise ContractRiskError("rate_sensitivity_sign must be -1 or 1.")
        method = str(row["dv01_method"]).strip()
        if not method:
            raise ContractRiskError("Contract-risk method must be non-empty.")
        output[binding.con_id] = ContractRisk(
            con_id=binding.con_id,
            risk_id=binding.risk_id,
            observation_date=observation_date,
            dv01_usd_per_bp=_positive_decimal(row["dv01_usd_per_bp"], "dv01_usd_per_bp"),
            rate_sensitivity_sign=sign,
            method=method,
        )
    return output


def calculate_portfolio_dv01(
    positions: dict[int, int],
    risks: dict[int, ContractRisk],
) -> PortfolioDV01:
    gross = Decimal("0")
    net = Decimal("0")
    for con_id, quantity in positions.items():
        if type(quantity) is not int:
            raise ContractRiskError("Portfolio position quantity must be an integer.")
        if quantity == 0:
            continue
        risk = risks.get(con_id)
        if risk is None:
            raise ContractRiskError(f"Missing contract risk for conId {con_id}.")
        exposure = Decimal(quantity) * risk.dv01_usd_per_bp
        gross += exposure.copy_abs()
        net += exposure * Decimal(risk.rate_sensitivity_sign)

    if gross == 0:
        residual = Decimal("0")
    else:
        with localcontext() as context:
            context.prec = 50
            residual = net.copy_abs() / gross
    return PortfolioDV01(gross=gross, net=net, residual_fraction=residual)
