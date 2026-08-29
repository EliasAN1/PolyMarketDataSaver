"""FastAPI dashboard server."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.concurrency import iterate_in_threadpool
from starlette.middleware.gzip import GZipMiddleware

from pmdsaver.analyse.features import build_analyse_report
from pmdsaver.backtest import tape as lab_tape
from pmdsaver.backtest.engine import BacktestConfig, iter_backtest, run_backtest
from pmdsaver.backtest.fees import CRYPTO_TAKER_FEE_RATE
from pmdsaver.live_hub import HUB
from pmdsaver.runtime import static_dir
from pmdsaver.ui import queries

STATIC_DIR = static_dir()

app = FastAPI(title="PolyMarket BTC 5m Dashboard")
app.add_middleware(GZipMiddleware, minimum_size=1024)


def _conn() -> sqlite3.Connection:
    try:
        return queries.connect()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/")
def index() -> HTMLResponse:
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    return HTMLResponse(
        content=html,
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate",
            "Pragma": "no-cache",
        },
    )


@app.get("/backtest")
def backtest_page() -> HTMLResponse:
    html = (STATIC_DIR / "backtest.html").read_text(encoding="utf-8")
    return HTMLResponse(
        content=html,
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate",
            "Pragma": "no-cache",
        },
    )


@app.get("/analyse")
def analyse_page() -> HTMLResponse:
    html = (STATIC_DIR / "analyse.html").read_text(encoding="utf-8")
    return HTMLResponse(
        content=html,
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate",
            "Pragma": "no-cache",
        },
    )


@app.get("/replay")
def replay_page() -> HTMLResponse:
    html = (STATIC_DIR / "replay.html").read_text(encoding="utf-8")
    return HTMLResponse(
        content=html,
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate",
            "Pragma": "no-cache",
        },
    )


@app.get("/api/health")
def health() -> dict:
    return {"collector_connected": HUB.connected}


@app.websocket("/ws/live")
async def live_ws(websocket: WebSocket) -> None:
    await websocket.accept()
    sub = HUB.subscribe()
    try:
        snapshot = HUB.full_message()
        await websocket.send_json(snapshot)
        HUB.acknowledge_snapshot(sub, snapshot)
        while True:
            message = await sub.queue.get()
            await websocket.send_json(message)
    except WebSocketDisconnect:
        pass
    finally:
        HUB.unsubscribe(sub)


@app.get("/api/status")
def status(window_id: int | None = Query(default=None)) -> dict:
    conn = _conn()
    try:
        window = None
        if window_id is not None:
            row = conn.execute("SELECT * FROM windows WHERE id = ?", (window_id,)).fetchone()
            window = dict(row) if row else None
        else:
            window = queries.current_window(conn)
        if window is None:
            return {"window": None, "counts": queries.table_counts(conn)}

        wid = int(window["id"])
        window["seconds_remaining"] = max(0, int(window["window_end"]) - __import__("time").time())
        window["price_to_beat"] = window.get("price_to_beat_gamma") or window.get("price_to_beat_rtds")
        open_time_ms = int(window["window_start"]) * 1000

        return {
            "window": window,
            "counts": queries.table_counts(conn, wid),
            "latest_odds": queries.latest_odds(conn, wid),
            "latest_prices": queries.latest_prices(conn, wid),
            "latest_volume": queries.latest_volume(conn, wid, open_time_ms),
            "db_path": str(queries.db_path()),
        }
    finally:
        conn.close()


@app.get("/api/windows")
def windows(limit: int = Query(default=20, ge=1, le=500)) -> dict:
    conn = _conn()
    try:
        return {"windows": queries.list_windows(conn, limit)}
    finally:
        conn.close()


@app.get("/api/series")
def series(
    window_id: int = Query(..., ge=1),
    points: int = Query(default=800, ge=50, le=5000),
) -> dict:
    conn = _conn()
    try:
        return {
            "odds": queries.odds_series(conn, window_id, points),
            "prices": queries.price_series(conn, window_id, points),
            "twap": queries.twap_series(conn, window_id, points),
            "volume": queries.volume_series(conn, window_id, points),
        }
    finally:
        conn.close()


@app.get("/api/recent")
def recent(
    window_id: int = Query(..., ge=1),
    limit: int = Query(default=40, ge=1, le=200),
) -> dict:
    conn = _conn()
    try:
        return {
            "odds": queries.recent_odds(conn, window_id, limit),
            "prices": queries.recent_prices(conn, window_id, limit),
        }
    finally:
        conn.close()


@app.get("/api/window/{window_id}/replay")
def api_window_replay(window_id: int) -> dict:
    conn = _conn()
    try:
        data = queries.window_replay(conn, window_id)
        if data is None:
            raise HTTPException(status_code=404, detail=f"Window {window_id} not found")
        return data
    finally:
        conn.close()


class BacktestRequest(BaseModel):
    strategy: str = "combo"
    stake: float = Field(default=1.0, gt=0, le=10_000)
    fill: Literal["ask", "mid"] = "ask"
    fee_rate: float = Field(default=CRYPTO_TAKER_FEE_RATE, ge=0, le=1)
    entry_after_s: float = Field(default=15.0, ge=0, le=300)
    min_distance: float = Field(default=10.0, ge=0)
    max_ask: float = Field(default=0.75, gt=0, lt=1)
    cheap_ask: float = Field(default=0.55, gt=0, lt=1)
    hit_odds: float = Field(default=0.25, gt=0, lt=1)
    last_minutes: float = Field(default=3.0, gt=0, le=5)
    use_last_minutes: bool = True
    use_odds: bool = True
    use_spot: bool = False
    use_twap: bool = False
    use_volume: bool = False
    min_volume: float = Field(default=0.0, ge=0)
    use_venues: bool = False
    min_venues: int = Field(default=2, ge=1, le=4)
    workers: int = Field(default=0, ge=0, le=16)
    slug: str | None = None
    start_ts: int | None = None
    end_ts: int | None = None

    def to_config(self) -> BacktestConfig:
        return BacktestConfig(
            strategy=self.strategy,
            stake=self.stake,
            fill=self.fill,
            fee_rate=self.fee_rate,
            entry_after_s=self.entry_after_s,
            min_distance=self.min_distance,
            max_ask=self.max_ask,
            cheap_ask=self.cheap_ask,
            hit_odds=self.hit_odds,
            last_minutes=self.last_minutes,
            use_last_minutes=self.use_last_minutes,
            use_odds=self.use_odds,
            use_spot=self.use_spot,
            use_twap=self.use_twap,
            use_volume=self.use_volume,
            min_volume=self.min_volume,
            use_venues=self.use_venues,
            min_venues=self.min_venues,
            workers=self.workers,
            slug=self.slug or None,
            start_ts=self.start_ts,
            end_ts=self.end_ts,
        )


@app.post("/api/backtest")
def api_backtest(body: BacktestRequest) -> dict:
    conn = _conn()
    try:
        return run_backtest(conn, body.to_config()).to_dict()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        conn.close()


@app.post("/api/backtest/stream")
def api_backtest_stream(body: BacktestRequest) -> StreamingResponse:
    config = body.to_config()

    def generate():
        try:
            conn = queries.connect()
        except FileNotFoundError as exc:
            yield json.dumps({"type": "error", "detail": str(exc)}) + "\n"
            return
        try:
            for event in iter_backtest(conn, config):
                payload = dict(event)
                if payload["type"] == "done":
                    payload["report"] = payload["report"].to_dict()
                yield json.dumps(payload) + "\n"
        except Exception as exc:
            yield json.dumps({"type": "error", "detail": str(exc)}) + "\n"
        finally:
            conn.close()

    return StreamingResponse(
        iterate_in_threadpool(generate()),
        media_type="application/x-ndjson",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/analyse")
def api_analyse(
    at_s: int = Query(default=180, ge=0, le=300),
    workers: int = Query(default=0, ge=0, le=16),
    slug: str | None = Query(default=None),
) -> dict:
    conn = _conn()
    try:
        return build_analyse_report(conn, at_s, slug=slug or None, workers=workers)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        conn.close()


@app.get("/api/lab/tape")
def api_lab_tape(
    range_days: int = Query(default=3, ge=0, le=365),
    slug: str | None = Query(default=None),
    workers: int = Query(default=0, ge=0, le=16),
) -> StreamingResponse:
    start_ts = None if range_days <= 0 else int(time.time()) - range_days * 86400

    def generate():
        try:
            conn = queries.connect()
        except FileNotFoundError as exc:
            yield json.dumps({"type": "error", "detail": str(exc)}) + "\n"
            return
        try:
            cache_conn = lab_tape.connect_cache()
        except Exception as exc:  # noqa: BLE001 - report any cache-open failure to the client
            conn.close()
            yield json.dumps({"type": "error", "detail": str(exc)}) + "\n"
            return
        try:
            for event in lab_tape.scan_new_windows(
                conn,
                cache_conn,
                slug=slug or None,
                start_ts=start_ts,
                workers=workers,
            ):
                yield json.dumps(event) + "\n"
            data = lab_tape.load_tape(cache_conn, slug=slug or None, start_ts=start_ts)
            data["type"] = "data"
            data["range_days"] = range_days
            yield json.dumps(data) + "\n"
        except Exception as exc:  # noqa: BLE001 - stream the error instead of a bare 500
            yield json.dumps({"type": "error", "detail": str(exc)}) + "\n"
        finally:
            conn.close()
            cache_conn.close()

    return StreamingResponse(
        iterate_in_threadpool(generate()),
        media_type="application/x-ndjson",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


def _no_cache_file(path: Path, media_type: str) -> FileResponse:
    return FileResponse(
        path,
        media_type=media_type,
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate",
            "Pragma": "no-cache",
        },
    )


@app.get("/static/app.css")
def app_css() -> FileResponse:
    return _no_cache_file(STATIC_DIR / "app.css", "text/css")


@app.get("/static/app.js")
def app_js() -> FileResponse:
    return _no_cache_file(STATIC_DIR / "app.js", "application/javascript")


@app.get("/static/backtest.css")
def backtest_css() -> FileResponse:
    return _no_cache_file(STATIC_DIR / "backtest.css", "text/css")


@app.get("/static/backtest.js")
def backtest_js() -> FileResponse:
    return _no_cache_file(STATIC_DIR / "backtest.js", "application/javascript")


@app.get("/static/analyse.css")
def analyse_css() -> FileResponse:
    return _no_cache_file(STATIC_DIR / "analyse.css", "text/css")


@app.get("/static/analyse.js")
def analyse_js() -> FileResponse:
    return _no_cache_file(STATIC_DIR / "analyse.js", "application/javascript")


@app.get("/static/lab.css")
def lab_css() -> FileResponse:
    return _no_cache_file(STATIC_DIR / "lab.css", "text/css")


@app.get("/static/lab_engine.js")
def lab_engine_js() -> FileResponse:
    return _no_cache_file(STATIC_DIR / "lab_engine.js", "application/javascript")


@app.get("/static/lab.js")
def lab_js() -> FileResponse:
    return _no_cache_file(STATIC_DIR / "lab.js", "application/javascript")


@app.get("/static/lab_worker.js")
def lab_worker_js() -> FileResponse:
    return _no_cache_file(STATIC_DIR / "lab_worker.js", "application/javascript")


@app.get("/static/replay.css")
def replay_css() -> FileResponse:
    return _no_cache_file(STATIC_DIR / "replay.css", "text/css")


@app.get("/static/replay.js")
def replay_js() -> FileResponse:
    return _no_cache_file(STATIC_DIR / "replay.js", "application/javascript")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
