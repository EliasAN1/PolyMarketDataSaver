"""UTC-aligned 5-minute window helpers for Polymarket BTC UP/DOWN markets."""

from __future__ import annotations

import time
from dataclasses import dataclass

WINDOW_SECONDS = 300
ASSET = "btc"


@dataclass(frozen=True, slots=True)
class Window:
    start: int
    end: int
    slug: str

    @property
    def seconds_remaining(self) -> int:
        return max(0, self.end - int(time.time()))


def floor_window_start(timestamp: float | int | None = None) -> int:
    ts = int(timestamp if timestamp is not None else time.time())
    return ts - (ts % WINDOW_SECONDS)


def window_from_start(start: int) -> Window:
    return Window(start=start, end=start + WINDOW_SECONDS, slug=slug_for_start(start))


def current_window(timestamp: float | int | None = None) -> Window:
    return window_from_start(floor_window_start(timestamp))


def next_window(timestamp: float | int | None = None) -> Window:
    return window_from_start(floor_window_start(timestamp) + WINDOW_SECONDS)


def slug_for_start(start: int) -> str:
    return f"{ASSET}-updown-5m-{start}"


def window_from_slug(slug: str) -> Window | None:
    prefix = f"{ASSET}-updown-5m-"
    if not slug.startswith(prefix):
        return None
    try:
        start = int(slug[len(prefix) :])
    except ValueError:
        return None
    return window_from_start(start)
