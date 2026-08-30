"""Pull everything the CLOB + Data APIs expose for the trading wallet."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from pmtrader.config import env

logger = logging.getLogger(__name__)

DATA_API = "https://data-api.polymarket.com"


def funder_address() -> str | None:
    return env("POLYMARKET_WALLET_ADDRESS") or env("POLYMARKET_FUNDER")


def collect_profile(client: Any | None) -> dict[str, Any]:
    wallet = funder_address()
    signer = None
    if client is not None:
        getter = getattr(client, "get_address", None)
        if getter is not None:
            try:
                signer = getter()
            except Exception as exc:
                logger.warning("get_address failed: %s", exc)

    profile: dict[str, Any] = {
        "wallet": wallet,
        "signer": signer,
        "signature_type": env("POLYMARKET_SIGNATURE_TYPE"),
        "dry_run": client is None,
        "clob": {},
        "data": {},
        "errors": [],
    }

    if client is not None:
        profile["clob"] = _clob_snapshot(client, profile["errors"])
    if wallet:
        profile["data"] = _data_api_snapshot(wallet, profile["errors"])
    return profile


def _clob_snapshot(client: Any, errors: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    calls = [
        ("balance_collateral", lambda: _balance(client, "COLLATERAL")),
        ("open_orders", lambda: _safe_list(client.get_open_orders, only_first_page=True)),
        ("recent_trades", lambda: _safe_list(client.get_trades, only_first_page=True)),
        ("notifications", lambda: _call(client.get_notifications)),
        ("closed_only_mode", lambda: _call(client.get_closed_only_mode)),
        ("server_time", lambda: _call(client.get_server_time)),
    ]
    for key, fn in calls:
        try:
            out[key] = _jsonable(fn())
        except Exception as exc:
            errors.append(f"clob.{key}: {exc}")
            out[key] = None
    return out


def _balance(client: Any, asset_type: str) -> Any:
    try:
        from py_clob_client_v2.clob_types import AssetType, BalanceAllowanceParams

        kind = getattr(AssetType, asset_type, asset_type)
        return client.get_balance_allowance(BalanceAllowanceParams(asset_type=kind))
    except Exception:
        return client.get_balance_allowance()


def _data_api_snapshot(wallet: str, errors: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    paths = {
        "value": f"/value?user={wallet}",
        "positions": f"/positions?user={wallet}",
        "trades": f"/trades?user={wallet}&limit=50",
        "activity": f"/activity?user={wallet}&limit=50",
    }
    with httpx.Client(base_url=DATA_API, timeout=15.0) as http:
        for key, path in paths.items():
            try:
                response = http.get(path)
                response.raise_for_status()
                out[key] = response.json()
            except Exception as exc:
                errors.append(f"data.{key}: {exc}")
                out[key] = None
    return out


def _safe_list(fn: Any, **kwargs: Any) -> Any:
    try:
        return fn(**kwargs)
    except TypeError:
        return fn()


def _call(fn: Any) -> Any:
    return fn()


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if hasattr(value, "__dict__"):
        return _jsonable(vars(value))
    return str(value)
