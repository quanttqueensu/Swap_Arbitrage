from __future__ import annotations

import argparse
import calendar
import json
import math
import random
import time
from io import StringIO
from numbers import Real
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd

from config import (
    BACKOFF_CAP_SECONDS,
    CACHE_DIR,
    CME_SWAP_DATA_FILE,
    DATA_DIR,
    END_DATE,
    ERIS_PUBLIC_BASE_URL,
    ERIS_PUBLIC_START_DATE,
    ERIS_SOFR_SWAP_FUTURES,
    FRED_CMT_SERIES,
    FRED_CSV_BASE_URL,
    INTEREST_RATE_COLUMNS,
    MATURITIES,
    NYFED_BASE_URL,
    NYFED_RATE_CONFIG,
    NYFED_RATES,
    RATES_FILE,
    RAW_PRICE_DATA_FILE,
    RETRIES,
    START_DATE,
    SWAP_COLUMNS,
    SWAP_B_USD_COLUMNS,
    SWAP_C_USD_COLUMNS,
    SWAP_DV01_COLUMNS,
    SWAP_DV01_YEARS,
    SWAP_EFFECTIVE_DATE_COLUMNS,
    SWAP_EQUIVALENT_PAR_RATE_COLUMNS,
    SWAP_FIXED_COUPON_COLUMNS,
    SWAP_LAST_TRADE_DATE_COLUMNS,
    SWAP_MATURITY_DATE_COLUMNS,
    SWAP_PV01_COLUMNS,
    SWAP_RATES_FILE,
    SWAP_RETURN_COLUMNS,
    SWAP_TICKER_COLUMNS,
    TIMEOUT,
    TREASURY_FUTURES,
    TREASURY_FUTURES_DATA_FILE,
    TREASURY_FUTURES_DV01_METHOD,
    TREASURY_FUTURES_FACE_VALUE,
    TREASURY_FUTURES_FILE,
    TREASURY_FUTURES_HEDGE_RATIOS,
    TREASURY_FUTURES_PRICE_COLUMNS,
    TREASURY_FUTURES_RETURN_COLUMNS,
    TREASURY_FUTURES_SOURCE_SYMBOLS,
    USER_AGENT,
    YAHOO_CHART_BASE_URL,
)
from clean_data import clean_existing_derived_csvs, save_derived_csv, without_dv01_columns


def ensure_directories() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    CACHE_DIR.mkdir(exist_ok=True)


def clean_price_frame(df: pd.DataFrame) -> pd.DataFrame:
    output = df.copy()

    if "date" not in output.columns:
        raise RuntimeError("Dataframe must contain a date column.")

    output["date"] = pd.to_datetime(output["date"], errors="coerce")
    output = output.dropna(subset=["date"])
    output = output.drop_duplicates(subset=["date"])
    output = output.sort_values("date").reset_index(drop=True)

    eris_date_cols = [
        column
        for columns in (
            SWAP_EFFECTIVE_DATE_COLUMNS,
            SWAP_MATURITY_DATE_COLUMNS,
            SWAP_LAST_TRADE_DATE_COLUMNS,
        )
        for column in columns.values()
        if column in output
    ]
    for column in eris_date_cols:
        output[column] = pd.to_datetime(output[column], errors="coerce").dt.normalize()

    text_cols = ["date", *eris_date_cols, *[column for column in output if column.endswith("_ticker")]]
    numeric_cols = output.columns.difference(text_cols)
    output[numeric_cols] = output[numeric_cols].apply(pd.to_numeric, errors="coerce")

    return output


def load_csv(path: Path) -> pd.DataFrame:
    return clean_price_frame(pd.read_csv(path))


def fetch_text(
    url: str,
    cache_file: Path,
    retries: int = RETRIES,
    timeout: int = TIMEOUT,
    accept: str = "*/*",
) -> str:
    last_error = None

    for attempt in range(1, retries + 1):
        try:
            req = Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept})

            with urlopen(req, timeout=timeout) as response:
                text = response.read().decode("utf-8", errors="replace")

            cache_file.write_text(text, encoding="utf-8")
            return text

        except (HTTPError, URLError, TimeoutError) as error:
            last_error = error

            if isinstance(error, HTTPError) and error.code in {400, 401, 403, 404}:
                break

            if attempt < retries:
                sleep_time = min((2 ** (attempt - 1)) + random.uniform(0, 0.75), BACKOFF_CAP_SECONDS)
                print(f"[WARN] Request failed. Attempt {attempt}/{retries}. Retrying in {sleep_time:.1f}s...")
                time.sleep(sleep_time)

    if cache_file.exists():
        print(f"[WARN] Using cached file: {cache_file}")
        return cache_file.read_text(encoding="utf-8")

    raise RuntimeError(f"Failed to fetch URL after {retries} attempts: {url}\nLast error: {last_error}")


