"""CLOB V2 FAK buys — live via py-clob-client-v2, or dry-run."""

from __future__ import annotations

import inspect
import json
import logging
import re
import time
from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN
from pathlib import Path
from typing import Any

from pmtrader.config import env

logger = logging.getLogger(__name__)

CLOB_HOST = "https://clob.polymarket.com"


@dataclass(frozen=True, slots=True)
class OrderResult:
    ok: bool
    dry_run: bool
    side: str
    token_id: str
    limit: float
    stake_usd: float
    response: dict[str, Any] | None
    error: str | None = None


class OrderClient:
    def __init__(self, *, dry_run: bool, tick_size: str, log_path: Path) -> None:
        self.dry_run = dry_run
        self.tick_size = tick_size
        self.log_path = log_path
        self._client: Any | None = None
        self.wallet: str | None = None

    @property
    def client(self) -> Any | None:
        return self._client

    def connect(self) -> None:
        if self.dry_run:
            logger.info("Dry-run: orders will be logged, not posted")
            return
        private_key = env("POLYMARKET_PRIVATE_KEY")
        if not private_key:
            raise SystemExit("Live mode needs POLYMARKET_PRIVATE_KEY in the environment or .env")
        wallet = env("POLYMARKET_WALLET_ADDRESS") or env("POLYMARKET_FUNDER")
        if not wallet:
            raise SystemExit(
                "Live mode needs POLYMARKET_WALLET_ADDRESS or POLYMARKET_FUNDER in the environment or .env"
            )

        from py_clob_client_v2 import ApiCreds, ClobClient

        chain_id = int(env("POLYMARKET_CHAIN_ID") or "137")
        signature_type = env("POLYMARKET_SIGNATURE_TYPE")
        creds = _env_creds()
        kwargs: dict[str, Any] = {
            "host": CLOB_HOST,
            "key": private_key,
        }
        ctor = inspect.signature(ClobClient.__init__).parameters
        if "chain_id" in ctor:
            kwargs["chain_id"] = chain_id
        elif "chain" in ctor:
            kwargs["chain"] = chain_id
        if creds is not None and "creds" in ctor:
            kwargs["creds"] = creds
        if signature_type and "signature_type" in ctor:
            kwargs["signature_type"] = int(signature_type)
        if wallet and "funder" in ctor:
            kwargs["funder"] = wallet
        self.wallet = wallet

        client = ClobClient(**kwargs)
        if creds is None:
            derive = getattr(client, "create_or_derive_api_key", None) or getattr(
                client, "create_or_derive_api_creds", None
            )
            if derive is None:
                raise SystemExit("Could not derive CLOB API creds; set CLOB_API_KEY / SECRET / PASS_PHRASE")
            derived = derive()
            setter = getattr(client, "set_api_creds", None)
            if setter is not None:
                setter(derived)
            elif "creds" in ctor:
                kwargs["creds"] = derived
                client = ClobClient(**kwargs)
        self._client = client
        logger.info("CLOB client ready (wallet %s…)", wallet[:10])

    def place_fak_buy(self, *, token_id: str, side: str, limit: float, stake_usd: float) -> OrderResult:
        aligned = _align_price(limit, float(self.tick_size))
        maker_usd, shares = _buy_amounts(stake_usd, aligned)
        if maker_usd <= 0 or shares <= 0:
            return OrderResult(
                ok=False,
                dry_run=self.dry_run,
                side=side,
                token_id=token_id,
                limit=aligned,
                stake_usd=round(float(stake_usd), 2),
                response=None,
                error="invalid amounts: could not size a 2-decimal USDC / 4-decimal share FAK",
            )
        if self.dry_run or self._client is None:
            result = OrderResult(
                ok=True,
                dry_run=True,
                side=side,
                token_id=token_id,
                limit=aligned,
                stake_usd=maker_usd,
                response={"dry_run": True, "shares": shares},
            )
            logger.info(
                "DRY-RUN FAK BUY %s token=%s… limit=%.2f stake=%.2f shares=%.4f",
                side.upper(),
                token_id[:12],
                aligned,
                maker_usd,
                shares,
            )
            return result

        response, error = _post_fak(self._client, token_id, aligned, maker_usd, shares, self.tick_size)
        ok = error is None and _response_ok(response)
        result = OrderResult(
            ok=ok,
            dry_run=False,
            side=side,
            token_id=token_id,
            limit=aligned,
            stake_usd=maker_usd,
            response=response if isinstance(response, dict) else {"raw": response},
            error=error if not ok else None,
        )
        if ok:
            logger.info(
                "FAK BUY %s posted limit=%.2f stake=%.2f shares=%.4f resp=%s",
                side.upper(),
                aligned,
                maker_usd,
                shares,
                response,
            )
        else:
            logger.error("FAK BUY %s failed: %s %s", side.upper(), error, response)
        return result

    def place_fak_sell(self, *, token_id: str, shares: float, limit: float) -> OrderResult:
        aligned = _align_price(limit, float(self.tick_size)) if limit > 0 else 0.0
        size = float(Decimal(str(shares)).quantize(Decimal("0.01"), rounding=ROUND_DOWN))
        if size <= 0:
            return OrderResult(
                ok=False,
                dry_run=self.dry_run,
                side="sell",
                token_id=token_id,
                limit=aligned,
                stake_usd=0.0,
                response=None,
                error="invalid amounts: share size rounded to 0",
            )
        tick = self.tick_size
        if self._client is not None:
            getter = getattr(self._client, "get_tick_size", None)
            if getter is not None:
                try:
                    tick = str(getter(token_id))
                except Exception:
                    pass
        if self.dry_run or self._client is None:
            logger.info("DRY-RUN FAK SELL token=%s… limit=%.2f shares=%.2f", token_id[:12], aligned, size)
            return OrderResult(
                ok=True,
                dry_run=True,
                side="sell",
                token_id=token_id,
                limit=aligned,
                stake_usd=round(aligned * size, 4),
                response={"dry_run": True, "shares": size},
            )
        response, error = _post_fak_sell(self._client, token_id, aligned, size, tick)
        ok = error is None and _response_ok(response)
        if ok:
            logger.info("FAK SELL posted limit=%.2f shares=%.2f resp=%s", aligned, size, response)
        else:
            logger.error("FAK SELL failed: %s %s", error, response)
        return OrderResult(
            ok=ok,
            dry_run=False,
            side="sell",
            token_id=token_id,
            limit=aligned,
            stake_usd=round(aligned * size, 4),
            response=response if isinstance(response, dict) else {"raw": response},
            error=error if not ok else None,
        )

    def append_log(self, record: dict[str, Any]) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        if "ts" not in record:
            record = {"ts": int(time.time()), **record}
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, default=str) + "\n")


