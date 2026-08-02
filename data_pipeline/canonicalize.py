"""Fail-closed, deterministic transforms for approved market inputs."""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Callable, Mapping


class CanonicalizationError(ValueError):
    pass


@dataclass(frozen=True)
class SourceTiming:
    effective_from: date
    effective_to: date
    observation_time_utc: time
    availability_delay: timedelta
    source: str
    classification: str
    proxy_label: str = ""

    def __post_init__(self) -> None:
        if type(self.effective_from) is not date or type(self.effective_to) is not date:
            raise CanonicalizationError("timing bounds must be dates")
        if self.effective_from > self.effective_to:
            raise CanonicalizationError("timing effective_from must not exceed effective_to")
        if self.availability_delay < timedelta():
            raise CanonicalizationError("timing availability delay must be nonnegative")
        if self.observation_time_utc.tzinfo is None or self.observation_time_utc.utcoffset() != timedelta():
            raise CanonicalizationError("timing observation clock must be UTC")
        if self.classification not in {"exact", "proxy", "assumed", "unavailable"} or (self.classification == "proxy") != bool(self.proxy_label):
            raise CanonicalizationError("invalid timing classification or proxy label")


@dataclass(frozen=True)
class FuturesCanonicalization:
    settlements_by_year: dict[int, list[dict[str, str]]]
    risk_by_year: dict[int, list[dict[str, str]]]


RATE_COLUMNS = {
    "dgs2": ("UST", "DGS2", "2Y"),
    "dgs5": ("UST", "DGS5", "5Y"),
    "sofr": ("NYFED", "SOFR", "ON"),
    "effr": ("NYFED", "EFFR", "ON"),
}
RATE_HEADER = ["date", "dgs1mo", "dgs2mo", "dgs3mo", "dgs4mo", "dgs6mo", "dgs1", "dgs2", "dgs3", "dgs5", "dgs7", "dgs10", "dgs20", "dgs30", "sofr", "effr"]
FUTURES_HEADER = ["date", "ticker", "price", "dv01"]
SWAP_PRICE_HEADER = ["date", "eris_swap_2y_price", "eris_swap_2y_return", "eris_swap_5y_price", "eris_swap_5y_return"]
TREASURY_PRICE_HEADER = ["date", "treasury_futures_2y_price", "treasury_futures_2y_return", "treasury_futures_5y_price", "treasury_futures_5y_return"]
MONTHS = {"H": "03", "M": "06", "U": "09", "Z": "12"}


def _read(path: Path, header: list[str]) -> list[dict[str, str]]:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames != header:
                raise CanonicalizationError(f"header must equal {header}")
            rows = list(reader)
    except OSError as error:
        raise CanonicalizationError(f"cannot read {path}") from error
    for number, row in enumerate(rows, 2):
        if None in row or any(row[column] is None for column in header):
            raise CanonicalizationError(f"row {number}: row width differs from header")
    return rows


def _date(value: str) -> date:
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) is None:
        raise CanonicalizationError("date must be ISO YYYY-MM-DD")
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise CanonicalizationError("date must be ISO YYYY-MM-DD") from error


def _positive(value: str, name: str) -> Decimal:
    if value == "":
        raise CanonicalizationError(f"{name} is required")
    try:
        decimal = Decimal(value)
    except InvalidOperation as error:
        raise CanonicalizationError(f"{name} must be decimal") from error
    if not decimal.is_finite():
        raise CanonicalizationError(f"{name} must be finite")
    if decimal <= 0:
        raise CanonicalizationError(f"{name} must be positive")
    return decimal


def _text(value: Decimal) -> str:
    output = format(value, "f")
    return output.rstrip("0").rstrip(".") if "." in output else output


def percent_to_bps(value: str) -> str:
    return _text(_positive(value, "rate") * Decimal("100"))


def _partition(rows: list[dict[str, str]], ordering: tuple[str, ...]) -> dict[int, list[dict[str, str]]]:
    partitions: dict[int, list[dict[str, str]]] = {}
    for row in rows:
        partitions.setdefault(_date(row["observation_date"]).year, []).append(row)
    for rows_for_year in partitions.values():
        rows_for_year.sort(key=lambda row: tuple(row[column] for column in ordering))
    return dict(sorted(partitions.items()))


def canonicalize_rates(path: Path) -> dict[int, list[dict[str, str]]]:
    result: list[dict[str, str]] = []
    dates: set[str] = set()
    for row in _read(path, RATE_HEADER):
        observed = row["date"]
        _date(observed)
        if observed in dates:
            raise CanonicalizationError(f"duplicate rate date {observed}")
        dates.add(observed)
        for column, (source, series_id, maturity) in RATE_COLUMNS.items():
            result.append({"observation_date": observed, "source": source, "series_id": series_id, "maturity": maturity, "rate_bps": percent_to_bps(row[column])})
    return _partition(result, ("observation_date", "source", "series_id", "maturity"))