def parse_fred_series(
    source: pd.DataFrame,
    series_id: str,
    output_col: str,
) -> pd.DataFrame:
    date_col = next(
        (column for column in ("observation_date", "DATE", "date") if column in source),
        None,
    )
    if date_col is None or series_id not in source:
        raise RuntimeError(f"FRED response is missing {series_id}")
    return clean_price_frame(
        pd.DataFrame({"date": source[date_col], output_col: source[series_id]})
    ).dropna(subset=[output_col])


def get_fred_cmt_series(
    series_id: str,
    start_date: str,
    end_date: str,
) -> pd.DataFrame:
    output_col = FRED_CMT_SERIES.get(series_id)
    if output_col is None:
        raise ValueError(f"Unsupported FRED CMT series: {series_id}")
    print(f"[PULL] FRED {series_id}...")
    url = f"{FRED_CSV_BASE_URL}?{urlencode({'id': series_id, 'cosd': start_date, 'coed': end_date})}"
    text = fetch_text(
        url=url,
        cache_file=CACHE_DIR / f"fred_{series_id.lower()}_{start_date}_{end_date}.csv",
        accept="text/csv,*/*",
    )
    return parse_fred_series(pd.read_csv(StringIO(text)), series_id, output_col)


def get_treasury_data(start_date: str, end_date: str | None = None) -> pd.DataFrame:
    start_ts = pd.to_datetime(start_date)
    end_ts = pd.to_datetime(end_date) if end_date else pd.Timestamp.today().normalize()
    start = start_ts.strftime("%Y-%m-%d")
    end = end_ts.strftime("%Y-%m-%d")
    frames = [get_fred_cmt_series(series_id, start, end) for series_id in FRED_CMT_SERIES]
    merged = frames[0]
    for frame in frames[1:]:
        merged = merged.merge(frame, on="date", how="outer")
    return clean_price_frame(merged)


def extract_json_records(payload) -> list[dict]:
    if isinstance(payload, list):
        return payload

    if not isinstance(payload, dict):
        return []

    for key in ["refRates", "rates", "data", "observations", "results"]:
        value = payload.get(key)

        if isinstance(value, list):
            return value

    records = []

    for value in payload.values():
        if isinstance(value, list):
            records.extend([item for item in value if isinstance(item, dict)])

    return records


def parse_nyfed_rate_json(json_text: str, output_col: str) -> pd.DataFrame:
    rows = []

    for record in extract_json_records(json.loads(json_text)):
        date_value = (
            record.get("effectiveDate")
            or record.get("date")
            or record.get("observationDate")
            or record.get("valueDate")
        )
        rate_value = (
            record.get("percentRate")
            or record.get("rate")
            or record.get("value")
            or record.get("dailyRate")
        )

        if date_value is not None and rate_value is not None:
            rows.append({"date": date_value, output_col: rate_value})

    return pd.DataFrame() if not rows else clean_price_frame(pd.DataFrame(rows))


def nyfed_urls(rate_name: str, start_date: str, end_date: str) -> list[str]:
    api_path = NYFED_RATE_CONFIG[rate_name.upper()]["api_path"]
    base = f"{NYFED_BASE_URL}{api_path}"
    return [
        f"{base}?{urlencode({'startDate': start_date, 'endDate': end_date, 'type': 'rate'})}",
        f"{base}?{urlencode({'startDate': start_date, 'endDate': end_date})}",
    ]


def get_nyfed_rate(rate_name: str, start_date: str, end_date: str | None = None) -> pd.DataFrame:
    rate_name = rate_name.upper()

    if rate_name not in NYFED_RATE_CONFIG:
        raise ValueError(f"Unsupported NY Fed rate: {rate_name}")

    start = pd.to_datetime(start_date).strftime("%Y-%m-%d")
    end = pd.to_datetime(end_date).strftime("%Y-%m-%d") if end_date else pd.Timestamp.today().strftime("%Y-%m-%d")
    output_col = NYFED_RATE_CONFIG[rate_name]["output_col"]
    print(f"[PULL] New York Fed {rate_name}...")
    last_error = None

    for i, url in enumerate(nyfed_urls(rate_name, start, end), start=1):
        try:
            json_text = fetch_text(
                url=url,
                cache_file=CACHE_DIR / f"nyfed_{rate_name.lower()}_{start}_{end}_{i}.json",
                accept="application/json,*/*",
            )
            df = parse_nyfed_rate_json(json_text, output_col)

            if not df.empty:
                return df

            last_error = "JSON loaded but no usable records were found."

        except Exception as error:
            last_error = error
            print(f"[WARN] {rate_name} endpoint failed: {error}")

    raise RuntimeError(f"Failed to load {rate_name}. Last error: {last_error}")


