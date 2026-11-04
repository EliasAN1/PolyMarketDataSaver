package com.eliasan.centionaire

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test

class NotifyCopyTest {
    private fun entry(
        order: String,
        side: String = "UP",
        price: Double = 0.52,
        stake: Double = 50.0,
        start: Long = 1728374400,
        end: Long = 1728374700,
    ) = JSONObject()
        .put("id", "entry:$order:1000")
        .put("event", "entry")
        .put("order_id", order)
        .put("slug", "btc-updown-5m-$start")
        .put("side", side)
        .put("fill_price", price)
        .put("stake_usd", stake)
        .put("window_start_ts", start)
        .put("window_end_ts", end)

    private fun resolve(
        order: String,
        won: Boolean = true,
        pnl: Double = 42.50,
        start: Long = 1728374400,
        end: Long = 1728374700,
    ) = JSONObject()
        .put("id", "resolve:$order:1050")
        .put("event", "resolve")
        .put("order_id", order)
        .put("slug", "btc-updown-5m-$start")
        .put("won", won)
        .put("net_pnl_usd", pnl)
        .put("window_start_ts", start)
        .put("window_end_ts", end)

    @Test
    fun singleResolvedTradeFormatsClearly() {
        val e = entry("o1", side = "UP", price = 0.52, stake = 50.0)
        val r = resolve("o1", won = true, pnl = 42.50)
        val data = NotifyCopy.summary(null, listOf(r), listOf(e, r), null)

        assertTrue(data.hasContent)
        assertEquals("Won +$42.50", data.title)
        assertTrue(data.text.contains("Bought UP"))
        assertTrue(data.text.contains("52¢"))
        assertTrue(data.text.contains("$50"))
    }

    @Test
    fun multipleResolvedTradesFormatAggregateAndBullets() {
        val e1 = entry("o1", side = "UP", price = 0.52, stake = 50.0, start = 1728374400, end = 1728374700)
        val r1 = resolve("o1", won = true, pnl = 42.50, start = 1728374400, end = 1728374700)

        val e2 = entry("o2", side = "DOWN", price = 0.54, stake = 50.0, start = 1728375000, end = 1728375300)
        val r2 = resolve("o2", won = false, pnl = -50.00, start = 1728375000, end = 1728375300)

        val all = listOf(e1, r1, e2, r2)
        val data = NotifyCopy.summary(null, listOf(r1, r2), all, null)

        assertTrue(data.hasContent)
        assertTrue(data.title.contains("−$7.50"))
        assertTrue(data.title.contains("2 trades (1 won, 1 lost)"))
        assertTrue(data.text.contains("1 won, 1 lost"))
        assertTrue(data.bigText.contains("• Won +$42.50"))
        assertTrue(data.bigText.contains("• Lost −$50"))
    }

    @Test
    fun onlyLiveTradeReportsOpenPosition() {
        val liveObj = JSONObject()
            .put("traded", true)
            .put("slug", "btc-updown-5m-1728374400")
            .put("side", "UP")
            .put("seconds_left", 145)
            .put("order", JSONObject().put("ok", true).put("limit", 0.52))

        val liveInfo = NotifyCopy.buildLiveTradeInfo(liveObj, emptyList())
        assertNotNull(liveInfo)
        assertEquals("UP", liveInfo!!.side)
        assertEquals(0.52, liveInfo.fillPrice, 0.001)

        val data = NotifyCopy.summary(null, emptyList(), emptyList(), liveInfo)
        assertTrue(data.hasContent)
        assertEquals("Live Trade: UP", data.title)
        assertTrue(data.text.contains("Bought UP"))
        assertTrue(data.text.contains("52¢"))
        assertTrue(data.text.contains("2m 25s left"))
    }

    @Test
    fun liveTradeCombinedWithResolvedTrades() {
        val e = entry("o1", side = "UP", price = 0.52, stake = 50.0)
        val r = resolve("o1", won = true, pnl = 42.50)

        val liveObj = JSONObject()
            .put("traded", true)
            .put("slug", "btc-updown-5m-1728375000")
            .put("side", "DOWN")
            .put("seconds_left", 120)
            .put("order", JSONObject().put("ok", true).put("limit", 0.53))

        val liveInfo = NotifyCopy.buildLiveTradeInfo(liveObj, emptyList())
        val data = NotifyCopy.summary(null, listOf(r), listOf(e, r), liveInfo)

        assertTrue(data.hasContent)
        assertTrue(data.title.contains("+$42.50"))
        assertTrue(data.title.contains("Live: DOWN"))
        assertTrue(data.text.contains("LIVE: DOWN 53¢ (2m left)"))
        assertTrue(data.bigText.contains("• LIVE: Bought DOWN"))
    }

    @Test
    fun emptyWhenNoResolvedAndNoLive() {
        val data = NotifyCopy.summary(null, emptyList(), emptyList(), null)
        assertFalse(data.hasContent)
        assertEquals("", data.title)
    }
}
