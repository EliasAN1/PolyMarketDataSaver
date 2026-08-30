/** Parse JSONL trade logs — entry, optional flip legs, and resolve. */

function num(v) {
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

function flipLeg(row, priceKey, sharesKey, feeKey, { strict = false } = {}) {
  if (!row) return null;
  const explicitPrice = num(row[priceKey]);
  const explicitShares = num(row[sharesKey]);
  if (strict && explicitPrice == null && explicitShares == null) return null;
  const price = explicitPrice ?? num(row.fill_price);
  const shares = explicitShares ?? num(row.fill_shares);
  if (price == null && shares == null) return null;
  return {
    price,
    shares,
    fee: num(row[feeKey] ?? row.fee_usd),
    side: row.flip_buy_side ?? row.side ?? null,
  };
}

function cashoutMatchesEntry(row, entry) {
  if (!row) return false;
  const side = row.side;
  if (side && entry.side && side !== entry.side) return false;
  if (row.inverted != null && !!row.inverted !== !!entry.inverted) return false;
  return true;
}

/** Auto cash-out bid threshold (matches strategy eighty_cent). */
export function cashoutBidThreshold(
  fillPrice,
  inverted,
  { invertMult = 2, origRoi = 0.8 } = {},
) {
  const p = Number(fillPrice);
  const mult = Number(invertMult);
  const roi = Number(origRoi);
  if (!Number.isFinite(p) || p <= 0) return null;
  if (inverted) {
    if (!Number.isFinite(mult) || mult <= 0) return null;
    return p * mult;
  }
  if (!Number.isFinite(roi) || roi <= 0 || roi > 1) return null;
  return p + roi * (1 - p);
}

export function oddsToneClass(oddsFrac) {
  if (oddsFrac == null || !Number.isFinite(oddsFrac)) return "";
  if (oddsFrac >= 0.55) return " odds-strong";
  if (oddsFrac < 0.45) return " odds-weak";
  return " odds-neutral";
}

export function cashoutPnlUsd(t) {
  if (!t.cashout?.price || !t.cashout?.shares) return null;
  const entryCost = (t.fillPrice ?? 0) * (t.fillShares ?? 0) + (t.feeUsd ?? 0);
  const exitProceeds = t.cashout.price * t.cashout.shares - (t.cashout.fee ?? 0);
  return exitProceeds - entryCost;
}

/** Dollars won/lost on this fill — not a rescaled stake. */
export function tradePnl(t) {
  if (t.resolved && t.netPnl != null && Number.isFinite(t.netPnl)) return t.netPnl;
  if (t.cashedOut) return cashoutPnlUsd(t);
  return null;
}

/** P&L per stake unit — resolve row when present, else cashout estimate. */
export function effectiveNormPnl(t) {
  const usd = tradePnl(t);
  if (usd != null && t.stake > 0) return usd / t.stake;
  return null;
}

export function effectiveWon(t) {
  if (t.resolved && t.won != null) return t.won;
  if (t.cashedOut) {
    const pnl = cashoutPnlUsd(t);
    if (pnl != null) return pnl > 0;
  }
  return null;
}

/** Closed for stats: resolved at expiry or cashed out early. */
export function isStatClosed(t) {
  return (t.resolved && t.won != null) || t.cashedOut;
}

export function tradeStratLabel(t) {
  if (t.cashedOut) return "Cashed out";
  if (t.flipped) {
    return t.flipSell ||
      (t.flipBuy?.side && t.entrySide && t.flipBuy.side !== t.entrySide)
      ? "Flip"
      : "Add";
  }
  return t.inverted ? "Inv" : "Orig";
}

export function bandLabel(t) {
  if (t.cashedOut) return "out";
  if (t.flipped) return "flip";
  if (t.inverted) return "inv";
  return "orig";
}

export function parseJsonl(text) {
  const records = [];
  for (const line of text.split(/\r?\n/)) {
    const t = line.trim();
    if (!t) continue;
    try {
      records.push(JSON.parse(t));
    } catch {
      /* ignore bad lines */
    }
  }
  return records;
}

export function localHour(ts) {
  return new Date((ts ?? 0) * 1000).getHours();
}

export function localDateKey(ts) {
  const d = new Date((ts ?? 0) * 1000);
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

export function formatDayLabel(dayKey) {
  if (!dayKey) return "";
  const d = new Date(`${dayKey}T12:00:00`);
  if (Number.isNaN(d.getTime())) return dayKey;
  return d.toLocaleDateString(undefined, {
    weekday: "short",
    month: "short",
    day: "numeric",
  });
}

/**
 * Build one row per filled order from entry + optional flip legs + resolve.
 * Trades are shown exactly as logged — no what-if recalc.
 */
export function buildTrades(records) {
  const entries = new Map();
  const resolves = new Map();
  const flipsBySlug = new Map();
  const order = [];

  for (const r of records) {
    const ev = r.event;
    if (ev === "skip") continue;

    if (ev === "entry") {
      const oid = r.order_id;
      if (!oid) continue;
      if (!entries.has(oid)) {
        entries.set(oid, r);
        order.push(oid);
      }
    } else if (ev === "flip_sell" || ev === "flip_buy" || ev === "cashout") {
      const slug = r.slug;
      if (!slug) continue;
      let flip = flipsBySlug.get(slug);
      if (!flip) {
        flip = { sell: null, buy: null, cashouts: [] };
        flipsBySlug.set(slug, flip);
      }
      if (ev === "flip_sell") flip.sell = r;
      else if (ev === "flip_buy") flip.buy = r;
      else flip.cashouts.push(r);
    } else if (ev === "resolve") {
      const oid = r.order_id;
      if (oid) {
        resolves.set(oid, r);
      } else {
        for (const [eoid, e] of entries.entries()) {
          if (e.slug !== r.slug) continue;
          if (
            e.side === r.side ||
            Math.abs((e.fill_price ?? 0) - (r.fill_price ?? 0)) < 1e-6 ||
            r.flip_buy_side != null
          ) {
            resolves.set(eoid, r);
            break;
          }
        }
      }
    }
  }

  const trades = [];
  for (const oid of order) {
    const e = entries.get(oid);
    const rv = resolves.get(oid);
    if (!e) continue;

    const flip = flipsBySlug.get(e.slug);
    let flipSell = flipLeg(flip?.sell, "flip_sell_price", "flip_sell_shares", "flip_sell_fee_usd");
    let flipBuy = flipLeg(flip?.buy, "flip_buy_price", "flip_buy_shares", "flip_buy_fee_usd");

    let cashoutLeg = null;
    for (const row of flip?.cashouts ?? []) {
      if (!cashoutMatchesEntry(row, e)) continue;
      cashoutLeg = flipLeg(row, "flip_sell_price", "flip_sell_shares", "flip_sell_fee_usd");
      if (cashoutLeg) break;
    }

    if (rv) {
      const rvSell = flipLeg(rv, "flip_sell_price", "flip_sell_shares", "flip_sell_fee_usd", {
        strict: true,
      });
      const rvBuy = flipLeg(rv, "flip_buy_price", "flip_buy_shares", "flip_buy_fee_usd", {
        strict: true,
      });
      if (!flipBuy) flipBuy = rvBuy;
      if (!cashoutLeg && rvSell && !rvBuy && !flip?.sell) {
        cashoutLeg = rvSell;
      } else if (!flipSell && rvSell && !cashoutLeg) {
        flipSell = rvSell;
      }
    }

    const hasFlipEvents = !!(flip?.sell || flip?.buy);
    const cashedOut = !!cashoutLeg;
    const flipped =
      !cashedOut &&
      (!!flipBuy || hasFlipEvents || (!!flipSell && !!flipBuy));
    const entrySide = e.side ?? null;
    const finalSide = flipped
      ? (flipBuy?.side ?? rv?.side ?? entrySide)
      : (rv?.side ?? entrySide);

    const fillPrice = num(e.fill_price);
    const fillShares = num(e.fill_shares);
    const fillCost = fillPrice != null && fillShares != null ? fillPrice * fillShares : null;
    const requested = num(e.stake_usd) ?? num(e.requested_stake_usd);
    const stake = fillCost > 0 ? fillCost : requested > 0 ? requested : 1;
    const entryTs = Number(e.entry_ts ?? e.ts ?? 0);
    const windowEnd = Number(e.window_end_ts ?? rv?.window_end_ts ?? 0);
    const secLeft = windowEnd && entryTs ? windowEnd - entryTs : null;
    const netPnl = rv != null ? Number(rv.net_pnl_usd ?? 0) : null;
    const normPnl = netPnl != null && stake > 0 ? netPnl / stake : null;

    const upAsk = e.up_ask_at_entry;
    const downAsk = e.down_ask_at_entry;
    const upBid = e.up_bid_at_entry;

    trades.push({
      oid,
      slug: e.slug,
      title: e.title ?? "",
      side: finalSide,
      entrySide,
      inverted: !!e.inverted,
      flipped,
      cashedOut,
      flipSell,
      flipBuy,
      cashout: cashoutLeg,
      fillCount: rv?.fill_count != null ? Number(rv.fill_count) : flipped ? (flipSell && flipBuy ? 3 : 2) : 1,
      entryBand: e.entry_band ?? null,
      entryTs,
      windowEnd,
      secLeft,
      triggerAsk: e.trigger_ask,
      fillPrice: e.fill_price,
      fillShares: e.fill_shares,
      stake,
      feeUsd: e.fee_usd ?? null,
      upAsk,
      downAsk,
      upBid,
      downBid: e.down_bid_at_entry,
      beat: e.beat_at_entry ?? null,
      vig: upAsk != null && downAsk != null ? upAsk + downAsk : null,
      spread: upAsk != null && upBid != null ? upAsk - upBid : null,
      resolved: rv != null,
      won: rv?.won ?? null,
      outcome: rv?.outcome ?? null,
      result: rv?.result ?? null,
      netPnl,
      grossPnl: rv != null ? Number(rv.gross_pnl_usd ?? 0) : null,
      fees: rv != null ? Number(rv.total_fees_usd ?? e.fee_usd ?? 0) : null,
      normPnl,
      balancePusd: rv?.balance_pusd ?? null,
      hour: localHour(windowEnd),
      dayKey: localDateKey(windowEnd || entryTs),
    });
  }

  trades.sort((a, b) => (a.entryTs ?? 0) - (b.entryTs ?? 0));
  return trades;
}