def build_rates_dataset(start_date: str, end_date: str | None = None) -> pd.DataFrame:
    frames = [get_treasury_data(start_date=start_date, end_date=end_date)]

    for rate_name in NYFED_RATES:
        try:
            frames.append(get_nyfed_rate(rate_name=rate_name, start_date=start_date, end_date=end_date))
        except Exception as error:
            print(f"[ERROR] Could not load {rate_name}: {error}")

    merged = frames[0]

    for frame in frames[1:]:
        merged = pd.merge(merged, frame, on="date", how="outer")

    output = clean_price_frame(merged)
    return output[[col for col in ["date", *INTEREST_RATE_COLUMNS] if col in output.columns]]


def eris_archive_month_folder(date: pd.Timestamp) -> str:
    return f"{date.month:02d}-{calendar.month_name[date.month]}"


def eris_settlement_url_candidates(date: pd.Timestamp) -> list[str]:
    ymd = date.strftime("%Y%m%d")
    archive_month = eris_archive_month_folder(date)
    return [
        f"{ERIS_PUBLIC_BASE_URL}/Eris_Instruments_{ymd}_Settles.csv",
        f"{ERIS_PUBLIC_BASE_URL}/archives/sofrdata/{ymd}/Eris_Instruments_{ymd}_Settles_SOFR.csv",
        f"{ERIS_PUBLIC_BASE_URL}/archives/sofrdata/{ymd}/Eris_Instruments_Settles_SOFR.csv",
        f"{ERIS_PUBLIC_BASE_URL}/archives/{date.year}/{archive_month}/Eris_Instruments_{ymd}_Settles.csv",
    ]


