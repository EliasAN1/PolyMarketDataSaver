"""Binance spot and USDT-M futures price streams (no candle volume)."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from pmtrader.streams.base import ReconnectingWebSocket

logger = logging.getLogger(__name__)

BINANCE_SPOT_URL = "wss://stream.binance.com:9443/ws/btcusdt@aggTrade"
BINANCE_FUTURES_URL = "wss://fstream.binance.com/ws/btcusdt@aggTrade"
BINANCE_FUTURES_REST = "https://fapi.binance.com/fapi/v1/ticker/price"
BINANCE_KLINES = "https://api.binance.com/api/v3/klines"
BINANCE_FUTURES_KLINES = "https://fapi.binance.com/fapi/v1/klines"
BYBIT_KLINES = "https://api.bybit.com/v5/market/kline"
COINBASE_CANDLES = "https://api.exchange.coinbase.com/products/BTC-USD/candles"
# BTC trade streams tick many times per second; 30s of silence means a dead socket.
IDLE_TIMEOUT_S = 30.0

PriceTickCallback = Callable[[dict[str, Any]], Awaitable[None]]


async def fetch_spot_open_at(open_time_s: int) -> str | None:
    """1m candle open at the 5m window start — late-join PTB fallback."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                BINANCE_KLINES,
                params={
                    "symbol": "BTCUSDT",
                    "interval": "1m",
                    "startTime": open_time_s * 1000,
                    "limit": 1,
                },
            )
            response.raise_for_status()
            rows = response.json()
            if isinstance(rows, list) and rows:
                return str(rows[0][1])
    except Exception:
        logger.exception("Binance 1m open fetch failed for %s", open_time_s)
    return None


async def fetch_prev_5m_delta(window_start_s: int) -> float | None:
    """Close minus open of the closed 5m BTCUSDT candle [window_start - 300, window_start]."""
    prev_open_ms = (int(window_start_s) - 300) * 1000
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    BINANCE_KLINES,
                    params={
                        "symbol": "BTCUSDT",
                        "interval": "5m",
                        "startTime": prev_open_ms,
                        "limit": 1,
                    },
                )
                response.raise_for_status()
                rows = response.json()
                if isinstance(rows, list) and rows and int(rows[0][0]) == prev_open_ms:
                    return round(float(rows[0][4]) - float(rows[0][1]), 2)
        except Exception:
            logger.exception("Binance 5m kline fetch failed for %s", window_start_s - 300)
        if attempt < 2:
            await asyncio.sleep(0.4)
    return None


