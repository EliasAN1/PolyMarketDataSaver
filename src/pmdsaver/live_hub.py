"""In-memory live state shared by the collector and dashboard."""

from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

_MAX_QUEUE = 8
_MAX_RECENT = 24
_MAX_CHART = 800
_CHART_MIN_MS = 150
_ODDS_MOVE = 0.005
_PRICE_MOVE = 1.0
_TWAP_MOVE = 1.0
_VOLUME_MOVE = 0.01
_PRICE_SOURCES = ("binance_spot", "binance_futures", "coinbase_spot", "bybit_spot")


@dataclass(eq=False)
class LiveSubscriber:
    queue: asyncio.Queue[dict[str, Any]]
    epoch: int = -1
    last_odds_ms: int = 0
    last_twap_ms: int = 0
    last_volume_ms: int = 0
    last_price_ms: dict[str, int] = field(
        default_factory=lambda: {source: 0 for source in _PRICE_SOURCES}
    )


class LiveHub:
    def __init__(self) -> None:
        self.connected = False
        self._epoch = 0
        self.snapshot: dict[str, Any] = {
            "connected": False,
            "window": None,
            "latest_odds": None,
            "latest_prices": {},
            "latest_volume": {},
            "counts": {"odds_ticks": 0, "price_ticks": 0, "twap_ticks": 0},
            "ingest": {
                "odds_per_sec": 0,
                "prices_per_sec": 0,
                "twap_per_sec": 0,
                "prices_by_source": {},
            },
            "server_ts_ms": 0,
        }
        self.recent_odds: deque[dict[str, Any]] = deque(maxlen=_MAX_RECENT)
        self.chart_odds: deque[dict[str, Any]] = deque(maxlen=_MAX_CHART)
        self.chart_prices: dict[str, deque[dict[str, Any]]] = {
            source: deque(maxlen=_MAX_CHART) for source in _PRICE_SOURCES
        }
        self.chart_twap: deque[dict[str, Any]] = deque(maxlen=_MAX_CHART)
        self.chart_volume: deque[dict[str, Any]] = deque(maxlen=_MAX_CHART)
        self._odds_times: deque[int] = deque()
        self._price_times: deque[int] = deque()
        self._price_times_by_source: dict[str, deque[int]] = {
            source: deque() for source in _PRICE_SOURCES
        }
        self._twap_times: deque[int] = deque()
        self._subscribers: set[LiveSubscriber] = set()
        self._dirty = asyncio.Event()

    def subscribe(self) -> LiveSubscriber:
        sub = LiveSubscriber(queue=asyncio.Queue(maxsize=_MAX_QUEUE))
        self._subscribers.add(sub)
        return sub

    def unsubscribe(self, sub: LiveSubscriber) -> None:
        self._subscribers.discard(sub)

    def acknowledge_snapshot(self, sub: LiveSubscriber, payload: dict[str, Any] | None = None) -> None:
        if payload is None:
            self._sync_cursors(sub)
            return
        sub.epoch = int(payload.get("epoch") or self._epoch)
        sub.last_odds_ms = _last_ts_list(payload.get("chart_odds") or [])
        sub.last_twap_ms = _last_ts_list(payload.get("chart_twap") or [])
        sub.last_volume_ms = _last_ts_list(payload.get("chart_volume") or [])
        prices = payload.get("chart_prices") or {}
        for source in _PRICE_SOURCES:
            sub.last_price_ms[source] = _last_ts_list(prices.get(source) or [])

    def mark_connected(self, connected: bool) -> None:
        self.connected = connected
        self.snapshot["connected"] = connected
        self._dirty.set()

    def set_window(self, window: dict[str, Any]) -> None:
        self._epoch += 1
        self.snapshot["window"] = window
        self.snapshot["counts"] = {"odds_ticks": 0, "price_ticks": 0, "twap_ticks": 0}
        self.snapshot["latest_odds"] = None
        self.snapshot["latest_volume"] = {}
        self.recent_odds.clear()
        self.chart_odds.clear()
        self.chart_twap.clear()
        self.chart_volume.clear()
        for buf in self.chart_prices.values():
            buf.clear()
        self._dirty.set()

    def update_window_fields(self, **fields: Any) -> None:
        window = dict(self.snapshot.get("window") or {})
        window.update(fields)
        self.snapshot["window"] = window or None
        self._dirty.set()

    def on_odds(self, tick: dict[str, Any]) -> None:
        self.snapshot["latest_odds"] = tick
        self.snapshot["counts"]["odds_ticks"] += 1
        self.recent_odds.appendleft(
            {
                "recv_ts_ms": tick.get("recv_ts_ms"),
                "event_type": tick.get("event_type"),
                "up_mid": tick.get("up_mid"),
                "last_trade_price": tick.get("last_trade_price"),
                "last_trade_side": tick.get("last_trade_side"),
            }
        )
        if tick.get("up_mid") is not None:
            point = {
                "recv_ts_ms": tick.get("recv_ts_ms"),
                "up_mid": tick.get("up_mid"),
                "up_bid": tick.get("up_bid"),
                "up_ask": tick.get("up_ask"),
            }
            self._maybe_append(
                self.chart_odds,
                point,
                value_key="up_mid",
                min_move=_ODDS_MOVE,
            )
        self._note(self._odds_times)
        self._dirty.set()

    def on_price(self, tick: dict[str, Any]) -> None:
        source = str(tick.get("source") or "")
        prices = dict(self.snapshot.get("latest_prices") or {})
        prices[source] = tick
        self.snapshot["latest_prices"] = prices
        self.snapshot["counts"]["price_ticks"] += 1
        buf = self.chart_prices.get(source)
        if buf is not None:
            self._maybe_append(
                buf,
                {"recv_ts_ms": tick.get("recv_ts_ms"), "price": tick.get("price")},
                value_key="price",
                min_move=_PRICE_MOVE,
            )
        self._note(self._price_times)
        if source in self._price_times_by_source:
            self._note(self._price_times_by_source[source])
        self._dirty.set()

    def on_volume(self, tick: dict[str, Any]) -> None:
        volume = dict(self.snapshot.get("latest_volume") or {})
        volume[tick["source"]] = tick
        self.snapshot["latest_volume"] = volume
        if tick.get("source") == "binance_spot" and tick.get("base_volume") is not None:
            self._maybe_append(
                self.chart_volume,
                {
                    "recv_ts_ms": tick.get("recv_ts_ms"),
                    "value": tick.get("base_volume"),
                },
                value_key="value",
                min_move=_VOLUME_MOVE,
            )
        self._dirty.set()

    def on_twap(self, tick: dict[str, Any]) -> None:
        self.snapshot["counts"]["twap_ticks"] += 1
        self._maybe_append(
            self.chart_twap,
            {"recv_ts_ms": tick.get("recv_ts_ms"), "value": tick.get("value")},
            value_key="value",
            min_move=_TWAP_MOVE,
        )
        self._note(self._twap_times)
        self._dirty.set()

    def snapshot_payload(self) -> dict[str, Any]:
        now_ms = int(time.time() * 1000)
        window = self.snapshot.get("window")
        if window and window.get("window_end"):
            window = dict(window)
            window["seconds_remaining"] = max(0, int(window["window_end"]) - int(time.time()))
            window["price_to_beat"] = window.get("price_to_beat_gamma") or window.get(
                "price_to_beat_rtds"
            )
        rates = {
            source: self._rate(times)
            for source, times in self._price_times_by_source.items()
        }
        return {
            "connected": self.connected,
            "server_ts_ms": now_ms,
            "epoch": self._epoch,
            "window": window,
            "latest_odds": self.snapshot.get("latest_odds"),
            "latest_prices": self.snapshot.get("latest_prices") or {},
            "latest_volume": self.snapshot.get("latest_volume") or {},
            "counts": self.snapshot.get("counts") or {},
            "ingest": {
                "odds_per_sec": self._rate(self._odds_times),
                "prices_per_sec": self._rate(self._price_times),
                "twap_per_sec": self._rate(self._twap_times),
                "prices_by_source": rates,
            },
        }

    def full_message(self) -> dict[str, Any]:
        payload = self.snapshot_payload()
        payload["type"] = "snapshot"
        payload["recent_odds"] = list(self.recent_odds)
        payload["chart_odds"] = list(self.chart_odds)
        payload["chart_twap"] = list(self.chart_twap)
        payload["chart_volume"] = list(self.chart_volume)
        payload["chart_prices"] = {
            source: list(buf) for source, buf in self.chart_prices.items()
        }
        return payload

    def build_message(self, sub: LiveSubscriber) -> dict[str, Any]:
        if sub.epoch != self._epoch:
            payload = self.full_message()
            self._sync_cursors(sub)
            return payload
        payload = self.snapshot_payload()
        payload["type"] = "delta"
        odds, sub.last_odds_ms = _since(self.chart_odds, sub.last_odds_ms)
        twap, sub.last_twap_ms = _since(self.chart_twap, sub.last_twap_ms)
        volume, sub.last_volume_ms = _since(self.chart_volume, sub.last_volume_ms)
        prices: dict[str, list[dict[str, Any]]] = {}
        for source, buf in self.chart_prices.items():
            pts, sub.last_price_ms[source] = _since(buf, sub.last_price_ms.get(source, 0))
            if pts:
                prices[source] = pts
        payload["append_odds"] = odds
        payload["append_twap"] = twap
        payload["append_volume"] = volume
        payload["append_prices"] = prices
        payload["append_events"] = list(self.recent_odds)[:8]
        return payload

    def _sync_cursors(self, sub: LiveSubscriber) -> None:
        sub.epoch = self._epoch
        sub.last_odds_ms = _last_ts(self.chart_odds)
        sub.last_twap_ms = _last_ts(self.chart_twap)
        sub.last_volume_ms = _last_ts(self.chart_volume)
        for source, buf in self.chart_prices.items():
            sub.last_price_ms[source] = _last_ts(buf)

    def _maybe_append(
        self,
        buf: deque[dict[str, Any]],
        point: dict[str, Any],
        *,
        value_key: str,
        min_move: float,
    ) -> None:
        ts = int(point.get("recv_ts_ms") or 0)
        if not buf:
            buf.append(point)
            return
        last = buf[-1]
        last_ts = int(last.get("recv_ts_ms") or 0)
        if ts - last_ts >= _CHART_MIN_MS:
            buf.append(point)
            return
        if _moved(last.get(value_key), point.get(value_key), min_move):
            buf.append(point)

    def _note(self, times: deque[int]) -> None:
        now = time.monotonic()
        times.append(int(now * 1000))
        cutoff = int((now - 1) * 1000)
        while times and times[0] < cutoff:
            times.popleft()

    def _rate(self, times: deque[int]) -> int:
        now = int(time.monotonic() * 1000)
        cutoff = now - 1000
        while times and times[0] < cutoff:
            times.popleft()
        return len(times)

    async def publisher_loop(self, stop: asyncio.Event) -> None:
        """Push coalesced deltas; snapshots only on connect or window change."""
        while not stop.is_set():
            try:
                await asyncio.wait_for(self._dirty.wait(), timeout=0.2)
            except asyncio.TimeoutError:
                continue
            # Wait out the coalesce window so one WS message covers a burst of ticks.
            await asyncio.sleep(0.15)
            self._dirty.clear()
            if not self._subscribers:
                continue
            dead: list[LiveSubscriber] = []
            for sub in list(self._subscribers):
                message = self.build_message(sub)
                if sub.queue.full():
                    try:
                        sub.queue.get_nowait()
                    except asyncio.QueueEmpty:
                        pass
                try:
                    sub.queue.put_nowait(message)
                except asyncio.QueueFull:
                    dead.append(sub)
            for sub in dead:
                self._subscribers.discard(sub)


def _last_ts_list(points: list[dict[str, Any]]) -> int:
    if not points:
        return 0
    return int(points[-1].get("recv_ts_ms") or 0)


def _last_ts(buf: deque[dict[str, Any]]) -> int:
    if not buf:
        return 0
    return int(buf[-1].get("recv_ts_ms") or 0)


def _since(buf: deque[dict[str, Any]], last_ms: int) -> tuple[list[dict[str, Any]], int]:
    out: list[dict[str, Any]] = []
    max_ms = last_ms
    for point in buf:
        ts = int(point.get("recv_ts_ms") or 0)
        if ts > last_ms:
            out.append(point)
            if ts > max_ms:
                max_ms = ts
    return out, max_ms


def _moved(prev: Any, current: Any, min_move: float) -> bool:
    try:
        return abs(float(current) - float(prev)) >= min_move
    except (TypeError, ValueError):
        return current is not None and current != prev


HUB = LiveHub()
