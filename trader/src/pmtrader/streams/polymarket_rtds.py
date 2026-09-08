"""Polymarket RTDS Chainlink 60s TWAP stream."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from pmtrader.clock import WINDOW_SECONDS
from pmtrader.streams.base import ReconnectingWebSocket

logger = logging.getLogger(__name__)

RTDS_WS_URL = "wss://ws-live-data.polymarket.com"
TWAP_LOOKBACK_SECONDS = 60
# Official strike is the 60s TWAP at 5m open. Ticks after this are a later TWAP.
PTB_CAPTURE_WINDOW_SECONDS = 45
# Chainlink prints about once a second. PING keeps a dead socket "connected"
# with no data — that is how 16:00–16:35 lost every PTB (zero TWAP ticks).
RTDS_IDLE_TIMEOUT_S = 15.0
TWAP_STALE_AFTER_S = 5.0

TwapTickCallback = Callable[[dict[str, Any]], Awaitable[None]]
PtbCapturedCallback = Callable[[int, str], Awaitable[None]]


class RtdsTwapStream:
    def __init__(
        self,
        on_tick: TwapTickCallback,
        on_ptb_captured: PtbCapturedCallback | None = None,
    ) -> None:
        self.on_tick = on_tick
        self.on_ptb_captured = on_ptb_captured
        self.latest_value: str | None = None
        self.latest_ts_ms: int | None = None
        self._ptb_by_window: dict[int, str] = {}
        self._miss_logged: set[int] = set()
        self._ws: ReconnectingWebSocket | None = None

    def price_to_beat_for(self, window_start: int) -> str | None:
        return self._ptb_by_window.get(window_start)

    def seed_price_to_beat(self, window_start: int, value: str) -> None:
        """Record a backfilled strike so later lookups match a live capture."""
        if window_start not in self._ptb_by_window:
            self._ptb_by_window[window_start] = value

    def twap_age_s(self, now_s: float | None = None) -> float | None:
        if self.latest_ts_ms is None:
            return None
        now = time.time() if now_s is None else now_s
        return now - self.latest_ts_ms / 1000.0

    def wait_reason(self, window_start: int, now_s: float | None = None) -> str:
        """Why this window still has no RTDS strike (for logs / UI)."""
        if window_start in self._ptb_by_window:
            return "captured"
        now = time.time() if now_s is None else now_s
        age = self.twap_age_s(now)
        elapsed = now - window_start
        if age is None:
            return "RTDS: no TWAP ticks yet"
        if age > TWAP_STALE_AFTER_S:
            return f"RTDS silent {age:.0f}s"
        if elapsed > PTB_CAPTURE_WINDOW_SECONDS:
            return f"missed open TWAP (elapsed {elapsed:.0f}s)"
        return "waiting for open TWAP"

    def capture_open_if_fresh(self, window_start: int, now_s: float) -> str | None:
        """Copy the live 60s TWAP as the open strike if the feed is healthy."""
        existing = self._ptb_by_window.get(window_start)
        if existing is not None:
            return existing
        offset = now_s - window_start
        age = self.twap_age_s(now_s)
        if self.latest_value is None or age is None:
            if offset > PTB_CAPTURE_WINDOW_SECONDS:
                self._log_miss(window_start, "no TWAP ticks this window")
            return None
        if age > TWAP_STALE_AFTER_S:
            self._log_miss(window_start, f"TWAP feed stale ({age:.0f}s since last tick)")
            return None
        if offset > PTB_CAPTURE_WINDOW_SECONDS:
            self._log_miss(
                window_start,
                f"elapsed {offset:.0f}s; open print never arrived",
            )
            return None
        self._ptb_by_window[window_start] = self.latest_value
        logger.info(
            "Snapshotted open TWAP as PTB for window %s: %s (tick age %.2fs, elapsed %.1fs)",
            window_start,
            self.latest_value,
            age,
            offset,
        )
        return self.latest_value

    def start(self) -> None:
        if self._ws is not None:
            return

        async def subscribe(ws: Any) -> None:
            payload = {
                "action": "subscribe",
                "subscriptions": [
                    {
                        "topic": "crypto_prices_twap_sixty",
                        "type": "update",
                        "filters": '{"symbol":"btc/usd"}',
                    }
                ],
            }
            await ws.send(json.dumps(payload))

        self._ws = ReconnectingWebSocket(
            name="polymarket-rtds-twap",
            url=RTDS_WS_URL,
            on_message=self._handle_message,
            ping_interval_s=5.0,
            ping_payload="PING",
            subscribe=subscribe,
            idle_timeout_s=RTDS_IDLE_TIMEOUT_S,
        )
        self._ws.start()

    async def stop(self) -> None:
        if self._ws is not None:
            await self._ws.stop()
            self._ws = None

    async def _handle_message(self, message: dict[str, Any]) -> None:
        topic = message.get("topic")
        if topic not in {
            "crypto_prices_twap_sixty",
            "prices.crypto.chainlink.twap",
        }:
            return

        payload = message.get("payload") or {}
        symbol = str(payload.get("symbol") or "").lower()
        if symbol and symbol != "btc/usd":
            return

        window_s = payload.get("window_s") or payload.get("windowSeconds")
        if window_s is not None and int(window_s) != TWAP_LOOKBACK_SECONDS:
            return

        value = payload.get("value")
        if value is None:
            value = payload.get("full_accuracy_value")
        if value is None:
            return

        value_str = str(value)
        exchange_ts_ms = parse_timestamp_ms(payload.get("timestamp"))
        recv_ts_ms = int(time.time() * 1000)
        self.latest_value = value_str
        self.latest_ts_ms = recv_ts_ms

        # Assign the strike to the wall-clock window we are in. Payload
        # timestamps lag a couple of seconds and used to attach the open
        # print to the previous 5m, so this window never got a PTB.
        captured_window = self._capture_price_to_beat(recv_ts_ms, value_str)
        if captured_window is not None and self.on_ptb_captured is not None:
            await self.on_ptb_captured(captured_window, value_str)

        await self.on_tick(
            {
                "recv_ts_ms": recv_ts_ms,
                "exchange_ts_ms": exchange_ts_ms,
                "symbol": symbol or "btc/usd",
                "value": value_str,
                "window_seconds": TWAP_LOOKBACK_SECONDS,
            }
        )

    def _capture_price_to_beat(self, recv_ts_ms: int, value: str) -> int | None:
        now_s = recv_ts_ms / 1000.0
        window_start = int(now_s) - (int(now_s) % WINDOW_SECONDS)
        offset_s = now_s - window_start
        if window_start in self._ptb_by_window:
            return None
        if offset_s > PTB_CAPTURE_WINDOW_SECONDS:
            self._log_miss(
                window_start,
                f"first tick at +{offset_s:.0f}s (need ≤{PTB_CAPTURE_WINDOW_SECONDS}s)",
            )
            return None
        self._ptb_by_window[window_start] = value
        logger.info(
            "Captured 60s TWAP price-to-beat for window %s: %s (elapsed %.1fs)",
            window_start,
            value,
            offset_s,
        )
        return window_start

    def _log_miss(self, window_start: int, why: str) -> None:
        if window_start in self._miss_logged:
            return
        self._miss_logged.add(window_start)
        logger.warning("No RTDS PTB for window %s: %s", window_start, why)


def parse_timestamp_ms(value: Any) -> int | None:
    if value is None:
        return None
    try:
        ts = int(value)
    except (TypeError, ValueError):
        return None
    if ts < 10_000_000_000:
        return ts * 1000
    return ts
