"""Download prices + fundamentals from Yahoo Finance into a local DuckDB file."""
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

import duckdb
import pandas as pd
import yfinance as yf
from yfinance.exceptions import YFRateLimitError

from .config import DATA_DIR, DB_PATH

logging.getLogger("yfinance").setLevel(logging.CRITICAL)

INFO_FIELDS = {
    "shortName": "name",
    "sector": "sector",
    "industry": "industry",
    "marketCap": "market_cap",
    "currentPrice": "price",
    "totalRevenue": "revenue_ttm",
    "revenueGrowth": "revenue_growth",
    "earningsGrowth": "earnings_growth",
    "grossMargins": "gross_margin",
    "operatingMargins": "operating_margin",
    "returnOnEquity": "roe",
    "freeCashflow": "free_cash_flow",
    "trailingPE": "trailing_pe",
    "forwardPE": "forward_pe",
    "trailingPegRatio": "peg",
    "priceToSalesTrailing12Months": "price_to_sales",
    "trailingEps": "trailing_eps",
    "forwardEps": "forward_eps",
    "targetMeanPrice": "target_price",
    "recommendationMean": "analyst_rating",
}


def connect() -> duckdb.DuckDBPyConnection:
    DATA_DIR.mkdir(exist_ok=True)
    con = duckdb.connect(str(DB_PATH))
    con.execute(
        "CREATE TABLE IF NOT EXISTS prices (ticker VARCHAR, date DATE, close DOUBLE, volume DOUBLE)"
    )
    cols = ", ".join(f"{c} {'VARCHAR' if c in ('name', 'sector', 'industry') else 'DOUBLE'}"
                     for c in INFO_FIELDS.values())
    con.execute(
        f"CREATE TABLE IF NOT EXISTS fundamentals (ticker VARCHAR PRIMARY KEY, fetched_at TIMESTAMP, {cols})"
    )
    return con


def update_prices(con, tickers: list[str], period: str = "2y", chunk: int = 100) -> None:
    frames = []
    for i in range(0, len(tickers), chunk):
        batch = tickers[i:i + chunk]
        print(f"  prices {i + 1}-{i + len(batch)} of {len(tickers)}")
        raw = yf.download(batch, period=period, auto_adjust=True, group_by="ticker",
                          threads=True, progress=False)
        for t in batch:
            if t not in raw.columns.get_level_values(0):
                continue
            df = raw[t][["Close", "Volume"]].dropna(subset=["Close"])
            if df.empty:
                continue
            df = df.reset_index().rename(columns={"Date": "date", "Close": "close", "Volume": "volume"})
            df["ticker"] = t
            frames.append(df[["ticker", "date", "close", "volume"]])
    if not frames:
        print("  ! no price data downloaded")
        return
    prices = pd.concat(frames, ignore_index=True)
    prices["date"] = pd.to_datetime(prices["date"]).dt.date
    con.register("new_prices", prices)
    con.execute("DELETE FROM prices WHERE ticker IN (SELECT DISTINCT ticker FROM new_prices)")
    con.execute("INSERT INTO prices SELECT ticker, date, close, volume FROM new_prices")
    con.unregister("new_prices")


class RateLimited(Exception):
    pass


def _fetch_info(ticker: str) -> dict | None:
    for attempt in range(3):
        try:
            info = yf.Ticker(ticker).info
            if not info or info.get("quoteType") not in (None, "EQUITY"):
                return None
            row = {"ticker": ticker, "fetched_at": datetime.now()}
            row.update({dst: info.get(src) for src, dst in INFO_FIELDS.items()})
            if row["market_cap"] is None:  # partial/blocked response; don't cache it
                return None
            return row
        except YFRateLimitError:
            raise RateLimited()
        except Exception:
            time.sleep(2 * (attempt + 1))
    return None


def _save_fundamentals(con, rows: list[dict]) -> None:
    if not rows:
        return
    df = pd.DataFrame(rows)
    df = df[["ticker", "fetched_at", *INFO_FIELDS.values()]]
    for col in INFO_FIELDS.values():
        if col not in ("name", "sector", "industry"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
    con.register("new_f", df)
    con.execute("DELETE FROM fundamentals WHERE ticker IN (SELECT ticker FROM new_f)")
    con.execute("INSERT INTO fundamentals SELECT * FROM new_f")
    con.unregister("new_f")


def update_fundamentals(con, tickers: list[str], max_age_hours: float = 24, workers: int = 3,
                        batch: int = 25, max_backoffs: int = 6) -> None:
    """Fetch fundamentals in small batches, saving as it goes so an interrupted or
    rate-limited run resumes where it left off. Backs off when Yahoo returns HTTP 429."""
    cutoff = datetime.now() - timedelta(hours=max_age_hours)
    fresh = {r[0] for r in con.execute(
        "SELECT ticker FROM fundamentals WHERE fetched_at >= ? AND market_cap IS NOT NULL", [cutoff]).fetchall()}
    todo = [t for t in tickers if t not in fresh]
    print(f"  fundamentals: {len(fresh & set(tickers))} cached, {len(todo)} to fetch")
    done, backoffs, i = 0, 0, 0
    while i < len(todo):
        chunk = todo[i:i + batch]
        rows, limited = [], False
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(_fetch_info, t) for t in chunk]
            for fut in as_completed(futures):
                try:
                    if (row := fut.result()) is not None:
                        rows.append(row)
                except RateLimited:
                    limited = True
        _save_fundamentals(con, rows)
        if limited:
            backoffs += 1
            if backoffs > max_backoffs:
                print("  ! Yahoo is still rate-limiting; stopping. Re-run later to resume.")
                return
            wait = 30 * backoffs
            print(f"  ! rate-limited by Yahoo, saved progress, waiting {wait}s...")
            time.sleep(wait)
            got = {r["ticker"] for r in rows}
            todo = todo[:i] + [t for t in chunk if t not in got] + todo[i + batch:]
            i += len(got)
            continue
        done += len(chunk)
        i += batch
        print(f"    {done}/{len(todo)}")
        time.sleep(1)
