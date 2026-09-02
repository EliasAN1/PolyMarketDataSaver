"""Window roll, live snapshot, and one FAK attempt per 5m market."""

from __future__ import annotations

import asyncio
import logging
import math
import sys
import time
from dataclasses import dataclass, field

from pmtrader.clock import (
    WINDOW_SECONDS,
    Window,
    current_window,
    next_window,
    window_from_slug,
    window_from_start,
)
from pmtrader.config import TraderConfig, env
from pmtrader.gamma import GammaClient, MarketInfo, extract_final_price, extract_resolved_outcome
from pmtrader.orders import OrderClient
from pmtrader.outcome import fetch_clob_odds, infer_outcome_from_clob
from pmtrader.snapshot import LiveSnapshot
from pmtrader.strategy import Decision, evaluate
from pmtrader.tradelog import entry_record, resolve_record, unresolved_entries
from pmtrader.ui.server import serve_background
from pmtrader.streams.binance import BinanceFuturesStream, BinanceSpotStream, fetch_spot_open_at
from pmtrader.streams.bybit import BybitSpotStream
from pmtrader.streams.coinbase import CoinbaseSpotStream
from pmtrader.streams.polymarket_clob import ClobOddsStream
from pmtrader.streams.polymarket_rtds import PTB_CAPTURE_WINDOW_SECONDS, RtdsTwapStream

logger = logging.getLogger(__name__)

ROLLOVER_LEAD_SECONDS = 30
MARKET_POLL_SECONDS = 0.25
SETTLE_SECONDS = 3
RESOLVE_AFTER_SECONDS = 3
# Lab tape row t holds the state just before elapsed t + 0.5; sampling at k + 0.45
# reproduces row t = k - window_start.
SAMPLE_OFFSET_S = 0.45