def fetch_eris_settlement_text(date: pd.Timestamp) -> str | None:
    ymd = date.strftime("%Y%m%d")
    cache_dir = CACHE_DIR / "eris_sofr_settlements_v3"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / f"Eris_Instruments_{ymd}_Settles.csv"

    if cache_file.exists():
        return cache_file.read_text(encoding="utf-8")

    last_error = None

    for url in eris_settlement_url_candidates(date):
        try:
            req = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/csv,*/*"})

            with urlopen(req, timeout=TIMEOUT) as response:
                text = response.read().decode("utf-8", errors="replace")

            cache_file.write_text(text, encoding="utf-8")
            return text

        except HTTPError as error:
            last_error = error

            if error.code in {400, 401, 403, 404}:
                continue

        except (URLError, TimeoutError) as error:
            last_error = error

    if last_error and not isinstance(last_error, HTTPError):
        print(f"[WARN] Eris settlement fetch failed for {ymd}: {last_error}")

    return None


def read_eris_settlement_file(date: pd.Timestamp) -> pd.DataFrame:
    text = fetch_eris_settlement_text(date)

    if not text:
        return pd.DataFrame()

    try:
        return pd.read_csv(StringIO(text))
    except Exception as error:
        print(f"[WARN] Could not parse Eris settlement file for {date.date()}: {error}")
        return pd.DataFrame()


def _is_finite_real(value: object) -> bool:
    return isinstance(value, Real) and not isinstance(value, bool) and math.isfinite(value)


def equivalent_par_sofr_swap_rate_bps(
    settlement_price: object,
    fixed_coupon_pct: object,
    b_usd: object,
    c_usd: object,
    pv01_usd_per_bp: object,
) -> float | None:
    values = (settlement_price, fixed_coupon_pct, b_usd, c_usd, pv01_usd_per_bp)

    if not all(_is_finite_real(value) for value in values):
        return None
    if settlement_price <= 0 or pv01_usd_per_bp <= 0:
        return None

    a_usd = (settlement_price - 100.0 - b_usd + c_usd) * 1000.0
    equivalent_par_rate_pct = fixed_coupon_pct - (a_usd / pv01_usd_per_bp) / 100.0
    equivalent_par_rate_bps = equivalent_par_rate_pct * 100.0
    return round(equivalent_par_rate_bps, 10) if math.isfinite(equivalent_par_rate_bps) else None


def extract_eris_swap_row(
    settlements: pd.DataFrame,
    fallback_date: pd.Timestamp,
    active_contracts: dict[str, str] | None = None,
) -> dict:
    required_cols = {"ExchangeSymbol (EX005)", "FinalSettlementPrice", "LastTradeDate"}

    if settlements.empty or not required_cols.issubset(settlements.columns):
        return {}

    frame = settlements.copy()

    if "FloatingIndex" in frame.columns:
        frame = frame[frame["FloatingIndex"].astype(str).str.upper().eq("SOFR")]

    if "EvaluationDate" in frame.columns:
        evaluation_date = pd.to_datetime(frame["EvaluationDate"], errors="coerce").dropna()
        row_date = evaluation_date.iloc[0].normalize() if not evaluation_date.empty else fallback_date
    else:
        row_date = fallback_date

    frame["_last_trade_date"] = pd.to_datetime(frame["LastTradeDate"], errors="coerce").dt.normalize()
    row = {"date": row_date}

    for maturity, symbol in ERIS_SOFR_SWAP_FUTURES.items():
        price_col = SWAP_COLUMNS[maturity]
        dv01_col = SWAP_DV01_COLUMNS.get(maturity)
        ticker_col = SWAP_TICKER_COLUMNS.get(maturity)
        candidates = frame[frame["ExchangeSymbol (EX005)"].eq(symbol)].copy()
        candidates = candidates[candidates["_last_trade_date"] >= row_date]

        if candidates.empty:
            continue

        sort_cols = ["_last_trade_date"]
        target_dv01 = None

        if "DV01" in candidates.columns and maturity in SWAP_DV01_YEARS:
            candidates["DV01"] = pd.to_numeric(candidates["DV01"], errors="coerce")
            target_dv01 = SWAP_DV01_YEARS[maturity] * 10.0
            lower_dv01 = target_dv01 * 0.9
            upper_dv01 = target_dv01 * 1.1
            candidates["_dv01_gap"] = (candidates["DV01"] - target_dv01).abs()
            near_tenor = candidates[(candidates["DV01"] >= lower_dv01) & (candidates["DV01"] <= upper_dv01)]

            if not near_tenor.empty:
                candidates = near_tenor

            sort_cols = ["_dv01_gap", "_last_trade_date"]

        selected = None

        if active_contracts is not None and "Symbol" in candidates.columns:
            active_symbol = active_contracts.get(maturity)

            if active_symbol:
                active = candidates[candidates["Symbol"].eq(active_symbol)]

                if not active.empty:
                    active_row = active.sort_values("_last_trade_date").iloc[0]
                    active_dv01 = active_row.get("DV01")
                    in_band = (
                        target_dv01 is None
                        or pd.isna(active_dv01)
                        or lower_dv01 <= active_dv01 <= upper_dv01
                    )

                    if in_band:
                        selected = active_row

        if selected is None:
            selected = candidates.sort_values(sort_cols).iloc[0]

        price = pd.to_numeric(selected.get("FinalSettlementPrice"), errors="coerce")

        if pd.isna(price):
            continue

        if active_contracts is not None and "Symbol" in selected.index and pd.notna(selected["Symbol"]):
            active_contracts[maturity] = selected["Symbol"]

        row[price_col] = price

        if ticker_col and "Symbol" in selected.index and pd.notna(selected["Symbol"]):
            row[ticker_col] = str(selected["Symbol"]).strip()

        if dv01_col and "DV01" in selected.index:
            dv01 = pd.to_numeric(selected.get("DV01"), errors="coerce")

            if not pd.isna(dv01):
                row[dv01_col] = dv01

        fixed_coupon_pct = selected.get("Coupon (%)")
        b_usd = selected.get("PastFxdFltPmts (B)")
        c_usd = selected.get("ErisPAI (C)")
        pv01_usd_per_bp = selected.get("PV01")
        retained_values = {
            SWAP_FIXED_COUPON_COLUMNS[maturity]: fixed_coupon_pct,
            SWAP_B_USD_COLUMNS[maturity]: b_usd,
            SWAP_C_USD_COLUMNS[maturity]: c_usd,
            SWAP_PV01_COLUMNS[maturity]: pv01_usd_per_bp,
            SWAP_EFFECTIVE_DATE_COLUMNS[maturity]: selected.get("EffectiveDate"),
            SWAP_MATURITY_DATE_COLUMNS[maturity]: selected.get("MaturityDate"),
            SWAP_LAST_TRADE_DATE_COLUMNS[maturity]: selected.get("LastTradeDate"),
        }

        for column, value in retained_values.items():
            if pd.notna(value):
                row[column] = value

        equivalent_par_rate_bps = equivalent_par_sofr_swap_rate_bps(
            price,
            fixed_coupon_pct,
            b_usd,
            c_usd,
            pv01_usd_per_bp,
        )
        if equivalent_par_rate_bps is not None:
            row[SWAP_EQUIVALENT_PAR_RATE_COLUMNS[maturity]] = equivalent_par_rate_bps

    return row if len(row) > 1 else {}


def get_eris_public_swap_data(start_date: str, end_date: str | None = None) -> pd.DataFrame:
    start_ts = pd.to_datetime(start_date)
    end_ts = pd.to_datetime(end_date) if end_date else pd.Timestamp.today().normalize()
    public_start = pd.to_datetime(ERIS_PUBLIC_START_DATE)

    if start_ts < public_start:
        print(f"[INFO] Eris public SOFR pull starts at {public_start.date()}; requested start was {start_ts.date()}.")
        start_ts = public_start

    dates = pd.bdate_range(start_ts, end_ts)
    rows = []
    misses = 0
    active_contracts: dict[str, str] = {}

    print(f"[PULL] Eris public SOFR swap settlements {start_ts.date()} to {end_ts.date()}...")

    for index, date in enumerate(dates, start=1):
        row = extract_eris_swap_row(read_eris_settlement_file(date), date, active_contracts=active_contracts)

        if row:
            rows.append(row)
        else:
            misses += 1

        if index % 100 == 0:
            print(f"[PULL] Eris settlements checked {index:,}/{len(dates):,} dates...")

    if not rows:
        raise RuntimeError("No Eris public SOFR swap futures data loaded.")

    output = clean_price_frame(pd.DataFrame(rows))

    for maturity in MATURITIES:
        price_col = SWAP_COLUMNS.get(maturity)
        return_col = SWAP_RETURN_COLUMNS.get(maturity)

        if price_col in output.columns and return_col:
            output[return_col] = output[price_col].pct_change()

    preferred_order = ["date"]

    for maturity in MATURITIES:
        preferred_order.extend(
            col
            for col in [
                SWAP_TICKER_COLUMNS.get(maturity),
                SWAP_COLUMNS.get(maturity),
                SWAP_RETURN_COLUMNS.get(maturity),
                SWAP_DV01_COLUMNS.get(maturity),
                SWAP_EQUIVALENT_PAR_RATE_COLUMNS.get(maturity),
                SWAP_FIXED_COUPON_COLUMNS.get(maturity),
                SWAP_B_USD_COLUMNS.get(maturity),
                SWAP_C_USD_COLUMNS.get(maturity),
                SWAP_PV01_COLUMNS.get(maturity),
                SWAP_EFFECTIVE_DATE_COLUMNS.get(maturity),
                SWAP_MATURITY_DATE_COLUMNS.get(maturity),
                SWAP_LAST_TRADE_DATE_COLUMNS.get(maturity),
            ]
            if col in output.columns
        )

    print(
        "[OK] Eris public SOFR swaps: "
        f"{len(output):,} rows from {output['date'].min().date()} to {output['date'].max().date()} "
        f"({misses:,} dates skipped)"
    )
    return output[preferred_order]


def build_cme_swap_data(eris: pd.DataFrame) -> pd.DataFrame:
    frames = []

    for maturity in MATURITIES:
        ticker_col = SWAP_TICKER_COLUMNS.get(maturity)
        price_col = SWAP_COLUMNS.get(maturity)
        dv01_col = SWAP_DV01_COLUMNS.get(maturity)

        if not ticker_col or not price_col or not dv01_col:
            continue

        columns = {"date", ticker_col, price_col, dv01_col}

        if not columns.issubset(eris.columns):
            continue

        frames.append(
            eris[["date", ticker_col, price_col, dv01_col]].rename(
                columns={
                    ticker_col: "ticker",
                    price_col: "price",
                    dv01_col: "dv01",
                }
            )
        )

    if not frames:
        raise RuntimeError("No selected CME swap rows available for the master file.")

    output = pd.concat(frames, ignore_index=True)
    output["date"] = pd.to_datetime(output["date"], errors="coerce").dt.normalize()
    output["ticker"] = output["ticker"].astype("string").str.strip()
    output["price"] = pd.to_numeric(output["price"], errors="coerce")
    output["dv01"] = pd.to_numeric(output["dv01"], errors="coerce")
    output = output.dropna(subset=["date", "ticker", "price", "dv01"])
    output["ticker"] = output["ticker"].astype("str")
    output = output[
        (output["ticker"] != "")
        & (output["price"] > 0)
        & (output["dv01"] > 0)
    ]

    if output.duplicated(["date", "ticker"]).any():
        raise RuntimeError("Duplicate date/ticker rows in CME swap master data.")

    return output.sort_values(["date", "ticker"]).reset_index(drop=True)


def strategy_swap_prices(eris: pd.DataFrame) -> pd.DataFrame:
    output = without_dv01_columns(eris)

    for maturity in MATURITIES:
        ticker_col = SWAP_TICKER_COLUMNS.get(maturity)
        price_col = SWAP_COLUMNS.get(maturity)
        return_col = SWAP_RETURN_COLUMNS.get(maturity)

        if not ticker_col or ticker_col not in eris or not price_col or price_col not in output:
            continue

        ticker = eris[ticker_col].astype("string").str.strip()
        price = pd.to_numeric(output[price_col], errors="coerce")
        roll = ticker.notna() & ticker.shift().notna() & ticker.ne(ticker.shift())
        roll_adjustment = (price.shift() - price).where(roll, 0.0).fillna(0.0).cumsum()
        output[price_col] = price + roll_adjustment

        if return_col and return_col in output:
            output.loc[roll, return_col] = 0.0

    omitted_columns = [
        *[column for column in output if column.endswith("_ticker")],
        *[column for column in SWAP_FIXED_COUPON_COLUMNS.values() if column in output],
        *[column for column in SWAP_B_USD_COLUMNS.values() if column in output],
        *[column for column in SWAP_C_USD_COLUMNS.values() if column in output],
        *[column for column in SWAP_PV01_COLUMNS.values() if column in output],
        *[column for column in SWAP_EFFECTIVE_DATE_COLUMNS.values() if column in output],
        *[column for column in SWAP_MATURITY_DATE_COLUMNS.values() if column in output],
        *[column for column in SWAP_LAST_TRADE_DATE_COLUMNS.values() if column in output],
    ]
    return output.drop(columns=omitted_columns)


def parse_yahoo_chart(text: str, ticker: str) -> pd.DataFrame:
    payload = json.loads(text)
    chart = payload.get("chart", {})
    results = chart.get("result") or []

    if not results:
        raise RuntimeError(f"No public Treasury futures data returned for {ticker}.")

    result = results[0]
    timestamps = result.get("timestamp") or []
    quotes = result.get("indicators", {}).get("quote") or []
    closes = quotes[0].get("close", []) if quotes else []
    output = pd.DataFrame({"timestamp": timestamps, "price": closes})
    output["date"] = pd.to_datetime(output["timestamp"], unit="s", utc=True).dt.tz_localize(None).dt.normalize()
    output["ticker"] = ticker
    output["price"] = pd.to_numeric(output["price"], errors="coerce")
    output = output.dropna(subset=["date", "price"])
    return output[["date", "ticker", "price"]].sort_values("date").reset_index(drop=True)


def get_public_treasury_futures_prices(
    start_date: str,
    end_date: str | None = None,
) -> pd.DataFrame:
    start = pd.to_datetime(start_date).normalize()
    end = pd.to_datetime(end_date).normalize() if end_date else pd.Timestamp.today().normalize()
    period1 = int(start.tz_localize("UTC").timestamp())
    period2 = int((end + pd.Timedelta(days=1)).tz_localize("UTC").timestamp())
    frames = []

    for ticker in TREASURY_FUTURES_SOURCE_SYMBOLS.values():
        params = urlencode(
            {
                "period1": period1,
                "period2": period2,
                "interval": "1d",
                "events": "history",
            }
        )
        url = f"{YAHOO_CHART_BASE_URL}/{ticker}?{params}"
        safe_ticker = ticker.replace("=", "_")
        text = fetch_text(
            url,
            CACHE_DIR / f"yahoo_{safe_ticker}_{start.date()}_{end.date()}.json",
            accept="application/json,*/*",
        )
        frames.append(parse_yahoo_chart(text, ticker))

    output = pd.concat(frames, ignore_index=True).sort_values(["date", "ticker"]).reset_index(drop=True)
    print(
        "[WARN] Treasury futures research prices use public continuous closes; "
        f"DV01 method={TREASURY_FUTURES_DV01_METHOD}. Production settlement/CTD "
        "data requires CME DataMine or another licensed source."
    )
    return output


