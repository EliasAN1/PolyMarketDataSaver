"""Polymarket CLOB market websocket for UP/DOWN odds.

Official market channel (public, no auth):
  wss://ws-subscriptions-clob.polymarket.com/ws/market

Subscribe immediately after connect with token IDs (assets_ids), keep the
socket alive with text PING every 10s, and rebuild top-of-book from REST
GET/POST /book(s) on subscribe and whenever the socket goes quiet.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import httpx

from pmdsaver.streams.base import ReconnectingWebSocket

logger = logging.getLogger(__name__)

CLOB_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
CLOB_REST_URL = "https://clob.polymarket.com/book"
CLOB_REST_BOOKS_URL = "https://clob.polymarket.com/books"

OddsTickCallback = Callable[[dict[str, Any]], Awaitable[None]]

EMPTY_BID = Decimal("0.01")
EMPTY_ASK = Decimal("0.99")
MAX_MID_SPREAD = Decimal("0.15")
REST_GUARD_SECONDS = 1.5
WS_STALE_MS = 2_500

CAMEL_TO_SNAKE = {
    "tokenId": "token_id",
    "assetId": "asset_id",
    "bestBid": "best_bid",
    "bestAsk": "best_ask",
    "priceChanges": "price_changes",
    "eventType": "event_type",
    "lastTradePrice": "last_trade_price",
}


@dataclass
class TokenBook:
    best_bid: str | None = None
    best_ask: str | None = None
    last_valid_mid: str | None = None
    last_trade_price: str | None = None
    last_trade_size: str | None = None
    last_trade_side: str | None = None
    has_snapshot: bool = False

    def apply_top_of_book(self, best_bid: str | None, best_ask: str | None) -> None:
        if best_bid is not None and best_ask is not None and is_placeholder_book(best_bid, best_ask):
            return
        if best_bid is not None:
            self.best_bid = best_bid
        if best_ask is not None:
            self.best_ask = best_ask

    def mid(self) -> str | None:
        computed = compute_mid(self.best_bid, self.best_ask)
        if computed is not None:
            self.last_valid_mid = computed
            return computed
        return self.last_valid_mid


@dataclass
class OrderBookState:
    up_token_id: str
    down_token_id: str
    books: dict[str, TokenBook] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.up_token_id:
            self.books[self.up_token_id] = TokenBook()
        if self.down_token_id:
            self.books[self.down_token_id] = TokenBook()

    def set_tokens(self, up_token_id: str, down_token_id: str) -> None:
        self.up_token_id = up_token_id
        self.down_token_id = down_token_id
        self.books.setdefault(up_token_id, TokenBook())
        self.books.setdefault(down_token_id, TokenBook())

    def book_for(self, token_id: str) -> TokenBook:
        book = self.books.get(token_id)
        if book is None:
            book = TokenBook()
            self.books[token_id] = book
        return book

    def snapshot(self, event_type: str) -> dict[str, Any] | None:
        if not self.up_token_id or not self.down_token_id:
            return None
        up = self.books[self.up_token_id]
        down = self.books[self.down_token_id]
        up_mid = up.mid()
        down_mid = down.mid()
        if up_mid is None and down_mid is None:
            return None
        return {
            "recv_ts_ms": int(time.time() * 1000),
            "event_type": event_type,
            "up_bid": up.best_bid,
            "up_ask": up.best_ask,
            "up_mid": up_mid,
            "down_bid": down.best_bid,
            "down_ask": down.best_ask,
            "down_mid": down_mid,
            "last_trade_token": None,
            "last_trade_price": None,
            "last_trade_size": None,
            "last_trade_side": None,
        }


class ClobOddsStream:
    def __init__(self, on_tick: OddsTickCallback) -> None:
        self.on_tick = on_tick
        self.state = OrderBookState(up_token_id="", down_token_id="")
        self._ws: ReconnectingWebSocket | None = None
        self._current_ws: Any | None = None
        self._subscribed_tokens: set[str] = set()
        self._socket_tokens: set[str] = set()
        self._tokens_ready = asyncio.Event()
        self._sync_lock = asyncio.Lock()
        self._last_ws_event_ms: int | None = None
        self._http = httpx.AsyncClient(timeout=10.0)
        self._guard_task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    def set_tokens(self, up_token_id: str, down_token_id: str) -> None:
        self.state.set_tokens(up_token_id, down_token_id)
        self._subscribed_tokens = {up_token_id, down_token_id}
        self._tokens_ready.set()
        self._schedule_sync()

    def prefetch_tokens(self, up_token_id: str, down_token_id: str) -> None:
        """Subscribe to upcoming window tokens before rollover."""
        self.state.book_for(up_token_id)
        self.state.book_for(down_token_id)
        self._subscribed_tokens.update({up_token_id, down_token_id})
        self._tokens_ready.set()
        self._schedule_sync()

    def _schedule_sync(self) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self._sync_subscriptions(), name="clob-sync")

    async def _wait_for_tokens(self) -> None:
        while not self._stop.is_set() and not self._tokens_ready.is_set():
            try:
                await asyncio.wait_for(self._tokens_ready.wait(), timeout=0.2)
            except asyncio.TimeoutError:
                continue

    async def bootstrap_from_rest(self) -> None:
        tokens = [t for t in (self.state.up_token_id, self.state.down_token_id) if t]
        if not tokens:
            return
        updated = False
        try:
            response = await self._http.post(
                CLOB_REST_BOOKS_URL,
                json=[{"token_id": token_id} for token_id in tokens],
            )
            if response.status_code == 200:
                payload = response.json()
                books = payload if isinstance(payload, list) else [payload]
                for book_payload in books:
                    if not isinstance(book_payload, dict):
                        continue
                    token_id = str(
                        book_payload.get("asset_id")
                        or book_payload.get("token_id")
                        or book_payload.get("tokenId")
                        or ""
                    )
                    if token_id and await self._apply_rest_book(token_id, book_payload):
                        updated = True
            elif response.status_code in {400, 404}:
                logger.debug("CLOB REST /books: %s (market may not be live yet)", response.status_code)
            else:
                logger.warning("CLOB REST /books failed: %s", response.status_code)
                for token_id in tokens:
                    if await self._bootstrap_one_book(token_id):
                        updated = True
        except Exception:
            logger.exception("CLOB REST /books bootstrap failed")
            for token_id in tokens:
                if await self._bootstrap_one_book(token_id):
                    updated = True
        if updated:
            tick = self.state.snapshot("book_rest")
            if tick is not None:
                await self.on_tick(tick)

    async def _bootstrap_one_book(self, token_id: str) -> bool:
        try:
            response = await self._http.get(CLOB_REST_URL, params={"token_id": token_id})
            if response.status_code != 200:
                logger.warning(
                    "CLOB REST book unavailable for %s: %s",
                    token_id[:12],
                    response.status_code,
                )
                return False
            return await self._apply_rest_book(token_id, response.json())
        except Exception:
            logger.exception("CLOB REST bootstrap failed for %s", token_id[:12])
            return False

    async def _apply_rest_book(self, token_id: str, payload: dict[str, Any]) -> bool:
        book = self.state.book_for(token_id)
        bid = best_level_price(payload.get("bids") or [], side="bid")
        ask = best_level_price(payload.get("asks") or [], side="ask")
        if bid is None and ask is None:
            return False
        if bid is not None and ask is not None and is_placeholder_book(bid, ask):
            return False
        book.apply_top_of_book(bid, ask)
        book.has_snapshot = True
        return True

    async def _sync_subscriptions(self) -> None:
        ws = self._current_ws
        if ws is None:
            return
        async with self._sync_lock:
            wanted = {token for token in self._subscribed_tokens if token}
            if not wanted:
                return
            add = wanted - self._socket_tokens
            drop = self._socket_tokens - wanted
            try:
                if add:
                    await ws.send(
                        json.dumps(
                            {
                                "assets_ids": list(add),
                                "operation": "subscribe",
                                "custom_feature_enabled": True,
                            }
                        )
                    )
                    self._socket_tokens |= add
                    logger.info("CLOB subscribed +%s tokens", len(add))
                if drop:
                    await ws.send(
                        json.dumps({"assets_ids": list(drop), "operation": "unsubscribe"})
                    )
                    self._socket_tokens -= drop
                    logger.info("CLOB unsubscribed -%s tokens", len(drop))
            except Exception:
                logger.warning("CLOB live subscribe failed; will retry on reconnect")
                return
            await self.bootstrap_from_rest()

    def start(self) -> None:
        if self._ws is not None:
            return
        self._stop.clear()

        async def subscribe(ws: Any) -> None:
            self._current_ws = ws
            tokens = [token for token in self._subscribed_tokens if token]
            if not tokens:
                return
            payload = {
                "type": "market",
                "assets_ids": tokens,
                "custom_feature_enabled": True,
                "initial_dump": True,
            }
            await ws.send(json.dumps(payload))
            self._socket_tokens = set(tokens)
            logger.info("CLOB market subscribe %s tokens", len(tokens))
            await self.bootstrap_from_rest()

        self._ws = ReconnectingWebSocket(
            name="polymarket-clob",
            url=CLOB_WS_URL,
            on_message=self._handle_message,
            ping_interval_s=10.0,
            ping_payload="PING",
            subscribe=subscribe,
            before_connect=self._wait_for_tokens,
            idle_timeout_s=20.0,
            min_backoff_s=0.5,
            max_backoff_s=5.0,
        )
        self._ws.start()
        self._guard_task = asyncio.create_task(self._rest_guard_loop(), name="clob-rest-guard")

    async def stop(self) -> None:
        self._stop.set()
        self._tokens_ready.set()
        if self._guard_task is not None:
            self._guard_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._guard_task
            self._guard_task = None
        if self._ws is not None:
            await self._ws.stop()
            self._ws = None
        self._current_ws = None
        self._socket_tokens.clear()
        await self._http.aclose()

    async def _rest_guard_loop(self) -> None:
        while not self._stop.is_set():
            await asyncio.sleep(REST_GUARD_SECONDS)
            if not self.state.up_token_id:
                continue
            now_ms = int(time.time() * 1000)
            stale = (
                self._last_ws_event_ms is None
                or now_ms - self._last_ws_event_ms >= WS_STALE_MS
            )
            if stale:
                await self.bootstrap_from_rest()

    async def _handle_message(self, message: dict[str, Any]) -> None:
        message = normalize_market_event(message)
        event_type = message.get("event_type") or message.get("type")
        if event_type is None:
            return

        exchange_ts_ms = parse_timestamp_ms(message.get("timestamp"))
        if event_type == "book":
            await self._handle_book(message, exchange_ts_ms)
        elif event_type == "price_change":
            await self._handle_price_change(message, exchange_ts_ms)
        elif event_type == "last_trade_price":
            await self._handle_last_trade(message, exchange_ts_ms)
        elif event_type == "best_bid_ask":
            await self._handle_best_bid_ask(message, exchange_ts_ms)

    def _mark_ws_event(self) -> None:
        self._last_ws_event_ms = int(time.time() * 1000)
        if self._ws is not None:
            self._ws.mark_data()

    def _is_current_token(self, token_id: str) -> bool:
        return token_id in {self.state.up_token_id, self.state.down_token_id}

    async def _emit_if_current(self, event_type: str, exchange_ts_ms: int | None) -> None:
        tick = self.state.snapshot(event_type)
        if tick is None:
            return
        tick["exchange_ts_ms"] = exchange_ts_ms
        await self.on_tick(tick)

    async def _handle_book(self, message: dict[str, Any], exchange_ts_ms: int | None) -> None:
        asset_id = asset_id_of(message)
        if not asset_id:
            return
        book = self.state.book_for(asset_id)
        self._mark_ws_event()
        bid = best_level_price(message.get("bids") or [], side="bid")
        ask = best_level_price(message.get("asks") or [], side="ask")
        if bid is None and ask is None:
            book.has_snapshot = True
            return
        if bid is not None and ask is not None and is_placeholder_book(bid, ask):
            book.has_snapshot = True
            return
        book.apply_top_of_book(bid, ask)
        book.has_snapshot = True
        if self._is_current_token(asset_id):
            await self._emit_if_current("book", exchange_ts_ms)

    async def _handle_price_change(
        self,
        message: dict[str, Any],
        exchange_ts_ms: int | None,
    ) -> None:
        changes = message.get("price_changes") or message.get("changes") or []
        if isinstance(message.get("asset_id"), str) or isinstance(message.get("token_id"), str):
            if not changes:
                changes = [message]

        updated_current = False
        for change in changes:
            if not isinstance(change, dict):
                continue
            change = normalize_market_event(change)
            asset_id = asset_id_of(change)
            if not asset_id:
                continue
            book = self.state.book_for(asset_id)
            self._mark_ws_event()
            if not book.has_snapshot and not (
                change.get("best_bid") is not None and change.get("best_ask") is not None
            ):
                continue
            bid = str(change["best_bid"]) if change.get("best_bid") is not None else book.best_bid
            ask = str(change["best_ask"]) if change.get("best_ask") is not None else book.best_ask
            if bid is not None and ask is not None and is_placeholder_book(bid, ask):
                continue
            book.apply_top_of_book(
                str(change["best_bid"]) if change.get("best_bid") is not None else None,
                str(change["best_ask"]) if change.get("best_ask") is not None else None,
            )
            book.has_snapshot = True
            if self._is_current_token(asset_id):
                updated_current = True

        if updated_current:
            await self._emit_if_current("price_change", exchange_ts_ms)

    async def _handle_last_trade(
        self,
        message: dict[str, Any],
        exchange_ts_ms: int | None,
    ) -> None:
        asset_id = asset_id_of(message)
        if not asset_id:
            return
        book = self.state.book_for(asset_id)
        self._mark_ws_event()
        price = message.get("price")
        if price is not None:
            book.last_trade_price = str(price)
            book.last_trade_size = (
                str(message["size"]) if message.get("size") is not None else None
            )
            book.last_trade_side = (
                str(message["side"]) if message.get("side") is not None else None
            )
        if not self._is_current_token(asset_id):
            return
        tick = self.state.snapshot("last_trade_price")
        if tick is None:
            return
        tick["exchange_ts_ms"] = exchange_ts_ms
        tick["last_trade_token"] = asset_id
        tick["last_trade_price"] = book.last_trade_price
        tick["last_trade_size"] = book.last_trade_size
        tick["last_trade_side"] = book.last_trade_side
        await self.on_tick(tick)

    async def _handle_best_bid_ask(
        self,
        message: dict[str, Any],
        exchange_ts_ms: int | None,
    ) -> None:
        asset_id = asset_id_of(message)
        if not asset_id:
            return
        book = self.state.book_for(asset_id)
        self._mark_ws_event()
        bid = str(message["best_bid"]) if message.get("best_bid") is not None else book.best_bid
        ask = str(message["best_ask"]) if message.get("best_ask") is not None else book.best_ask
        if bid is not None and ask is not None and is_placeholder_book(bid, ask):
            return
        book.apply_top_of_book(
            str(message["best_bid"]) if message.get("best_bid") is not None else None,
            str(message["best_ask"]) if message.get("best_ask") is not None else None,
        )
        book.has_snapshot = True
        if self._is_current_token(asset_id):
            await self._emit_if_current("best_bid_ask", exchange_ts_ms)


def normalize_market_event(message: dict[str, Any]) -> dict[str, Any]:
    payload = message.get("payload")
    if isinstance(payload, dict) and message.get("event_type") is None:
        merged = dict(payload)
        event_type = message.get("type")
        if event_type and "event_type" not in merged:
            merged["event_type"] = event_type
        message = merged
    out: dict[str, Any] = {}
    for key, value in message.items():
        out[CAMEL_TO_SNAKE.get(key, key)] = value
    if not out.get("asset_id") and out.get("token_id"):
        out["asset_id"] = out["token_id"]
    changes = out.get("price_changes")
    if isinstance(changes, list):
        out["price_changes"] = [
            normalize_market_event(item) if isinstance(item, dict) else item for item in changes
        ]
    return out


def asset_id_of(message: dict[str, Any]) -> str:
    return str(message.get("asset_id") or message.get("token_id") or "")


def is_placeholder_book(best_bid: str, best_ask: str) -> bool:
    bid = Decimal(best_bid)
    ask = Decimal(best_ask)
    if bid <= EMPTY_BID and ask >= EMPTY_ASK:
        return True
    return ask - bid > MAX_MID_SPREAD


def compute_mid(best_bid: str | None, best_ask: str | None) -> str | None:
    if best_bid is None or best_ask is None:
        return None
    if is_placeholder_book(best_bid, best_ask):
        return None
    bid = Decimal(best_bid)
    ask = Decimal(best_ask)
    return str((bid + ask) / 2)


def level_price(level: Any) -> Decimal | None:
    if isinstance(level, dict):
        price = level.get("price")
    elif isinstance(level, (list, tuple)) and level:
        price = level[0]
    else:
        return None
    try:
        return Decimal(str(price))
    except Exception:
        return None


def best_level_price(levels: list[Any], side: str) -> str | None:
    if not levels:
        return None
    prices = [p for p in (level_price(level) for level in levels) if p is not None]
    if not prices:
        return None
    if side == "bid":
        return str(max(prices))
    return str(min(prices))


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
