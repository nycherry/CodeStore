from io import StringIO

import pandas as pd
import requests

SOURCES = {
    "sp500": ("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies", "Symbol"),
    "sp400": ("https://en.wikipedia.org/wiki/List_of_S%26P_400_companies", "Symbol"),
    "nasdaq100": ("https://en.wikipedia.org/wiki/List_of_NASDAQ-100_companies", "Ticker"),
}

FALLBACK = [
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "AVGO", "TSLA", "NFLX", "AMD",
    "CRM", "ADBE", "NOW", "INTU", "PANW", "CRWD", "ANET", "UBER", "SHOP", "PLTR",
    "LLY", "ISRG", "VRTX", "COST", "MELI", "TTD", "DDOG", "ZS", "SNOW", "AXON",
]

_HEADERS = {"User-Agent": "Mozilla/5.0 (growth-screener)"}


def _fetch_index(name: str) -> list[str]:
    url, col = SOURCES[name]
    html = requests.get(url, headers=_HEADERS, timeout=30).text
    for table in pd.read_html(StringIO(html)):
        if col in table.columns:
            return table[col].astype(str).str.strip().tolist()
    raise ValueError(f"no '{col}' column found for {name}")


def get_universe(indexes: list[str], extra: list[str] | None = None) -> list[str]:
    tickers: set[str] = set()
    for name in indexes:
        try:
            tickers.update(_fetch_index(name))
        except Exception as e:  # network / layout changes
            print(f"  ! could not load {name}: {e}")
    if not tickers:
        print("  ! using built-in fallback ticker list")
        tickers.update(FALLBACK)
    tickers.update(extra or [])
    # Yahoo uses '-' for share classes (BRK.B -> BRK-B)
    return sorted({t.replace(".", "-").upper() for t in tickers if t and t != "nan"})
