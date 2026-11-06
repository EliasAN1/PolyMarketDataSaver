"""Load trader.toml / config.toml and optional .env files."""

from __future__ import annotations

import math
import os
import re
import tomllib
from dataclasses import dataclass, fields
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from pmtrader.when import (
    SESSION_ALIASES,
    SESSION_KEYS,
    WEEKDAY_INDEX,
    dump_hours,
    dump_sessions,
    dump_weekdays,
    normalize_hours,
    normalize_sessions,
    normalize_weekdays,
    passes_when,
    session_catalog,
    when_active,
    when_label,
)

BTC_SOURCES = ("binance_spot", "coinbase_spot", "bybit_spot", "median")


@dataclass(slots=True)
class TraderConfig:
    """Entry rules aligned with Strategy Lab (elapsed band, odds enter, |BTC−PTB| band).

    Entries are sampled once per second on the Lab's tape boundary and mirror
    ``findEntry`` in lab_engine.js: same-sample band cross, fill at the ask.
    ``fak_limit`` is a hard cap on that ask (skip when the ask is above it).

    Older keys still work: ``use_entry_last`` + ``entry_last_minutes`` if
    ``elapsed_from_min`` / ``elapsed_to_min`` are omitted.
    """

    elapsed_from_min: float | None = None
    elapsed_to_min: float | None = None
    entry_last_minutes: float = 3.0
    use_entry_last: bool = True
    odds_min: float = 0.20
    odds_max: float = 0.30
    fak_limit: float | None = None
    use_btc_distance: bool = False
    min_btc_away: float = 5.0
    max_btc_away: float | None = None
    btc_source: str = "binance_spot"
    use_twap: bool = False
    use_venues: bool = False
    min_venues: int = 2
    use_fresh_turn: bool = False
    stake_usd: float = 10.0
    min_seconds_left: float = 3.0
    tick_size: str = "0.01"
    # Empty / omitted = no clock filter (trade every hour / day / session).
    # Hours 0-23 local. Weekdays 0=Sun..6=Sat or Mon,Tue,... Sessions: tokyo_open,
    # london_open, wall_open, asia, london, wall, overlap, off.
    hours: tuple[int, ...] | None = None
    weekdays: tuple[int, ...] | None = None
    sessions: tuple[str, ...] | None = None
    # Local hours that buy the opposite of the signal. Empty = never invert.
    invert_hours: tuple[int, ...] | None = None

    def __post_init__(self) -> None:
        self.hours = normalize_hours(self.hours)
        self.weekdays = normalize_weekdays(self.weekdays)
        self.sessions = normalize_sessions(self.sessions)
        self.invert_hours = normalize_hours(self.invert_hours)
        if not 0 < self.odds_min <= self.odds_max < 1:
            raise ValueError("odds_min / odds_max must satisfy 0 < min <= max < 1")
        cap = self.effective_fak_limit()
        if not 0 < cap < 1:
            raise ValueError("fak_limit must satisfy 0 < limit < 1")
        if cap < self.odds_max:
            raise ValueError("fak_limit must be >= odds_max")
        if self.stake_usd <= 0:
            raise ValueError("stake_usd must be positive")
        if self.min_venues < 1:
            raise ValueError("min_venues must be >= 1")
        if self.btc_source not in BTC_SOURCES:
            raise ValueError(f"btc_source must be one of {', '.join(BTC_SOURCES)}; got {self.btc_source!r}")
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

    def effective_fak_limit(self) -> float:
        """Max FAK limit price; defaults to odds_max when fak_limit is omitted."""
        return self.odds_max if self.fak_limit is None else self.fak_limit

    def trigger_band_label(self) -> str:
        lo, hi = self.odds_min, self.odds_max
        if lo == hi:
            return f"{lo:.2f}"
        return f"{lo:.2f}-{hi:.2f}"

    def fillable_ask_label(self) -> str:
        return f"<= {self.effective_fak_limit():.2f}"

    def when_filter_on(self) -> bool:
        return when_active(self.hours, self.weekdays, self.sessions)

    def when_ok(self, ts: float | None) -> bool:
        return passes_when(ts, hours=self.hours, weekdays=self.weekdays, sessions=self.sessions)

    def when_label(self) -> str:
        return when_label(hours=self.hours, weekdays=self.weekdays, sessions=self.sessions)

    def set_when(self, *, hours: Any = None, weekdays: Any = None, sessions: Any = None) -> None:
        self.hours = normalize_hours(hours)
        self.weekdays = normalize_weekdays(weekdays)
        self.sessions = normalize_sessions(sessions)

    def hour_is_inverted(self, ts: float | None) -> bool:
        if not self.invert_hours or ts is None or ts <= 0:
            return False
        return datetime.fromtimestamp(float(ts)).hour in self.invert_hours

    def when_payload(self) -> dict[str, Any]:
        return {
            "hours": dump_hours(self.hours),
            "weekdays": dump_weekdays(self.weekdays),
            "sessions": dump_sessions(self.sessions),
            "invert_hours": dump_hours(self.invert_hours),
            "active": self.when_filter_on(),
            "label": self.when_label(),
        }

    def apply_settings(
        self,
        *,
        stake_usd: Any,
        hours: Any,
        weekdays: Any,
        sessions: Any,
        invert_hours: Any,
    ) -> None:
        stake = parse_stake(stake_usd)
        allowed_hours = parse_hours(hours, field="hours")
        allowed_days = parse_weekdays(weekdays)
        allowed_markets = parse_sessions(sessions)
        flipped_hours = parse_hours(invert_hours, field="invert_hours")
        self.stake_usd = stake
        self.hours = allowed_hours
        self.weekdays = allowed_days
        self.sessions = allowed_markets
        self.invert_hours = flipped_hours

    def settings_payload(self) -> dict[str, Any]:
        return {
            "stake_usd": self.stake_usd,
            **self.when_payload(),
            "catalog": session_catalog(),
        }


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


