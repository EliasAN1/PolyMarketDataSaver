"""Bybit spot public trade websocket stream."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from pmdsaver.streams.base import ReconnectingWebSocket

logger = logging.getLogger(__name__)

BYBIT_SPOT_URL = "wss://stream.bybit.com/v5/public/spot"
IDLE_TIMEOUT_S = 30.0

PriceTickCallback = Callable[[dict[str, Any]], Awaitable[None]]


class BybitSpotStream:
    def __init__(self, on_price_tick: PriceTickCallback) -> None:
        self.on_price_tick = on_price_tick
        self.latest_price: str | None = None
        self._ws: ReconnectingWebSocket | None = None

    def start(self) -> None:
        if self._ws is not None:
            return

        async def subscribe(ws: Any) -> None:
            await ws.send(
                json.dumps({"op": "subscribe", "args": ["publicTrade.BTCUSDT"]})
            )

        self._ws = ReconnectingWebSocket(
            name="bybit-spot",
            url=BYBIT_SPOT_URL,
            on_message=self._handle_message,
            subscribe=subscribe,
            idle_timeout_s=IDLE_TIMEOUT_S,
        )
        self._ws.start()

    async def stop(self) -> None:
        if self._ws is not None:
            await self._ws.stop()
            self._ws = None

    async def _handle_message(self, message: dict[str, Any]) -> None:
        topic = message.get("topic")
        if topic != "publicTrade.BTCUSDT":
            return
        for trade in message.get("data") or []:
            price = str(trade.get("p") or trade.get("price"))
            size = str(trade.get("v") or trade.get("size") or "")
            self.latest_price = price
            exchange_ts_ms = None
            ts = trade.get("T") or trade.get("time")
            if ts is not None:
                exchange_ts_ms = int(ts)
                if exchange_ts_ms < 10_000_000_000:
                    exchange_ts_ms *= 1000
            await self.on_price_tick(
                {
                    "recv_ts_ms": int(time.time() * 1000),
                    "exchange_ts_ms": exchange_ts_ms,
                    "source": "bybit_spot",
                    "price": price,
                    "size": size or None,
                }
            )
