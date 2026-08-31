"""Serve the Trade Analyzer static UI and JSON APIs."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles

from pmtrader.config import env
from pmtrader.live import live_payload
from pmtrader.profile import collect_profile
from pmtrader.tradelog import analyzer_records

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"


def create_app(*, log_path: Path, order_client: Any | None = None, trader: Any | None = None) -> FastAPI:
    app = FastAPI(title="pmtrader")
    app.state.log_path = log_path
    app.state.order_client = order_client
    app.state.trader = trader
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def add_no_cache_headers(request: Request, call_next: Any) -> Response:
        response = await call_next(request)
        # Ensure fresh UI assets without aggressive browser caching
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache, no-store, must-revalidate"})

    @app.get("/styles.css")
    def styles() -> FileResponse:
        return FileResponse(STATIC_DIR / "styles.css", media_type="text/css", headers={"Cache-Control": "no-cache, no-store, must-revalidate"})

    @app.get("/api/config")
    def api_config() -> dict[str, Any]:
        return {
            "cashout": False,
            "cashout_invert_mult": 2,
            "cashout_orig_roi": 0.8,
            "stake_usd": None,
        }

    @app.get("/api/logs/trades.jsonl")
    def api_trades() -> PlainTextResponse:
        rows = analyzer_records(app.state.log_path)
        body = "\n".join(json.dumps(row, default=str) for row in rows)
        if body:
            body += "\n"
        return PlainTextResponse(body, media_type="application/x-ndjson")

    @app.get("/api/logs/balance.json")
    def api_balance() -> JSONResponse:
        client = app.state.order_client
        payload: dict[str, Any] = {"wallet": env("POLYMARKET_WALLET_ADDRESS") or env("POLYMARKET_FUNDER")}
        if client is None:
            return JSONResponse(payload)
        try:
            from py_clob_client_v2.clob_types import AssetType, BalanceAllowanceParams

            raw = client.get_balance_allowance(BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
            if isinstance(raw, dict):
                payload.update(raw)
                bal = raw.get("balance") or raw.get("balance_pusd")
                if bal is not None:
                    payload["balance_pusd"] = float(bal) / (1e6 if float(bal) > 1000 else 1)
        except Exception as exc:
            logger.warning("balance fetch failed: %s", exc)
            payload["error"] = str(exc)
        return JSONResponse(payload)

    @app.get("/api/logs/books")
    def api_books() -> dict[str, list[str]]:
        return {"slugs": []}

    @app.get("/api/live")
    def api_live() -> JSONResponse:
        return JSONResponse(live_payload(app.state.trader))

    @app.get("/api/notify")
    def api_notify() -> JSONResponse:
        """Compact event list for the Android watcher (fills + settlement)."""
        rows = analyzer_records(app.state.log_path)
        events: list[dict[str, Any]] = []
        for row in rows[-50:]:
            event = str(row.get("event") or "")
            order_id = str(row.get("order_id") or "")
            ts = int(row.get("ts") or 0)
            events.append(
                {
                    "id": f"{event}:{order_id}:{ts}",
                    "event": event,
                    "order_id": order_id,
                    "slug": row.get("slug"),
                    "side": row.get("side"),
                    "ts": ts,
                    "won": row.get("won"),
                    "outcome": row.get("outcome"),
                    "fill_price": row.get("fill_price"),
                    "stake_usd": row.get("stake_usd"),
                    "net_pnl_usd": row.get("net_pnl_usd"),
                    "dry_run": bool(row.get("dry_run")),
                }
            )
        live = live_payload(app.state.trader)
        return JSONResponse(
            {
                "events": events,
                "live": {
                    "slug": live.get("slug"),
                    "state": live.get("state"),
                    "traded": live.get("traded"),
                    "side": live.get("side"),
                    "seconds_left": live.get("seconds_left"),
                    "elapsed_s": live.get("elapsed_s"),
                },
            }
        )

    @app.get("/api/profile")
    def api_profile() -> JSONResponse:
        return JSONResponse(collect_profile(app.state.order_client))

    app.mount("/js", StaticFiles(directory=STATIC_DIR / "js"), name="js")
    return app


def serve_background(
    *,
    log_path: Path,
    order_client: Any | None,
    host: str,
    port: int,
    trader: Any | None = None,
) -> None:
    import threading
    import uvicorn

    app = create_app(log_path=log_path, order_client=order_client, trader=trader)
    config = uvicorn.Config(app, host=host, port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="pmtrader-ui", daemon=True)
    thread.start()
    logger.info("Analyzer UI http://%s:%s", host, port)
