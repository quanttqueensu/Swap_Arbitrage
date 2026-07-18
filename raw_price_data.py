from __future__ import annotations

import argparse
import calendar
import json
import random
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from io import StringIO
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
    ERIS_PUBLIC_BASE_URL,
    ERIS_PUBLIC_START_DATE,
    ERIS_SOFR_SWAP_FUTURES,
    IBKR_BAR_SIZE,
    IBKR_CLIENT_ID,
    IBKR_DURATION,
    IBKR_EXCHANGES_TO_TRY,
    IBKR_HOST,
    IBKR_MARKET_DATA_FILE,
    IBKR_PORT,
    IBKR_SWAP_COLUMNS,
    IBKR_SWAP_RETURN_COLUMNS,
    IBKR_TREASURY_COLUMNS,
    IBKR_TREASURY_RETURN_COLUMNS,
    IBKR_USE_RTH,
    IBKR_WHAT_TO_SHOW,
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
    SWAP_DV01_COLUMNS,
    SWAP_DV01_YEARS,
    SWAP_RATES_FILE,
    SWAP_RETURN_COLUMNS,
    TIMEOUT,
    TREASURY_FUTURES,
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

    frame["LastTradeDate"] = pd.to_datetime(frame["LastTradeDate"], errors="coerce").dt.normalize()
    row = {"date": row_date}

    for maturity, symbol in ERIS_SOFR_SWAP_FUTURES.items():
        price_col = SWAP_COLUMNS[maturity]
        dv01_col = SWAP_DV01_COLUMNS.get(maturity)
        candidates = frame[frame["ExchangeSymbol (EX005)"].eq(symbol)].copy()
        candidates = candidates[candidates["LastTradeDate"] >= row_date]

        if candidates.empty:
            continue

        sort_cols = ["LastTradeDate"]
        target_dv01 = None

        if "DV01" in candidates.columns and maturity in SWAP_DV01_YEARS:
            candidates["DV01"] = pd.to_numeric(candidates["DV01"], errors="coerce")
            target_dv01 = SWAP_DV01_YEARS[maturity] * 10.0
            lower_dv01 = target_dv01 * 0.9
            upper_dv01 = target_dv01 * 1.1
            candidates["_dv01_gap"] = (candidates["DV01"] - target_dv01).abs()
            near_tenor = candidates[(candidates["DV01"] >= lower_dv01) & (candidates["DV01"] <= upper_dv01)]

            # ponytail: use DV01 to avoid owning a full Eris roll calendar.
            if not near_tenor.empty:
                candidates = near_tenor

            sort_cols = ["_dv01_gap", "LastTradeDate"]

        selected = None

        if active_contracts is not None and "Symbol" in candidates.columns:
            active_symbol = active_contracts.get(maturity)

            if active_symbol:
                active = candidates[candidates["Symbol"].eq(active_symbol)]

                if not active.empty:
                    active_row = active.sort_values("LastTradeDate").iloc[0]
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

        price = pd.to_numeric(selected["FinalSettlementPrice"], errors="coerce")

        if pd.isna(price):
            continue

        if active_contracts is not None and "Symbol" in selected.index and pd.notna(selected["Symbol"]):
            active_contracts[maturity] = selected["Symbol"]

        row[price_col] = price

        if dv01_col and "DV01" in selected.index:
            dv01 = pd.to_numeric(selected["DV01"], errors="coerce")

            if not pd.isna(dv01):
                row[dv01_col] = dv01

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
                SWAP_COLUMNS.get(maturity),
                SWAP_RETURN_COLUMNS.get(maturity),
                SWAP_DV01_COLUMNS.get(maturity),
            ]
            if col in output.columns
        )

    print(
        "[OK] Eris public SOFR swaps: "
        f"{len(output):,} rows from {output['date'].min().date()} to {output['date'].max().date()} "
        f"({misses:,} dates skipped)"
    )
    return output[preferred_order]


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


def get_one_ibkr_future_series(
    ib,
    symbol: str,
    output_col: str,
    return_col: str,
    label: str,
) -> pd.DataFrame:
    contract = resolve_front_future(ib=ib, symbol=symbol, exchanges_to_try=IBKR_EXCHANGES_TO_TRY)

    if contract is None:
        raise RuntimeError(f"Could not resolve IBKR contract for {label} / {symbol}")

    df = request_daily_bars(ib=ib, contract=contract, symbol=symbol).rename(columns={"close": output_col})
    df[return_col] = df[output_col].pct_change()
    print(f"[OK] {label} {symbol}: {len(df):,} rows from {df['date'].min().date()} to {df['date'].max().date()}")
    return df


