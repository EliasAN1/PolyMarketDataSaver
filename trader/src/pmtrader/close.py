"""Scan wallet positions, redeem settled markets, FAK-sell the rest."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx

from pmtrader.config import env
from pmtrader.profile import DATA_API, funder_address
from pmtrader.redeem import BATCH_SIZE, DepositRelayer, builder_creds

logger = logging.getLogger(__name__)

CLOB_HOST = "https://clob.polymarket.com"
MIN_SIZE = 0.01

Kind = Literal["redeem", "sell", "skip"]


@dataclass(slots=True)
class CloseJob:
    kind: Kind
    condition_id: str | None = None
    token_id: str | None = None
    slug: str | None = None
    title: str | None = None
    outcome: str | None = None
    negative_risk: bool = False
    size: float = 0.0
    bid: float | None = None
    value_usd: float = 0.0
    reason: str | None = None

    def summary(self) -> str:
        label = self.slug or self.title or self.condition_id or "?"
        if self.kind == "redeem":
            tag = "winner" if self.value_usd > 0.01 else "loser"
            return f"redeem [{tag}] {label}  ~${self.value_usd:.2f}"
        if self.kind == "sell":
            side = self.outcome or "?"
            bid = f" @ {self.bid:.2f}" if self.bid is not None else ""
            return f"sell {self.size:.2f} {side} in {label}{bid}"
        return f"skip: {self.reason}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "condition_id": self.condition_id,
            "token_id": self.token_id,
            "slug": self.slug,
            "title": self.title,
            "outcome": self.outcome,
            "size": self.size,
            "bid": self.bid,
            "value_usd": round(self.value_usd, 4),
            "reason": self.reason,
            "summary": self.summary(),
        }


@dataclass
class CloseReport:
    scanned: int = 0
    redeemed: int = 0
    sold: int = 0
    skipped: int = 0
    failed: int = 0
    dry_run: bool = False
    scan_only: bool = False
    jobs: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "scanned": self.scanned,
            "redeemed": self.redeemed,
            "sold": self.sold,
            "skipped": self.skipped,
            "failed": self.failed,
            "dry_run": self.dry_run,
            "scan_only": self.scan_only,
            "jobs": self.jobs,
            "errors": self.errors,
        }


def fetch_positions(wallet: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    page = 500
    offset = 0
    with httpx.Client(base_url=DATA_API, timeout=20.0) as http:
        while offset <= 10_000:
            response = http.get(
                "/positions",
                params={"user": wallet, "limit": page, "offset": offset, "sizeThreshold": 0},
            )
            response.raise_for_status()
            chunk = response.json()
            if isinstance(chunk, dict):
                chunk = chunk.get("data") or chunk.get("positions") or []
            if not isinstance(chunk, list):
                break
            rows.extend(item for item in chunk if isinstance(item, dict))
            if len(chunk) < page:
                break
            offset += page
    return rows


def _f(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _best_bid(token_id: str, client: Any | None) -> float | None:
    book: Any = None
    if client is not None:
        try:
            book = client.get_order_book(token_id)
        except Exception as exc:
            logger.debug("CLOB book failed for %s: %s", token_id[:12], exc)
    if book is None:
        try:
            with httpx.Client(timeout=10.0) as http:
                response = http.get(f"{CLOB_HOST}/book", params={"token_id": token_id})
                response.raise_for_status()
                book = response.json()
        except Exception:
            return None
    bids = book.get("bids") if isinstance(book, dict) else getattr(book, "bids", None)
    if not bids:
        return None
    prices: list[float] = []
    for level in bids:
        if isinstance(level, dict):
            prices.append(_f(level.get("price")))
        else:
            prices.append(_f(getattr(level, "price", 0)))
    prices = [p for p in prices if p > 0]
    return max(prices) if prices else None


def build_close_queue(positions: list[dict[str, Any]], *, clob: Any | None = None) -> list[CloseJob]:
    by_condition: dict[str, list[dict[str, Any]]] = {}
    jobs: list[CloseJob] = []
    for pos in positions:
        size = _f(pos.get("size"))
        cid = str(pos.get("conditionId") or pos.get("condition_id") or "")
        token = str(pos.get("asset") or pos.get("token_id") or pos.get("asset_id") or "")
        if size < MIN_SIZE:
            jobs.append(
                CloseJob(
                    kind="skip",
                    condition_id=cid or None,
                    token_id=token or None,
                    reason=f"dust below {MIN_SIZE}",
                )
            )
            continue
        if not cid:
            jobs.append(CloseJob(kind="skip", token_id=token or None, reason="missing condition_id"))
            continue
        by_condition.setdefault(cid, []).append(pos)

    redeem_ids: set[str] = set()
    for cid, legs in by_condition.items():
        any_redeemable = any(bool(p.get("redeemable")) for p in legs)
        if not any_redeemable:
            continue
        value = sum(_f(p.get("currentValue") or p.get("current_value")) for p in legs)
        slug = next((str(p.get("slug") or "") for p in legs if p.get("slug")), None) or None
        title = next((str(p.get("title") or "") for p in legs if p.get("title")), None) or None
        negative_risk = any(bool(p.get("negativeRisk") or p.get("negative_risk")) for p in legs)
        redeem_ids.add(cid)
        jobs.append(
            CloseJob(
                kind="redeem",
                condition_id=cid,
                slug=slug,
                title=title,
                negative_risk=negative_risk,
                value_usd=value,
            )
        )

    for cid, legs in by_condition.items():
        if cid in redeem_ids:
            continue
        for pos in legs:
            token = str(pos.get("asset") or pos.get("token_id") or pos.get("asset_id") or "")
            size = _f(pos.get("size"))
            price = _f(pos.get("curPrice") or pos.get("cur_price"))
            bid = _best_bid(token, clob) if token and price > 0.001 else None
            if bid is None and price <= 0.001:
                jobs.append(
                    CloseJob(
                        kind="skip",
                        condition_id=cid,
                        token_id=token or None,
                        slug=str(pos.get("slug") or "") or None,
                        title=str(pos.get("title") or "") or None,
                        reason="resolved loser with no bid — redeem when API marks redeemable",
                    )
                )
                continue
            if bid is None:
                bid = _best_bid(token, clob) if token else None
            if bid is None:
                jobs.append(
                    CloseJob(
                        kind="skip",
                        condition_id=cid,
                        token_id=token or None,
                        slug=str(pos.get("slug") or "") or None,
                        reason="no bid to sell into",
                    )
                )
                continue
            jobs.append(
                CloseJob(
                    kind="sell",
                    condition_id=cid,
                    token_id=token,
                    slug=str(pos.get("slug") or "") or None,
                    title=str(pos.get("title") or "") or None,
                    outcome=str(pos.get("outcome") or "") or None,
                    size=size,
                    bid=bid,
                )
            )

    def rank(job: CloseJob) -> tuple[int, float]:
        if job.kind == "redeem":
            return (0 if job.value_usd > 0.01 else 1, -job.value_usd)
        if job.kind == "sell":
            return (2, -(job.bid or 0) * job.size)
        return (3, 0.0)

    jobs.sort(key=rank)
    return jobs


def scan_positions(*, clob: Any | None = None) -> tuple[list[CloseJob], str]:
    wallet = funder_address()
    if not wallet:
        raise RuntimeError("POLYMARKET_FUNDER or POLYMARKET_WALLET_ADDRESS is required")
    positions = fetch_positions(wallet)
    return build_close_queue(positions, clob=clob), wallet


def _print_scan(jobs: list[CloseJob], wallet: str, *, dry_run: bool, scan_only: bool = False) -> None:
    mode = "SCAN" if scan_only else ("DRY-RUN" if dry_run else "LIVE")
    redeem_n = sum(1 for j in jobs if j.kind == "redeem")
    sell_n = sum(1 for j in jobs if j.kind == "sell")
    skip_n = sum(1 for j in jobs if j.kind == "skip")
    win_usd = sum(j.value_usd for j in jobs if j.kind == "redeem" and j.value_usd > 0.01)
    print(f"=== close-positions {mode} ===")
    print(f" Wallet:  {wallet}")
    print(
        f" Queue:   {len(jobs)} jobs  "
        f"({redeem_n} redeems ~${win_usd:.2f}, {sell_n} sells, {skip_n} skipped)"
    )
    print()
    show = jobs if len(jobs) <= 40 else jobs[:40]
    for i, job in enumerate(show, 1):
        print(f"  {i:>4}. {job.summary()}")
    if len(jobs) > 40:
        print(f"  ... and {len(jobs) - 40} more")
    print()


def _chunks(items: list[CloseJob], size: int) -> list[list[CloseJob]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def run_close_cycle(
    *,
    orders: Any | None,
    dry_run: bool,
    scan_only: bool = False,
) -> CloseReport:
    clob = None if orders is None else orders.client
    jobs, wallet = scan_positions(clob=clob)
    report = CloseReport(
        scanned=len(jobs),
        dry_run=dry_run,
        scan_only=scan_only,
        jobs=[j.as_dict() for j in jobs],
    )
    _print_scan(jobs, wallet, dry_run=dry_run, scan_only=scan_only)
    if scan_only:
        report.redeemed = sum(1 for j in jobs if j.kind == "redeem")
        report.sold = sum(1 for j in jobs if j.kind == "sell")
        report.skipped = sum(1 for j in jobs if j.kind == "skip")
        return report

    relayer: DepositRelayer | None = None
    redeem_jobs = [j for j in jobs if j.kind == "redeem"]
    if redeem_jobs and not dry_run:
        if int(env("POLYMARKET_SIGNATURE_TYPE") or "3") != 3:
            raise RuntimeError("On-chain EOA redeem is not wired; this closer is for signature type 3")
        if not builder_creds():
            raise RuntimeError("POLYMARKET_BUILDER_API_KEY / SECRET / PASSPHRASE required to redeem")
        relayer = DepositRelayer()

    if redeem_jobs:
        batches = _chunks(redeem_jobs, BATCH_SIZE)
        for i, batch in enumerate(batches, 1):
            print(f"         redeem x{len(batch)} in one tx  ({i}/{len(batches)})")
            if dry_run:
                report.redeemed += len(batch)
                print("         -> dry-run")
                continue
            try:
                assert relayer is not None
                tx = relayer.redeem_batch([(j.condition_id or "", j.negative_risk) for j in batch])
                report.redeemed += len(batch)
                print(f"         -> OK  {tx[:10]}…{tx[-6:] if len(tx) > 16 else tx}")
            except Exception as exc:
                logger.warning("batch redeem failed: %s", exc)
                print(f"         falling back to one-by-one ({exc})")
                for job in batch:
                    try:
                        assert relayer is not None
                        tx = relayer.redeem_batch([(job.condition_id or "", job.negative_risk)])
                        report.redeemed += 1
                        print(f"         -> OK  {job.summary()}")
                    except Exception as inner:
                        report.failed += 1
                        report.errors.append(f"redeem {job.summary()}: {inner}")
                        print(f"         -> FAIL  {job.summary()}  {inner}")
                    time.sleep(1)
            time.sleep(1)

    for job in jobs:
        if job.kind == "skip":
            report.skipped += 1
            continue
        if job.kind != "sell":
            continue
        print(f"         {job.summary()}")
        if dry_run:
            report.sold += 1
            print("         -> dry-run")
            continue
        if orders is None or orders.client is None:
            report.failed += 1
            report.errors.append(f"sell {job.summary()}: CLOB client not connected")
            print("         -> FAIL  no CLOB client")
            continue
        try:
            result = orders.place_fak_sell(
                token_id=job.token_id or "",
                shares=job.size,
                limit=job.bid or 0.01,
            )
            if not result.ok:
                raise RuntimeError(result.error or "sell rejected")
            report.sold += 1
            print("         -> OK")
        except Exception as exc:
            report.failed += 1
            report.errors.append(f"sell {job.summary()}: {exc}")
            print(f"         -> FAIL  {exc}")
        time.sleep(1)

    print(
        f"   redeemed: {report.redeemed}  |  sold: {report.sold}  |  "
        f"skipped: {report.skipped}  |  failed: {report.failed}"
    )
    return report
