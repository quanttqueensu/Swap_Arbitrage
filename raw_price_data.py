from __future__ import annotations

import argparse
import json
import random
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd

from config import (
    BACKOFF_CAP_SECONDS,
    CACHE_DIR,
    DATA_DIR,
    END_DATE,
    ERIS_SOFR_SWAP_FUTURES,
    IBKR_BAR_SIZE,
    IBKR_CLIENT_ID,
    IBKR_DURATION,
    IBKR_EXCHANGES_TO_TRY,
    IBKR_HOST,
    IBKR_PORT,
    IBKR_USE_RTH,
    IBKR_WHAT_TO_SHOW,
    MATURITIES,
    NYFED_BASE_URL,
    NYFED_RATE_CONFIG,
    NYFED_RATES,
    RATES_FILE,
    RAW_PRICE_DATA_FILE,
    RETRIES,
    START_DATE,
    SWAP_COLUMNS,
    SWAP_RATES_FILE,
    SWAP_RETURN_COLUMNS,
    TIMEOUT,
    TREASURY_COLUMN_MAP,
    TREASURY_PULL_SLEEP_SECONDS,
    TREASURY_XML_BASE_URL,
    TREASURY_XML_DATASET,
    USER_AGENT,
)


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

    numeric_cols = output.columns.difference(["date"])
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


def local_name(tag: str) -> str:
    return tag.split("}", 1)[-1] if "}" in tag else tag


def parse_treasury_xml(xml_text: str) -> pd.DataFrame:
    root = ET.fromstring(xml_text)
    rows = []

    for entry in root.iter():
        if local_name(entry.tag) != "entry":
            continue

        properties = next((child for child in entry.iter() if local_name(child.tag) == "properties"), None)

        if properties is None:
            continue

        rows.append({local_name(item.tag): item.text for item in properties})

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    keep_cols = [col for col in TREASURY_COLUMN_MAP if col in df.columns]
    df = df[keep_cols].rename(columns=TREASURY_COLUMN_MAP)

    return clean_price_frame(df)


def treasury_year_url(year: int) -> str:
    params = {"data": TREASURY_XML_DATASET, "field_tdr_date_value": year}
    return f"{TREASURY_XML_BASE_URL}?{urlencode(params)}"


def get_treasury_year(year: int) -> pd.DataFrame:
    print(f"[PULL] Treasury.gov yield curve for {year}...")

    xml_text = fetch_text(
        url=treasury_year_url(year),
        cache_file=CACHE_DIR / f"treasury_yield_curve_{year}.xml",
        accept="application/xml,*/*",
    )
    df = parse_treasury_xml(xml_text)

    if df.empty:
        print(f"[WARN] No Treasury rows found for {year}.")

    return df


def get_treasury_data(start_date: str, end_date: str | None = None) -> pd.DataFrame:
    start_ts = pd.to_datetime(start_date)
    end_ts = pd.to_datetime(end_date) if end_date else pd.Timestamp.today().normalize()
    frames = []

    for year in range(start_ts.year, end_ts.year + 1):
        try:
            df_year = get_treasury_year(year)

            if not df_year.empty:
                frames.append(df_year)

        except Exception as error:
            print(f"[ERROR] Treasury pull failed for {year}: {error}")

        time.sleep(TREASURY_PULL_SLEEP_SECONDS)

    if not frames:
        raise RuntimeError("No Treasury data loaded.")

    df = pd.concat(frames, ignore_index=True)
    df = clean_price_frame(df)
    return df[(df["date"] >= start_ts) & (df["date"] <= end_ts)].reset_index(drop=True)


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

    preferred_order = ["date", "dgs1", "dgs2", "dgs5", "dgs10", "dgs30", "sofr", "effr"]
    output = clean_price_frame(merged)
    return output[[col for col in preferred_order if col in output.columns]]


def ibkr_tools():
    try:
        from ib_insync import IB, Future, util
    except ImportError as exc:
        raise ImportError("Missing dependency: ib_insync. Install it with: pip install ib_insync") from exc

    return IB, Future, util


def connect_ibkr():
    IB, _, _ = ibkr_tools()
    ib = IB()

    print(f"[CONNECT] IBKR {IBKR_HOST}:{IBKR_PORT}, clientId={IBKR_CLIENT_ID}")
    ib.connect(host=IBKR_HOST, port=IBKR_PORT, clientId=IBKR_CLIENT_ID, timeout=20)

    if not ib.isConnected():
        raise RuntimeError("IBKR connection failed.")

    print("[OK] Connected to IBKR")
    return ib


def parse_contract_month(value: str) -> pd.Timestamp | None:
    if not value:
        return None

    for fmt in ["%Y%m%d", "%Y%m"]:
        try:
            return pd.to_datetime(datetime.strptime(str(value).strip(), fmt))
        except ValueError:
            pass

    return None


def pick_front_contract(details) -> object | None:
    today = pd.Timestamp.today().normalize()
    candidates = []

    for item in details:
        expiry = parse_contract_month(getattr(item.contract, "lastTradeDateOrContractMonth", ""))

        if expiry is not None and expiry >= today:
            candidates.append((expiry, item.contract))

    return None if not candidates else sorted(candidates, key=lambda x: x[0])[0][1]