def parse_stake(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError("stake_usd must be a positive number")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("stake_usd must be a positive number") from exc
    if not math.isfinite(number) or number <= 0:
        raise ValueError("stake_usd must be positive")
    cents = round(number * 100)
    if abs(number * 100 - cents) > 1e-6:
        raise ValueError("stake_usd must have at most 2 decimal places")
    return cents / 100


def parse_hours(value: Any, *, field: str) -> tuple[int, ...] | None:
    if value is None:
        return None
    if isinstance(value, (str, bytes)) or not isinstance(value, Iterable):
        raise ValueError(f"{field} must be a list")
    out: list[int] = []
    seen: set[int] = set()
    for item in value:
        if isinstance(item, bool):
            raise ValueError(f"unknown hour in {field}: {item}")
        if isinstance(item, int):
            hour = item
        elif isinstance(item, float) and item.is_integer():
            hour = int(item)
        elif isinstance(item, str) and item.strip().isdigit():
            hour = int(item.strip())
        else:
            raise ValueError(f"unknown hour in {field}: {item}")
        if hour < 0 or hour > 23:
            raise ValueError(f"unknown hour in {field}: {hour}")
        if hour not in seen:
            seen.add(hour)
            out.append(hour)
    return tuple(out) or None


def parse_weekdays(value: Any) -> tuple[int, ...] | None:
    if value is None:
        return None
    if isinstance(value, (str, bytes)) or not isinstance(value, Iterable):
        raise ValueError("weekdays must be a list")
    out: list[int] = []
    seen: set[int] = set()
    for item in value:
        if isinstance(item, bool):
            raise ValueError(f"unknown weekday: {item}")
        day: int | None
        if isinstance(item, str):
            key = item.strip().lower()
            day = WEEKDAY_INDEX.get(key[:3]) if key else None
            if day is None:
                day = WEEKDAY_INDEX.get(key)
        elif isinstance(item, int) or (isinstance(item, float) and item.is_integer()):
            day = int(item)
        else:
            day = None
        if day is None or day < 0 or day > 6:
            raise ValueError(f"unknown weekday: {item}")
        if day not in seen:
            seen.add(day)
            out.append(day)
    return tuple(out) or None


def parse_sessions(value: Any) -> tuple[str, ...] | None:
    if value is None:
        return None
    if isinstance(value, (str, bytes)) or not isinstance(value, Iterable):
        raise ValueError("sessions must be a list")
    out: list[str] = []
    seen: set[str] = set()
    for item in value:
        key = str(item).strip().lower().replace(" ", "_")
        key = SESSION_ALIASES.get(key, key)
        if key not in SESSION_KEYS:
            raise ValueError(f"unknown market: {item}")
        if key not in seen:
            seen.add(key)
            out.append(key)
    return tuple(out) or None


def _toml_number(value: float) -> str:
    rounded = round(float(value), 2)
    if rounded == int(rounded):
        return str(int(rounded))
    return f"{rounded:.2f}".rstrip("0").rstrip(".")


def _toml_array(values: list[Any]) -> str:
    if not values:
        return "[]"
    if all(isinstance(v, str) for v in values):
        inner = ", ".join('"' + str(v).replace('"', "") + '"' for v in values)
        return f"[{inner}]"
    return "[" + ", ".join(str(int(v)) for v in values) + "]"


def persist_when(path: Path, cfg: TraderConfig) -> None:
    """Rewrite the saved schedule, stake, and invert hours. Keep every other line."""
    persist_settings(path, cfg)


def persist_settings(path: Path, cfg: TraderConfig) -> None:
    text = path.read_text(encoding="utf-8")
    updates = {
        "stake_usd": _toml_number(cfg.stake_usd),
        "hours": _toml_array(dump_hours(cfg.hours)),
        "weekdays": _toml_array(dump_weekdays(cfg.weekdays)),
        "sessions": _toml_array(dump_sessions(cfg.sessions)),
        "invert_hours": _toml_array(dump_hours(cfg.invert_hours)),
    }
    for key, value in updates.items():
        line = f"{key} = {value}"
        pattern = re.compile(rf"(?m)^{re.escape(key)}\s*=\s*.*$")
        if pattern.search(text):
            text = pattern.sub(line, text, count=1)
        else:
            text = text.rstrip() + f"\n{line}\n"
    path.write_text(text, encoding="utf-8")
