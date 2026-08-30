import { fmtUsd } from "./stats.js";
import { tradePnl } from "./parse.js";

export function fmtTs(ts) {
  if (!ts) return "—";
  return new Date(ts * 1000).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function fmtOdds(n) {
  if (n == null || Number.isNaN(n)) return "—";
  return `${(n * 100).toFixed(1)}¢`;
}

export function fmtNum(n, d = 2) {
  if (n == null || Number.isNaN(n)) return "—";
  return n.toFixed(d);
}

export function slugUrl(slug) {
  return `https://polymarket.com/event/${encodeURIComponent(slug ?? "")}`;
}

export function slugLabel(slug) {
  const m = (slug ?? "").match(/-(\d{10,})$/);
  if (m) {
    const d = new Date(Number(m[1]) * 1000);
    return d.toLocaleString(undefined, {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  }
  return "Market";
}

export function fmtSide(t) {
  const side = (t.side ?? "—").toUpperCase();
  if (t.flipped && t.entrySide && t.entrySide !== t.side) {
    return `${side}←${t.entrySide.toUpperCase()}`;
  }
  return side;
}

export function fmtFill(t) {
  if (t.flipped && t.flipBuy?.price != null) {
    return `${fmtOdds(t.fillPrice)}→${fmtOdds(t.flipBuy.price)}`;
  }
  return fmtOdds(t.fillPrice);
}

export function outcomeLabel(t) {
  if (!t.resolved) return "open";
  if (t.won === true) return "won";
  if (t.won === false) return "lost";
  return "—";
}

export function pnlAtStake(t) {
  return fmtUsd(tradePnl(t));
}
