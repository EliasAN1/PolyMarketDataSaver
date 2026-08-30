"""Coinbase Advanced Trade price stream (trades only)."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from pmtrader.streams.base import ReconnectingWebSocket

logger = logging.getLogger(__name__)

COINBASE_WS_URL = "wss://advanced-trade-ws.coinbase.com"

PriceTickCallback = Callable[[dict[str, Any]], Awaitable[None]]


class CoinbaseSpotStream:
    def __init__(self, on_price_tick: PriceTickCallback) -> None:
        self.on_price_tick = on_price_tick
        self.latest_price: str | None = None
        self._ws: ReconnectingWebSocket | None = None

    def start(self) -> None:
        if self._ws is not None:
            return

        async def subscribe(ws: Any) -> None:
            await ws.send(
                json.dumps(
                    {
                        "type": "subscribe",
                        "product_ids": ["BTC-USD"],
                        "channel": "market_trades",
                    }
                )
            )

        self._ws = ReconnectingWebSocket(
            name="coinbase-spot",
            url=COINBASE_WS_URL,
            on_message=self._handle_message,
            subscribe=subscribe,
        )
        self._ws.start()

    async def stop(self) -> None:
        if self._ws is not None:
            await self._ws.stop()
            self._ws = None

    async def _handle_message(self, message: dict[str, Any]) -> None:
        if message.get("channel") != "market_trades":
            return
        for event in message.get("events") or []:
            for trade in event.get("trades") or []:
                price = str(trade.get("price"))
                size = str(trade.get("size")) if trade.get("size") is not None else None
                self.latest_price = price
                await self.on_price_tick(
                    {
                        "recv_ts_ms": int(time.time() * 1000),
                        "exchange_ts_ms": parse_iso_ms(trade.get("time")),
                        "source": "coinbase_spot",
                        "price": price,
                        "size": size,
                    }
                )


def parse_iso_ms(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        ts = int(value)
        return ts * 1000 if ts < 10_000_000_000 else ts
    text = str(value)
    if text.isdigit():
        ts = int(text)
        return ts * 1000 if ts < 10_000_000_000 else ts
    try:
        from datetime import datetime

        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return int(dt.timestamp() * 1000)
    except ValueError:
        return None
