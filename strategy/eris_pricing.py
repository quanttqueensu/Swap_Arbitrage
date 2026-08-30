from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


def _require_decimal(
    name: str,
    value: object,
    *,
    positive: bool = False,
    nonnegative: bool = False,
) -> Decimal:
    if type(value) is not Decimal or not value.is_finite():
        raise TypeError(f"{name} must be a finite Decimal")
    if positive and value <= 0:
        raise ValueError(f"{name} must be positive")
    if nonnegative and value < 0:
        raise ValueError(f"{name} must be nonnegative")
    return value


def _require_aware_datetime(name: str, value: datetime) -> None:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


@dataclass(frozen=True)
class ErisReference:
    contract_id: str
    fixed_rate_decimal: Decimal
    b_price_points: Decimal
    c_price_points: Decimal
    pv01_usd_per_bp: Decimal
    effective_date: str
    maturity_date: str
    observed_at: datetime

    def __post_init__(self) -> None:
        if not self.contract_id.strip():
            raise ValueError("contract_id is required")
        _require_decimal("fixed_rate_decimal", self.fixed_rate_decimal)
        _require_decimal("b_price_points", self.b_price_points)
        _require_decimal("c_price_points", self.c_price_points)
        _require_decimal("pv01_usd_per_bp", self.pv01_usd_per_bp)
        _require_aware_datetime("observed_at", self.observed_at)


@dataclass(frozen=True)
class PriceQuote:
    contract_id: str
    symbol: str
    observed_at: datetime
    bid: Decimal
    ask: Decimal
    last: Decimal | None
    min_tick: Decimal

    def __post_init__(self) -> None:
        if not self.contract_id.strip():
            raise ValueError("contract_id is required")
        if not self.symbol.strip():
            raise ValueError("symbol is required")
        _require_aware_datetime("observed_at", self.observed_at)
        _require_decimal("bid", self.bid, positive=True)
        _require_decimal("ask", self.ask, positive=True)
        if self.last is not None:
            _require_decimal("last", self.last, positive=True)
        _require_decimal("min_tick", self.min_tick, positive=True)


@dataclass(frozen=True)
class ErisParRateQuote:
    contract_id: str
    symbol: str
    observed_at: datetime
    bid_price: Decimal
    ask_price: Decimal
    mid_price: Decimal
    bid_par_rate_bps: Decimal
    ask_par_rate_bps: Decimal
    mid_par_rate_bps: Decimal


def eris_a_usd(price: Decimal, reference: ErisReference) -> Decimal:
    p = _require_decimal("price", price, positive=True)
    b = _require_decimal("b_price_points", reference.b_price_points)
    c = _require_decimal("c_price_points", reference.c_price_points)
    _require_decimal("pv01_usd_per_bp", reference.pv01_usd_per_bp, positive=True)
    return (p - Decimal("100") - b + c) * Decimal("1000")


def eris_par_rate_bps(price: Decimal, reference: ErisReference) -> Decimal:
    fixed = _require_decimal("fixed_rate_decimal", reference.fixed_rate_decimal)
    pv01 = _require_decimal(
        "pv01_usd_per_bp", reference.pv01_usd_per_bp, positive=True
    )
    a_usd = eris_a_usd(price, reference)
    return fixed * Decimal("10000") - (a_usd / pv01)


def convert_eris_quote(
    quote: PriceQuote,
    reference: ErisReference,
) -> ErisParRateQuote:
    if quote.contract_id != reference.contract_id:
        raise ValueError("ERIS quote/reference contract mismatch")

    bid = _require_decimal("bid", quote.bid, positive=True)
    ask = _require_decimal("ask", quote.ask, positive=True)
    if bid > ask:
        raise ValueError("crossed ERIS quote")

    mid = (bid + ask) / Decimal("2")
    return ErisParRateQuote(
        contract_id=quote.contract_id,
        symbol=quote.symbol,
        observed_at=quote.observed_at,
        bid_price=bid,
        ask_price=ask,
        mid_price=mid,
        bid_par_rate_bps=eris_par_rate_bps(bid, reference),
        ask_par_rate_bps=eris_par_rate_bps(ask, reference),
        mid_par_rate_bps=eris_par_rate_bps(mid, reference),
    )
