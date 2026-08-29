"""Aggregate feature rows into calibration, distance buckets, and KPIs."""

from __future__ import annotations

from typing import Any

ODDS_EDGES = (0.0, 0.2, 0.4, 0.6, 0.8, 1.01)
DISTANCE_EDGES = (-1e12, -50.0, -20.0, -5.0, 5.0, 20.0, 50.0, 1e12)
DISTANCE_LABELS = ("< -50", "-50 to -20", "-20 to -5", "-5 to 5", "5 to 20", "20 to 50", "> 50")


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return round(sum(values) / len(values), 6)


def _odds_label(lo: float, hi: float) -> str:
    return f"{lo:.1f}–{min(hi, 1.0):.1f}"


def _bucket(value: float, edges: tuple[float, ...]) -> int:
    for i in range(len(edges) - 1):
        if edges[i] <= value < edges[i + 1]:
            return i
    return len(edges) - 2


def summarize(rows: list[Any]) -> dict[str, Any]:
    up_n = sum(1 for row in rows if row.outcome == "up")
    abs_dist = [abs(row.btc_minus_ptb) for row in rows if row.btc_minus_ptb is not None]
    up_mid_win = [row.up_mid for row in rows if row.outcome == "up" and row.up_mid is not None]
    up_mid_lose = [row.up_mid for row in rows if row.outcome == "down" and row.up_mid is not None]
    agree = [row.odds_agree_spot for row in rows if row.odds_agree_spot is not None]
    return {
        "windows": len(rows),
        "up_count": up_n,
        "down_count": len(rows) - up_n,
        "up_rate": None if not rows else round(up_n / len(rows), 4),
        "mean_abs_btc_ptb": _mean(abs_dist),
        "mean_up_mid_winners": _mean(up_mid_win),
        "mean_up_mid_losers": _mean(up_mid_lose),
        "odds_spot_agree_rate": None
        if not agree
        else round(sum(1 for v in agree if v) / len(agree), 4),
    }


def calibration(rows: list[Any]) -> list[dict[str, Any]]:
    buckets = [
        {"label": _odds_label(ODDS_EDGES[i], ODDS_EDGES[i + 1]), "n": 0, "up": 0}
        for i in range(len(ODDS_EDGES) - 1)
    ]
    for row in rows:
        if row.up_mid is None:
            continue
        bucket = buckets[_bucket(row.up_mid, ODDS_EDGES)]
        bucket["n"] += 1
        if row.outcome == "up":
            bucket["up"] += 1
    out = []
    for bucket in buckets:
        n = bucket["n"]
        out.append(
            {
                "label": bucket["label"],
                "n": n,
                "up_rate": None if n == 0 else round(bucket["up"] / n, 4),
            }
        )
    return out


def distance_buckets(rows: list[Any]) -> list[dict[str, Any]]:
    buckets = [{"label": label, "n": 0, "up": 0} for label in DISTANCE_LABELS]
    for row in rows:
        if row.btc_minus_ptb is None:
            continue
        bucket = buckets[_bucket(row.btc_minus_ptb, DISTANCE_EDGES)]
        bucket["n"] += 1
        if row.outcome == "up":
            bucket["up"] += 1
    out = []
    for bucket in buckets:
        n = bucket["n"]
        out.append(
            {
                "label": bucket["label"],
                "n": n,
                "up_rate": None if n == 0 else round(bucket["up"] / n, 4),
            }
        )
    return out


def scatter_points(rows: list[Any]) -> list[dict[str, Any]]:
    points = []
    for row in rows:
        if row.btc_minus_ptb is None or row.up_mid is None:
            continue
        points.append(
            {
                "slug": row.slug,
                "x": row.btc_minus_ptb,
                "y": row.up_mid,
                "outcome": row.outcome,
            }
        )
    return points
