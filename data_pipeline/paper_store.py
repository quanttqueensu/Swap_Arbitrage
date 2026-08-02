from __future__ import annotations

import csv
import ipaddress
import os
import re
import tempfile
from collections.abc import Iterable, Mapping
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from data_pipeline.contracts import CsvContract, SCHEMAS, validate_csv


SCHEMA_FILES = {
    "paper_quotes": "quotes.csv",
    "paper_orders": "orders.csv",
    "paper_fills": "fills.csv",
    "paper_positions": "positions.csv",
}
_IDENTIFIER = re.compile(r"^[A-Za-z0-9_.-]+$")
_SENSITIVE_ACCOUNT = re.compile(r"(?:^|\b)(?:DU|U)\d{3,}(?:\b|$)", re.IGNORECASE)
_SENSITIVE_LABEL = re.compile(
    r"(?:^|[?&,;\s])(?:host|hostname|password|credential|secret|token|client[_ -]?id)\s*[:=]",
    re.IGNORECASE,
)
_LOWER_BARE_HOST = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)+$")
_HOSTNAME = re.compile(r"^[A-Za-z][A-Za-z0-9-]*(?:\.[A-Za-z][A-Za-z0-9-]*)+$")


class PaperEventStore:
    def __init__(self, root: Path, agent_id: str, run_id: str) -> None:
        self._root = Path(root).resolve()
        self.agent_id = self._safe_id("agent_id", agent_id)
        self.run_id = self._safe_id("run_id", run_id)

    def path_for(self, schema_id: str) -> Path:
        path = (
            self._root / "data" / "paper" / self.agent_id / self.run_id / self._filename(schema_id)
        ).resolve()
        if not path.is_relative_to(self._root):
            raise ValueError("paper path escapes root")
        return path

    def write(self, schema_id: str, rows: Iterable[Mapping[str, object]]) -> int:
        contract = self._contract(schema_id)
        path = self.path_for(schema_id)
        fieldnames = [column.name for column in contract.columns]
        merged = self._read_existing(contract, path)
        for source in rows:
            row = self._serialize_row(contract, source)
            key = tuple(row[name] for name in contract.unique_key)
            existing = merged.get(key)
            if existing is None or existing == row:
                merged[key] = row
            elif schema_id == "paper_orders" and self._only_order_fields_changed(existing, row):
                merged[key] = row
            else:
                raise ValueError("conflicting duplicate key")
        ordered = sorted(merged.values(), key=lambda row: self._ordering_key(contract, row))
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", newline="", delete=False,
                dir=path.parent, prefix=f".{path.name}.", suffix=".tmp",
            ) as handle:
                temporary = Path(handle.name)
                writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
                writer.writeheader()
                writer.writerows(ordered)
                handle.flush()
                os.fsync(handle.fileno())
            validate_csv(contract, temporary)
            temporary.replace(path)
            temporary = None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return len(ordered)

    @staticmethod
    def _safe_id(name: str, value: str) -> str:
        if not isinstance(value, str) or value in {".", ".."} or _IDENTIFIER.fullmatch(value) is None:
            raise ValueError(f"invalid {name}")
        return value

    @staticmethod
    def _filename(schema_id: str) -> str:
        try:
            return SCHEMA_FILES[schema_id]
        except KeyError as error:
            raise ValueError("unsupported schema") from error

    @staticmethod
    def _contract(schema_id: str) -> CsvContract:
        PaperEventStore._filename(schema_id)
        return SCHEMAS[schema_id]

    @staticmethod
    def _serialize_row(contract: CsvContract, source: Mapping[str, object]) -> dict[str, str]:
        fieldnames = [column.name for column in contract.columns]
        if set(source) != set(fieldnames):
            raise ValueError("row fields must equal the schema header")
        return {name: PaperEventStore._serialize_scalar(name, source[name]) for name in fieldnames}

    @staticmethod
    def _serialize_scalar(field_name: str, value: object) -> str:
        if value is None:
            return ""
        if isinstance(value, datetime):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("datetime values must include a timezone")
            value = value.astimezone(timezone.utc)
            return value.isoformat(timespec="microseconds").replace("+00:00", "Z").replace(".000000Z", "Z")
        if isinstance(value, Decimal):
            if not value.is_finite():
                raise ValueError("decimal values must be finite")
            return format(value, "f")
        text = str(value)
        if PaperEventStore._is_sensitive(field_name, text):
            raise ValueError("sensitive values are not permitted")
        return text

    @staticmethod
    def _read_existing(contract: CsvContract, path: Path) -> dict[tuple[str, ...], dict[str, str]]:
        if not path.exists():
            return {}
        validate_csv(contract, path)
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return {
                tuple(row[name] for name in contract.unique_key): dict(row)
                for row in csv.DictReader(handle)
            }

    @staticmethod
    def _only_order_fields_changed(old: Mapping[str, str], new: Mapping[str, str]) -> bool:
        return all(old[name] == new[name] for name in old if name not in {"status", "ibkr_order_id"})

    @staticmethod
    def _is_sensitive(field_name: str, value: str) -> bool:
        lowered = value.casefold()
        return (
            _SENSITIVE_ACCOUNT.search(value) is not None
            or _SENSITIVE_LABEL.search(value) is not None
            or lowered in {"host", "localhost", "password", "credential", "secret", "token", "client_id", "client id"}
            or "://" in value
            or PaperEventStore._is_exact_endpoint(value)
            or (field_name == "instrument_id" and PaperEventStore._is_ambiguous_bare_host(value))
        )

    @staticmethod
    def _is_exact_endpoint(value: str) -> bool:
        try:
            ipaddress.ip_address(value)
            return True
        except ValueError:
            pass
        host, separator, port = value.rpartition(":")
        if separator and port.isdigit() and host:
            try:
                ipaddress.ip_address(host)
                return True
            except ValueError:
                return _HOSTNAME.fullmatch(host) is not None or PaperEventStore._is_ambiguous_bare_host(host)
        return _HOSTNAME.fullmatch(value) is not None

    @staticmethod
    def _is_ambiguous_bare_host(value: str) -> bool:
        return value.casefold() == "localhost" or _LOWER_BARE_HOST.fullmatch(value) is not None

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
                values.append(date.fromisoformat(value))
            elif scalar_type == "integer":
                values.append(int(value))
            elif scalar_type == "decimal":
                values.append(Decimal(value))
            else:
                values.append(value)
        return tuple(values)
