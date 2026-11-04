package com.eliasan.centionaire

import android.content.Context
import android.text.format.DateFormat
import org.json.JSONObject
import java.util.Calendar
import java.util.Locale
import kotlin.math.abs

data class SummaryData(
    val title: String,
    val text: String,
    val bigText: String,
    val hasContent: Boolean,
)

data class LiveTradeInfo(
    val side: String,
    val fillPrice: Double,
    val stakeUsd: Double,
    val secondsLeft: Int,
    val windowStartSec: Long,
    val windowEndSec: Long,
    val dryRun: Boolean,
)

data class ResolvedTradeItem(
    val won: Boolean,
    val netPnlUsd: Double,
    val side: String,
    val fillPrice: Double,
    val stakeUsd: Double,
    val windowStartSec: Long,
    val windowEndSec: Long,
    val unmatched: Boolean,
    val dryRun: Boolean,
)

object NotifyCopy {
    private val SLUG_RE = Regex(".*-(\\d+)$")

    fun notifyKey(event: JSONObject): String {
        val order = clean(event, "order_id")
        if (order.isNotEmpty()) return "order:$order"
        val slug = clean(event, "slug")
        if (slug.isNotEmpty()) return "slug:$slug"
        return clean(event, "id").ifEmpty { "event" }
    }

    fun parseSlugStart(slug: String): Long {
        val m = SLUG_RE.find(slug) ?: return 0L
        return m.groupValues[1].toLongOrNull() ?: 0L
    }

    fun buildLiveTradeInfo(
        live: JSONObject?,
        allRows: List<JSONObject>,
    ): LiveTradeInfo? {
        if (live == null) return null
        val traded = live.optBoolean("traded", false)
        if (!traded) return null
        val order = live.optJSONObject("order")
        if (order != null && order.has("ok") && !order.optBoolean("ok")) {
            return null
        }
        val secondsLeft = live.optInt("seconds_left", -1)
        val state = live.optString("state")
        if (secondsLeft == 0 && state != "sent") return null

        val slug = live.optString("slug")
        val side = (live.optString("side").ifBlank { order?.optString("side").orEmpty() }).uppercase().ifBlank { "UP" }

        val entry = if (slug.isNotEmpty()) {
            allRows.lastOrNull { it.optString("slug") == slug && it.optString("event") == "entry" }
        } else null

        var price = live.optDouble("fill_price", Double.NaN)
        if (price.isNaN() && order != null) price = order.optDouble("limit", Double.NaN)
        if (price.isNaN() && entry != null) price = entry.optDouble("fill_price", Double.NaN)

        var stake = live.optDouble("stake_usd", Double.NaN)
        if (stake.isNaN() && entry != null) stake = entry.optDouble("stake_usd", Double.NaN)

        var start = live.optLong("window_start_ts", 0L)
        var end = live.optLong("window_end_ts", 0L)
        if (start == 0L && entry != null) start = entry.optLong("window_start_ts", 0L)
        if (end == 0L && entry != null) end = entry.optLong("window_end_ts", 0L)
        if (start == 0L && slug.isNotEmpty()) {
            val parsed = parseSlugStart(slug)
            if (parsed > 0) {
                start = parsed
                end = parsed + 300L
            }
        }

        val dryRun = order?.optBoolean("dry_run", false) ?: (entry?.optBoolean("dry_run", false) ?: false)

        return LiveTradeInfo(
            side = side,
            fillPrice = price,
            stakeUsd = stake,
            secondsLeft = secondsLeft.coerceAtLeast(0),
            windowStartSec = start,
            windowEndSec = end,
            dryRun = dryRun,
        )
    }