def resolve_front_future(ib, symbol: str, exchanges_to_try: list[str]) -> object | None:
    _, Future, _ = ibkr_tools()

    for exchange in exchanges_to_try:
        print(f"[RESOLVE] Trying {symbol} on {exchange}")

        try:
            details = ib.reqContractDetails(Future(symbol=symbol, exchange=exchange, currency="USD"))

            if not details:
                print(f"[WARN] No contract details for {symbol} on {exchange}")
                continue

            front = pick_front_contract(details)

            if front is None:
                print(f"[WARN] Found details for {symbol} on {exchange}, but no live contract.")
                continue

            qualified = ib.qualifyContracts(front)

            if qualified:
                selected = qualified[0]
                print(
                    "[OK] Resolved "
                    f"{symbol}: localSymbol={selected.localSymbol}, "
                    f"expiry={selected.lastTradeDateOrContractMonth}, "
                    f"exchange={selected.exchange}"
                )
                return selected

        except Exception as error:
            print(f"[WARN] Resolve failed for {symbol} on {exchange}: {error}")

        time.sleep(0.25)

    return None


def request_daily_bars(ib, contract, symbol: str) -> pd.DataFrame:
    _, _, util = ibkr_tools()
    print(f"[PULL] Historical daily bars for {symbol} / {contract.localSymbol}")

    bars = ib.reqHistoricalData(
        contract,
        endDateTime="",
        durationStr=IBKR_DURATION,
        barSizeSetting=IBKR_BAR_SIZE,
        whatToShow=IBKR_WHAT_TO_SHOW,
        useRTH=IBKR_USE_RTH,
        formatDate=1,
        keepUpToDate=False,
    )

    if not bars:
        raise RuntimeError(f"No historical bars returned for {symbol} / {contract.localSymbol}")

    df = util.df(bars)

    if df.empty:
        raise RuntimeError(f"IBKR returned empty dataframe for {symbol} / {contract.localSymbol}")

    df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.tz_localize(None)
    return clean_price_frame(df[["date", "close"]])


def get_one_swap_future_series(ib, maturity: str) -> pd.DataFrame:
    symbol = ERIS_SOFR_SWAP_FUTURES[maturity]
    output_col = SWAP_COLUMNS[maturity]
    return_col = SWAP_RETURN_COLUMNS[maturity]
    contract = resolve_front_future(ib=ib, symbol=symbol, exchanges_to_try=IBKR_EXCHANGES_TO_TRY)

    if contract is None:
        raise RuntimeError(f"Could not resolve IBKR contract for {maturity} / {symbol}")

    df = request_daily_bars(ib=ib, contract=contract, symbol=symbol).rename(columns={"close": output_col})
    df[return_col] = df[output_col].pct_change()
    print(f"[OK] {maturity} {symbol}: {len(df):,} rows from {df['date'].min().date()} to {df['date'].max().date()}")
    return df


def get_swap_futures_data(ib) -> pd.DataFrame:
    frames = []
    failures = {}

    for maturity in MATURITIES:
        if maturity not in ERIS_SOFR_SWAP_FUTURES:
            print(f"[SKIP] No IBKR Eris swap future configured for {maturity}")
            continue

        try:
            frames.append(get_one_swap_future_series(ib=ib, maturity=maturity))
        except Exception as error:
            failures[maturity] = str(error)
            print(f"[ERROR] Could not load {maturity} swap future: {error}")

        time.sleep(1.0)

    if not frames:
        for maturity, error in failures.items():
            print(f"{maturity}: {error}")

        raise RuntimeError("No Eris SOFR swap futures data loaded.")

    merged = frames[0]

    for frame in frames[1:]:
        merged = pd.merge(merged, frame, on="date", how="outer")

    preferred_order = ["date"]

    for maturity in MATURITIES:
        preferred_order.extend(
            col for col in [SWAP_COLUMNS.get(maturity), SWAP_RETURN_COLUMNS.get(maturity)] if col in merged.columns
        )

    output = clean_price_frame(merged)
    return output[preferred_order]


def merge_price_data(rates: pd.DataFrame, swaps: pd.DataFrame | None) -> pd.DataFrame:
    if swaps is None or swaps.empty:
        return clean_price_frame(rates)

    return clean_price_frame(pd.merge(rates, swaps, on="date", how="outer"))


def build_raw_price_data(
    refresh_treasury: bool = False,
    refresh_swaps: bool = False,
    start_date: str = START_DATE,
    end_date: str | None = END_DATE,
    save: bool = True,
) -> pd.DataFrame:
    ensure_directories()

    if refresh_treasury or not RATES_FILE.exists():
        rates = build_rates_dataset(start_date=start_date, end_date=end_date)
        rates.to_csv(RATES_FILE, index=False)
        print(f"[SAVED] {RATES_FILE}")
    else:
        rates = load_csv(RATES_FILE)

    swaps = None

    if refresh_swaps:
        ib = connect_ibkr()

        try:
            swaps = get_swap_futures_data(ib)
        finally:
            ib.disconnect()
            print("[DISCONNECT] IBKR")

        swaps.to_csv(SWAP_RATES_FILE, index=False)
        print(f"[SAVED] {SWAP_RATES_FILE}")

    elif SWAP_RATES_FILE.exists():
        swaps = load_csv(SWAP_RATES_FILE)

    raw = merge_price_data(rates, swaps)

    if save:
        raw.to_csv(RAW_PRICE_DATA_FILE, index=False)
        print(f"[SAVED] {RAW_PRICE_DATA_FILE}")

    print(f"[RAW PRICE DATA] rows={len(raw):,} range={raw['date'].min().date()} to {raw['date'].max().date()}")
    return raw


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build consolidated raw price data.")
    parser.add_argument("--pull-treasury", action="store_true", help="Refresh Treasury and NY Fed data.")
    parser.add_argument("--pull-swaps", action="store_true", help="Refresh Eris swap futures from IBKR.")
    parser.add_argument("--start", default=START_DATE)
    parser.add_argument("--end", default=END_DATE)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    build_raw_price_data(
        refresh_treasury=args.pull_treasury,
        refresh_swaps=args.pull_swaps,
        start_date=args.start,
        end_date=args.end,
    )


if __name__ == "__main__":
    main()
