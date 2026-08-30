"""Load trader.toml / config.toml and optional .env files."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, fields
from pathlib import Path


@dataclass(slots=True)
class TraderConfig:
    entry_last_minutes: float = 3.0
    use_entry_last: bool = True
    odds_min: float = 0.20
    odds_max: float = 0.28
    use_btc_distance: bool = True
    min_btc_away: float = 15.0
    use_twap: bool = True
    use_venues: bool = True
    min_venues: int = 2
    stake_usd: float = 10.0
    min_seconds_left: float = 3.0
    tick_size: str = "0.01"

    def __post_init__(self) -> None:
        if not 0 < self.odds_min <= self.odds_max < 1:
            raise ValueError("odds_min / odds_max must satisfy 0 < min <= max < 1")
        if self.stake_usd <= 0:
            raise ValueError("stake_usd must be positive")
        if self.min_venues < 1:
            raise ValueError("min_venues must be >= 1")


def load_config(path: Path) -> TraderConfig:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    known = {item.name for item in fields(TraderConfig)}
    kwargs = {key: value for key, value in data.items() if key in known}
    return TraderConfig(**kwargs)


def load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name, default)
    if value is None:
        return None
    value = value.strip()
    return value or None
