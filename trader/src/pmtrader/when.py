"""Clock filters shared with Strategy Lab (lab_engine.js).

Hours and weekdays use the machine's local timezone (same as Lab
``Date#getHours`` / ``getDay``). Market sessions are UTC, DST-widened so
Tokyo / London / Wall Street opens are not dropped.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from typing import Any, Iterable, Sequence
from zoneinfo import ZoneInfo

# JS getDay(): 0=Sun .. 6=Sat
WEEKDAY_NAMES = ("Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat")
WEEKDAY_INDEX = {name.lower(): i for i, name in enumerate(WEEKDAY_NAMES)}
WEEKDAY_INDEX.update({"sunday": 0, "monday": 1, "tuesday": 2, "wednesday": 3, "thursday": 4, "friday": 5, "saturday": 6})

# Minutes from 00:00 UTC. end < start wraps midnight (Off hours).
SESSIONS: tuple[dict[str, Any], ...] = (
    {"key": "tokyo_open", "label": "Tokyo open", "short": "Tokyo", "start": 0, "end": 150},
    {"key": "london_open", "label": "London open", "short": "Lon open", "start": 7 * 60, "end": 10 * 60},
    {"key": "wall_open", "label": "Wall St open", "short": "NY open", "start": 13 * 60 + 30, "end": 16 * 60 + 30},
    {"key": "asia", "label": "Asia", "short": "Asia", "start": 0, "end": 8 * 60},
    {"key": "london", "label": "London", "short": "London", "start": 7 * 60, "end": 16 * 60 + 30},
    {"key": "wall", "label": "Wall Street", "short": "Wall St", "start": 13 * 60, "end": 21 * 60},
    {"key": "overlap", "label": "London–NY", "short": "Overlap", "start": 13 * 60, "end": 16 * 60 + 30},
    {"key": "off", "label": "Off hours", "short": "Off", "start": 21 * 60, "end": 0},
)
SESSION_KEYS = {row["key"] for row in SESSIONS}
SESSION_BY_KEY = {row["key"]: row for row in SESSIONS}
SESSION_ALIASES = {
    "tokyo": "tokyo_open",
    "ny": "wall",
    "nyc": "wall",
    "wall_street": "wall",
    "wallstreet": "wall",
}


def clock_utc(mins: int) -> str:
    wrapped = ((mins % 1440) + 1440) % 1440
    return f"{wrapped // 60:02d}:{wrapped % 60:02d}"


def in_session_range(mins: int, start: int, end: int) -> bool:
    if start == end:
        return False
    if start < end:
        return start <= mins < end
    return mins >= start or mins < end


def utc_minutes(dt: datetime) -> int:
    aware = dt if dt.tzinfo is not None else dt.astimezone()
    utc = aware.astimezone(timezone.utc)
    return utc.hour * 60 + utc.minute


def sessions_for(dt: datetime) -> list[str]:
    mins = utc_minutes(dt)
    return [row["key"] for row in SESSIONS if in_session_range(mins, row["start"], row["end"])]


def js_weekday(dt: datetime) -> int:
    """Match JavaScript Date#getDay (Sunday = 0)."""
    return int(dt.strftime("%w"))


def normalize_hours(value: Any) -> tuple[int, ...] | None:
    if value is None:
        return None
    if isinstance(value, (str, bytes)) or not isinstance(value, Iterable):
        value = [value]
    out: list[int] = []
    seen: set[int] = set()
    for item in value:
        try:
            hour = int(item)
        except (TypeError, ValueError):
            continue
        if hour < 0 or hour > 23 or hour in seen:
            continue
        seen.add(hour)
        out.append(hour)
    return tuple(out) or None


def normalize_weekdays(value: Any) -> tuple[int, ...] | None:
    if value is None:
        return None
    if isinstance(value, (str, bytes)) or not isinstance(value, Iterable):
        value = [value]
    out: list[int] = []
    seen: set[int] = set()
    for item in value:
        day: int | None
        if isinstance(item, str):
            day = WEEKDAY_INDEX.get(item.strip().lower()[:3])
            if day is None:
                day = WEEKDAY_INDEX.get(item.strip().lower())
        else:
            try:
                day = int(item)
            except (TypeError, ValueError):
                day = None
        if day is None or day < 0 or day > 6 or day in seen:
            continue
        seen.add(day)
        out.append(day)
    return tuple(out) or None


def normalize_sessions(value: Any) -> tuple[str, ...] | None:
    if value is None:
        return None
    if isinstance(value, (str, bytes)):
        value = [value]
    elif not isinstance(value, Iterable):
        return None
    out: list[str] = []
    seen: set[str] = set()
    for item in value:
        key = str(item).strip().lower().replace(" ", "_")
        key = SESSION_ALIASES.get(key, key)
        if key not in SESSION_KEYS or key in seen:
            continue
        seen.add(key)
        out.append(key)
    return tuple(out) or None


