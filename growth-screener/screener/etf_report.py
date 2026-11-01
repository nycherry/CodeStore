"""Render the ETF analysis to a self-contained HTML report + CSV."""
from datetime import datetime

import pandas as pd
from jinja2 import Template

from .config import REPORTS_DIR
from .report import _fmt

COLUMNS = [
    ("ticker", "Ticker", "s"), ("name", "Name", "s"), ("score", "Score", "f0"),
    ("group_rank", "Rank", "f0"), ("expense_ratio", "Fee", "pct2"), ("aum", "AUM", "aum"),
    ("yield", "Yield", "pct1"), ("ret_1y", "1Y", "pct"), ("cagr_3y", "3Y/yr", "pct"),
    ("cagr_5y", "5Y/yr", "pct"), ("cagr_10y", "10Y/yr", "pct"), ("volatility", "Vol", "pct"),
    ("max_drawdown", "Max DD", "pct"), ("sharpe", "Sharpe", "f2"), ("beta", "Beta", "f2"),
    ("pct_below_high", "Off High", "pct"),
]

GROUP_LABELS = {
    "core": "Core US market", "growth": "Large-cap growth", "tech": "Technology", "semis": "Semiconductors",
    "biotech": "Biotech", "energy": "Energy", "financials": "Financials", "utilities": "Utilities",
    "consumer_disc": "Consumer discretionary", "communication": "Communication services",
    "option_income": "Option income (covered calls)", "dividend": "Dividend growth", "bdc": "BDCs (private credit)",
    "leveraged_inverse": "Leveraged / inverse (not scored)", "volatility": "Volatility (not scored)",
}


def fmt(v, kind):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "–"
    if kind == "pct2":
        return f"{v * 100:.2f}%"
    if kind == "pct1":
        return f"{v * 100:.1f}%"
    if kind == "aum":
        return f"${v / 1e9:.1f}B" if v >= 1e9 else f"${v / 1e6:.0f}M"
    return _fmt(v, kind)


TEMPLATE = Template("""<!doctype html><html><head><meta charset="utf-8">
<title>ETF Analysis {{ date }}</title>
<style>
body{font-family:-apple-system,Segoe UI,Roboto,sans-serif;margin:24px;color:#1a1a1a;max-width:1400px}
h1{margin:0 0 4px} h2{margin:28px 0 8px;font-size:18px} h3{margin:18px 0 6px;font-size:15px}
.sub{color:#666;margin-bottom:16px}
table{border-collapse:collapse;font-size:13px}
th,td{padding:5px 8px;border-bottom:1px solid #eee;text-align:right;white-space:nowrap}
th{background:#f6f8fa} .l{text-align:left}
tr.own td{background:#f3f8ff} tr.own td:first-child{font-weight:700}
.tag{font-size:11px;background:#0969da;color:#fff;border-radius:3px;padding:0 4px;margin-left:4px}
.warn{color:#9a6700;cursor:help} .neg{color:#cf222e} .pos{color:#1a7f37}
.note{color:#777;font-size:12px;margin-top:20px;max-width:1000px;line-height:1.5}
.hm td{width:34px;text-align:center;font-size:11px;padding:3px}
</style></head><body>
<h1>ETF Analysis</h1>
<div class="sub">{{ date }} &middot; prices through {{ last_date }} &middot; {{ n_owned }} of your funds vs {{ n_cand }} alternatives
&middot; highlighted rows = yours</div>

<h2>Suggested swaps</h2>
{% if swaps %}<table><tr><th class="l">You hold</th><th class="l">Consider</th><th class="l">Why</th><th>Score</th><th>Fee</th><th>5Y/yr</th></tr>
{% for s in swaps %}<tr><td class="l"><b>{{ s.own }}</b></td><td class="l"><b>{{ s.alt }}</b>{% if s.alt_owned %}<span class="tag">you own</span>{% endif %}</td>
<td class="l">{{ s.reason }}</td><td>{{ s.scores }}</td><td>{{ s.fees }}</td><td>{{ s.cagrs }}</td></tr>{% endfor %}</table>
{% else %}<p>None.</p>{% endif %}

<h2>Overlapping funds you own</h2>
<p class="sub">Pairs whose daily returns over 3 years correlate &ge; {{ corr_min }}. Owning both adds little diversification; keep the higher score.
Top-10 overlap is a lower bound (Yahoo lists only the top 10 holdings).</p>
<table><tr><th class="l">Pair</th><th>Correlation</th><th>Top-10 overlap</th><th>Scores</th><th class="l">Keep</th></tr>
{% for r in redundant %}<tr><td class="l">{{ r.a }} / {{ r.b }}</td><td>{{ r.corr }}</td><td>{{ r.overlap }}</td><td>{{ r.scores }}</td><td class="l"><b>{{ r.keep }}</b></td></tr>{% endfor %}</table>

<h2>Warnings on your funds</h2>
<table>{% for t, w in warnings %}<tr><td class="l"><b>{{ t }}</b></td><td class="l" style="white-space:normal">{{ w }}</td></tr>{% endfor %}</table>

<h2>All funds by group</h2>
<p class="sub">Score = 0–100, percentile-ranked only against funds in the same group. Vol / Max DD / Sharpe use the same window
for every fund in a group (5y, or 3y when a fund is younger; shown next to the group name). Returns include dividends.</p>
{% for g in groups %}<h3>{{ g.label }} <span class="sub">({{ g.window }})</span></h3>
<table><tr>{% for _, label, _ in cols %}<th{% if loop.index <= 2 %} class="l"{% endif %}>{{ label }}</th>{% endfor %}<th class="l">Notes</th></tr>
{% for r in g.rows %}<tr{% if r.own %} class="own"{% endif %}>{% for c in r.cells %}<td{% if loop.index <= 2 %} class="l"{% endif %}>{{ c }}{% if loop.index == 1 and r.own %}<span class="tag">own</span>{% endif %}</td>{% endfor %}
<td class="l">{% if r.warn %}<span class="warn" title="{{ r.warn }}">&#9888; {{ r.warn[:60] }}{% if r.warn|length > 60 %}…{% endif %}</span>{% endif %}</td></tr>{% endfor %}</table>
{% endfor %}

<h2>Correlation of your funds (3y daily returns)</h2>
<table class="hm"><tr><th></th>{% for c in corr_cols %}<th>{{ c }}</th>{% endfor %}</tr>
{% for name, row in corr_rows %}<tr><th>{{ name }}</th>{% for v, color in row %}<td style="background:{{ color }}">{{ v }}</td>{% endfor %}</tr>{% endfor %}</table>

<div class="note">Weights: {{ weights }}. Risk-free rate for Sharpe: {{ rf }}.<br>
Expense ratios, AUM, and holdings come from Yahoo Finance. Yield = trailing-12-month distributions / price.
Data may be delayed, missing, or wrong. Past returns don't predict future returns. This is a research tool, not investment advice.</div>
</body></html>""")