_ERROR_QUOTED = re.compile(r"""['"]error['"]\s*:\s*['"]([^'"]+)['"]""")


def describe_clob_error(error: str | None) -> tuple[str, str]:
    """Short badge label plus the CLOB's own error sentence."""
    raw = (error or "").strip()
    if not raw:
        return "rejected", "Order rejected"
    match = _ERROR_QUOTED.search(raw)
    detail = match.group(1).strip() if match else raw
    low = detail.lower()
    if "no orders found" in low or "no match" in low:
        return "no match", detail
    if "invalid amounts" in low:
        return "bad size", detail
    if "balance" in low or "allowance" in low or "not enough" in low:
        return "balance", detail
    short = detail.split(".")[0].strip()
    if len(short) > 28:
        short = short[:27].rstrip() + "…"
    return short or "rejected", detail


def _env_creds() -> Any | None:
    key = env("CLOB_API_KEY")
    secret = env("CLOB_SECRET")
    phrase = env("CLOB_PASS_PHRASE")
    if not (key and secret and phrase):
        return None
    from py_clob_client_v2 import ApiCreds

    return ApiCreds(api_key=key, api_secret=secret, api_passphrase=phrase)


def _align_price(price: float, tick: float) -> float:
    if tick <= 0:
        return float(Decimal(str(price)).quantize(Decimal("0.01")))
    tick_d = Decimal(str(tick))
    steps = (Decimal(str(price)) / tick_d).quantize(Decimal("1"))
    return float((steps * tick_d).quantize(Decimal("0.0001")))


