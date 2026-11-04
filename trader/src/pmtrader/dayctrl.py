"""Today/tomorrow overlay on the saved hour schedule.

Stop a day or skip individual hours without rewriting config.toml.
Today expires at local midnight. Tomorrow's overlay becomes today then.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

Clock = Callable[[], datetime]


def empty_day_payload() -> dict[str, Any]:
    today = datetime.now().date()
    return {
        "stopped": False,
        "skip_hours": [],
        "date": today.isoformat(),
        "status": "Trading",
        "tomorrow": {
            "stopped": False,
            "skip_hours": [],
            "date": (today + timedelta(days=1)).isoformat(),
            "status": "Trading tomorrow",
        },
    }


def _hours_set(raw: Any) -> set[int]:
    skip: set[int] = set()
    if not isinstance(raw, list):
        return skip
    for item in raw:
        try:
            value = int(item)
        except (TypeError, ValueError):
            continue
        if 0 <= value <= 23:
            skip.add(value)
    return skip


class DayControl:
    def __init__(self, path: Path, *, clock: Clock | None = None) -> None:
        self.path = Path(path)
        self._clock = clock or datetime.now
        self._lock = threading.Lock()
        self._stopped = False
        self._skip_hours: set[int] = set()
        self._tomorrow_stopped = False
        self._tomorrow_skip_hours: set[int] = set()
        self._date = ""
        self._load()
        with self._lock:
            self._expire_unlocked()

    def entry_block_reason(self, window_start_ts: float | None) -> str | None:
        with self._lock:
            self._expire_unlocked()
            if self._stopped:
                return "stopped_for_day"
            ts = window_start_ts if window_start_ts and window_start_ts > 0 else self._now().timestamp()
            hour = datetime.fromtimestamp(float(ts)).hour
            if hour in self._skip_hours:
                return "skipped_hour"
            return None

    def stop(self, when: str = "today") -> dict[str, Any]:
        return self._set_stopped(when, True)

    def start(self, when: str = "today") -> dict[str, Any]:
        return self._set_stopped(when, False)

    def toggle_hour(self, hour: int, when: str = "today") -> dict[str, Any]:
        hour = int(hour)
        if hour < 0 or hour > 23:
            raise ValueError("hour must be 0-23")
        layer = self._parse_when(when)
        with self._lock:
            self._expire_unlocked()
            target = self._skip_hours if layer == "today" else self._tomorrow_skip_hours
            if hour in target:
                target.discard(hour)
            else:
                target.add(hour)
            self._save_unlocked()
            return self._payload_unlocked()

    def payload(self) -> dict[str, Any]:
        with self._lock:
            self._expire_unlocked()
            return self._payload_unlocked()

    def _set_stopped(self, when: str, stopped: bool) -> dict[str, Any]:
        layer = self._parse_when(when)
        with self._lock:
            self._expire_unlocked()
            if layer == "today":
                self._stopped = stopped
            else:
                self._tomorrow_stopped = stopped
            self._save_unlocked()
            return self._payload_unlocked()

    def _parse_when(self, when: str | None) -> str:
        layer = (when or "today").strip().lower()
        if layer not in ("today", "tomorrow"):
            raise ValueError("when must be today or tomorrow")
        return layer

    def _now(self) -> datetime:
        return self._clock()

    def _today(self) -> str:
        return self._now().date().isoformat()

    def _tomorrow_date(self) -> str:
        return (self._now().date() + timedelta(days=1)).isoformat()

    def _load(self) -> None:
        if not self.path.is_file():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("day-control load failed: %s", exc)
            return
        if not isinstance(data, dict):
            return
        self._date = str(data.get("date") or "")
        self._stopped = bool(data.get("stopped"))
        self._skip_hours = _hours_set(data.get("skip_hours"))
        tomorrow = data.get("tomorrow") if isinstance(data.get("tomorrow"), dict) else {}
        self._tomorrow_stopped = bool(tomorrow.get("stopped"))
        self._tomorrow_skip_hours = _hours_set(tomorrow.get("skip_hours"))

    def _save_unlocked(self) -> None:
        payload = {
            "date": self._date,
            "stopped": self._stopped,
            "skip_hours": sorted(self._skip_hours),
            "tomorrow": {
                "stopped": self._tomorrow_stopped,
                "skip_hours": sorted(self._tomorrow_skip_hours),
            },
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload) + "\n", encoding="utf-8")
        tmp.replace(self.path)

    def _expire_unlocked(self) -> None:
        today = self._today()
        if self._date == today:
            return
        stored = self._date
        if stored and stored == (self._now().date() - timedelta(days=1)).isoformat():
            self._stopped = self._tomorrow_stopped
            self._skip_hours = set(self._tomorrow_skip_hours)
        else:
            self._stopped = False
            self._skip_hours = set()
        self._tomorrow_stopped = False
        self._tomorrow_skip_hours = set()
        self._date = today
        try:
            self._save_unlocked()
        except OSError as exc:
            logger.warning("day-control save failed: %s", exc)

    def _payload_unlocked(self) -> dict[str, Any]:
        skipped = sorted(self._skip_hours)
        tomorrow_skipped = sorted(self._tomorrow_skip_hours)
        if self._stopped:
            status = "Off until midnight"
        elif skipped:
            label = ", ".join(f"{h:02d}" for h in skipped)
            status = f"Trading · skipped {label}"
        else:
            status = "Trading"
        if self._tomorrow_stopped:
            tomorrow_status = "Off tomorrow"
        elif tomorrow_skipped:
            label = ", ".join(f"{h:02d}" for h in tomorrow_skipped)
            tomorrow_status = f"Tomorrow · skipped {label}"
        else:
            tomorrow_status = "Trading tomorrow"
        return {
            "stopped": self._stopped,
            "skip_hours": skipped,
            "date": self._date,
            "status": status,
            "tomorrow": {
                "stopped": self._tomorrow_stopped,
                "skip_hours": tomorrow_skipped,
                "date": self._tomorrow_date(),
                "status": tomorrow_status,
            },
        }