def get_ibkr_traded_futures_data(ib) -> pd.DataFrame:
    frames = []
    failures = {}

    for maturity in MATURITIES:
        if maturity in ERIS_SOFR_SWAP_FUTURES and maturity in IBKR_SWAP_COLUMNS:
            symbol = ERIS_SOFR_SWAP_FUTURES[maturity]

            try:
                frames.append(
                    get_one_ibkr_future_series(
                        ib=ib,
                        symbol=symbol,
                        output_col=IBKR_SWAP_COLUMNS[maturity],
                        return_col=IBKR_SWAP_RETURN_COLUMNS[maturity],
                        label=f"IBKR Eris {maturity}",
                    )
                )
            except Exception as error:
                failures[f"swap_{maturity}"] = str(error)
                print(f"[ERROR] Could not load IBKR {maturity} swap future: {error}")

            time.sleep(1.0)

        if maturity in TREASURY_FUTURES and maturity in IBKR_TREASURY_COLUMNS:
            symbol = TREASURY_FUTURES[maturity]

            try:
                frames.append(
                    get_one_ibkr_future_series(
                        ib=ib,
                        symbol=symbol,
                        output_col=IBKR_TREASURY_COLUMNS[maturity],
                        return_col=IBKR_TREASURY_RETURN_COLUMNS[maturity],
                        label=f"IBKR Treasury {maturity}",
                    )
                )
            except Exception as error:
                failures[f"treasury_{maturity}"] = str(error)
                print(f"[ERROR] Could not load IBKR {maturity} Treasury future: {error}")

            time.sleep(1.0)

    if not frames:
        for instrument, error in failures.items():
            print(f"{instrument}: {error}")

        raise RuntimeError("No IBKR traded futures data loaded.")

    merged = frames[0]

    for frame in frames[1:]:
        merged = pd.merge(merged, frame, on="date", how="outer")

    preferred_order = ["date"]

    for maturity in MATURITIES:
        preferred_order.extend(
            col
            for col in [
                IBKR_SWAP_COLUMNS.get(maturity),
                IBKR_SWAP_RETURN_COLUMNS.get(maturity),
                IBKR_TREASURY_COLUMNS.get(maturity),
                IBKR_TREASURY_RETURN_COLUMNS.get(maturity),
            ]
            if col in merged.columns
        )

    output = clean_price_frame(merged)
    return output[preferred_order]


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
    refresh_treasury: bool = False,
    refresh_eris: bool = False,
    refresh_swaps: bool = False,
    refresh_ibkr: bool = False,
    start_date: str = START_DATE,
    end_date: str | None = END_DATE,
    save: bool = True,
) -> pd.DataFrame:
    ensure_directories()
    refresh_interest_rates = refresh_interest_rates or refresh_treasury
    refresh_eris = refresh_eris or refresh_swaps

    if refresh_interest_rates or not RATES_FILE.exists():
        rates = build_rates_dataset(start_date=start_date, end_date=end_date)
        rates.to_csv(RATES_FILE, index=False)
        print(f"[SAVED] {RATES_FILE}")
    else:
        rates = load_csv(RATES_FILE)

    eris = None

    if refresh_eris:
        eris = get_eris_public_swap_data(start_date=start_date, end_date=end_date)
        eris.to_csv(SWAP_RATES_FILE, index=False)
        print(f"[SAVED] {SWAP_RATES_FILE}")

    elif SWAP_RATES_FILE.exists():
        eris = load_csv(SWAP_RATES_FILE)

    ibkr = None

    if refresh_ibkr:
        live = connect_ibkr()

        try:
            ibkr = get_ibkr_traded_futures_data(live)
        finally:
            live.disconnect()
            print("[DISCONNECT] IBKR")

        ibkr.to_csv(IBKR_MARKET_DATA_FILE, index=False)
        print(f"[SAVED] {IBKR_MARKET_DATA_FILE}")

    elif IBKR_MARKET_DATA_FILE.exists():
        ibkr = load_csv(IBKR_MARKET_DATA_FILE)

    raw = merge_price_data(rates, eris, ibkr)

    if save:
        raw.to_csv(RAW_PRICE_DATA_FILE, index=False)
        print(f"[SAVED] {RAW_PRICE_DATA_FILE}")

    print(f"[RAW PRICE DATA] rows={len(raw):,} range={raw['date'].min().date()} to {raw['date'].max().date()}")
    return raw


def self_check() -> None:
    rates = pd.DataFrame({"date": ["2024-01-02"], "dgs2": [4.1], "sofr": [5.3]})
    eris = pd.DataFrame({"date": ["2024-01-02"], "eris_swap_2y_price": [100.0], "eris_swap_2y_dv01": [19.0]})
    ibkr = pd.DataFrame({"date": ["2024-01-02"], "ibkr_treasury_2y_price": [102.0]})
    merged = merge_price_data(rates, eris, ibkr)

    assert merged.loc[0, "dgs2"] == 4.1
    assert merged.loc[0, "eris_swap_2y_dv01"] == 19.0
    assert merged.loc[0, "ibkr_treasury_2y_price"] == 102.0
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
    parser.add_argument("--ibkr", action="store_true", help="Refresh IBKR bars for the traded futures universe.")
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
        refresh_ibkr=args.ibkr,
        start_date=args.start,
        end_date=args.end,
    )


if __name__ == "__main__":
    main()
