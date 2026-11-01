"""Render the ranked screen to a self-contained HTML report."""
from datetime import datetime

import pandas as pd
from jinja2 import Template

from .config import REPORTS_DIR

COLUMNS = [
    ("ticker", "Ticker", "s"), ("name", "Name", "s"), ("sector", "Sector", "s"),
    ("score", "Score", "f0"), ("growth_score", "Growth", "f0"), ("quality_score", "Quality", "f0"),
    ("momentum_score", "Mom.", "f0"), ("valuation_score", "Value", "f0"),
    ("market_cap", "Mkt Cap", "cap"), ("last_close", "Price", "f2"),
    ("revenue_growth", "Rev Gr", "pct"), ("earnings_growth", "EPS Gr", "pct"),
    ("fwd_eps_growth", "Fwd EPS Gr", "pct"), ("gross_margin", "Gross M", "pct"),
    ("operating_margin", "Op M", "pct"), ("forward_pe", "Fwd P/E", "f1"), ("peg", "PEG", "f2"),
    ("ret_6m", "6M", "pct"), ("ret_12m", "12M", "pct"), ("pct_below_high", "Off High", "pct"),
    ("upside_to_target", "To Target", "pct"),
]


def _fmt(v, kind):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "–"
    if kind == "pct":
        return f"{v * 100:.0f}%"
    if kind == "cap":
        return f"${v / 1e12:.2f}T" if v >= 1e12 else f"${v / 1e9:.0f}B"
    if kind.startswith("f"):
        return f"{v:,.{int(kind[1:])}f}"
    return str(v)


TEMPLATE = Template("""<!doctype html><html><head><meta charset="utf-8">
<title>Growth Screen {{ date }}</title>
<style>
body{font-family:-apple-system,Segoe UI,Roboto,sans-serif;margin:24px;color:#1a1a1a}
h1{margin:0 0 4px} .sub{color:#666;margin-bottom:16px}
table{border-collapse:collapse;font-size:13px;width:100%}
th,td{padding:6px 8px;border-bottom:1px solid #eee;text-align:right;white-space:nowrap}
th{background:#f6f8fa;position:sticky;top:0;cursor:pointer} td:nth-child(-n+4),th:nth-child(-n+4){text-align:left}
tr:hover{background:#fafcff} .score{font-weight:600}
.note{color:#888;font-size:12px;margin-top:16px;max-width:900px}
</style></head><body>
<h1>Growth Screen</h1>
<div class="sub">{{ date }} &middot; {{ n_pass }} of {{ n_universe }} stocks passed filters &middot; top {{ rows|length }} shown</div>
<table id="t"><thead><tr><th>#</th>{% for _, label, _ in cols %}<th>{{ label }}</th>{% endfor %}</tr></thead>
<tbody>{% for r in rows %}<tr><td>{{ loop.index }}</td>{% for c in r %}<td{% if loop.index == 4 %} class="score"{% endif %}>{{ c }}</td>{% endfor %}</tr>{% endfor %}</tbody></table>
<div class="note">Filters: {{ filters }}<br>Weights: {{ weights }}<br>
Scores are 0–100 percentile ranks vs. the whole universe. Data from Yahoo Finance; may be delayed or incomplete.
This is a screening tool, not investment advice.</div>
<script>
document.querySelectorAll('#t th').forEach((th,i)=>th.onclick=()=>{const tb=document.querySelector('#t tbody');
const rows=[...tb.rows];const asc=th.dataset.asc!=='1';th.dataset.asc=asc?'1':'0';
const num=s=>{const m=s.replace(/[$,%TB]/g,'');return isNaN(parseFloat(m))?null:parseFloat(m)*(s.includes('T')?1000:1)};
rows.sort((a,b)=>{const x=a.cells[i].innerText,y=b.cells[i].innerText,nx=num(x),ny=num(y);
const r=(nx!==null&&ny!==null)?nx-ny:x.localeCompare(y);return asc?r:-r});rows.forEach(r=>tb.appendChild(r))});
</script></body></html>""")


def write_report(ranked: pd.DataFrame, universe: pd.DataFrame, cfg: dict) -> str:
    REPORTS_DIR.mkdir(exist_ok=True)
    top = ranked.head(cfg["top_n"])
    rows = [[_fmt(r.get(c), k) for c, _, k in COLUMNS] for _, r in top.iterrows()]
    now = datetime.now()
    html = TEMPLATE.render(date=now.strftime("%Y-%m-%d %H:%M"), rows=rows, cols=COLUMNS,
                           n_pass=len(ranked), n_universe=len(universe),
                           filters=cfg["filters"], weights=cfg["weights"])
    path = REPORTS_DIR / f"growth_screen_{now:%Y%m%d}.html"
    path.write_text(html)
    (REPORTS_DIR / "latest.html").write_text(html)
    ranked.to_csv(REPORTS_DIR / f"growth_screen_{now:%Y%m%d}.csv", index_label="rank")
    return str(path)