def _eris_id(ticker: str) -> str:
    if len(ticker) == 6 and ticker[:3] in {"YIT", "YIW"}:
        root, month, year = ticker[:3], ticker[3], ticker[4:]
    else:
        raise CanonicalizationError(f"unapproved Eris ticker {ticker}")
    if month not in MONTHS or len(year) != 2 or not year.isdigit():
        raise CanonicalizationError(f"unapproved Eris ticker {ticker}")
    return f"ERIS-{root}-20{year}{MONTHS[month]}"


def _treasury_id(ticker: str) -> str:
    if ticker not in {"ZT=F", "ZF=F"}:
        raise CanonicalizationError(f"unapproved Treasury proxy ticker {ticker}")
    return f"YAHOO-CONTINUOUS-{ticker[:2]}"


def canonicalize_futures(swap_path: Path, treasury_path: Path) -> FuturesCanonicalization:
    settlements: list[dict[str, str]] = []
    risks: list[dict[str, str]] = []
    identities: set[tuple[str, str]] = set()
    sources: tuple[tuple[Path, str, Callable[[str], str], str], ...] = (
        (swap_path, "ERIS", _eris_id, "eris_settlement_dv01"),
        (treasury_path, "YAHOO", _treasury_id, "cme_fixed_ics_ratio_proxy"),
    )
    for path, source, identify, method in sources:
        for row in _read(path, FUTURES_HEADER):
            observed = row["date"]
            _date(observed)
            instrument_id = identify(row["ticker"])
            identity = (observed, instrument_id)
            if identity in identities:
                raise CanonicalizationError(f"duplicate futures identity {identity}")
            identities.add(identity)
            settlements.append({"observation_date": observed, "source": source, "instrument_id": instrument_id, "settlement_price": _text(_positive(row["price"], "price")), "dv01_usd_per_bp": ""})
            risks.append({"observation_date": observed, "instrument_id": instrument_id, "dv01_usd_per_bp": _text(_positive(row["dv01"], "dv01")), "rate_sensitivity_sign": "-1", "dv01_method": method})
    return FuturesCanonicalization(
        settlements_by_year=_partition(settlements, ("observation_date", "source", "instrument_id")),
        risk_by_year=_partition(risks, ("observation_date", "instrument_id")),
    )


def _timing_for(source: str, observation_date: date, timing_rules: Mapping[str, tuple[SourceTiming, ...]]) -> SourceTiming:
    rules = timing_rules.get(source, ())
    matches = [rule for rule in rules if rule.source == source and rule.effective_from <= observation_date <= rule.effective_to]
    if len(matches) != 1:
        raise CanonicalizationError(f"expected exactly one applicable timing rule for {source} on {observation_date.isoformat()}")
    return matches[0]


def _market_rows(path: Path, header: list[str], columns: tuple[tuple[str, str], ...], source: str, timing_rules: Mapping[str, tuple[SourceTiming, ...]]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    dates: set[str] = set()
    for row in _read(path, header):
        observed = row["date"]
        day = _date(observed)
        if observed in dates:
            raise CanonicalizationError(f"duplicate market date {observed}")
        dates.add(observed)
        timing = _timing_for(source, day, timing_rules)
        source_time = datetime.combine(day, timing.observation_time_utc, tzinfo=timezone.utc)
        available = source_time + timing.availability_delay
        for column, instrument_id in columns:
            result.append({
                "observation_date": observed, "series_id": "", "instrument_id": instrument_id,
                "value": _text(_positive(row[column], "price")), "value_unit": "price_points",
                "source_observation_time_utc": source_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "available_at_utc": available.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "source": source, "classification": timing.classification, "proxy_label": timing.proxy_label,
            })
    return result


def canonicalize_daily_market(swap_prices_path: Path, treasury_prices_path: Path, timing_rules: Mapping[str, tuple[SourceTiming, ...]]) -> dict[int, list[dict[str, str]]]:
    rows = _market_rows(swap_prices_path, SWAP_PRICE_HEADER, (("eris_swap_2y_price", "ERIS-YIT"), ("eris_swap_5y_price", "ERIS-YIW")), "ERIS", timing_rules)
    rows.extend(_market_rows(treasury_prices_path, TREASURY_PRICE_HEADER, (("treasury_futures_2y_price", "YAHOO-CONTINUOUS-ZT"), ("treasury_futures_5y_price", "YAHOO-CONTINUOUS-ZF")), "YAHOO", timing_rules))
    return _partition(rows, ("observation_date", "series_id", "instrument_id", "source", "available_at_utc"))
