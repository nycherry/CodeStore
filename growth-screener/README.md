# Growth Screener

A starter growth-stock screener. It pulls about 1,000 US stocks (S&P 500, S&P 400 and Nasdaq-100) from Yahoo Finance and stores them in a local DuckDB file. Each stock gets a score for growth, quality, momentum and valuation. You get an HTML/CSV report plus an interactive Streamlit dashboard.

> This is a screening tool for finding candidates to research, not investment advice. Yahoo data is free but can be delayed, missing, or wrong.

## Setup (once)

Requires Python 3.10+.

```bash
cd growth-screener
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Run

```bash
# 1. Download data, screen, and write the report (first run takes ~5-10 min)
python -m screener.cli run

# 2. Open the interactive dashboard (http://localhost:8501)
streamlit run app.py
```

Other commands:

```bash
python -m screener.cli fetch                       # only refresh data
python -m screener.cli screen                      # re-screen cached data (instant)
python -m screener.cli run --tickers NVDA,PLTR,CRWD # screen just your own list
```

Outputs go to `reports/`. `latest.html` is the most recent report (columns sort when clicked), and `growth_screen_YYYYMMDD.csv` holds the full ranked list.
Fundamentals are cached for 24h, so re-running the same day only refreshes prices.

## How it scores

Hard filters (all must pass, edit in `config.yaml`):

| Filter | Default |
|---|---|
| Market cap | ≥ $2B |
| 50-day avg dollar volume | ≥ $20M/day |
| Revenue growth (YoY, latest quarter) | ≥ 15% |
| Gross margin | ≥ 30% |
| Price above 200-day moving average | yes |
| Distance below 52-week high | ≤ 35% |
| Excluded sectors | none (try `[Energy, Basic Materials]` to drop commodity-price-driven "growth") |

Each metric is percentile-ranked against the whole universe (0–100). The ranks are then averaged into four factors:

| Factor | Weight | Metrics |
|---|---|---|
| Growth | 40% | revenue growth, earnings growth, forward EPS growth (fwd/trailing EPS) |
| Quality | 20% | gross margin, operating margin, ROE |
| Momentum | 25% | 6-month return, 12-month return, closeness to 52-week high |
| Valuation | 15% | PEG, forward P/E (lower is better) |

To change the strategy, edit `config.yaml`, or use the sliders in the dashboard.

## Run it daily (optional)

macOS/Linux cron, weekdays at 5pm after the close:

```
0 17 * * 1-5 cd /path/to/growth-screener && .venv/bin/python -m screener.cli run >> reports/cron.log 2>&1
```

## Project layout

```
config.yaml           universe, filters, weights
screener/universe.py  index constituents (Wikipedia, with fallback list)
screener/data.py      Yahoo Finance -> DuckDB (data/market.duckdb)
screener/scoring.py   metrics, filters, composite score
screener/report.py    HTML/CSV report
screener/cli.py       command line
app.py                Streamlit dashboard
```

## Known limitations / next steps

- **No backtest yet.** Yahoo only gives *current* fundamentals, so a backtest using them would leak future information. A proper backtest needs point-in-time fundamentals (e.g. Financial Modeling Prep, Sharadar).
- Yahoo rate-limits heavy use. If fundamentals come back empty, wait a few minutes and run `fetch` again; cached rows are kept.
- Possible upgrades: paid data source, earnings-surprise/estimate-revision factors, email/Slack delivery of the daily report, LLM summaries of 10-Ks for the top picks.
