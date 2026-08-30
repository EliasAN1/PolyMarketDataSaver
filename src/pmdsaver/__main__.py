"""CLI entrypoint: collector + dashboard in one process."""

from __future__ import annotations

import asyncio
import logging
import multiprocessing
import os
import signal
import sys
import threading
import webbrowser

import uvicorn

from pmdsaver.collector import Collector
from pmdsaver.db import Database
from pmdsaver.gamma import GammaClient
from pmdsaver.runtime import data_dir, is_frozen
from pmdsaver.ui.server import app as ui_app


def _pause_if_frozen(message: str | None = None) -> None:
    """Keep the console open after a double-click crash so the error is readable."""
    if not is_frozen():
        return
    if message:
        print(message, file=sys.stderr)
    try:
        input("Press Enter to close this window...")
    except EOFError:
        pass


def main() -> None:
    multiprocessing.freeze_support()
    # Let the same exe double as the merge tool: `pmdsaver.exe merge <other.db>`.
    # Kept here (rather than a separate build target) so the frozen build on a
    # monitoring PC without Python can still merge databases brought over from
    # elsewhere.
    if len(sys.argv) > 1 and sys.argv[1] == "merge":
        from pmdsaver.mergedb import main as merge_main

        merge_main(sys.argv[2:])
        return
    if len(sys.argv) > 1 and sys.argv[1] in ("backfill-outcomes", "backfill"):
        from pmdsaver.backfill_outcomes import main as backfill_main

        backfill_main(sys.argv[2:])
        return

    logging.basicConfig(
        level=getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )

    db_dir = data_dir()
    os.environ.setdefault("DATA_DIR", str(db_dir))
    db_path = db_dir / "pmdsaver.db"
    host = os.getenv("UI_HOST", "127.0.0.1")
    port = int(os.getenv("UI_PORT", "8080"))
    open_browser = os.getenv("UI_OPEN_BROWSER", "1" if is_frozen() else "0") == "1"

    logging.getLogger("pmdsaver").info("Dashboard http://%s:%s", host, port)
    logging.getLogger("pmdsaver").info("SQLite %s", db_path)

    db = Database(db_path)
    gamma = GammaClient()
    collector = Collector(db=db, gamma=gamma)

    config = uvicorn.Config(
        ui_app,
        host=host,
        port=port,
        log_level=os.getenv("LOG_LEVEL", "info").lower(),
        loop="asyncio",
    )
    server = uvicorn.Server(config)
    # Windows: uvicorn would swallow Ctrl+C and leave the collector running.
    server.install_signal_handlers = False

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def _request_shutdown() -> None:
        logging.getLogger("pmdsaver").info("Shutdown requested")
        collector.request_stop()
        server.should_exit = True
        server.force_exit = True

    def _on_signal(*_: object) -> None:
        # Windows delivers Ctrl+C via signal.signal, not add_signal_handler.
        # Touch asyncio state only through the loop so waiters actually wake.
        loop.call_soon_threadsafe(_request_shutdown)

    for sig in (signal.SIGINT, getattr(signal, "SIGTERM", signal.SIGINT)):
        try:
            loop.add_signal_handler(sig, _request_shutdown)
        except (NotImplementedError, RuntimeError, ValueError):
            signal.signal(sig, _on_signal)

    async def _run() -> bool:
        collect_task = asyncio.create_task(collector.run(), name="collector")
        serve_task = asyncio.create_task(server.serve(), name="ui")
        if open_browser:
            threading.Timer(
                1.5,
                lambda: webbrowser.open(f"http://{host}:{port}"),
            ).start()
        try:
            await asyncio.wait(
                {collect_task, serve_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
        except asyncio.CancelledError:
            pass
        finally:
            _request_shutdown()
            await asyncio.sleep(0.05)
            for task in (collect_task, serve_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(collect_task, serve_task, return_exceptions=True)
        return bool(getattr(server, "started", False))

    started = False
    try:
        started = loop.run_until_complete(_run())
    except KeyboardInterrupt:
        _request_shutdown()
        try:
            loop.run_until_complete(collector.shutdown())
        except Exception:
            pass
    except Exception:
        logging.getLogger("pmdsaver").exception("pmdsaver crashed")
        if not loop.is_closed():
            loop.close()
        _pause_if_frozen()
        raise
    finally:
        if not loop.is_closed():
            loop.close()

    if not started:
        _pause_if_frozen(
            f"Could not start the dashboard on http://{host}:{port}.\n"
            "Usual cause: that port is already in use (another pmdsaver still running).\n"
            "Close the other window, or in PowerShell:\n"
            f"  $env:UI_PORT = '8081'\n"
            "  .\\pmdsaver.exe"
        )


if __name__ == "__main__":
    main()