    fun extractResolvedTrades(
        events: List<JSONObject>,
        allRows: List<JSONObject>,
    ): List<ResolvedTradeItem> {
        val items = mutableListOf<ResolvedTradeItem>()
        val entriesByOrder = mutableMapOf<String, JSONObject>()
        val entriesBySlug = mutableMapOf<String, JSONObject>()
        for (row in allRows) {
            if (row.optString("event") == "entry") {
                val oid = row.optString("order_id")
                if (oid.isNotEmpty()) entriesByOrder[oid] = row
                val slug = row.optString("slug")
                if (slug.isNotEmpty()) entriesBySlug[slug] = row
            }
        }

        val groups = events.groupBy { notifyKey(it) }
        for ((_, groupRows) in groups) {
            val resolve = groupRows.lastOrNull { it.optString("event") == "resolve" } ?: continue
            val oid = resolve.optString("order_id")
            val slug = resolve.optString("slug")
            val entry = groupRows.lastOrNull { it.optString("event") == "entry" }
                ?: (if (oid.isNotEmpty()) entriesByOrder[oid] else null)
                ?: (if (slug.isNotEmpty()) entriesBySlug[slug] else null)

            val won = resolve.optBoolean("won")
            val pnl = resolve.optDouble("net_pnl_usd", Double.NaN)
            val side = (resolve.optString("side").ifBlank { entry?.optString("side").orEmpty() }).uppercase().ifBlank { "—" }
            var fillPrice = resolve.optDouble("fill_price", Double.NaN)
            if (fillPrice.isNaN() && entry != null) fillPrice = entry.optDouble("fill_price", Double.NaN)

            var stakeUsd = resolve.optDouble("stake_usd", Double.NaN)
            if (stakeUsd.isNaN() && entry != null) stakeUsd = entry.optDouble("stake_usd", Double.NaN)

            val (rStart, rEnd) = windowOf(resolve)
            val (eStart, eEnd) = if (entry != null) windowOf(entry) else 0L to 0L
            var start = if (rStart > 0) rStart else eStart
            var end = if (rEnd > 0) rEnd else eEnd
            if (start == 0L && slug.isNotEmpty()) {
                val s = parseSlugStart(slug)
                if (s > 0) {
                    start = s
                    end = s + 300L
                }
            }

            items.add(
                ResolvedTradeItem(
                    won = won,
                    netPnlUsd = pnl,
                    side = side,
                    fillPrice = fillPrice,
                    stakeUsd = stakeUsd,
                    windowStartSec = start,
                    windowEndSec = end,
                    unmatched = resolve.optBoolean("unmatched"),
                    dryRun = resolve.optBoolean("dry_run") || (entry?.optBoolean("dry_run") == true),
                )
            )
        }
        return items
    }

