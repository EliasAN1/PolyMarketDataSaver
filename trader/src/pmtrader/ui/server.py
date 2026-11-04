"""Serve the Trade Analyzer static UI and JSON APIs."""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from pmtrader.config import env, persist_settings
from pmtrader.live import live_payload
from pmtrader.profile import collect_profile
from pmtrader.tradelog import analyzer_records

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"
_close_lock = threading.Lock()
_SLUG_START_RE = re.compile(r"-(\d{10,})$")
_LOG_NAME_RE = re.compile(r"^trades[\w.-]*\.jsonl$")
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_WINDOW_S = 300


def archive_label(name: str) -> str:
    stem = name.removesuffix(".jsonl")
    match = re.search(r"(\d{4})-(\d{2})-(\d{2})", stem)
    if match:
        year, month, day = match.groups()
        pretty = f"{int(day)} {_MONTHS[int(month) - 1]} {year}"
        return f"Backup {pretty}" if "backup" in stem.lower() else pretty
    if stem in {"trades", "trades.jsonl"}:
        return "Live"
    return stem.replace("trades-", "").replace("-", " ")


def resolve_log_source(log_path: Path, source: str | None) -> Path:
    live = log_path.resolve()
    raw = (source or "").strip()
    if not raw or raw in {"live", live.name, "trades.jsonl"}:
        return live
    if "/" in raw or "\\" in raw or raw in {".", ".."} or not _LOG_NAME_RE.match(raw):
        raise ValueError("invalid log source")
    candidate = (log_path.parent / raw).resolve()
    if candidate.parent != log_path.parent.resolve():
        raise ValueError("invalid log source")
    if not candidate.is_file():
        raise FileNotFoundError(raw)
    return candidate


def list_log_files(log_path: Path) -> list[dict[str, Any]]:
    parent = log_path.parent
    live = log_path.resolve()
    live_stat = live.stat() if live.is_file() else None
    files: list[dict[str, Any]] = [
        {
            "id": "live",
            "label": "Live",
            "file": log_path.name,
            "bytes": live_stat.st_size if live_stat else 0,
            "mtime": int(live_stat.st_mtime) if live_stat else None,
            "live": True,
        }
    ]
    extras: list[Path] = []
    if parent.is_dir():
        extras = [path for path in parent.glob("trades*.jsonl") if _LOG_NAME_RE.match(path.name)]
        extras.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    for path in extras:
        if path.resolve() == live:
            continue
        stat = path.stat()
        files.append(
            {
                "id": path.name,
                "label": archive_label(path.name),
                "file": path.name,
                "bytes": stat.st_size,
                "mtime": int(stat.st_mtime),
                "live": False,
            }
        )
    return files


def _notify_window(row: dict[str, Any]) -> tuple[int | None, int | None]:
    slug = str(row.get("slug") or "")
    match = _SLUG_START_RE.search(slug)
    start = int(match.group(1)) if match else None
    end_raw = row.get("window_end_ts")
    try:
        end = int(end_raw) if end_raw is not None else None
    except (TypeError, ValueError):
        end = None
    if start is not None and end is None:
        end = start + _WINDOW_S
    if end is not None and start is None:
        start = end - _WINDOW_S
    return start, end


class ClosePositionsBody(BaseModel):
    confirm: bool = False
    scan_only: bool = False
    dry_run: bool = False


class DayBody(BaseModel):
    action: str
    hour: int | None = None
    when: str = "today"


class SettingsBody(BaseModel):
    stake_usd: float
    hours: list[Any]
    weekdays: list[Any]
    sessions: list[Any]
    invert_hours: list[Any]


