from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pandas as pd

from strategy.eris_pricing import ErisReference


REFERENCE_COLUMNS = [
    "contract_id",
    "symbol",
    "fixed_rate_decimal",
    "b_price_points",
    "c_price_points",
    "pv01_usd_per_bp",
    "effective_date",
    "maturity_date",
    "observed_at",
]


class ErisReferenceError(RuntimeError):
    def __init__(self, reason_code: str, detail: str) -> None:
        super().__init__(detail)
        self.reason_code = reason_code


class CsvErisReferenceProvider:
    def __init__(self, path: Path, *, max_age_seconds: int) -> None:
        if max_age_seconds <= 0:
            raise ValueError("max_age_seconds must be positive")
        self.path = Path(path)
        self.max_age_seconds = max_age_seconds

    def get(self, contract_id: str, symbol: str, as_of: datetime) -> ErisReference:
        if as_of.utcoffset() is None:
            raise ValueError("as_of must be timezone-aware")
        if not self.path.exists():
            raise ErisReferenceError(
                "missing_eris_reference", f"missing ERIS reference source: {self.path}"
            )

        frame = pd.read_csv(self.path, dtype=str)
        missing = [column for column in REFERENCE_COLUMNS if column not in frame.columns]
        if missing:
            raise ErisReferenceError(
                "invalid_eris_reference",
                f"ERIS reference data missing columns: {missing}",
            )

        rows = frame[frame["contract_id"].astype(str).eq(str(contract_id))]
        if rows.empty:
            raise ErisReferenceError(
                "eris_reference_contract_mismatch",
                f"missing exact contract reference: {contract_id}",
            )
        if len(rows) != 1:
            raise ErisReferenceError(
                "invalid_eris_reference",
                f"duplicate exact contract reference: {contract_id}",
            )

        row = rows.iloc[0]
        if str(row["symbol"]).strip() != symbol:
            raise ErisReferenceError(
                "eris_reference_contract_mismatch",
                f"ERIS reference symbol mismatch for {contract_id}: "
                f"expected {symbol}, got {row['symbol']}",
            )

        observed = pd.to_datetime(row["observed_at"], errors="coerce", utc=True)
        if pd.isna(observed):
            raise ErisReferenceError(
                "invalid_eris_reference", "invalid ERIS reference timestamp"
            )
        observed_at = observed.to_pydatetime()
        age = (as_of.astimezone(timezone.utc) - observed_at).total_seconds()
        if age < 0:
            raise ErisReferenceError(
                "future_eris_reference", "ERIS reference timestamp is in the future"
            )
        if age > self.max_age_seconds:
            raise ErisReferenceError(
                "stale_eris_reference", "stale ERIS reference data"
            )

        try:
            fixed = Decimal(str(row["fixed_rate_decimal"]))
            b = Decimal(str(row["b_price_points"]))
            c = Decimal(str(row["c_price_points"]))
            pv01 = Decimal(str(row["pv01_usd_per_bp"]))
        except InvalidOperation as exc:
            raise ErisReferenceError(
                "invalid_eris_reference", "invalid numeric ERIS reference field"
            ) from exc

        if any(not value.is_finite() for value in (fixed, b, c, pv01)):
            raise ErisReferenceError(
                "invalid_eris_reference", "non-finite ERIS reference field"
            )
        if pv01 <= 0:
            raise ErisReferenceError(
                "invalid_eris_reference", "ERIS reference PV01 must be positive"
            )

        return ErisReference(
            contract_id=str(contract_id),
            fixed_rate_decimal=fixed,
            b_price_points=b,
            c_price_points=c,
            pv01_usd_per_bp=pv01,
            effective_date=str(row["effective_date"]),
            maturity_date=str(row["maturity_date"]),
            observed_at=observed_at,
        )
