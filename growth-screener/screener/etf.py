"""ETF analyzer: returns, risk, cost, income and overlap for a list of ETFs vs. alternatives."""
import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import duckdb
import numpy as np
import pandas as pd
import yfinance as yf

from .config import DATA_DIR, ROOT
from .data import update_prices

ETF_DB = DATA_DIR / "etf.duckdb"
TD = 252  # trading days per year

FACTORS = {
    # metric -> True if higher is better
    "returns": {"ret_1y": True, "cagr_3y": True, "cagr_5y": True},
    "risk_adjusted": {"sharpe": True, "sortino": True},
    "risk": {"max_drawdown": True, "volatility": False},  # drawdown is negative, so higher = better
    "cost": {"expense_ratio": False, "aum": True},
    "income": {"yield": True},
}


def load_etf_config(path=ROOT / "etf_config.yaml") -> dict:
    import yaml
    with open(path) as f:
        return yaml.safe_load(f)


def connect():
    DATA_DIR.mkdir(exist_ok=True)
    con = duckdb.connect(str(ETF_DB))
    con.execute("CREATE TABLE IF NOT EXISTS prices (ticker VARCHAR, date DATE, close DOUBLE, volume DOUBLE)")
    con.execute("""CREATE TABLE IF NOT EXISTS etf_info (ticker VARCHAR PRIMARY KEY, fetched_at TIMESTAMP,
        name VARCHAR, quote_type VARCHAR, category VARCHAR, family VARCHAR, aum DOUBLE,
        expense_ratio DOUBLE, yield DOUBLE, beta_3y DOUBLE, inception DATE, pe DOUBLE,
        top_holdings VARCHAR, sectors VARCHAR)""")
    return con


def _fetch_info(ticker: str) -> dict | None:
    for attempt in range(3):
        try:
            tk = yf.Ticker(ticker)
            i = tk.info or {}
            qt = i.get("quoteType")
            if not qt:
                return None
            holdings, sectors = {}, {}
            if qt == "ETF":
                try:
                    fd = tk.funds_data
                    holdings = fd.top_holdings["Holding Percent"].round(4).to_dict()
                    sectors = {k: round(v, 4) for k, v in fd.sector_weightings.items() if v}
                except Exception:
                    pass
            er = i.get("netExpenseRatio")
            price = i.get("regularMarketPrice") or i.get("previousClose") or i.get("navPrice")
            yld = None
            try:  # trailing-12-month distributions / price (Yahoo's "yield" field is unreliable)
                div = tk.dividends
                if price and not div.empty:
                    yld = float(div[div.index >= pd.Timestamp.now(tz=div.index.tz) - pd.Timedelta(days=365)].sum()) / price
                    if div.index[-1] < pd.Timestamp.now(tz=div.index.tz) - pd.Timedelta(days=400):
                        yld = 0.0
                elif price:
                    yld = 0.0
            except Exception:
                yld = i.get("yield")
            inc = i.get("fundInceptionDate")
            return {
                "ticker": ticker, "fetched_at": datetime.now(),
                "name": i.get("longName") or i.get("shortName"), "quote_type": qt,
                "category": i.get("category"), "family": i.get("fundFamily"),
                "aum": i.get("totalAssets") or i.get("marketCap"),
                "expense_ratio": er / 100 if er is not None else None,  # Yahoo reports percent
                "yield": yld, "beta_3y": i.get("beta3Year"),
                "inception": datetime.fromtimestamp(inc).date() if inc else None,
                "pe": i.get("trailingPE"),
                "top_holdings": json.dumps(holdings), "sectors": json.dumps(sectors),
            }
        except Exception:
            time.sleep(3 * (attempt + 1))
    return None


def fetch(cfg: dict, con) -> None:
    tickers = list(dict.fromkeys(cfg["portfolio"] + cfg["candidates"] + [cfg.get("benchmark", "VOO")]))
    print(f"ETF universe: {len(tickers)} tickers")
    update_prices(con, tickers, period=cfg.get("history", "10y"))
    print("  fund info...")
    with ThreadPoolExecutor(max_workers=3) as pool:
        rows = [r for r in pool.map(_fetch_info, tickers) if r]
    missing = set(tickers) - {r["ticker"] for r in rows}
    if missing:
        print(f"  ! no info for: {', '.join(sorted(missing))} (rate limit? re-run later)")
    if rows:
        df = pd.DataFrame(rows)
        con.register("new_i", df)
        con.execute("DELETE FROM etf_info WHERE ticker IN (SELECT ticker FROM new_i)")
        con.execute("INSERT INTO etf_info BY NAME SELECT * FROM new_i")
        con.unregister("new_i")