def create_app(
    *,
    log_path: Path,
    order_client: Any | None = None,
    trader: Any | None = None,
    orders: Any | None = None,
) -> FastAPI:
    app = FastAPI(title="pmtrader")
    app.state.log_path = log_path
    app.state.order_client = order_client
    app.state.trader = trader
    app.state.orders = orders
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

    @app.get("/compact.css")
    def compact_styles() -> FileResponse:
        return FileResponse(STATIC_DIR / "compact.css", media_type="text/css", headers={"Cache-Control": "no-cache, no-store, must-revalidate"})

    @app.get("/api/config")
    def api_config() -> dict[str, Any]:
        return {
            "cashout": False,
            "cashout_invert_mult": 2,
            "cashout_orig_roi": 0.8,
            "stake_usd": None,
        }

    @app.get("/api/logs")
    def api_log_sources() -> JSONResponse:
        return JSONResponse({"live": app.state.log_path.name, "files": list_log_files(app.state.log_path)})

    @app.get("/api/logs/trades.jsonl")
    def api_trades(source: str | None = Query(None)) -> Response:
        try:
            path = resolve_log_source(app.state.log_path, source)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except FileNotFoundError:
            return JSONResponse({"error": "log not found"}, status_code=404)
        rows = analyzer_records(path)
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
    def api_notify(since: int | None = Query(default=None, ge=0)) -> JSONResponse:
        """Compact event list for the Android watcher (fills + settlement)."""
        snapshot_ts = int(time.time())
        all_records = analyzer_records(app.state.log_path)
        entries: dict[str, dict[str, Any]] = {}
        for r in all_records:
            if r.get("event") == "entry":
                oid = str(r.get("order_id") or "")
                if oid:
                    entries[oid] = r
                s = str(r.get("slug") or "")
                if s:
                    entries[s] = r

        # Reconnecting clients need the whole away period, not just the last
        # 200 log rows (which can also contain non-notification records).
        rows = all_records[-200:] if since is None else [
            row for row in all_records if int(row.get("ts") or 0) >= since
        ]
        events: list[dict[str, Any]] = []
        for row in rows:
            event = str(row.get("event") or "")
            if event not in ("entry", "resolve"):
                continue
            if event == "entry" and row.get("unmatched"):
                continue
            order_id = str(row.get("order_id") or "")
            slug = str(row.get("slug") or "")
            matched_entry = entries.get(order_id) or entries.get(slug)
            fill_price = row.get("fill_price") or (matched_entry.get("fill_price") if matched_entry else None)
            stake_usd = row.get("stake_usd") or (matched_entry.get("stake_usd") if matched_entry else None)
            side = row.get("side") or (matched_entry.get("side") if matched_entry else None)
            dry_run = bool(row.get("dry_run")) or (bool(matched_entry.get("dry_run")) if matched_entry else False)
            ts = int(row.get("ts") or 0)
            start, end = _notify_window(row if row.get("window_end_ts") else (matched_entry or row))
            events.append(
                {
                    "id": f"{event}:{order_id}:{ts}",
                    "event": event,
                    "order_id": order_id,
                    "slug": slug,
                    "side": side,
                    "ts": ts,
                    "window_start_ts": start,
                    "window_end_ts": end,
                    "won": row.get("won"),
                    "outcome": row.get("outcome"),
                    "fill_price": fill_price,
                    "stake_usd": stake_usd,
                    "net_pnl_usd": row.get("net_pnl_usd"),
                    "dry_run": dry_run,
                    "unmatched": bool(row.get("unmatched")),
                    "error": row.get("error"),
                    "error_short": row.get("error_short"),
                }
            )
        live = live_payload(app.state.trader)
        live_slug = str(live.get("slug") or "")
        live_entry = entries.get(live_slug) if live_slug else None
        live_order = live.get("order") if isinstance(live.get("order"), dict) else {}
        live_fill_price = live_order.get("limit") or (live_entry.get("fill_price") if live_entry else None)
        trader_cfg = getattr(getattr(app.state, "trader", None), "cfg", None)
        live_stake_usd = (live_entry.get("stake_usd") if live_entry else None) or (
            getattr(trader_cfg, "stake_usd", None)
        )
        live_start, live_end = _notify_window(live_entry or {"slug": live_slug})
        return JSONResponse(
            {
                "events": events,
                "server_ts": snapshot_ts,
                "live": {
                    "slug": live.get("slug"),
                    "state": live.get("state"),
                    "traded": live.get("traded"),
                    "side": live.get("side"),
                    "seconds_left": live.get("seconds_left"),
                    "elapsed_s": live.get("elapsed_s"),
                    "order": live.get("order"),
                    "fill_price": live_fill_price,
                    "stake_usd": live_stake_usd,
                    "window_start_ts": live_start,
                    "window_end_ts": live_end,
                },
            }
        )

    @app.get("/api/profile")
    def api_profile() -> JSONResponse:
        return JSONResponse(collect_profile(app.state.order_client))

    @app.post("/api/settings")
    def api_settings(body: SettingsBody) -> JSONResponse:
        trader = app.state.trader
        cfg = getattr(trader, "cfg", None) if trader is not None else None
        path = getattr(trader, "config_path", None) if trader is not None else None
        if cfg is None or path is None:
            return JSONResponse({"error": "trader is not running"}, status_code=503)
        try:
            cfg.apply_settings(
                stake_usd=body.stake_usd,
                hours=body.hours,
                weekdays=body.weekdays,
                sessions=body.sessions,
                invert_hours=body.invert_hours,
            )
            persist_settings(path, cfg)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except OSError as exc:
            return JSONResponse({"error": str(exc)}, status_code=500)
        return JSONResponse(cfg.settings_payload())

    @app.post("/api/day")
    def api_day(body: DayBody) -> JSONResponse:
        trader = app.state.trader
        day = getattr(trader, "day", None) if trader is not None else None
        if day is None:
            return JSONResponse({"error": "trader is not running"}, status_code=503)
        action = (body.action or "").strip().lower()
        when = body.when or "today"
        try:
            if action == "stop":
                payload = day.stop(when)
            elif action == "start":
                payload = day.start(when)
            elif action == "toggle_hour":
                if body.hour is None:
                    return JSONResponse({"error": "hour is required"}, status_code=400)
                payload = day.toggle_hour(body.hour, when)
            else:
                return JSONResponse({"error": "action must be stop, start, or toggle_hour"}, status_code=400)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse(payload)

    @app.post("/api/close-positions")
    def api_close_positions(body: ClosePositionsBody) -> JSONResponse:
        if not body.scan_only and not body.confirm and not body.dry_run:
            return JSONResponse(
                {"error": "confirm=true is required to redeem/sell"},
                status_code=400,
            )
        if not _close_lock.acquire(blocking=False):
            return JSONResponse({"error": "a close-positions run is already in progress"}, status_code=409)
        try:
            from pmtrader.close import run_close_cycle
            from pmtrader.orders import OrderClient

            order_box = app.state.orders
            if order_box is None and app.state.order_client is not None:
                order_box = OrderClient(
                    dry_run=body.dry_run,
                    tick_size="0.01",
                    log_path=app.state.log_path,
                )
                order_box._client = app.state.order_client
            report = run_close_cycle(
                orders=order_box,
                dry_run=body.dry_run,
                scan_only=body.scan_only,
            )
            return JSONResponse(report.as_dict())
        except Exception as exc:
            logger.exception("close-positions failed")
            return JSONResponse({"error": str(exc)}, status_code=500)
        finally:
            _close_lock.release()

    app.mount("/js", StaticFiles(directory=STATIC_DIR / "js"), name="js")
    return app


def serve_background(
    *,
    log_path: Path,
    order_client: Any | None,
    host: str,
    port: int,
    trader: Any | None = None,
    orders: Any | None = None,
) -> None:
    import uvicorn

    app = create_app(log_path=log_path, order_client=order_client, trader=trader, orders=orders)
    config = uvicorn.Config(app, host=host, port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="pmtrader-ui", daemon=True)
    thread.start()
    logger.info("Analyzer UI http://%s:%s", host, port)