    fun summary(
        context: Context?,
        events: List<JSONObject>,
        allRows: List<JSONObject>,
        liveInfo: LiveTradeInfo?,
    ): SummaryData {
        val resolved = extractResolvedTrades(events, allRows)
        if (resolved.isEmpty() && liveInfo == null) {
            return SummaryData("", "", "", false)
        }

        val dryRun = resolved.any { it.dryRun } || (liveInfo?.dryRun == true)
        val prefix = if (dryRun) "Paper · " else ""

        // Case 1: Only live open trade active (no completed trades)
        if (resolved.isEmpty() && liveInfo != null) {
            val title = "${prefix}Live Trade: ${liveInfo.side}"
            val winStr = formatWindow(context, liveInfo.windowStartSec, liveInfo.windowEndSec)
            val leftStr = formatTimeLeft(liveInfo.secondsLeft)
            val bits = listOfNotNull(
                "Bought ${liveInfo.side}",
                formatCents(liveInfo.fillPrice),
                formatStake(liveInfo.stakeUsd),
                leftStr,
            )
            val text = "${bits.joinToString(" · ")}${if (winStr.isNotEmpty()) " ($winStr)" else ""}"
            val bigText = buildString {
                appendLine("${prefix}Live trade open")
                append("• ${bits.joinToString(" · ")}")
                if (winStr.isNotEmpty()) append(" ($winStr)")
            }
            return SummaryData(title, text, bigText, true)
        }

        // Case 2: Exactly 1 trade resolved, no live open trade
        if (resolved.size == 1 && liveInfo == null) {
            val t = resolved[0]
            val verb = if (t.unmatched) (if (t.won) "Would have won" else "Would have lost")
            else (if (t.won) "Won" else "Lost")
            val pnlStr = if (!t.netPnlUsd.isNaN()) {
                if (t.won) "+$${trimMoney(t.netPnlUsd)}" else "-$${trimMoney(abs(t.netPnlUsd))}"
            } else ""
            val title = "$prefix$verb${if (pnlStr.isNotEmpty()) " $pnlStr" else ""}"
            val winStr = formatWindow(context, t.windowStartSec, t.windowEndSec)
            val bought = if (t.unmatched) "No fill · ${t.side}" else "Bought ${t.side}"
            val bits = listOfNotNull(
                bought,
                formatCents(t.fillPrice),
                formatStake(t.stakeUsd),
                winStr.ifEmpty { null },
            )
            val text = bits.joinToString(" · ")
            val bigText = buildString {
                appendLine(title)
                append("• $text")
            }
            return SummaryData(title, text, bigText, true)
        }

        // Case 3: Multiple trades resolved, no live open trade
        if (resolved.size > 1 && liveInfo == null) {
            val totalPnl = resolved.map { if (it.netPnlUsd.isNaN()) 0.0 else it.netPnlUsd }.sum()
            val wonCount = resolved.count { it.won }
            val lostCount = resolved.size - wonCount
            val title = "${prefix}${signedMoney(totalPnl)} · ${resolved.size} trades ($wonCount won, $lostCount lost)"

            val minStart = resolved.map { it.windowStartSec }.filter { it > 0 }.minOrNull() ?: 0L
            val maxEnd = resolved.map { it.windowEndSec }.filter { it > 0 }.maxOrNull() ?: 0L
            val winSpan = formatWindow(context, minStart, maxEnd)
            val text = listOfNotNull(winSpan.ifEmpty { null }, "$wonCount won, $lostCount lost").joinToString(" · ")

            val bigText = buildString {
                appendLine(title)
                for (t in resolved) {
                    val verb = if (t.won) "Won" else "Lost"
                    val pnl = if (!t.netPnlUsd.isNaN()) signedMoney(t.netPnlUsd) else ""
                    val w = formatWindow(context, t.windowStartSec, t.windowEndSec)
                    val price = formatCents(t.fillPrice)
                    val sidePrice = listOfNotNull(t.side, price).joinToString(" ")
                    append("• $verb $pnl · $sidePrice")
                    if (w.isNotEmpty()) append(" ($w)")
                    appendLine()
                }
            }.trimEnd()
            return SummaryData(title, text, bigText, true)
        }

        // Case 4: Resolved trades + Live open trade active
        val nonNullLive = liveInfo!!
        val totalPnl = resolved.map { if (it.netPnlUsd.isNaN()) 0.0 else it.netPnlUsd }.sum()
        val wonCount = resolved.count { it.won }
        val lostCount = resolved.size - wonCount
        val liveLeft = formatTimeLeft(nonNullLive.secondsLeft)
        val livePrice = formatCents(nonNullLive.fillPrice)
        val liveSidePrice = listOfNotNull(nonNullLive.side, livePrice).joinToString(" ")

        val title = if (resolved.size == 1) {
            val t = resolved[0]
            val pnl = if (!t.netPnlUsd.isNaN()) signedMoney(t.netPnlUsd) else ""
            "${prefix}$pnl · 1 ${if (t.won) "won" else "lost"} · Live: ${nonNullLive.side}"
        } else {
            "${prefix}${signedMoney(totalPnl)} · ${resolved.size} trades · Live: ${nonNullLive.side}"
        }

        val text = if (resolved.size == 1) {
            val t = resolved[0]
            val verb = if (t.won) "Won" else "Lost"
            val pnl = if (!t.netPnlUsd.isNaN()) signedMoney(t.netPnlUsd) else ""
            "$verb $pnl · LIVE: $liveSidePrice ($liveLeft)"
        } else {
            "$wonCount won, $lostCount lost · LIVE: $liveSidePrice ($liveLeft)"
        }

        val bigText = buildString {
            appendLine(title)
            for (t in resolved) {
                val verb = if (t.won) "Won" else "Lost"
                val pnl = if (!t.netPnlUsd.isNaN()) signedMoney(t.netPnlUsd) else ""
                val w = formatWindow(context, t.windowStartSec, t.windowEndSec)
                val price = formatCents(t.fillPrice)
                val sidePrice = listOfNotNull(t.side, price).joinToString(" ")
                append("• $verb $pnl · $sidePrice")
                if (w.isNotEmpty()) append(" ($w)")
                appendLine()
            }
            val liveWin = formatWindow(context, nonNullLive.windowStartSec, nonNullLive.windowEndSec)
            val liveStake = formatStake(nonNullLive.stakeUsd)
            val liveBits = listOfNotNull("LIVE: Bought ${nonNullLive.side}", livePrice, liveStake, liveLeft).joinToString(" · ")
            append("• $liveBits")
            if (liveWin.isNotEmpty()) append(" ($liveWin)")
        }.trimEnd()

        return SummaryData(title, text, bigText, true)
    }