def when_active(hours: Sequence[int] | None, weekdays: Sequence[int] | None, sessions: Sequence[str] | None) -> bool:
    return bool(hours) or bool(weekdays) or bool(sessions)


def passes_when(
    ts: float | None,
    *,
    hours: Sequence[int] | None,
    weekdays: Sequence[int] | None,
    sessions: Sequence[str] | None,
) -> bool:
    if not when_active(hours, weekdays, sessions):
        return True
    if ts is None or ts <= 0:
        return False
    dt = datetime.fromtimestamp(float(ts))
    if hours and dt.hour not in hours:
        return False
    if weekdays and js_weekday(dt) not in weekdays:
        return False
    if sessions:
        hit = set(sessions_for(dt))
        if not any(key in hit for key in sessions):
            return False
    return True


def when_label(
    *,
    hours: Sequence[int] | None,
    weekdays: Sequence[int] | None,
    sessions: Sequence[str] | None,
) -> str:
    parts: list[str] = []
    if hours:
        parts.append(",".join(f"{h:02d}" for h in hours) + "h")
    if weekdays:
        parts.append(",".join(WEEKDAY_NAMES[d] for d in weekdays))
    if sessions:
        parts.append(", ".join(SESSION_BY_KEY[k]["short"] for k in sessions if k in SESSION_BY_KEY))
    return " · ".join(parts) if parts else "All hours"


# Clock hours of the actual market, in that market's timezone. The UTC filter
# windows above stay widened so a DST change cannot drop the open. Labels show
# today's real open and close in the machine's local timezone.
_MARKET_HOURS: dict[str, tuple[str, time, time]] = {
    "tokyo_open": ("Asia/Tokyo", time(9, 0), time(11, 30)),
    "london_open": ("Europe/London", time(8, 0), time(11, 0)),
    "wall_open": ("America/New_York", time(9, 30), time(12, 30)),
    "asia": ("Asia/Tokyo", time(9, 0), time(17, 0)),
    "london": ("Europe/London", time(8, 0), time(16, 30)),
    "wall": ("America/New_York", time(9, 30), time(16, 0)),
}


def _zoned_span(day: datetime, zone: ZoneInfo, start: time, end: time) -> tuple[datetime, datetime]:
    start_at = datetime.combine(day, start, zone)
    end_at = datetime.combine(day, end, zone)
    if end_at <= start_at:
        end_at += timedelta(days=1)
    return start_at, end_at


def _market_span(key: str, now: datetime) -> tuple[datetime, datetime]:
    if key in _MARKET_HOURS:
        zone_name, start, end = _MARKET_HOURS[key]
        zone = ZoneInfo(zone_name)
        return _zoned_span(now.astimezone(zone), zone, start, end)
    ny = ZoneInfo("America/New_York")
    london = ZoneInfo("Europe/London")
    tokyo = ZoneInfo("Asia/Tokyo")
    ny_now = now.astimezone(ny)
    if key == "overlap":
        lon_now = now.astimezone(london)
        lon_start, lon_end = _zoned_span(lon_now, london, time(8, 0), time(16, 30))
        ny_start, ny_end = _zoned_span(ny_now, ny, time(9, 30), time(16, 0))
        return max(lon_start, ny_start), min(lon_end, ny_end)
    close, _ = _zoned_span(ny_now, ny, time(16, 0), time(16, 1))
    reopen = datetime.combine(ny_now.date(), time(9, 0), tokyo)
    if reopen <= close:
        reopen += timedelta(days=1)
    return close, reopen


def _clock(moment: datetime, zone: datetime.tzinfo) -> str:
    local = moment.astimezone(zone)
    return f"{local.hour:02d}:{local.minute:02d}"


def session_catalog(now: datetime | None = None) -> list[dict[str, Any]]:
    moment = now or datetime.now().astimezone()
    if moment.tzinfo is None:
        moment = moment.astimezone()
    local_zone = moment.tzinfo
    rows = []
    for session in SESSIONS:
        start, end = _market_span(session["key"], moment)
        rows.append(
            {
                "key": session["key"],
                "label": session["label"],
                "short": session["short"],
                "utc": f"{_clock(start, timezone.utc)}–{_clock(end, timezone.utc)} UTC",
                "local": f"{_clock(start, local_zone)}–{_clock(end, local_zone)} local",
            }
        )
    return rows


def dump_hours(hours: Sequence[int] | None) -> list[int]:
    return list(hours or ())


def dump_weekdays(weekdays: Sequence[int] | None) -> list[str]:
    return [WEEKDAY_NAMES[d] for d in (weekdays or ())]


def dump_sessions(sessions: Sequence[str] | None) -> list[str]:
    return list(sessions or ())
