from __future__ import annotations

from dataclasses import dataclass
import calendar
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Literal
import math

from strategy.eris_pricing import PriceQuote
from strategy.live_signal import TreasuryYieldQuote


ContractKind = Literal["eris", "treasury_yield"]


class MarketDataError(RuntimeError):
    def __init__(self, reason_code: str, detail: str) -> None:
        super().__init__(detail)
        self.reason_code = reason_code


@dataclass(frozen=True)
class ContractRequest:
    symbol: str
    kind: ContractKind
    exchange: str = "CBOT"
    currency: str = "USD"


def _default_contract_factory(**kwargs: Any) -> Any:
    try:
        from ib_insync import Future
    except ImportError as exc:
        raise ImportError(
            "Missing dependency: ib_insync. Install requirements before live IBKR use."
        ) from exc
    return Future(**kwargs)


def _parse_contract_date(value: object, *, month_end: bool = False) -> date | None:
    raw = str(value or "").strip()
    if not raw.isdigit():
        return None
    try:
        if len(raw) == 6:
            parsed = datetime.strptime(raw, "%Y%m").date()
            if month_end:
                return date(
                    parsed.year,
                    parsed.month,
                    calendar.monthrange(parsed.year, parsed.month)[1],
                )
            return parsed
        if len(raw) == 8:
            return datetime.strptime(raw, "%Y%m%d").date()
    except ValueError:
        return None
    return None


def _as_decimal(name: str, value: object, *, positive: bool = True) -> Decimal:
    if value is None or isinstance(value, bool):
        raise MarketDataError("missing_market_field", f"missing {name}")
    if isinstance(value, float) and not math.isfinite(value):
        raise MarketDataError("invalid_market_field", f"non-finite {name}")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise MarketDataError("invalid_market_field", f"invalid {name}") from exc
    if not result.is_finite():
        raise MarketDataError("invalid_market_field", f"non-finite {name}")
    if positive and result <= 0:
        raise MarketDataError("invalid_market_field", f"non-positive {name}")
    return result


def _quote_time(ticker: Any) -> datetime:
    value = getattr(ticker, "time", None)
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise MarketDataError("invalid_quote_timestamp", "missing quote timestamp")
    return value.astimezone(timezone.utc)


