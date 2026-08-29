"""Paths that work both from source and from a frozen Windows exe."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def install_dir() -> Path:
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path.cwd()


def data_dir() -> Path:
    override = os.getenv("DATA_DIR")
    if override:
        return Path(override)
    return install_dir() / "data"


def _meipass() -> Path | None:
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return None


def static_dir() -> Path:
    here = Path(__file__).resolve().parent / "ui" / "static"
    candidates: list[Path] = []
    root = _meipass()
    if root is not None:
        candidates.extend(
            [
                root / "pmdsaver" / "ui" / "static",
                root / "ui" / "static",
                root / "static",
            ]
        )
    candidates.append(here)
    for path in candidates:
        if path.is_dir():
            return path
    return here