async def fetch_initial_venue_basis(window_start_s: int) -> dict[str, float]:
    """60m (12-candle) median USDT-to-USD basis for Binance Spot, Bybit Spot, and Binance Futures."""
    import statistics

    start_s = int(window_start_s) - 3600
    end_s = int(window_start_s)
    start_ms = start_s * 1000
    end_ms = end_s * 1000 - 1
    out: dict[str, float] = {}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            cb_res, bin_res, by_res, fut_res = await asyncio.gather(
                client.get(
                    COINBASE_CANDLES,
                    params={"granularity": 300, "start": start_s, "end": end_s},
                ),
                client.get(
                    BINANCE_KLINES,
                    params={"symbol": "BTCUSDT", "interval": "5m", "startTime": start_ms, "endTime": end_ms, "limit": 12},
                ),
                client.get(
                    BYBIT_KLINES,
                    params={"category": "spot", "symbol": "BTCUSDT", "interval": "5", "start": start_ms, "end": end_ms, "limit": 12},
                ),
                client.get(
                    BINANCE_FUTURES_KLINES,
                    params={"symbol": "BTCUSDT", "interval": "5m", "startTime": start_ms, "endTime": end_ms, "limit": 12},
                ),
                return_exceptions=True,
            )
        if isinstance(cb_res, Exception):
            return out
        cb_rows = cb_res.json()
        if not isinstance(cb_rows, list):
            return out
        cb_by_open = {int(r[0]): float(r[4]) for r in cb_rows if isinstance(r, list) and len(r) >= 5 and int(r[0]) < end_s}
        if not cb_by_open:
            return out

        if not isinstance(bin_res, Exception):
            bin_rows = bin_res.json()
            if isinstance(bin_rows, list):
                diffs = [float(r[4]) - cb_by_open[int(r[0]) // 1000] for r in bin_rows if (int(r[0]) // 1000) in cb_by_open]
                if diffs:
                    out["binance_spot"] = round(float(statistics.median(diffs)), 2)

        if not isinstance(by_res, Exception):
            by_rows = (by_res.json().get("result") or {}).get("list") or []
            if isinstance(by_rows, list):
                diffs = [float(r[4]) - cb_by_open[int(r[0]) // 1000] for r in by_rows if (int(r[0]) // 1000) in cb_by_open]
                if diffs:
                    out["bybit_spot"] = round(float(statistics.median(diffs)), 2)

        if not isinstance(fut_res, Exception):
            fut_rows = fut_res.json()
            if isinstance(fut_rows, list):
                diffs = [float(r[4]) - cb_by_open[int(r[0]) // 1000] for r in fut_rows if (int(r[0]) // 1000) in cb_by_open]
                if diffs:
                    out["binance_futures"] = round(float(statistics.median(diffs)), 2)
    except Exception:
        logger.exception("Initial venue basis bootstrap failed")
    return out


class BinanceSpotStream:
    def __init__(self, on_price_tick: PriceTickCallback) -> None:
        self.on_price_tick = on_price_tick
        self.latest_price: str | None = None
        self._ws: ReconnectingWebSocket | None = None

    def start(self) -> None:
        if self._ws is not None:
            return
        self._ws = ReconnectingWebSocket(
            name="binance-spot",
            url=BINANCE_SPOT_URL,
            on_message=self._handle_message,
            idle_timeout_s=IDLE_TIMEOUT_S,
        )
        self._ws.start()

    async def stop(self) -> None:
        if self._ws is not None:
            await self._ws.stop()
            self._ws = None

    async def _handle_message(self, message: dict[str, Any]) -> None:
        data = message.get("data") or message
        if data.get("e") != "aggTrade":
            return
        price = str(data["p"])
        self.latest_price = price
        await self.on_price_tick(
            {
                "recv_ts_ms": int(time.time() * 1000),
                "exchange_ts_ms": int(data.get("T") or data.get("E") or 0),
                "source": "binance_spot",
                "price": price,
                "size": str(data["q"]),
            }
        )


class BinanceFuturesStream:
    def __init__(self, on_price_tick: PriceTickCallback) -> None:
        self.on_price_tick = on_price_tick
        self.latest_price: str | None = None
        self._ws: ReconnectingWebSocket | None = None
        self._poll_task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._http = httpx.AsyncClient(timeout=10.0)
        self._last_ws_ms: int | None = None

    def start(self) -> None:
        if self._ws is not None:
            return
        self._stop.clear()
        # No idle watchdog here: the futures socket is blocked on some networks and
        # the REST poll below already covers it; a watchdog would reconnect forever.
        self._ws = ReconnectingWebSocket(
            name="binance-futures",
            url=BINANCE_FUTURES_URL,
            on_message=self._handle_message,
        )
        self._ws.start()
        self._poll_task = asyncio.create_task(
            self._rest_fallback_loop(),
            name="binance-futures-rest",
        )

    async def stop(self) -> None:
        self._stop.set()
        if self._poll_task is not None:
            self._poll_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._poll_task
            self._poll_task = None
        if self._ws is not None:
            await self._ws.stop()
            self._ws = None
        await self._http.aclose()

    async def _handle_message(self, message: dict[str, Any]) -> None:
        data = message.get("data") or message
        if data.get("e") != "aggTrade":
            return
        self._last_ws_ms = int(time.time() * 1000)
        await self._emit_price(str(data["p"]), str(data["q"]), int(data.get("T") or data.get("E") or 0))

    async def _rest_fallback_loop(self) -> None:
        warned = False
        while not self._stop.is_set():
            now_ms = int(time.time() * 1000)
            ws_recent = self._last_ws_ms is not None and now_ms - self._last_ws_ms < 15_000
            if not ws_recent:
                try:
                    response = await self._http.get(
                        BINANCE_FUTURES_REST,
                        params={"symbol": "BTCUSDT"},
                    )
                    response.raise_for_status()
                    payload = response.json()
                    await self._emit_price(
                        str(payload["price"]),
                        None,
                        int(payload.get("time") or now_ms),
                    )
                    if not warned:
                        logger.warning("Binance futures websocket unavailable; using REST fallback")
                        warned = True
                except Exception:
                    logger.exception("Binance futures REST fallback failed")
            await asyncio.sleep(1)

    async def _emit_price(self, price: str, size: str | None, exchange_ts_ms: int) -> None:
        self.latest_price = price
        await self.on_price_tick(
            {
                "recv_ts_ms": int(time.time() * 1000),
                "exchange_ts_ms": exchange_ts_ms,
                "source": "binance_futures",
                "price": price,
                "size": size,
            }
        )