@dataclass
class Trader:
    cfg: TraderConfig
    orders: OrderClient
    gamma: GammaClient = field(default_factory=GammaClient)
    snap: LiveSnapshot = field(default_factory=LiveSnapshot)
    current: Window = field(default_factory=current_window)
    current_market: MarketInfo | None = None
    next_market: MarketInfo | None = None
    _stop: asyncio.Event = field(default_factory=asyncio.Event)
    _tasks: list[asyncio.Task] = field(default_factory=list)
    _order_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _traded_slug: str | None = None
    _last_decision: Decision | None = None

    def __post_init__(self) -> None:
        self.snap.btc_source = self.cfg.btc_source
        self.rtds = RtdsTwapStream(on_tick=self._on_twap, on_ptb_captured=self._on_ptb)
        self.clob = ClobOddsStream(on_tick=self._on_odds)
        self.binance_spot = BinanceSpotStream(on_price_tick=self._on_price)
        self.binance_futures = BinanceFuturesStream(on_price_tick=self._on_price)
        self.coinbase = CoinbaseSpotStream(on_price_tick=self._on_price)
        self.bybit = BybitSpotStream(on_price_tick=self._on_price)

    async def run(self) -> None:
        self.orders.connect()
        ui_host = env("UI_HOST") or "127.0.0.1"
        ui_port = int(env("UI_PORT") or "3848")
        serve_background(
            log_path=self.orders.log_path,
            order_client=self.orders.client,
            host=ui_host,
            port=ui_port,
            trader=self,
        )
        logger.info("UI http://%s:%s", ui_host, ui_port)
        self.rtds.start()
        self.clob.start()
        self.binance_spot.start()
        self.binance_futures.start()
        self.coinbase.start()
        self.bybit.start()
        self._tasks = [
            asyncio.create_task(self._window_loop(), name="window-loop"),
            asyncio.create_task(self._sample_loop(), name="sample-loop"),
            asyncio.create_task(self._status_loop(), name="status-loop"),
            asyncio.create_task(self._settle_loop(), name="settle-loop"),
        ]
        try:
            await self._stop.wait()
        finally:
            await self.shutdown()

    def request_stop(self) -> None:
        self._stop.set()

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
        sys.stdout.write("\n")
        sys.stdout.flush()

    async def _window_loop(self) -> None:
        while not self._stop.is_set():
            now_window = current_window()
            if now_window.slug != self.current.slug:
                self.current = now_window
                self._traded_slug = None
                self._last_decision = None
                if self.next_market is not None and self.next_market.window.slug == now_window.slug:
                    await self._activate_market(self.next_market)
                    self.next_market = None
                else:
                    self.current_market = None

            await self._ensure_current_market()
            if self.current.seconds_remaining <= ROLLOVER_LEAD_SECONDS:
                await self._prefetch_next_market()
            await asyncio.sleep(MARKET_POLL_SECONDS if self.current_market is None else 1.0)

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
        ptb, source = await self._resolve_ptb(market)
        self.snap.reset_window(
            slug=market.window.slug,
            window_start=market.window.start,
            window_end=market.window.end,
            up_token_id=market.up_token_id,
            down_token_id=market.down_token_id,
            ptb=_to_float(ptb),
            ptb_source=source,
        )
        self.clob.set_tokens(market.up_token_id, market.down_token_id)
        await self.clob.bootstrap_from_rest()
        logger.info(
            "Active %s | PTB %s (%s) | up=%s down=%s",
            market.window.slug,
            ptb or "-",
            source or "waiting",
            market.up_token_id[:12],
            market.down_token_id[:12],
        )

    async def _resolve_ptb(self, market: MarketInfo) -> tuple[str | None, str | None]:
        window = market.window
        live = self.rtds.price_to_beat_for(window.start)
        if live is not None:
            return live, "rtds"
        if market.price_to_beat_gamma is not None:
            return market.price_to_beat_gamma, "gamma"

        elapsed = WINDOW_SECONDS - window.seconds_remaining
        if elapsed <= PTB_CAPTURE_WINDOW_SECONDS:
            return None, None

        prev = window_from_start(window.start - WINDOW_SECONDS)
        prev_event = await self.gamma.fetch_event(prev, missing_ok=True)
        final = extract_final_price(prev_event) if prev_event else None
        if final is not None:
            self.rtds.seed_price_to_beat(window.start, final)
            return final, "previous_final"

        open_px = await fetch_spot_open_at(window.start)
        if open_px is not None:
            self.rtds.seed_price_to_beat(window.start, open_px)
            return open_px, "binance_open"
        return None, None

    async def _on_ptb(self, window_start: int, value: str) -> None:
        if self.current.start == window_start:
            self.snap.set_ptb(value, "rtds")

    async def _on_odds(self, tick: dict) -> None:
        self.snap.apply_odds(tick)

    async def _on_price(self, tick: dict) -> None:
        self.snap.apply_price(tick)

    async def _on_twap(self, tick: dict) -> None:
        self.snap.apply_twap(tick)

    async def _sample_loop(self) -> None:
        """Evaluate once per second at the Lab's tape boundary, not on every tick."""
        while not self._stop.is_set():
            now = time.time()
            target = math.floor(now) + SAMPLE_OFFSET_S
            if target <= now:
                target += 1.0
            await asyncio.sleep(target - now)
            try:
                await self._maybe_trade()
            except Exception:
                logger.exception("sample evaluation failed")

    async def _maybe_trade(self) -> None:
        if self._traded_slug == self.snap.slug:
            return
        decision = evaluate(self.snap, self.cfg, now_s=time.time())
        self._last_decision = decision
        if not decision.ok or decision.side is None:
            return
        async with self._order_lock:
            if self._traded_slug == self.snap.slug:
                return
            self._traded_slug = self.snap.slug
            token_id = self.snap.token_for(decision.side)
            result = await asyncio.to_thread(
                self.orders.place_fak_buy,
                token_id=token_id,
                side=decision.side,
                limit=decision.limit,
                stake_usd=self.cfg.stake_usd,
            )
            if result.ok:
                self.orders.append_log(
                    entry_record(
                        snap=self.snap,
                        side=decision.side,
                        limit=decision.limit,
                        stake_usd=self.cfg.stake_usd,
                        result=result,
                        now_s=time.time(),
                    )
                )
            else:
                logger.error("Order failed %s %s: %s", self.snap.slug, decision.side, result.error)

    async def _settle_loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self._resolve_open_entries()
            except Exception:
                logger.exception("settle loop failed")
            await asyncio.sleep(SETTLE_SECONDS)

    async def _resolve_open_entries(self) -> None:
        pending = unresolved_entries(self.orders.log_path)
        now = time.time()
        for entry in pending:
            slug = str(entry.get("slug") or "")
            window = window_from_slug(slug)
            if window is None or now < window.end + RESOLVE_AFTER_SECONDS:
                continue
            outcome, source = await self._resolve_outcome(slug, window)
            if outcome is None:
                continue
            self.orders.append_log(
                resolve_record(entry, outcome=outcome, now_s=now)
            )
            logger.info(
                "Resolved %s outcome=%s won=%s (%s)",
                slug,
                outcome,
                outcome == entry.get("side"),
                source,
            )

    async def _resolve_outcome(self, slug: str, window: Window) -> tuple[str | None, str]:
        """Fast CLOB price inference first; Gamma official resolve as fallback."""
        if self.snap.slug == slug:
            outcome = infer_outcome_from_clob(
                up_mid=self.snap.up_mid,
                down_mid=self.snap.down_mid,
                up_ask=self.snap.up_ask,
                down_ask=self.snap.down_ask,
            )
            if outcome is not None:
                return outcome, "clob_live"

        market = await self.gamma.fetch_market(window)
        if market is not None:
            odds = await fetch_clob_odds(
                self.clob._http,
                up_token_id=market.up_token_id,
                down_token_id=market.down_token_id,
            )
            outcome = infer_outcome_from_clob(**odds)
            if outcome is not None:
                return outcome, "clob_rest"

        event = await self.gamma.fetch_event(window, missing_ok=True)
        if event is not None:
            outcome = extract_resolved_outcome(event)
            if outcome is not None:
                return outcome, "gamma"
        return None, ""

    async def _status_loop(self) -> None:
        while not self._stop.is_set():
            self._print_status()
            await asyncio.sleep(1.0)

    def _print_status(self) -> None:
        snap = self.snap
        left = max(0, int(snap.window_end - time.time())) if snap.window_end else 0
        delta = snap.btc_minus_ptb()
        twap_d = snap.twap_minus_ptb()
        if self._traded_slug == snap.slug:
            state = "sent"
        elif self._last_decision is None:
            state = "wait"
        elif self._last_decision.ok:
            state = "armed"
        else:
            state = f"skip:{self._last_decision.reason}"
        line = (
            f"{snap.slug}  {left:>3}s  "
            f"PTB {snap.ptb or '-'}  "
            f"BTC {fmt_delta(delta)}  "
            f"TWAP {fmt_delta(twap_d)}  "
            f"venues {snap.venues_on_side('up')}/{snap.venues_on_side('down')}  "
            f"UP {fmt_odds(snap.up_ask)}  DOWN {fmt_odds(snap.down_ask)}  "
            f"{state}"
        )
        sys.stdout.write("\r" + line[:160].ljust(160))
        sys.stdout.flush()


def fmt_delta(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:+.1f}"


def fmt_odds(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:.2f}"


def _to_float(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None