def build_treasury_futures_data(
    prices: pd.DataFrame,
    cme_swap_data: pd.DataFrame,
) -> pd.DataFrame:
    frames = []

    for maturity, treasury_ticker in TREASURY_FUTURES_SOURCE_SYMBOLS.items():
        swap_root = ERIS_SOFR_SWAP_FUTURES[maturity]
        ratio = TREASURY_FUTURES_HEDGE_RATIOS[maturity]
        treasury_rows = prices[prices["ticker"].eq(treasury_ticker)][["date", "ticker", "price"]]
        swap_rows = cme_swap_data[cme_swap_data["ticker"].str.startswith(swap_root)][["date", "dv01"]]
        paired = treasury_rows.merge(swap_rows, on="date", how="inner")
        paired["dv01"] = pd.to_numeric(paired["dv01"], errors="coerce") * ratio
        frames.append(paired)

    if not frames:
        raise RuntimeError("No paired Treasury futures prices and CME swap DV01 data available.")

    output = pd.concat(frames, ignore_index=True)
    output["date"] = pd.to_datetime(output["date"], errors="coerce").dt.normalize()
    output["ticker"] = output["ticker"].astype("string").str.strip()
    output["price"] = pd.to_numeric(output["price"], errors="coerce")
    output["dv01"] = pd.to_numeric(output["dv01"], errors="coerce")
    output = output.dropna(subset=["date", "ticker", "price", "dv01"])
    output = output[(output["ticker"] != "") & (output["price"] > 0) & (output["dv01"] > 0)]

    if output.duplicated(["date", "ticker"]).any():
        raise RuntimeError("Duplicate date/ticker rows in Treasury futures master data.")

    return output[["date", "ticker", "price", "dv01"]].sort_values(["date", "ticker"]).reset_index(drop=True)


