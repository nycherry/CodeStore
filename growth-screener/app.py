"""Interactive dashboard:  streamlit run app.py"""
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from screener.config import load_config
from screener.data import connect
from screener.scoring import apply_filters, build_metrics, score

st.set_page_config(page_title="Growth Screener", layout="wide")
cfg = load_config()


@st.cache_data(ttl=600)
def load_metrics():
    con = connect()
    df = build_metrics(con)
    con.close()
    return df


@st.cache_data(ttl=600)
def load_prices(ticker):
    con = connect()
    df = con.execute("SELECT date, close FROM prices WHERE ticker = ? ORDER BY date", [ticker]).df()
    con.close()
    return df


metrics = load_metrics()
if metrics.empty:
    st.error("No data yet. Run `python -m screener.cli fetch` first.")
    st.stop()

st.sidebar.header("Filters")
f = cfg["filters"]
flt = {
    "min_market_cap": st.sidebar.number_input("Min market cap ($B)", 0.0, 5000.0,
                                              f["min_market_cap"] / 1e9, 1.0) * 1e9,
    "min_avg_dollar_volume": st.sidebar.number_input("Min avg $ volume ($M/day)", 0.0, 10000.0,
                                                     f["min_avg_dollar_volume"] / 1e6, 5.0) * 1e6,
    "min_revenue_growth": st.sidebar.slider("Min revenue growth %", -50, 200,
                                            int(f["min_revenue_growth"] * 100)) / 100,
    "min_gross_margin": st.sidebar.slider("Min gross margin %", 0, 100,
                                          int(f["min_gross_margin"] * 100)) / 100,
    "max_pct_below_52w_high": st.sidebar.slider("Max % below 52w high", 0, 100,
                                                int(f["max_pct_below_52w_high"] * 100)) / 100,
    "require_above_200dma": st.sidebar.checkbox("Above 200-day MA", f["require_above_200dma"]),
}
st.sidebar.header("Score weights")
weights = {k: st.sidebar.slider(k.title(), 0.0, 1.0, float(v), 0.05) for k, v in cfg["weights"].items()}
sectors = sorted(metrics["sector"].dropna().unique())
chosen = st.sidebar.multiselect("Sectors", sectors, default=sectors)

df = score(metrics, weights)
df = df[apply_filters(df, flt) & df["sector"].isin(chosen)].sort_values("score", ascending=False)

st.title("Growth Screener")
asof = pd.to_datetime(metrics["last_date"].max()).date()
st.caption(f"{len(df)} of {len(metrics)} stocks pass · prices as of {asof} · not investment advice")

show = ["ticker", "name", "sector", "score", "growth_score", "quality_score", "momentum_score",
        "valuation_score", "market_cap", "revenue_growth", "earnings_growth", "fwd_eps_growth",
        "gross_margin", "operating_margin", "forward_pe", "peg", "ret_6m", "ret_12m",
        "pct_below_high", "upside_to_target"]
view = df[show].copy()
view["market_cap"] = view["market_cap"] / 1e9
pct_cols = ["revenue_growth", "earnings_growth", "fwd_eps_growth", "gross_margin", "operating_margin",
            "ret_6m", "ret_12m", "pct_below_high", "upside_to_target"]
view[pct_cols] = view[pct_cols] * 100
cc = st.column_config
st.dataframe(
    view, hide_index=True, width="stretch", height=520,
    column_config={
        "score": cc.ProgressColumn("Score", min_value=0, max_value=100, format="%.0f"),
        **{c: cc.NumberColumn(c.replace("_score", "").title(), format="%.0f")
           for c in ["growth_score", "quality_score", "momentum_score", "valuation_score"]},
        "market_cap": cc.NumberColumn("Mkt Cap ($B)", format="%.0f"),
        **{c: cc.NumberColumn(c.replace("_", " ").title() + " %", format="%.0f") for c in pct_cols},
        "forward_pe": cc.NumberColumn("Fwd P/E", format="%.1f"),
        "peg": cc.NumberColumn("PEG", format="%.2f"),
    },
)
st.download_button("Download CSV", df.to_csv(index=False), "growth_screen.csv")

if not df.empty:
    st.subheader("Chart")
    ticker = st.selectbox("Ticker", df["ticker"].tolist())
    p = load_prices(ticker)
    p["sma50"] = p["close"].rolling(50).mean()
    p["sma200"] = p["close"].rolling(200).mean()
    fig = go.Figure()
    fig.add_scatter(x=p["date"], y=p["close"], name="Close")
    fig.add_scatter(x=p["date"], y=p["sma50"], name="50d MA", line={"dash": "dot"})
    fig.add_scatter(x=p["date"], y=p["sma200"], name="200d MA", line={"dash": "dash"})
    fig.update_layout(height=420, margin={"l": 0, "r": 0, "t": 30, "b": 0})
    st.plotly_chart(fig, width="stretch")
    row = df[df["ticker"] == ticker].iloc[0]
    st.write(f"**{row['name']}** · {row['industry']}")