class IbkrLiveMarketSource:
    def __init__(
        self,
        ib: Any,
        *,
        contract_factory: Callable[..., Any] | None = None,
        quote_max_age_seconds: int = 30,
        min_days_to_expiry: int = 1,
        preferred_con_ids: dict[str, int] | None = None,
    ) -> None:
        if quote_max_age_seconds <= 0:
            raise ValueError("quote_max_age_seconds must be positive")
        if type(min_days_to_expiry) is not int or min_days_to_expiry <= 0:
            raise ValueError("min_days_to_expiry must be a positive integer")
        self.ib = ib
        self.contract_factory = contract_factory or _default_contract_factory
        self.quote_max_age_seconds = quote_max_age_seconds
        self.min_days_to_expiry = min_days_to_expiry
        self.preferred_con_ids = dict(preferred_con_ids or {})
        self._contract_cache: dict[tuple[object, ...], tuple[Any, Any]] = {}
        self._cache_date: date | None = None

    def set_preferred_contracts(self, con_ids: dict[str, int]) -> None:
        normalized = {
            str(symbol): con_id
            for symbol, con_id in con_ids.items()
            if type(con_id) is int and con_id > 0
        }
        if normalized != self.preferred_con_ids:
            self.preferred_con_ids = normalized
            self._contract_cache.clear()

    def snapshot(
        self,
        requests: list[ContractRequest],
        *,
        now: datetime,
    ) -> dict[str, PriceQuote | TreasuryYieldQuote]:
        if now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        if len({request.symbol for request in requests}) != len(requests):
            raise ValueError("duplicate signal symbol request")

        now_utc = now.astimezone(timezone.utc)
        resolved = [
            (request, *self._resolve_contract(request, now_utc.date()))
            for request in requests
        ]
        contracts = [contract for _, _, contract in resolved]
        tickers = list(self.ib.reqTickers(*contracts)) if contracts else []
        if len(tickers) != len(resolved):
            raise MarketDataError(
                "missing_market_quote",
                f"expected {len(resolved)} quote snapshots, got {len(tickers)}",
            )

        output: dict[str, PriceQuote | TreasuryYieldQuote] = {}
        for (request, detail, contract), ticker in zip(resolved, tickers):
            output[request.symbol] = self._normalize_quote(
                request, detail, contract, ticker, now_utc
            )
        return output

    def _resolve_contract(
        self,
        request: ContractRequest,
        as_of: date,
    ) -> tuple[Any, Any]:
        if self._cache_date != as_of:
            self._contract_cache.clear()
            self._cache_date = as_of
        cache_key = (
            request.symbol,
            request.kind,
            request.exchange,
            request.currency,
            self.preferred_con_ids.get(request.symbol),
        )
        cached = self._contract_cache.get(cache_key)
        if cached is not None:
            return cached

        seed = self.contract_factory(
            symbol=request.symbol,
            exchange=request.exchange,
            currency=request.currency,
        )
        details = list(self.ib.reqContractDetails(seed))
        if not details:
            raise MarketDataError(
                "contract_qualification_failure",
                f"no contract details for {request.symbol}",
            )

        candidates: list[tuple[object, Any]] = []
        expiry_threshold = as_of + timedelta(days=self.min_days_to_expiry)
        if request.kind == "eris":
            for detail in details:
                contract_month = _parse_contract_date(
                    getattr(detail.contract, "contractMonth", "")
                )
                expiry = _parse_contract_date(
                    getattr(
                        detail.contract, "lastTradeDateOrContractMonth", ""
                    ),
                    month_end=True,
                )
                if (
                    contract_month is not None
                    and contract_month <= as_of
                    and expiry is not None
                    and expiry > expiry_threshold
                ):
                    candidates.append(
                        ((-contract_month.toordinal(), expiry.toordinal()), detail)
                    )
            candidates.sort(key=lambda item: item[0])
        else:
            for detail in details:
                expiry = _parse_contract_date(
                    getattr(detail.contract, "lastTradeDateOrContractMonth", ""),
                    month_end=True,
                )
                if expiry is not None and expiry > expiry_threshold:
                    candidates.append((expiry, detail))
            candidates.sort(key=lambda item: item[0])

        if not candidates:
            raise MarketDataError(
                "contract_qualification_failure",
                f"no eligible contract for {request.symbol}",
            )

        preferred = self.preferred_con_ids.get(request.symbol)
        detail = next(
            (
                item[1]
                for item in candidates
                if getattr(getattr(item[1], "contract", None), "conId", None)
                == preferred
            ),
            candidates[0][1],
        )
        qualified = list(self.ib.qualifyContracts(detail.contract))
        if len(qualified) != 1:
            raise MarketDataError(
                "contract_qualification_failure",
                f"expected exactly one qualified contract for {request.symbol}",
            )
        resolved = (detail, qualified[0])
        self._contract_cache[cache_key] = resolved
        return resolved

    def _normalize_quote(
        self,
        request: ContractRequest,
        detail: Any,
        contract: Any,
        ticker: Any,
        now_utc: datetime,
    ) -> PriceQuote | TreasuryYieldQuote:
        observed_at = _quote_time(ticker)
        age = (now_utc - observed_at).total_seconds()
        if age < 0:
            raise MarketDataError(
                "future_quote", f"future quote for {request.symbol}"
            )
        if age > self.quote_max_age_seconds:
            raise MarketDataError(
                "stale_quote", f"stale quote for {request.symbol}"
            )

        bid = _as_decimal("bid", getattr(ticker, "bid", None))
        ask = _as_decimal("ask", getattr(ticker, "ask", None))
        if bid > ask:
            raise MarketDataError(
                "crossed_quote", f"crossed quote for {request.symbol}"
            )
        last_value = getattr(ticker, "last", None)
        last = None
        try:
            if last_value is not None:
                last = _as_decimal("last", last_value)
        except MarketDataError:
            last = None

        contract_id = str(getattr(contract, "conId", "")).strip()
        if not contract_id or contract_id == "0":
            raise MarketDataError(
                "contract_qualification_failure",
                f"missing contract id for {request.symbol}",
            )

        if request.kind == "treasury_yield":
            mid = (bid + ask) / Decimal("2")
            return TreasuryYieldQuote(
                contract_id=contract_id,
                symbol=request.symbol,
                observed_at=observed_at,
                bid_percent=bid,
                ask_percent=ask,
                mid_percent=mid,
            )

        min_tick = _as_decimal(
            "min_tick", getattr(detail, "minTick", None), positive=True
        )
        return PriceQuote(
            contract_id=contract_id,
            symbol=request.symbol,
            observed_at=observed_at,
            bid=bid,
            ask=ask,
            last=last,
            min_tick=min_tick,
        )