def build_ctd_treasury_futures_data(source: pd.DataFrame) -> pd.DataFrame:
    required = [
        "date",
        "ticker",
        "price",
        "ctd_cash_dv01_per_100k",
        "conversion_factor",
    ]

    if not set(required).issubset(source.columns):
        raise RuntimeError(f"CTD Treasury futures input requires columns {required}.")

    output = source[required].copy()
    output["date"] = pd.to_datetime(output["date"], errors="coerce").dt.normalize()
    output["ticker"] = output["ticker"].astype("string").str.strip()

    for column in ["price", "ctd_cash_dv01_per_100k", "conversion_factor"]:
        output[column] = pd.to_numeric(output[column], errors="coerce")

    face_multiplier = pd.Series(index=output.index, dtype="float64")

    for maturity, root in TREASURY_FUTURES.items():
        face_multiplier.loc[output["ticker"].str.startswith(root, na=False)] = (
            TREASURY_FUTURES_FACE_VALUE[maturity] / 100_000.0
        )

    invalid = (
        output[required].isna().any(axis=1)
        | output["ticker"].eq("")
        | output["price"].le(0)
        | output["ctd_cash_dv01_per_100k"].le(0)
        | output["conversion_factor"].le(0)
        | face_multiplier.isna()
    )

    if invalid.any():
        raise RuntimeError("CTD Treasury futures input contains missing or invalid values.")

    missing_roots = [
        root
        for root in TREASURY_FUTURES.values()
        if not output["ticker"].str.startswith(root).any()
    ]

    if missing_roots:
        required_roots = " and ".join(TREASURY_FUTURES.values())
        raise RuntimeError(f"CTD Treasury futures input requires {required_roots} contracts.")

    for maturity, root in TREASURY_FUTURES.items():
        root_rows = output[output["ticker"].str.startswith(root)]

        if root_rows["date"].duplicated().any():
            raise RuntimeError(f"Multiple {maturity} Treasury futures contracts on one date.")

    output["dv01"] = (
        output["ctd_cash_dv01_per_100k"]
        * face_multiplier
        / output["conversion_factor"]
    )
    output = output[["date", "ticker", "price", "dv01"]]

    if output.duplicated(["date", "ticker"]).any():
        raise RuntimeError("CTD Treasury futures input contains duplicate date/ticker rows.")

    return output.sort_values(["date", "ticker"]).reset_index(drop=True)


