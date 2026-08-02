from __future__ import annotations

import csv
import os
import re
import tempfile
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from data_pipeline.contracts import CsvContract, SCHEMAS, validate_csv


SCHEMA_FILES = {
    "paper_quotes": "quotes.csv",
    "paper_orders": "orders.csv",
    "paper_fills": "fills.csv",
    "paper_positions": "positions.csv",
}
_SAFE_ID = re.compile(r"^[A-Za-z0-9_.-]+$")


class PaperEventStore:
    def __init__(self, root: Path, agent_id: str, run_id: str) -> None:
        self._root = Path(root).resolve()
        self.agent_id = self._validate_id("agent_id", agent_id)
        self.run_id = self._validate_id("run_id", run_id)

    @staticmethod
    def _validate_id(name: str, value: str) -> str:
        if not isinstance(value, str) or _SAFE_ID.fullmatch(value) is None:
            raise ValueError(f"invalid {name}")
        return value

    def path_for(self, schema_id: str) -> Path:
        filename = self._filename_for(schema_id)
        path = (self._root / "data" / "paper" / self.agent_id / self.run_id / filename).resolve()
        if not path.is_relative_to(self._root):
            raise ValueError("paper path escapes root")
        return path

    def write(self, schema_id: str, rows: Iterable[Mapping[str, object]]) -> int:
        contract = self._contract_for(schema_id)
        path = self.path_for(schema_id)
        expected = [column.name for column in contract.columns]
        merged = self._load_existing(contract, path)
        for source_row in rows:
            row = self._serialize_row(expected, source_row)
            key = tuple(row[name] for name in contract.unique_key)
            old = merged.get(key)
            if old is None:
                merged[key] = row
            elif old == row:
                continue
            elif schema_id == "paper_orders" and self._order_fields_match_except_status(old, row):
                merged[key] = row
            else:
                raise ValueError(f"conflicting duplicate key {key}")
        ordered = sorted(merged.values(), key=lambda row: self._ordering_key(contract, row))
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", newline="", delete=False,
                dir=path.parent, prefix=f".{path.name}.", suffix=".tmp",
            ) as handle:
                temp_path = Path(handle.name)
                writer = csv.DictWriter(handle, fieldnames=expected, lineterminator="\n")
                writer.writeheader()
                writer.writerows(ordered)
                handle.flush()
                os.fsync(handle.fileno())
            validate_csv(contract, temp_path)
            temp_path.replace(path)
            temp_path = None
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)
        return len(ordered)

    @staticmethod
    def _filename_for(schema_id: str) -> str:
        try:
            return SCHEMA_FILES[schema_id]
        except KeyError as error:
            raise ValueError(f"unsupported schema {schema_id}") from error

    @staticmethod
    def _contract_for(schema_id: str) -> CsvContract:
        PaperEventStore._filename_for(schema_id)
        return SCHEMAS[schema_id]

    @staticmethod
    def _serialize_row(expected: list[str], source_row: Mapping[str, object]) -> dict[str, str]:
        if set(source_row) != set(expected):
            raise ValueError("row fields must equal the schema header")
        return {name: PaperEventStore._serialize_scalar(source_row[name]) for name in expected}

    @staticmethod
    def _serialize_scalar(value: object) -> str:
        if value is None:
            return ""
        if isinstance(value, datetime):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("datetime values must include a timezone")
            utc = value.astimezone(timezone.utc)
            return utc.isoformat(timespec="microseconds").replace("+00:00", "Z").replace(".000000Z", "Z")
        if isinstance(value, Decimal):
            if not value.is_finite():
                raise ValueError("decimal values must be finite")
            return format(value, "f")
        return str(value)

    @staticmethod
    def _load_existing(contract: CsvContract, path: Path) -> dict[tuple[str, ...], dict[str, str]]:
        if not path.exists():
            return {}
        validate_csv(contract, path)
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return {
                tuple(row[name] for name in contract.unique_key): dict(row)
                for row in csv.DictReader(handle)
            }

    @staticmethod
    def _order_fields_match_except_status(old: Mapping[str, str], new: Mapping[str, str]) -> bool:
        return all(old[name] == new[name] for name in old if name != "status")

    @staticmethod
    def _ordering_key(contract: CsvContract, row: Mapping[str, str]) -> tuple[object, ...]:
        columns = {column.name: column for column in contract.columns}
        values: list[object] = []
        for name in contract.ordering:
            value = row[name]
            scalar_type = columns[name].scalar_type
            if scalar_type == "datetime_utc":
                values.append(datetime.fromisoformat(value[:-1] + "+00:00"))
            elif scalar_type == "date":
                values.append(value)
            elif scalar_type == "integer":
                values.append(int(value))
            elif scalar_type == "decimal":
                values.append(Decimal(value))
            else:
                values.append(value)
        return tuple(values)
