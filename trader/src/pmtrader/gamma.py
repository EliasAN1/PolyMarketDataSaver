"""Gamma API helpers for Polymarket BTC 5m UP/DOWN events."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

import httpx

from pmtrader.clock import Window

logger = logging.getLogger(__name__)

GAMMA_BASE = "https://gamma-api.polymarket.com"


@dataclass(frozen=True, slots=True)
class MarketInfo:
    window: Window
    condition_id: str | None
    up_token_id: str
    down_token_id: str
    price_to_beat_gamma: str | None


class GammaClient:
    def __init__(self, timeout: float = 15.0) -> None:
        self._client = httpx.AsyncClient(base_url=GAMMA_BASE, timeout=timeout)

    async def close(self) -> None:
        await self._client.aclose()

    async def fetch_market(self, window: Window) -> MarketInfo | None:
        event = await self.fetch_event(window)
        if event is None:
            return None
        return parse_event(window, event)

    async def fetch_event(self, window: Window, *, missing_ok: bool = False) -> dict[str, Any] | None:
        response = await self._client.get(f"/events/slug/{window.slug}")
        if response.status_code == 404:
            if not missing_ok:
                logger.warning("Gamma event not found yet for slug %s", window.slug)
            return None
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else None

    async def fetch_price_to_beat(self, window: Window) -> str | None:
        event = await self.fetch_event(window, missing_ok=True)
        if event is None:
            return None
        return extract_price_to_beat(event)


def parse_event(window: Window, event: dict[str, Any]) -> MarketInfo | None:
    markets = event.get("markets") or []
    if not markets:
        logger.warning("No markets on event %s", window.slug)
        return None

    market = markets[0]
    token_ids = parse_json_string_list(market.get("clobTokenIds"))
    if len(token_ids) < 2:
        logger.warning("Expected 2 clobTokenIds for %s, got %s", window.slug, token_ids)
        return None

    return MarketInfo(
        window=window,
        condition_id=market.get("conditionId"),
        up_token_id=token_ids[0],
        down_token_id=token_ids[1],
        price_to_beat_gamma=extract_price_to_beat(event),
    )


def extract_price_to_beat(event: dict[str, Any]) -> str | None:
    return _metadata_field(event, "priceToBeat", "price_to_beat")


def extract_final_price(event: dict[str, Any]) -> str | None:
    return _metadata_field(event, "finalPrice", "final_price")


def extract_resolved_outcome(event: dict[str, Any]) -> str | None:
    """Authoritative UP/DOWN winner once Gamma marks the market resolved."""
    markets = event.get("markets") or []
    if not markets or not isinstance(markets[0], dict):
        return None
    market = markets[0]
    if not market.get("closed"):
        return None
    status = str(market.get("umaResolutionStatus") or "").strip().lower()
    if status and status not in ("resolved",):
        return None
    names = [name.strip().lower() for name in parse_json_string_list(market.get("outcomes"))]
    prices = parse_json_string_list(market.get("outcomePrices"))
    if len(names) < 2 or len(prices) < 2:
        return None
    winners: list[str] = []
    for name, raw in zip(names, prices, strict=False):
        try:
            price = float(raw)
        except (TypeError, ValueError):
            continue
        if price >= 0.999:
            winners.append(name)
        elif 0.001 < price < 0.999:
            return None
    if len(winners) != 1:
        return None
    winner = winners[0]
    if winner in ("up", "down"):
        return winner
    return None


def _metadata_field(event: dict[str, Any], *keys: str) -> str | None:
    blobs: list[dict[str, Any]] = []
    metadata = _as_dict(event.get("eventMetadata"))
    if metadata:
        blobs.append(metadata)
    for market in event.get("markets") or []:
        if isinstance(market, dict):
            nested = _as_dict(market.get("eventMetadata"))
            if nested:
                blobs.append(nested)
    for blob in blobs:
        for key in keys:
            value = blob.get(key)
            if value is not None and str(value) != "":
                return str(value)
    return None


def _as_dict(raw: Any) -> dict[str, Any] | None:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return None
        if isinstance(parsed, dict):
            return parsed
    return None


def parse_json_string_list(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(item) for item in raw]
    if isinstance(raw, str):
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return [str(item) for item in parsed]
    return []