def strategy_treasury_futures_prices(master: pd.DataFrame) -> pd.DataFrame:
    output = None

    for maturity, root in TREASURY_FUTURES.items():
        price_col = TREASURY_FUTURES_PRICE_COLUMNS[maturity]
        return_col = TREASURY_FUTURES_RETURN_COLUMNS[maturity]
        selected = master[master["ticker"].str.startswith(root)][["date", "ticker", "price"]]

        if selected["date"].duplicated().any():
            raise RuntimeError(f"Multiple {maturity} Treasury futures contracts on one date.")

        selected = selected.sort_values("date").reset_index(drop=True)
        price = pd.to_numeric(selected["price"], errors="coerce")
        ticker = selected["ticker"].astype("string").str.strip()
        roll = ticker.notna() & ticker.shift().notna() & ticker.ne(ticker.shift())
        adjustment = (price.shift() - price).where(roll, 0.0).fillna(0.0).cumsum()
        rows = selected[["date"]].copy()
        rows[price_col] = price + adjustment
        rows[return_col] = rows[price_col].pct_change()
        rows.loc[roll, return_col] = 0.0
        output = rows if output is None else output.merge(rows, on="date", how="outer")

    if output is None:
        raise RuntimeError("No Treasury futures strategy prices available.")

    preferred = ["date"]

    for maturity in TREASURY_FUTURES:
        preferred.extend(
            [
                TREASURY_FUTURES_PRICE_COLUMNS[maturity],
                TREASURY_FUTURES_RETURN_COLUMNS[maturity],
            ]
        )

    return clean_price_frame(output)[preferred]


def merge_price_data(*frames: pd.DataFrame | None) -> pd.DataFrame:
    loaded = [frame for frame in frames if frame is not None and not frame.empty]

    if not loaded:
        raise RuntimeError("No source data loaded for raw price data.")

    merged = loaded[0]

    for frame in loaded[1:]:
        merged = pd.merge(merged, frame, on="date", how="outer")

    return clean_price_frame(merged)


