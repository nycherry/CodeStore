"""Command line entry point.

  python -m screener.cli run            # fetch data + screen + write report
  python -m screener.cli fetch          # only refresh data
  python -m screener.cli screen         # only screen/report from cached data
"""
import argparse

import pandas as pd

from .config import load_config
from .data import connect, update_fundamentals, update_prices
from .report import write_report
from .scoring import run_screen
from .universe import get_universe


def fetch(cfg, tickers_override=None):
    tickers = tickers_override or get_universe(cfg["universe"], cfg.get("extra_tickers"))
    print(f"Universe: {len(tickers)} tickers")
    con = connect()
    update_prices(con, tickers)
    update_fundamentals(con, tickers, cfg.get("fundamentals_max_age_hours", 24))
    con.close()


def screen(cfg):
    con = connect()
    ranked, universe = run_screen(con, cfg)
    con.close()
    path = write_report(ranked, universe, cfg)
    with pd.option_context("display.width", 200, "display.max_columns", 20):
        cols = ["ticker", "name", "score", "revenue_growth", "gross_margin", "peg", "ret_6m"]
        print(ranked[cols].head(cfg["top_n"]).to_string(
            formatters={"score": "{:.0f}".format,
                        "revenue_growth": "{:.0%}".format, "gross_margin": "{:.0%}".format,
                        "peg": "{:.2f}".format, "ret_6m": "{:.0%}".format}))
    print(f"\n{len(ranked)} of {len(universe)} passed filters. Report: {path}")


def main():
    ap = argparse.ArgumentParser(description="Growth stock screener")
    ap.add_argument("command", choices=["run", "fetch", "screen"])
    ap.add_argument("--config", default=None, help="path to config.yaml")
    ap.add_argument("--tickers", help="comma-separated tickers to use instead of the index universe")
    args = ap.parse_args()
    cfg = load_config(args.config) if args.config else load_config()
    override = [t.strip().upper() for t in args.tickers.split(",")] if args.tickers else None
    if args.command in ("run", "fetch"):
        fetch(cfg, override)
    if args.command in ("run", "screen"):
        screen(cfg)


if __name__ == "__main__":
    main()