def _risk(s: pd.Series, bench: pd.Series, years: int, rf: float) -> dict:
    keys = ("volatility", "sharpe", "sortino", "max_drawdown")
    if len(s) <= years * TD:
        return {f"{k}_{years}y": np.nan for k in keys}
    w = s.iloc[-years * TD - 1:]
    r = w.pct_change().dropna()
    vol = r.std() * np.sqrt(TD)
    ann = (w.iloc[-1] / w.iloc[0]) ** (1 / years) - 1
    down = r[r < 0].std() * np.sqrt(TD)
    out = {f"volatility_{years}y": vol,
           f"sharpe_{years}y": (ann - rf) / vol if vol else np.nan,
           f"sortino_{years}y": (ann - rf) / down if down else np.nan,
           f"max_drawdown_{years}y": (w / w.cummax() - 1).min()}
    if years == 3:
        b = bench.pct_change().reindex(r.index).dropna()
        rr = r.reindex(b.index)
        out["beta"] = rr.cov(b) / b.var() if len(b) > TD else np.nan
    return out


def _price_stats(s: pd.Series, bench: pd.Series, rf: float) -> dict:
    s = s.dropna()
    out = {"last_close": s.iloc[-1], "last_date": s.index[-1], "years_history": len(s) / TD}

    def cagr(years):
        n = int(years * TD)
        return (s.iloc[-1] / s.iloc[-n - 1]) ** (1 / years) - 1 if len(s) > n else np.nan

    out["ret_1y"], out["cagr_3y"], out["cagr_5y"], out["cagr_10y"] = (cagr(1), cagr(3), cagr(5), cagr(10))
    out.update(_risk(s, bench, 3, rf))
    out.update(_risk(s, bench, 5, rf))
    yr = s[s.index >= s.index[-1] - pd.Timedelta(days=365)]
    out["pct_below_high"] = 1 - s.iloc[-1] / yr.max()
    out["above_200dma"] = bool(s.iloc[-1] > s.iloc[-200:].mean()) if len(s) >= 200 else None
    return out


def _closes(con) -> pd.DataFrame:
    p = con.execute("SELECT ticker, date, close FROM prices").df()
    p["date"] = pd.to_datetime(p["date"])
    return p.pivot(index="date", columns="ticker", values="close").sort_index()


def analyze(cfg: dict, con) -> dict:
    closes = _closes(con)
    info = con.execute("SELECT * FROM etf_info").df().set_index("ticker")
    group_of = {t: g for g, ts in cfg["groups"].items() for t in ts}
    owned = set(cfg["portfolio"])
    bench_t = cfg.get("benchmark", "VOO")
    bench = closes[bench_t].dropna() if bench_t in closes else pd.Series(dtype=float)
    if bench.empty:
        print(f"  ! no prices for benchmark {bench_t}; beta not computed")
    rows = []
    for t in dict.fromkeys(cfg["portfolio"] + cfg["candidates"]):
        if t not in closes or closes[t].dropna().empty:
            print(f"  ! no prices for {t}")
            continue
        row = {"ticker": t, "group": group_of.get(t, "other"), "owned": t in owned}
        row.update(_price_stats(closes[t], bench, cfg.get("risk_free_rate", 0.04)))
        if t in info.index:
            row.update(info.loc[t].drop(["fetched_at"]).to_dict())
        rows.append(row)
    df = pd.DataFrame(rows)
    for col in INFO_COLS:  # fund info may be missing entirely (e.g. Yahoo rate limit)
        if col not in df:
            df[col] = np.nan
    df = _score(df, cfg)
    df["warnings"] = df.apply(_warnings, axis=1)
    corr = _correlations(closes, [t for t in cfg["portfolio"] if t in closes])
    overlap = _overlap(df[df.owned])
    return {"funds": df, "corr": corr, "redundant": _redundant(df, corr, overlap, cfg),
            "swaps": _swaps(df, cfg, closes)}


INFO_COLS = ("name", "quote_type", "category", "family", "aum", "expense_ratio", "yield", "beta_3y",
             "inception", "pe", "top_holdings", "sectors")
RISK_KEYS = ("volatility", "sharpe", "sortino", "max_drawdown")