def build_raw_price_data(
    refresh_interest_rates: bool = False,
    refresh_eris: bool = False,
    refresh_treasury_futures: bool = False,
    treasury_futures_ctd_file: Path | None = None,
    start_date: str = START_DATE,
    end_date: str | None = END_DATE,
    save: bool = True,
) -> pd.DataFrame:
    ensure_directories()
    refresh_treasury_futures = (
        refresh_treasury_futures or refresh_eris or treasury_futures_ctd_file is not None
    )

    if refresh_interest_rates or not RATES_FILE.exists():
        rates = build_rates_dataset(start_date=start_date, end_date=end_date)
        save_derived_csv(rates, RATES_FILE)
        print(f"[SAVED] {RATES_FILE}")
        rates = load_csv(RATES_FILE)
    else:
        rates = load_csv(RATES_FILE)

    eris = None
    cme_swap_master = None

    if refresh_eris:
        selected = get_eris_public_swap_data(start_date=start_date, end_date=end_date)
        cme_swap_master = build_cme_swap_data(selected)
        cme_swap_master.to_csv(CME_SWAP_DATA_FILE, index=False, date_format="%Y-%m-%d")
        print(f"[SAVED] {CME_SWAP_DATA_FILE}")
        eris = strategy_swap_prices(selected)
        save_derived_csv(eris, SWAP_RATES_FILE)
        print(f"[SAVED] {SWAP_RATES_FILE}")
        eris = load_csv(SWAP_RATES_FILE)

    elif SWAP_RATES_FILE.exists():
        eris = load_csv(SWAP_RATES_FILE)

    treasury_futures = None

    if refresh_treasury_futures:
        if treasury_futures_ctd_file is not None:
            treasury_master = build_ctd_treasury_futures_data(
                pd.read_csv(treasury_futures_ctd_file)
            )
            print(f"[OK] Licensed CTD Treasury futures input: {treasury_futures_ctd_file}")
        else:
            if cme_swap_master is None:
                if not CME_SWAP_DATA_FILE.exists():
                    raise FileNotFoundError(
                        f"Missing {CME_SWAP_DATA_FILE}. Run `python -m data_pipeline.historical_data.historical_data_builder --eris` first."
                    )
                cme_swap_master = pd.read_csv(CME_SWAP_DATA_FILE)
                cme_swap_master["date"] = pd.to_datetime(cme_swap_master["date"], errors="coerce")

            treasury_prices = get_public_treasury_futures_prices(start_date, end_date)
            treasury_master = build_treasury_futures_data(treasury_prices, cme_swap_master)
        treasury_master.to_csv(TREASURY_FUTURES_DATA_FILE, index=False, date_format="%Y-%m-%d")
        print(f"[SAVED] {TREASURY_FUTURES_DATA_FILE}")
        treasury_futures = strategy_treasury_futures_prices(treasury_master)
        save_derived_csv(treasury_futures, TREASURY_FUTURES_FILE)
        print(f"[SAVED] {TREASURY_FUTURES_FILE}")
        treasury_futures = load_csv(TREASURY_FUTURES_FILE)

    elif TREASURY_FUTURES_FILE.exists():
        treasury_futures = load_csv(TREASURY_FUTURES_FILE)

    raw = merge_price_data(rates, eris, treasury_futures)

    if save:
        raw = save_derived_csv(raw, RAW_PRICE_DATA_FILE)
        print(f"[SAVED] {RAW_PRICE_DATA_FILE}")
        cleaned = clean_existing_derived_csvs(
            DATA_DIR,
            [CME_SWAP_DATA_FILE, TREASURY_FUTURES_DATA_FILE],
        )

        for path in cleaned:
            print(f"[CLEANED DV01] {path}")

    print(f"[RAW PRICE DATA] rows={len(raw):,} range={raw['date'].min().date()} to {raw['date'].max().date()}")
    return raw


def self_check() -> None:
    rates = pd.DataFrame({"date": ["2024-01-02"], "dgs2": [4.1], "sofr": [5.3]})
    selected = pd.DataFrame(
        {
            "date": ["2024-01-02"],
            "eris_swap_2y_ticker": ["YITH24"],
            "eris_swap_2y_price": [100.0],
            "eris_swap_2y_dv01": [19.0],
        }
    )
    master = build_cme_swap_data(selected)
    merged = merge_price_data(rates, strategy_swap_prices(selected))

    assert merged.loc[0, "dgs2"] == 4.1
    assert "eris_swap_2y_dv01" not in merged
    assert master.loc[0, "ticker"] == "YITH24"
    assert master.loc[0, "dv01"] == 19.0
    print("[OK] self-check passed")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build consolidated raw price data.")
    parser.add_argument(
        "--treasury",
        "--interest-rates",
        "--interest_rates",
        "--pull-treasury",
        dest="interest_rates",
        action="store_true",
        help="Refresh Treasury curve and NY Fed rate data.",
    )
    parser.add_argument(
        "--eris",
        "--pull-swaps",
        dest="eris",
        action="store_true",
        help="Refresh public Eris swap settlement and DV01 data.",
    )
    parser.add_argument(
        "--treasury-futures",
        dest="treasury_futures",
        action="store_true",
        help="Refresh continuous ZT/ZF research prices and the Treasury futures master.",
    )
    parser.add_argument(
        "--treasury-futures-ctd-file",
        type=Path,
        help=(
            "Build the Treasury master from a normalized licensed CTD CSV with "
            "date,ticker,price,ctd_cash_dv01_per_100k,conversion_factor."
        ),
    )
    parser.add_argument("--start", default=START_DATE)
    parser.add_argument("--end", default=END_DATE)
    parser.add_argument("--self-check", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if args.self_check:
        self_check()
        return

    build_raw_price_data(
        refresh_interest_rates=args.interest_rates,
        refresh_eris=args.eris,
        refresh_treasury_futures=args.treasury_futures,
        treasury_futures_ctd_file=args.treasury_futures_ctd_file,
        start_date=args.start,
        end_date=args.end,
    )


if __name__ == "__main__":
    main()
