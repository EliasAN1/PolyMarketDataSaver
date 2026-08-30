"""Load trader.toml / config.toml and optional .env files."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, fields
from pathlib import Path


@dataclass(slots=True)
class TraderConfig:
    """Entry rules aligned with Strategy Lab (elapsed band, odds enter, |BTC−PTB| band).

    Older keys still work: ``use_entry_last`` + ``entry_last_minutes`` if
    ``elapsed_from_min`` / ``elapsed_to_min`` are omitted.
    """

    elapsed_from_min: float | None = None
    elapsed_to_min: float | None = None
    entry_last_minutes: float = 3.0
    use_entry_last: bool = True
    odds_min: float = 0.20
    odds_max: float = 0.30
    use_btc_distance: bool = False
    min_btc_away: float = 5.0
    max_btc_away: float | None = None
    use_twap: bool = False
    use_venues: bool = False
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
        if self.min_btc_away < 0:
            raise ValueError("min_btc_away must be >= 0")
        if self.max_btc_away is not None:
            if self.max_btc_away < 0:
                raise ValueError("max_btc_away must be >= 0")
            if self.max_btc_away < self.min_btc_away:
                self.min_btc_away, self.max_btc_away = self.max_btc_away, self.min_btc_away
        if self.elapsed_from_min is not None and self.elapsed_to_min is not None:
            if self.elapsed_from_min > self.elapsed_to_min:
                self.elapsed_from_min, self.elapsed_to_min = (
                    self.elapsed_to_min,
                    self.elapsed_from_min,
                )

    def uses_elapsed_band(self) -> bool:
        return self.elapsed_from_min is not None or self.elapsed_to_min is not None

    def watch_span_s(self, duration_s: float) -> tuple[bool, float, float]:
        """Return (filter_on, elapsed_from_s, elapsed_to_s) in the 5m window."""
        duration_s = max(1.0, float(duration_s))
        if self.uses_elapsed_band():
            lo = 0.0 if self.elapsed_from_min is None else float(self.elapsed_from_min) * 60.0
            hi = duration_s if self.elapsed_to_min is None else float(self.elapsed_to_min) * 60.0
            if lo > hi:
                lo, hi = hi, lo
            return True, max(0.0, lo), min(duration_s, hi)
        if self.use_entry_last:
            return True, max(0.0, duration_s - float(self.entry_last_minutes) * 60.0), duration_s
        return False, 0.0, duration_s


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
