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
PTB_CAPTURE_WINDOW_SECONDS = 45

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
        self._ws: ReconnectingWebSocket | None = None

    def price_to_beat_for(self, window_start: int) -> str | None:
        return self._ptb_by_window.get(window_start)

    def seed_price_to_beat(self, window_start: int, value: str) -> None:
        """Record a backfilled strike so later lookups match a live capture."""
        if window_start not in self._ptb_by_window:
            self._ptb_by_window[window_start] = value

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
        obs_ts_ms = exchange_ts_ms or recv_ts_ms
        self.latest_value = value_str
        self.latest_ts_ms = obs_ts_ms

        captured_window = self._capture_price_to_beat(obs_ts_ms, value_str)
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

    def _capture_price_to_beat(self, obs_ts_ms: int, value: str) -> int | None:
        obs_ts_s = obs_ts_ms // 1000
        window_start = obs_ts_s - (obs_ts_s % WINDOW_SECONDS)
        offset_s = obs_ts_s - window_start
        if offset_s > PTB_CAPTURE_WINDOW_SECONDS:
            return None
        if window_start in self._ptb_by_window:
            return None
        self._ptb_by_window[window_start] = value
        logger.info(
            "Captured 60s TWAP price-to-beat for window %s: %s (offset %ss)",
            window_start,
            value,
            offset_s,
        )
        return window_start


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
