"""Turn raw prices + fundamentals into growth metrics, filters and a composite score."""
import numpy as np
import pandas as pd

FACTORS = {
    # metric -> True if higher is better
    "growth": {"revenue_growth": True, "earnings_growth": True, "fwd_eps_growth": True},
    "quality": {"gross_margin": True, "operating_margin": True, "roe": True},
    "momentum": {"ret_6m": True, "ret_12m": True, "pct_below_high": False},
    "valuation": {"peg": False, "forward_pe": False},
}


def price_metrics(con) -> pd.DataFrame:
    return con.execute("""
        WITH p AS (
            SELECT ticker, date, close, volume,
                   ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY date DESC) AS rn
            FROM prices
        )
        SELECT ticker,
               MAX(CASE WHEN rn = 1 THEN close END)                      AS last_close,
               MAX(CASE WHEN rn = 1 THEN date END)                       AS last_date,
               MAX(CASE WHEN rn = 64 THEN close END)                     AS close_3m,
               MAX(CASE WHEN rn = 127 THEN close END)                    AS close_6m,
               MAX(CASE WHEN rn = 253 THEN close END)                    AS close_12m,
               AVG(CASE WHEN rn <= 200 THEN close END)                   AS sma_200,
               AVG(CASE WHEN rn <= 50 THEN close END)                    AS sma_50,
               MAX(CASE WHEN rn <= 252 THEN close END)                   AS high_52w,
               AVG(CASE WHEN rn <= 50 THEN close * volume END)           AS avg_dollar_volume
        FROM p GROUP BY ticker
    """).df()


def build_metrics(con) -> pd.DataFrame:
    f = con.execute("SELECT * FROM fundamentals").df()
    p = price_metrics(con)
    df = f.merge(p, on="ticker", how="inner")
    df["ret_3m"] = df["last_close"] / df["close_3m"] - 1
    df["ret_6m"] = df["last_close"] / df["close_6m"] - 1
    df["ret_12m"] = df["last_close"] / df["close_12m"] - 1
    df["pct_below_high"] = 1 - df["last_close"] / df["high_52w"]
    df["above_200dma"] = df["last_close"] > df["sma_200"]
    df["fwd_eps_growth"] = np.where(df["trailing_eps"] > 0,
                                    df["forward_eps"] / df["trailing_eps"] - 1, np.nan)
    df["upside_to_target"] = df["target_price"] / df["last_close"] - 1
    # Negative / zero valuation ratios are meaningless for ranking
    for col in ("peg", "forward_pe"):
        df.loc[df[col] <= 0, col] = np.nan
    return df


def apply_filters(df: pd.DataFrame, flt: dict) -> pd.Series:
    mask = (
        (df["market_cap"] >= flt["min_market_cap"])
        & (df["avg_dollar_volume"] >= flt["min_avg_dollar_volume"])
        & (df["revenue_growth"] >= flt["min_revenue_growth"])
        & (df["gross_margin"] >= flt["min_gross_margin"])
        & (df["pct_below_high"] <= flt["max_pct_below_52w_high"])
    )
    if flt.get("exclude_sectors"):
        mask &= ~df["sector"].isin(flt["exclude_sectors"])
    if flt.get("require_above_200dma"):
        mask &= df["above_200dma"]
    return mask.fillna(False)


def score(df: pd.DataFrame, weights: dict) -> pd.DataFrame:
    """Percentile-rank each metric across the universe, average into factor scores (0-100),
    then combine factors with the configured weights."""
    out = df.copy()
    total_w = sum(weights.values()) or 1
    out["score"] = 0.0
    for factor, metrics in FACTORS.items():
        ranks = [out[m].rank(pct=True, ascending=higher) for m, higher in metrics.items()]
        out[f"{factor}_score"] = (pd.concat(ranks, axis=1).mean(axis=1) * 100).fillna(50)
        out["score"] += out[f"{factor}_score"] * weights.get(factor, 0) / total_w
    return out


def run_screen(con, cfg: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Returns (ranked passing stocks, full scored universe)."""
    df = score(build_metrics(con), cfg["weights"])
    df["passes"] = apply_filters(df, cfg["filters"])
    ranked = df[df["passes"]].sort_values("score", ascending=False).reset_index(drop=True)
    ranked.index += 1
    return ranked, df
