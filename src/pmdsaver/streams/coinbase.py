"""Coinbase Advanced Trade websocket streams."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from pmdsaver.streams.base import ReconnectingWebSocket

logger = logging.getLogger(__name__)

COINBASE_WS_URL = "wss://advanced-trade-ws.coinbase.com"
COINBASE_REST = "https://api.exchange.coinbase.com"

PriceTickCallback = Callable[[dict[str, Any]], Awaitable[None]]
CandleVolumeCallback = Callable[[dict[str, Any]], Awaitable[None]]


class CoinbaseSpotStream:
    def __init__(
        self,
        on_price_tick: PriceTickCallback,
        on_candle_volume: CandleVolumeCallback,
    ) -> None:
        self.on_price_tick = on_price_tick
        self.on_candle_volume = on_candle_volume
        self.latest_price: str | None = None
        self.latest_volume_base: str | None = None
        self._ws: ReconnectingWebSocket | None = None
        self._http = httpx.AsyncClient(timeout=10.0)

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
            await ws.send(
                json.dumps(
                    {
                        "type": "subscribe",
                        "product_ids": ["BTC-USD"],
                        "channel": "candles",
                        "granularity": "FIVE_MINUTE",
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
        await self._http.aclose()

    async def fetch_candle_fallback(self, open_time_s: int) -> None:
        try:
            response = await self._http.get(
                f"{COINBASE_REST}/products/BTC-USD/candles",
                params={"granularity": 300, "start": open_time_s, "end": open_time_s + 300},
            )
            response.raise_for_status()
            candles = response.json()
            if not candles:
                return
            candle = candles[0]
            # [time, low, high, open, close, volume]
            base_volume = str(candle[5])
            self.latest_volume_base = base_volume
            await self.on_candle_volume(
                {
                    "recv_ts_ms": int(time.time() * 1000),
                    "source": "coinbase_spot",
                    "open_time_ms": int(candle[0]) * 1000,
                    "base_volume": base_volume,
                    "quote_volume": None,
                    "is_closed": False,
                }
            )
        except Exception:
            logger.exception("Coinbase candle REST fallback failed")

    async def _handle_message(self, message: dict[str, Any]) -> None:
        channel = message.get("channel")
        if channel == "market_trades":
            await self._handle_market_trades(message)
        elif channel == "candles":
            await self._handle_candles(message)

    async def _handle_market_trades(self, message: dict[str, Any]) -> None:
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

    async def _handle_candles(self, message: dict[str, Any]) -> None:
        for event in message.get("events") or []:
            for candle in event.get("candles") or []:
                open_time_ms = parse_iso_ms(candle.get("start"))
                if open_time_ms is None:
                    continue
                base_volume = str(candle.get("volume") or "0")
                self.latest_volume_base = base_volume
                await self.on_candle_volume(
                    {
                        "recv_ts_ms": int(time.time() * 1000),
                        "source": "coinbase_spot",
                        "open_time_ms": open_time_ms,
                        "base_volume": base_volume,
                        "quote_volume": None,
                        "is_closed": bool(candle.get("complete")),
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