def _corr_color(v):
    if pd.isna(v):
        return "#fff"
    v = max(-1.0, min(1.0, float(v)))
    if v >= 0:
        a = int(255 - 155 * v)
        return f"rgb(255,{a},{a})"
    a = int(255 + 155 * v)
    return f"rgb({a},{a},255)"


def write_etf_report(res: dict, cfg: dict) -> str:
    REPORTS_DIR.mkdir(exist_ok=True)
    df = res["funds"]
    score = df.set_index("ticker")["score"]
    groups = []
    for g in cfg["groups"]:
        sub = df[df.group == g].sort_values(["score", "ret_1y"], ascending=False)
        if sub.empty:
            continue
        win = sub["risk_window_y"].dropna()
        groups.append({
            "label": GROUP_LABELS.get(g, g),
            "window": f"risk over {int(win.min())}y" if not win.empty else "",
            "rows": [{"own": r.owned, "warn": r.warnings,
                      "cells": [fmt(r.get(c), k) for c, _, k in COLUMNS]} for _, r in sub.iterrows()],
        })

    def pair_scores(a, b):
        return f"{fmt(score.get(a), 'f0')} / {fmt(score.get(b), 'f0')}"

    redundant = []
    for r in res["redundant"]:
        sa, sb = score.get(r["a"]), score.get(r["b"])
        keep = "–" if pd.isna(sa) or pd.isna(sb) else (r["a"] if sa >= sb else r["b"])
        redundant.append({**r, "overlap": fmt(r["overlap"], "pct"), "scores": pair_scores(r["a"], r["b"]), "keep": keep})
    swaps = [{**s, "scores": f"{s['own_score']:.0f} → {s['alt_score']:.0f}",
              "fees": f"{fmt(s['own_er'], 'pct2')} → {fmt(s['alt_er'], 'pct2')}",
              "cagrs": f"{fmt(s['own_cagr'], 'pct')} → {fmt(s['alt_cagr'], 'pct')}"} for s in res["swaps"]]
    corr = res["corr"]
    corr_rows = [(t, [(fmt(v, "f2") if t != c else "", _corr_color(v) if t != c else "#eee")
                      for c, v in corr.loc[t].items()]) for t in corr.index]
    owned = df[df.owned]
    now = datetime.now()
    html = TEMPLATE.render(
        date=now.strftime("%Y-%m-%d %H:%M"), last_date=pd.to_datetime(df["last_date"]).max().date(),
        n_owned=len(owned), n_cand=len(df) - len(owned), swaps=swaps, redundant=redundant,
        corr_min=cfg.get("redundant_corr", 0.97),
        warnings=[(r.ticker, r.warnings) for r in owned.itertuples() if r.warnings],
        groups=groups, cols=COLUMNS, corr_cols=list(corr.columns), corr_rows=corr_rows,
        weights=cfg["weights"], rf=cfg.get("risk_free_rate"))
    path = REPORTS_DIR / f"etf_analysis_{now:%Y%m%d}.html"
    path.write_text(html)
    (REPORTS_DIR / "etf_latest.html").write_text(html)
    df.drop(columns=["top_holdings", "sectors"], errors="ignore").to_csv(
        REPORTS_DIR / f"etf_analysis_{now:%Y%m%d}.csv", index=False)
    return str(path)