    fun pair(context: Context?, event: JSONObject): Pair<String, String> {
        val kind = event.optString("event")
        val side = event.optString("side").uppercase().ifBlank { "—" }
        val prefix = if (event.optBoolean("dry_run")) "Paper · " else ""
        val window = formatWindow(context, event)
        return when (kind) {
            "entry" -> {
                val details = listOfNotNull(window.ifBlank { null }, formatCents(event.optDouble("fill_price", Double.NaN)), formatStake(event.optDouble("stake_usd", Double.NaN)))
                    .joinToString(" · ")
                "${prefix}Bought $side" to details.ifBlank { "Filled" }
            }
            "resolve" -> {
                val unmatched = event.optBoolean("unmatched")
                val won = event.optBoolean("won")
                val result = resultLine(won, event.optDouble("net_pnl_usd", Double.NaN), unmatched)
                val bought = if (unmatched) "No fill · $side" else "Bought $side"
                val details = listOf(bought, window).filter { it.isNotBlank() }.joinToString(" · ")
                "$prefix$result" to details.ifBlank { "Settled" }
            }
            else -> "" to ""
        }
    }

    fun formatWindow(context: Context?, event: JSONObject): String {
        val (start, end) = windowOf(event)
        return formatWindow(context, start, end)
    }

    fun formatWindow(context: Context?, startSec: Long, endSec: Long): String {
        if (startSec <= 0 || endSec <= 0) return ""
        val use24 = runCatching { context?.let { DateFormat.is24HourFormat(it) } ?: false }.getOrDefault(false)
        val (startClock, startAmPm) = clock(startSec * 1000, use24)
        val (endClock, endAmPm) = clock(endSec * 1000, use24)
        return if (use24 || startAmPm == endAmPm) {
            "$startClock–$endClock${if (use24) "" else " $endAmPm"}"
        } else {
            "$startClock $startAmPm–$endClock $endAmPm"
        }
    }

    fun formatTimeLeft(seconds: Int): String {
        if (seconds <= 0) return "settling"
        val m = seconds / 60
        val s = seconds % 60
        return if (m > 0 && s > 0) "${m}m ${s}s left"
        else if (m > 0) "${m}m left"
        else "${s}s left"
    }

    fun formatCents(price: Double): String? {
        if (price.isNaN() || price <= 0.0) return null
        val c = price * 100.0
        val text = if (abs(c - c.toLong()) < 0.05) "${c.toLong()}" else "%.1f".format(Locale.US, c)
        return "${text}¢"
    }

    fun formatStake(stake: Double): String? {
        if (stake.isNaN() || stake <= 0.0) return null
        return "$${trimMoney(stake)}"
    }

    fun signedMoney(value: Double): String {
        if (value.isNaN()) return "$0"
        val sign = if (value >= 0) "+" else "−"
        return "$sign$${trimMoney(abs(value))}"
    }

    fun trimMoney(value: Double): String {
        if (value.isNaN()) return "0"
        val text = "%.2f".format(Locale.US, value)
        return if (text.endsWith(".00")) text.dropLast(3) else text
    }

    private fun resultLine(won: Boolean, pnl: Double, unmatched: Boolean = false): String {
        val verb = if (unmatched) {
            if (won) "Would have won" else "Would have lost"
        } else if (won) {
            "Won"
        } else {
            "Lost"
        }
        return when {
            pnl.isNaN() -> verb
            won -> "$verb +$${trimMoney(pnl)}"
            else -> "$verb -$${trimMoney(abs(pnl))}"
        }
    }

    private fun windowOf(event: JSONObject): Pair<Long, Long> {
        return event.optLong("window_start_ts", 0L) to event.optLong("window_end_ts", 0L)
    }

    private fun clock(ms: Long, use24: Boolean): Pair<String, String> {
        val cal = Calendar.getInstance()
        cal.timeInMillis = ms
        val minute = cal.get(Calendar.MINUTE)
        return if (use24) {
            "%d:%02d".format(Locale.US, cal.get(Calendar.HOUR_OF_DAY), minute) to ""
        } else {
            val hour = cal.get(Calendar.HOUR).let { if (it == 0) 12 else it }
            val ampm = if (cal.get(Calendar.AM_PM) == Calendar.AM) "AM" else "PM"
            "%d:%02d".format(Locale.US, hour, minute) to ampm
        }
    }

    private fun clean(event: JSONObject, key: String): String {
        if (!event.has(key) || event.isNull(key)) return ""
        return event.optString(key).trim().let { if (it == "null") "" else it }
    }
}