def _buy_amounts(stake: float, price: float) -> tuple[float, float]:
    """USDC (2 dp) and shares (2 dp) such that shares × price is exactly 2 dp.

    CLOB FAK buys reject maker USDC past 2 decimals and shares past 4.
    Naive size = stake / 0.54 → 92.59 × 0.54 = 49.9986, which the book kills.
    """
    p = Decimal(str(price))
    if p <= 0:
        return 0.0, 0.0
    share_step = Decimal("0.01")
    shares = (Decimal(str(stake)) / p).quantize(share_step, rounding=ROUND_DOWN)
    while shares > 0:
        maker = shares * p
        if maker == maker.quantize(Decimal("0.01")):
            return float(maker.quantize(Decimal("0.01"))), float(shares)
        shares -= share_step
    return 0.0, 0.0


def _post_fak(
    client: Any,
    token_id: str,
    limit: float,
    maker_usd: float,
    shares: float,
    tick_size: str,
) -> tuple[Any, str | None]:
    from py_clob_client_v2 import MarketOrderArgs, OrderType, PartialCreateOrderOptions, Side

    options = PartialCreateOrderOptions(tick_size=tick_size)
    args = _market_args(token_id, maker_usd, limit)
    try:
        response = client.create_and_post_market_order(
            order_args=args,
            options=options,
            order_type=OrderType.FAK,
        )
        return response, None
    except TypeError:
        try:
            response = client.create_and_post_market_order(args, options, OrderType.FAK)
            return response, None
        except Exception as exc:
            return None, str(exc)
    except Exception as exc:
        logger.warning("Market FAK failed (%s); trying limit FAK", exc)
        try:
            from py_clob_client_v2 import OrderArgs

            response = client.create_and_post_order(
                order_args=OrderArgs(token_id=token_id, price=limit, size=shares, side=Side.BUY),
                options=options,
                order_type=OrderType.FAK,
            )
            return response, None
        except Exception as fallback:
            return None, str(fallback)


def _market_args(token_id: str, amount: float, limit: float, *, side: str = "BUY") -> Any:
    from py_clob_client_v2 import MarketOrderArgs, OrderType, Side

    params = inspect.signature(MarketOrderArgs).parameters
    side_val = Side.SELL if str(side).upper() == "SELL" else Side.BUY
    kwargs: dict[str, Any] = {
        "token_id": token_id,
        "amount": amount,
        "side": side_val,
    }
    if "order_type" in params:
        kwargs["order_type"] = OrderType.FAK
    if "price" in params and limit > 0:
        kwargs["price"] = limit
    return MarketOrderArgs(**kwargs)


def _post_fak_sell(
    client: Any,
    token_id: str,
    limit: float,
    shares: float,
    tick_size: str,
) -> tuple[Any, str | None]:
    from py_clob_client_v2 import OrderType, PartialCreateOrderOptions, Side

    options = PartialCreateOrderOptions(tick_size=tick_size)
    args = _market_args(token_id, shares, limit, side="SELL")
    try:
        response = client.create_and_post_market_order(
            order_args=args,
            options=options,
            order_type=OrderType.FAK,
        )
        return response, None
    except TypeError:
        try:
            response = client.create_and_post_market_order(args, options, OrderType.FAK)
            return response, None
        except Exception as exc:
            return None, str(exc)
    except Exception as exc:
        logger.warning("Market FAK sell failed (%s); trying limit FAK", exc)
        try:
            from py_clob_client_v2 import OrderArgs

            response = client.create_and_post_order(
                order_args=OrderArgs(token_id=token_id, price=max(limit, 0.01), size=shares, side=Side.SELL),
                options=options,
                order_type=OrderType.FAK,
            )
            return response, None
        except Exception as fallback:
            return None, str(fallback)


def _response_ok(response: Any) -> bool:
    if response is None:
        return False
    if isinstance(response, dict):
        if response.get("success") is False:
            return False
        if response.get("error") or response.get("errorMsg"):
            return False
        status = str(response.get("status") or "").lower()
        if status in {"matched", "live", "delayed", "ok"}:
            return True
        if response.get("orderID") or response.get("order_id") or response.get("id"):
            return True
        return "error" not in response
    return True
