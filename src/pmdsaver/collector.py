"""Main collector orchestrating all data feeds."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

from pmdsaver.clock import WINDOW_SECONDS, Window, current_window, next_window, window_from_slug, window_from_start
from pmdsaver.db import Database, WindowRow
from pmdsaver.gamma import GammaClient, MarketInfo, extract_final_price, extract_price_to_beat, extract_resolved_outcome
from pmdsaver.live_hub import HUB, LiveHub
from pmdsaver.streams.binance import BinanceFuturesStream, BinanceSpotStream, fetch_spot_open_at
from pmdsaver.streams.bybit import BybitSpotStream
from pmdsaver.streams.coinbase import CoinbaseSpotStream
from pmdsaver.streams.polymarket_clob import ClobOddsStream
from pmdsaver.streams.polymarket_rtds import PTB_CAPTURE_WINDOW_SECONDS, RtdsTwapStream

logger = logging.getLogger(__name__)

ROLLOVER_LEAD_SECONDS = 30
GAMMA_POLL_SECONDS = 2
MARKET_POLL_SECONDS = 0.25
STATUS_INTERVAL_SECONDS = 1
SETTLE_FAST_RETRY_SECONDS = 120
SETTLE_RECONCILE_SECONDS = 15
SETTLE_RECONCILE_BATCH = 4


@dataclass
class LiveStatus:
    slug: str = "-"
    seconds_left: int = 0
    price_to_beat_rtds: str | None = None
    price_to_beat_gamma: str | None = None
    price_to_beat_source: str | None = None
    up_mid: str | None = None
    binance_spot: str | None = None
    binance_futures: str | None = None
    coinbase_spot: str | None = None
    bybit_spot: str | None = None
    binance_volume_base: str | None = None
    coinbase_volume_base: str | None = None


@dataclass
class Collector:
    db: Database
    gamma: GammaClient
    status: LiveStatus = field(default_factory=LiveStatus)
    current: Window = field(default_factory=current_window)
    current_market: MarketInfo | None = None
    next_market: MarketInfo | None = None
    hub: LiveHub = field(default_factory=lambda: HUB)
    _stop: asyncio.Event = field(default_factory=asyncio.Event)
    _tasks: list[asyncio.Task] = field(default_factory=list)
    _pending_settle: Window | None = None

    def __post_init__(self) -> None:
        self.rtds = RtdsTwapStream(
            on_tick=self._on_twap_tick,
            on_ptb_captured=self._on_ptb_captured,
        )
        self.clob = ClobOddsStream(on_tick=self._on_odds_tick)
        self.binance_spot = BinanceSpotStream(
            on_price_tick=self._on_price_tick,
            on_candle_volume=self._on_candle_volume,
        )
        self.binance_futures = BinanceFuturesStream(on_price_tick=self._on_price_tick)
        self.coinbase = CoinbaseSpotStream(
            on_price_tick=self._on_price_tick,
            on_candle_volume=self._on_candle_volume,
        )
        self.bybit = BybitSpotStream(on_price_tick=self._on_price_tick)

    async def run(self) -> None:
        await self.db.open()
        self.hub.mark_connected(True)
        self.rtds.start()
        self.clob.start()
        self.binance_spot.start()
        self.binance_futures.start()
        self.coinbase.start()
        self.bybit.start()

        self._tasks = [
            asyncio.create_task(self._window_loop(), name="window-loop"),
            asyncio.create_task(self._gamma_poll_loop(), name="gamma-poll"),
            asyncio.create_task(self._status_loop(), name="status-loop"),
            asyncio.create_task(self._coinbase_volume_fallback_loop(), name="coinbase-fallback"),
            asyncio.create_task(self.hub.publisher_loop(self._stop), name="live-hub"),
        ]

        try:
            await self._stop.wait()
        finally:
            await self.shutdown()

    async def shutdown(self) -> None:
        if getattr(self, "_shutdown_done", False):
            return
        self._shutdown_done = True
        self._stop.set()
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
            self._tasks.clear()
        await self.rtds.stop()
        await self.clob.stop()
        await self.binance_spot.stop()
        await self.binance_futures.stop()
        await self.coinbase.stop()
        await self.bybit.stop()
        await self.gamma.close()
        await self.db.close()
        self.hub.mark_connected(False)

    def request_stop(self) -> None:
        self._stop.set()

    def _window_id(self) -> int | None:
        return self.db.window_id(self.current.slug)

    async def _window_loop(self) -> None:
        while not self._stop.is_set():
            now_window = current_window()
            if now_window.slug != self.current.slug:
                closed = self.current
                self.current = now_window
                self._pending_settle = closed
                await self._try_settle_window(closed)
                if (
                    self.next_market is not None
                    and self.next_market.window.slug == now_window.slug
                ):
                    await self._activate_market(self.next_market)
                    self.next_market = None
                else:
                    self.current_market = None

            await self._ensure_current_market()
            if self.current.seconds_remaining <= ROLLOVER_LEAD_SECONDS:
                await self._prefetch_next_market()
            sleep_s = MARKET_POLL_SECONDS if self.current_market is None else 1.0
            await asyncio.sleep(sleep_s)

    async def _ensure_current_market(self) -> None:
        if self.current_market and self.current_market.window.slug == self.current.slug:
            return

        if self.next_market and self.next_market.window.slug == self.current.slug:
            await self._activate_market(self.next_market)
            self.next_market = None
            return

        market = await self.gamma.fetch_market(self.current)
        if market is None:
            logger.info("Waiting for Gamma event %s", self.current.slug)
            return

        await self._activate_market(market)

    async def _prefetch_next_market(self) -> None:
        upcoming = next_window()
        if self.next_market and self.next_market.window.slug == upcoming.slug:
            return
        market = await self.gamma.fetch_market(upcoming)
        if market is not None:
            self.next_market = market
            self.clob.prefetch_tokens(market.up_token_id, market.down_token_id)
            logger.info("Prefetched next market %s", upcoming.slug)

    async def _activate_market(self, market: MarketInfo) -> None:
        self.current = market.window
        self.current_market = market
        self.status.slug = market.window.slug
        self.status.seconds_left = market.window.seconds_remaining
        self.status.price_to_beat_gamma = market.price_to_beat_gamma
        self.status.price_to_beat_rtds = None
        self.status.price_to_beat_source = None

        runtime_ptb, source = await self._resolve_runtime_ptb(market)
        self.status.price_to_beat_rtds = runtime_ptb
        self.status.price_to_beat_source = source

        row = WindowRow(
            slug=market.window.slug,
            window_start=market.window.start,
            window_end=market.window.end,
            condition_id=market.condition_id,
            up_token_id=market.up_token_id,
            down_token_id=market.down_token_id,
            price_to_beat_rtds=runtime_ptb,
            price_to_beat_gamma=market.price_to_beat_gamma,
            price_to_beat_source=source,
        )
        await self.db.upsert_window(row)
        stored_rtds, stored_gamma = await self.db.load_window_ptb(market.window.slug)
        if stored_rtds and runtime_ptb is None:
            self.status.price_to_beat_rtds = stored_rtds
            runtime_ptb = stored_rtds
            self.status.price_to_beat_source = "db"
            self.rtds.seed_price_to_beat(market.window.start, stored_rtds)
        if stored_gamma and self.status.price_to_beat_gamma is None:
            self.status.price_to_beat_gamma = stored_gamma

        self.clob.set_tokens(market.up_token_id, market.down_token_id)
        await self.clob.bootstrap_from_rest()
        self.hub.set_window(
            {
                "id": self._window_id(),
                "slug": market.window.slug,
                "window_start": market.window.start,
                "window_end": market.window.end,
                "price_to_beat_rtds": runtime_ptb,
                "price_to_beat_gamma": self.status.price_to_beat_gamma,
                "price_to_beat_source": self.status.price_to_beat_source,
            }
        )
        logger.info(
            "Active window %s | PTB %s (%s) | up=%s down=%s",
            market.window.slug,
            runtime_ptb or "-",
            self.status.price_to_beat_source or "waiting",
            market.up_token_id[:12],
            market.down_token_id[:12],
        )

    async def _resolve_runtime_ptb(self, market: MarketInfo) -> tuple[str | None, str | None]:
        window = market.window
        live = self.rtds.price_to_beat_for(window.start)
        if live is not None:
            return live, "rtds"
        if market.price_to_beat_gamma is not None:
            return market.price_to_beat_gamma, "gamma"

        stored_rtds, stored_gamma = await self.db.load_window_ptb(window.slug)
        if stored_rtds:
            self.rtds.seed_price_to_beat(window.start, stored_rtds)
            return stored_rtds, "db"
        if stored_gamma:
            return stored_gamma, "gamma"

        elapsed = WINDOW_SECONDS - window.seconds_remaining
        if elapsed <= PTB_CAPTURE_WINDOW_SECONDS:
            return None, None

        prev = window_from_start(window.start - WINDOW_SECONDS)
        prev_event = await self.gamma.fetch_event(prev, missing_ok=True)
        final = extract_final_price(prev_event) if prev_event else None
        if final is not None:
            self.rtds.seed_price_to_beat(window.start, final)
            logger.info("PTB from previous window Chainlink close: %s", final)
            return final, "previous_final"

        open_px = await fetch_spot_open_at(window.start)
        if open_px is not None:
            self.rtds.seed_price_to_beat(window.start, open_px)
            logger.info("PTB from Binance 1m open (late join): %s", open_px)
            return open_px, "binance_open"
        return None, None

    async def _try_settle_window(self, window: Window) -> bool:
        event = await self.gamma.fetch_event(window, missing_ok=True)
        if event is None:
            return False
        gamma_ptb = extract_price_to_beat(event)
        final = extract_final_price(event)
        outcome = extract_resolved_outcome(event)
        if gamma_ptb is not None:
            await self.db.update_window_price_to_beat(
                window.slug,
                price_to_beat_gamma=gamma_ptb,
            )
        if outcome is None:
            return False
        await self.db.set_window_settlement(
            window.slug,
            final_price=final,
            outcome=outcome,
            price_to_beat_gamma=gamma_ptb,
            outcome_source="polymarket",
        )
        logger.info(
            "Settled %s outcome=%s final=%s ptb=%s source=polymarket",
            window.slug,
            outcome,
            final or "-",
            gamma_ptb or "-",
        )
        return True

    async def _reconcile_unsettled(self) -> None:
        slugs = await self.db.list_unsettled_slugs(
            before_ts=int(time.time()),
            limit=SETTLE_RECONCILE_BATCH,
        )
        for slug in slugs:
            window = window_from_slug(slug)
            if window is None:
                continue
            try:
                await self._try_settle_window(window)
            except Exception:
                logger.exception("Failed reconciling settlement for %s", slug)

    async def _gamma_poll_loop(self) -> None:
        last_reconcile = 0.0
        while not self._stop.is_set():
            if self._pending_settle is not None:
                settled = await self._try_settle_window(self._pending_settle)
                if settled or (time.time() - self._pending_settle.end > SETTLE_FAST_RETRY_SECONDS):
                    self._pending_settle = None

            now = time.time()
            if now - last_reconcile >= SETTLE_RECONCILE_SECONDS:
                last_reconcile = now
                await self._reconcile_unsettled()

            if self.current_market is None:
                await asyncio.sleep(GAMMA_POLL_SECONDS)
                continue

            slug = self.current.slug
            if self.status.price_to_beat_gamma is None:
                ptb = await self.gamma.fetch_price_to_beat(self.current)
                if ptb is not None:
                    self.status.price_to_beat_gamma = ptb
                    await self.db.update_window_price_to_beat(
                        slug,
                        price_to_beat_gamma=ptb,
                    )
                    self.hub.update_window_fields(price_to_beat_gamma=ptb)
                    logger.info("Gamma priceToBeat for %s: %s", slug, ptb)

            if self.status.price_to_beat_rtds is None:
                ptb = self.rtds.price_to_beat_for(self.current.start)
                source = "rtds"
                if ptb is None:
                    ptb, source = await self._resolve_runtime_ptb(self.current_market)
                if ptb is not None:
                    self.status.price_to_beat_rtds = ptb
                    self.status.price_to_beat_source = source
                    await self.db.set_window_price_to_beat_rtds(slug, ptb, source=source)
                    self.hub.update_window_fields(
                        price_to_beat_rtds=ptb,
                        price_to_beat_source=source,
                    )

            await asyncio.sleep(GAMMA_POLL_SECONDS)

    async def _coinbase_volume_fallback_loop(self) -> None:
        last_open_time_s: int | None = None
        while not self._stop.is_set():
            open_time_s = self.current.start
            if (
                self.coinbase.latest_volume_base is None
                and last_open_time_s != open_time_s
            ):
                await self.coinbase.fetch_candle_fallback(open_time_s)
                last_open_time_s = open_time_s
            await asyncio.sleep(30)

    async def _status_loop(self) -> None:
        while not self._stop.is_set():
            self.status.seconds_left = self.current.seconds_remaining
            self.status.binance_spot = self.binance_spot.latest_price
            self.status.binance_futures = self.binance_futures.latest_price
            self.status.coinbase_spot = self.coinbase.latest_price
            self.status.bybit_spot = self.bybit.latest_price
            self.status.binance_volume_base = self.binance_spot.latest_volume_base
            self.status.coinbase_volume_base = self.coinbase.latest_volume_base
            up_mid = self.clob.state.books.get(self.clob.state.up_token_id)
            self.status.up_mid = up_mid.mid() if up_mid else None

            line = (
                f"{self.status.slug} | t-{self.status.seconds_left}s | "
                f"PTB rtds={self.status.price_to_beat_rtds or '-'} "
                f"gamma={self.status.price_to_beat_gamma or '-'} | "
                f"Up mid={self.status.up_mid or '-'} | "
                f"BN spot={self.status.binance_spot or '-'} "
                f"fut={self.status.binance_futures or '-'} | "
                f"CB={self.status.coinbase_spot or '-'} "
                f"BY={self.status.bybit_spot or '-'} | "
                f"vol BN={self.status.binance_volume_base or '-'} "
                f"CB={self.status.coinbase_volume_base or '-'}"
            )
            print(line, flush=True)
            await asyncio.sleep(STATUS_INTERVAL_SECONDS)

    async def _on_odds_tick(self, tick: dict) -> None:
        window_id = self._window_id()
        if window_id is None:
            return
        self.hub.on_odds(tick)
        await self.db.enqueue_odds_tick(
            (
                window_id,
                tick["recv_ts_ms"],
                tick.get("exchange_ts_ms"),
                tick["event_type"],
                tick.get("up_bid"),
                tick.get("up_ask"),
                tick.get("up_mid"),
                tick.get("down_bid"),
                tick.get("down_ask"),
                tick.get("down_mid"),
                tick.get("last_trade_token"),
                tick.get("last_trade_price"),
                tick.get("last_trade_size"),
                tick.get("last_trade_side"),
            )
        )

    async def _on_price_tick(self, tick: dict) -> None:
        self.hub.on_price(tick)
        await self.db.enqueue_price_tick(
            (
                tick["recv_ts_ms"],
                tick.get("exchange_ts_ms"),
                tick["source"],
                tick["price"],
                tick.get("size"),
                self._window_id(),
            )
        )

    async def _on_candle_volume(self, tick: dict) -> None:
        expected_open_ms = self.current.start * 1000
        if tick["open_time_ms"] != expected_open_ms:
            return
        self.hub.on_volume(tick)
        await self.db.enqueue_candle_volume(
            (
                tick["recv_ts_ms"],
                tick["source"],
                tick["open_time_ms"],
                tick.get("base_volume"),
                tick.get("quote_volume"),
                1 if tick.get("is_closed") else 0,
                self._window_id(),
            )
        )

    async def _on_ptb_captured(self, window_start: int, value: str) -> None:
        if self.current.start != window_start:
            return
        slug = self.current.slug
        self.status.price_to_beat_rtds = value
        self.status.price_to_beat_source = "rtds"
        self.hub.update_window_fields(price_to_beat_rtds=value, price_to_beat_source="rtds")
        if self.db.window_id(slug) is not None:
            await self.db.set_window_price_to_beat_rtds(slug, value, source="rtds")

    async def _on_twap_tick(self, tick: dict) -> None:
        self.hub.on_twap(tick)
        await self.db.enqueue_twap_tick(
            (
                tick["recv_ts_ms"],
                tick.get("exchange_ts_ms"),
                tick["symbol"],
                tick["value"],
                self._window_id(),
            )
        )
