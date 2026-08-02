"""Pure, fail-closed transforms for approved market-data inputs."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Mapping


class CanonicalizationError(ValueError):
    """An input cannot safely be represented by a canonical contract."""


@dataclass(frozen=True)
class SourceTiming:
    observation_time_utc: time
    availability_delay: timedelta
    source: str
    classification: str
    proxy_label: str = ""


RATE_COLUMNS = {
    "dgs2": ("UST", "DGS2", "2Y"),
    "dgs5": ("UST", "DGS5", "5Y"),
    "sofr": ("NYFED", "SOFR", "ON"),
    "effr": ("NYFED", "EFFR", "ON"),
}
RATE_HEADER = [
    "date", "dgs1mo", "dgs2mo", "dgs3mo", "dgs4mo", "dgs6mo", "dgs1", "dgs2",
    "dgs3", "dgs5", "dgs7", "dgs10", "dgs20", "dgs30", "sofr", "effr",
]
FUTURES_HEADER = ["date", "ticker", "price", "dv01"]
SWAP_PRICE_HEADER = ["date", "eris_swap_2y_price", "eris_swap_2y_return", "eris_swap_5y_price", "eris_swap_5y_return"]
TREASURY_PRICE_HEADER = ["date", "treasury_futures_2y_price", "treasury_futures_2y_return", "treasury_futures_5y_price", "treasury_futures_5y_return"]
ERIS_MONTHS = {"H": "03", "M": "06", "U": "09", "Z": "12"}


def _decimal(value: str, label: str) -> Decimal:
    if value == "":
        raise CanonicalizationError(f"{label} is required")
    try:
        parsed = Decimal(value)
    except InvalidOperation as error:
        raise CanonicalizationError(f"{label} must be decimal") from error
    if not parsed.is_finite():
        raise CanonicalizationError(f"{label} must be finite")
    if parsed <= 0:
        raise CanonicalizationError(f"{label} must be positive")
    return parsed


def _number_text(value: Decimal) -> str:
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def percent_to_bps(value: str) -> str:
    return _number_text(_decimal(value, "rate") * Decimal("100"))


def _date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise CanonicalizationError("date must be ISO YYYY-MM-DD") from error


def _read_rows(path: Path, expected_header: list[str]) -> list[dict[str, str]]:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames != expected_header:
                raise CanonicalizationError(f"header must equal {expected_header}")
            rows = list(reader)
    except OSError as error:
        raise CanonicalizationError(f"cannot read {path}") from error
    for number, row in enumerate(rows, start=2):
        if None in row or any(row[name] is None for name in expected_header):
            raise CanonicalizationError(f"row {number}: row width differs from header")
    return rows


def _partition(rows: list[dict[str, str]], ordering: tuple[str, ...]) -> dict[int, list[dict[str, str]]]:
    partitions: dict[int, list[dict[str, str]]] = {}
    for row in rows:
        year = _date(row["observation_date"]).year
        partitions.setdefault(year, []).append(row)
    for partition in partitions.values():
        partition.sort(key=lambda row: tuple(row[name] for name in ordering))
    return dict(sorted(partitions.items()))


def canonicalize_rates(path: Path) -> dict[int, list[dict[str, str]]]:
    output: list[dict[str, str]] = []
    seen_dates: set[str] = set()
    for row in _read_rows(path, RATE_HEADER):
        observation_date = row["date"]
        _date(observation_date)
        if observation_date in seen_dates:
            raise CanonicalizationError(f"duplicate rate date {observation_date}")
        seen_dates.add(observation_date)
        for column, (source, series_id, maturity) in RATE_COLUMNS.items():
            output.append({
                "observation_date": observation_date,
                "source": source,
                "series_id": series_id,
                "maturity": maturity,
                "rate_bps": percent_to_bps(row[column]),
            })
    return _partition(output, ("observation_date", "source", "series_id", "maturity"))


def _eris_instrument_id(ticker: str) -> str:
    if len(ticker) != 5 or ticker[:3] not in {"YIT", "YIW"} or ticker[3] not in ERIS_MONTHS or not ticker[4:].isdigit():
        raise CanonicalizationError(f"unapproved Eris ticker {ticker}")
    return f"ERIS-{ticker[:3]}-20{ticker[4:]}{ERIS_MONTHS[ticker[3]]}"


def _treasury_instrument_id(ticker: str) -> str:
    if ticker not in {"ZT=F", "ZF=F"}:
        raise CanonicalizationError(f"unapproved Treasury proxy ticker {ticker}")
    return f"YAHOO-CONTINUOUS-{ticker[:2]}"


def canonicalize_futures(
    swap_path: Path, treasury_path: Path,
) -> tuple[dict[int, list[dict[str, str]]], dict[int, list[dict[str, str]]]]:
    settlements: list[dict[str, str]] = []
    risk: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for path, source, identify, method in (
        (swap_path, "ERIS", _eris_instrument_id, "eris_settlement_dv01"),
        (treasury_path, "YAHOO", _treasury_instrument_id, "cme_fixed_ics_ratio_proxy"),
    ):
        for row in _read_rows(path, FUTURES_HEADER):
            observation_date = row["date"]
            _date(observation_date)
            instrument_id = identify(row["ticker"])
            identity = (observation_date, instrument_id)
            if identity in seen:
                raise CanonicalizationError(f"duplicate futures identity {identity}")
            seen.add(identity)
            price = _number_text(_decimal(row["price"], "price"))
            dv01 = _number_text(_decimal(row["dv01"], "dv01"))
            settlements.append({"observation_date": observation_date, "source": source, "instrument_id": instrument_id, "settlement_price": price, "dv01_usd_per_bp": ""})
            risk.append({"observation_date": observation_date, "instrument_id": instrument_id, "dv01_usd_per_bp": dv01, "rate_sensitivity_sign": "-1", "dv01_method": method})
    return (
        _partition(settlements, ("observation_date", "source", "instrument_id")),
        _partition(risk, ("observation_date", "instrument_id")),
    )


def _timestamp(observation_date: str, timing: SourceTiming) -> tuple[str, str]:
    day = _date(observation_date)
    observed = datetime.combine(day, timing.observation_time_utc, tzinfo=timezone.utc)
    available = observed + timing.availability_delay
    return observed.strftime("%Y-%m-%dT%H:%M:%SZ"), available.strftime("%Y-%m-%dT%H:%M:%SZ")


def _market_rows(path: Path, header: list[str], mappings: tuple[tuple[str, str], ...], timing_rules: Mapping[str, SourceTiming], source: str) -> list[dict[str, str]]:
    timing = timing_rules.get(source)
    if timing is None or timing.source != source:
        raise CanonicalizationError(f"no timing rule for {source}")
    if timing.classification not in {"exact", "proxy", "assumed", "unavailable"}:
        raise CanonicalizationError(f"invalid classification for {source}")
    if (timing.classification == "proxy") != bool(timing.proxy_label):
        raise CanonicalizationError(f"invalid proxy label for {source}")
    output: list[dict[str, str]] = []
    seen_dates: set[str] = set()
    for row in _read_rows(path, header):
        observation_date = row["date"]
        _date(observation_date)
        if observation_date in seen_dates:
            raise CanonicalizationError(f"duplicate market date {observation_date}")
        seen_dates.add(observation_date)
        observed, available = _timestamp(observation_date, timing)
        for column, instrument_id in mappings:
            output.append({
                "observation_date": observation_date, "series_id": "", "instrument_id": instrument_id,
                "value": _number_text(_decimal(row[column], "price")), "value_unit": "price_points",
                "source_observation_time_utc": observed, "available_at_utc": available,
                "source": source, "classification": timing.classification, "proxy_label": timing.proxy_label,
            })
    return output


def canonicalize_daily_market(
    swap_prices_path: Path, treasury_prices_path: Path, timing_rules: Mapping[str, SourceTiming],
) -> dict[int, list[dict[str, str]]]:
    rows = _market_rows(
        swap_prices_path, SWAP_PRICE_HEADER,
        (("eris_swap_2y_price", "ERIS-YIT"), ("eris_swap_5y_price", "ERIS-YIW")), timing_rules, "ERIS",
    )
    rows.extend(_market_rows(
        treasury_prices_path, TREASURY_PRICE_HEADER,
        (("treasury_futures_2y_price", "YAHOO-CONTINUOUS-ZT"), ("treasury_futures_5y_price", "YAHOO-CONTINUOUS-ZF")), timing_rules, "YAHOO",
    ))
    return _partition(rows, ("observation_date", "series_id", "instrument_id", "source", "available_at_utc"))