def _score(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    df = df.copy()
    for k in RISK_KEYS:  # unscored groups show 5y (or 3y) stats
        df[k] = df[f"{k}_5y"].fillna(df[f"{k}_3y"])
    df["risk_window_y"] = np.where(df["sharpe_5y"].notna(), 5, np.where(df["sharpe_3y"].notna(), 3, np.nan))
    w = cfg["weights"]
    df["score"] = np.nan
    for g, sub in df.groupby("group"):
        if g in cfg.get("unscored", []) or len(sub) < 2:
            continue
        # Compare risk over the same window: 5y if every fund with 3y+ history has 5y, else 3y.
        sub = sub.copy()
        eligible = sub[sub.years_history > 3]
        win = 5 if eligible["sharpe_5y"].notna().all() else 3
        for k in RISK_KEYS:
            sub[k] = sub[f"{k}_{win}y"]
            df.loc[sub.index, k] = sub[k]
        df.loc[sub.index, "risk_window_y"] = win
        factors = [f for f in FACTORS if f != "income" or g in cfg.get("income_groups", [])]
        total = sum(w[f] for f in factors)
        score = pd.Series(0.0, index=sub.index)
        for f in factors:
            ranks = [sub[m].rank(pct=True, ascending=hi) for m, hi in FACTORS[f].items()]
            fs = (pd.concat(ranks, axis=1).mean(axis=1) * 100).fillna(50)
            df.loc[sub.index, f"{f}_score"] = fs
            score += fs * w[f] / total
        df.loc[sub.index, "score"] = score
        df.loc[sub.index, "group_rank"] = score.rank(ascending=False, method="min")
    return df


def _warnings(r) -> str:
    w = []
    name = str(r.get("name") or "")
    if r.get("quote_type") == "INDEX":
        w.append("Index, not a fund: you can't buy it. VIX funds (VIXY/UVXY) track futures and lose value over time.")
    elif r.get("quote_type") == "EQUITY":
        w.append("Single stock (BDC), not an ETF.")
    if r["group"] == "leveraged_inverse" or "Ultra" in name:
        w.append("Leveraged/inverse with daily reset: returns decay in choppy markets; meant for short holding periods.")
    if r["group"] == "volatility" and r.get("quote_type") == "ETF":
        w.append("VIX futures fund: long-term value decay from futures roll; a short-term hedge only.")
    er = r.get("expense_ratio")
    if er is not None and not pd.isna(er) and er >= 0.0075:
        extra = (" (mostly the underlying BDCs' own fees, which you also pay indirectly holding BDC stocks)"
                 if r["group"] == "bdc" else "")
        w.append(f"High expense ratio ({er:.2%}){extra}.")
    aum = r.get("aum")
    if aum is not None and not pd.isna(aum) and aum < 500e6:
        w.append(f"Small fund (${aum / 1e6:.0f}M AUM): wider spreads, closure risk.")
    if r.get("years_history", 99) < 3:
        w.append(f"Short history ({r['years_history']:.1f}y): risk stats not computed.")
    elif r.get("years_history", 99) < 5:
        w.append(f"Only {r['years_history']:.1f}y of history: hasn't been through the 2022 bear market.")
    return " ".join(w)


def _correlations(closes: pd.DataFrame, tickers: list[str]) -> pd.DataFrame:
    r = closes[tickers].iloc[-3 * TD - 1:].pct_change(fill_method=None)
    return r.corr(min_periods=TD // 2)


def _overlap(owned: pd.DataFrame) -> dict:
    """Top-10 holdings overlap (sum of min weights). Lower bound, since Yahoo only lists the top 10."""
    h = {r.ticker: json.loads(r.top_holdings) for r in owned.itertuples()
         if isinstance(r.top_holdings, str) and r.top_holdings != "{}"}
    out = {}
    for a in h:
        for b in h:
            if a < b:
                common = set(h[a]) & set(h[b])
                out[(a, b)] = sum(min(h[a][k], h[b][k]) for k in common)
    return out


def _redundant(df, corr, overlap, cfg) -> list[dict]:
    score = df.set_index("ticker")["score"]
    out = []
    cols = list(corr.columns)
    skip = {t for t in df[df.group.isin(cfg.get("unscored", []))].ticker}
    cols = [c for c in cols if c not in skip]
    for i, a in enumerate(cols):
        for b in cols[i + 1:]:
            c = corr.loc[a, b]
            if pd.notna(c) and c >= cfg.get("redundant_corr", 0.95):
                out.append({"a": a, "b": b, "corr": c,
                            "overlap": overlap.get(tuple(sorted((a, b)))),
                            "a_score": score.get(a), "b_score": score.get(b)})
    return sorted(out, key=lambda x: -x["corr"])


def _swaps(df, cfg, closes) -> list[dict]:
    out = []
    rets = closes.iloc[-3 * TD - 1:].pct_change(fill_method=None)
    gain = cfg.get("swap_min_gain", 10)
    for _, r in df[df.owned & df.score.notna()].iterrows():
        peers = df[(df.group == r.group) & (df.ticker != r.ticker) & df.score.notna()]
        if peers.empty:
            continue
        best = peers.loc[peers.score.idxmax()]
        reason = None
        if best.score - r.score >= gain:
            reason = "Scores higher in its group"
        else:  # near-identical fund that's cheaper (e.g. QQQ -> QQQM)
            for _, p in peers.iterrows():
                c = rets[r.ticker].corr(rets[p.ticker])
                if c >= 0.995 and p.expense_ratio <= r.expense_ratio - 0.0002 and not p.owned:
                    best, reason = p, f"Near-identical holdings (corr {c:.3f}), lower fee"
                    break
        if reason:
            out.append({"own": r.ticker, "alt": best.ticker, "group": r.group, "reason": reason,
                        "own_score": r.score, "alt_score": best.score,
                        "own_er": r.get("expense_ratio"), "alt_er": best.get("expense_ratio"),
                        "own_cagr": r.get("cagr_5y"), "alt_cagr": best.get("cagr_5y"),
                        "alt_owned": bool(best.owned)})
    return out
