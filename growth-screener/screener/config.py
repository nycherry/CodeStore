from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
REPORTS_DIR = ROOT / "reports"
DB_PATH = DATA_DIR / "market.duckdb"


def load_config(path: Path | str = ROOT / "config.yaml") -> dict:
    with open(path) as f:
        cfg = yaml.safe_load(f)
    # PyYAML reads "2e9" as a string; coerce numeric-looking filter values
    for section in ("filters", "weights"):
        for k, v in cfg.get(section, {}).items():
            if isinstance(v, str) and k != "exclude_sectors":
                cfg[section][k] = float(v)
    return cfg
