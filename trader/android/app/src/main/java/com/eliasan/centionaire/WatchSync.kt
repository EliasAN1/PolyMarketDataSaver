package com.eliasan.centionaire

import android.app.PendingIntent
import android.content.Context
import android.content.ContextWrapper
import android.content.Intent
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

/** One bounded fetch. No service lifetime, polling loop, or application wake lock. */
class WatchSync(context: Context) : ContextWrapper(context.applicationContext) {
    fun check(network: android.net.Network? = null) {
        val base = Prefs.url(this)
        if (base.isBlank() || !WatchScheduler.canConnect(this)) return
        if (!NotificationManagerCompat.from(this).areNotificationsEnabled()) return
        if (getSystemService(android.app.NotificationManager::class.java)
                .getNotificationChannel(App.CHANNEL_RESULTS)?.importance == android.app.NotificationManager.IMPORTANCE_NONE) return
        val lastPoll = Prefs.lastPollMs(this)
        val started = System.currentTimeMillis()
        val suffix = if (lastPoll > 0) "?since=${(lastPoll / 1000L - 15L).coerceAtLeast(0L)}" else ""
        val connectionNetwork = network ?: WatchScheduler.connectionNetwork(this) ?: return
        val conn = connectionNetwork.openConnection(URL("$base/api/notify$suffix")) as HttpURLConnection
        conn.connectTimeout = 8_000
        conn.readTimeout = 8_000
        conn.setRequestProperty("Accept", "application/json")
        try {
            if (conn.responseCode !in 200..299) return
            val json = conn.inputStream.bufferedReader().use { JSONObject(it.readText()) }
            if (Thread.currentThread().isInterrupted || base != Prefs.url(this)) return
            val events = json.getJSONArray("events")
            val seen = Prefs.seenIds(this)
            val rows = (0 until events.length()).map { events.getJSONObject(it) }
            val batch = RecapBatch.select(rows, seen, lastPoll)
            val now = json.optLong("server_ts", 0L).let { if (it > 0) it * 1000L else started }
            val liveObj = json.optJSONObject("live")
            val liveInfo = NotifyCopy.buildLiveTradeInfo(liveObj, rows)

            val summaryData = NotifyCopy.summary(this, batch, rows, liveInfo)
            if (summaryData.hasContent) {
                postSummaryNotice(summaryData)
                for (event in batch) cancelNotice(idHash(NotifyCopy.notifyKey(event)))
                cancelNotice(PAPER_SUMMARY_NOTIFY_ID)
            } else {
                cancelNotice(SUMMARY_NOTIFY_ID)
            }

            liveObj?.let {
                Prefs.setLive(this, it.optString("state"), it.optString("slug"), it.optString("side"))
            }
            for (event in rows) event.optString("id").takeIf { it.isNotEmpty() }?.let { seen.add(it) }
            Prefs.setCheckpoint(this, seen, now)
        } catch (e: Exception) {
            // Keep the checkpoint so the next connection includes the whole away period.
            Log.d("CentionaireSync", "Report check did not complete", e)
        } finally {
            conn.disconnect()
        }
    }

    fun checkpointSilently(network: android.net.Network? = null) {
        val base = Prefs.url(this)
        if (base.isBlank() || !WatchScheduler.canConnect(this)) return
        val connectionNetwork = network ?: WatchScheduler.connectionNetwork(this) ?: return
        val conn = connectionNetwork.openConnection(URL("$base/api/notify")) as HttpURLConnection
        conn.connectTimeout = 6_000
        conn.readTimeout = 6_000
        conn.setRequestProperty("Accept", "application/json")
        try {
            if (conn.responseCode !in 200..299) return
            val json = conn.inputStream.bufferedReader().use { JSONObject(it.readText()) }
            if (Thread.currentThread().isInterrupted || base != Prefs.url(this)) return
            val events = json.getJSONArray("events")
            val seen = Prefs.seenIds(this)
            val rows = (0 until events.length()).map { events.getJSONObject(it) }
            val now = json.optLong("server_ts", 0L).let { if (it > 0) it * 1000L else System.currentTimeMillis() }

            for (event in rows) {
                event.optString("id").takeIf { it.isNotEmpty() }?.let { seen.add(it) }
            }
            Prefs.setCheckpoint(this, seen, now)

            val liveObj = json.optJSONObject("live")
            val liveInfo = NotifyCopy.buildLiveTradeInfo(liveObj, rows)
            if (liveInfo == null) {
                cancelNotice(SUMMARY_NOTIFY_ID)
            }
        } catch (e: Exception) {
            Log.d("CentionaireSync", "Silent checkpoint did not complete", e)
        } finally {
            conn.disconnect()
        }
    }

    private fun postSummaryNotice(data: SummaryData) {
        if (android.os.Build.VERSION.SDK_INT >= 33 &&
            androidx.core.content.ContextCompat.checkSelfPermission(this, android.Manifest.permission.POST_NOTIFICATIONS)
            != android.content.pm.PackageManager.PERMISSION_GRANTED
        ) {
            throw SecurityException("Notification permission is disabled")
        }
        val open = PendingIntent.getActivity(
            this,
            SUMMARY_NOTIFY_ID,
            Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        val style = NotificationCompat.BigTextStyle()
            .bigText(data.bigText)
        val notification = NotificationCompat.Builder(this, App.CHANNEL_RESULTS)
            .setSmallIcon(R.drawable.ic_mark)
            .setContentTitle(data.title)
            .setContentText(data.text)
            .setStyle(style)
            .setCategory(NotificationCompat.CATEGORY_STATUS)
            .setContentIntent(open)
            .setAutoCancel(true)
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .extend(NotificationCompat.CarExtender())
            .build()
        NotificationManagerCompat.from(this).notify(SUMMARY_NOTIFY_ID, notification)
        Prefs.setLastAlert(this, data.title, data.text)
    }

    private fun notifyEvent(event: JSONObject) {
        val kind = event.optString("event")
        if (event.optBoolean("unmatched") && kind == "entry") return
        val (title, text) = NotifyCopy.pair(this, event)
        if (title.isEmpty()) return
        val channel = if (kind == "resolve") App.CHANNEL_RESULTS else App.CHANNEL_TRADES
        postNotice(idHash(NotifyCopy.notifyKey(event)), channel, title, text)
    }

    private fun postNotice(id: Int, channel: String, title: String, text: String) {
        if (android.os.Build.VERSION.SDK_INT >= 33 &&
            androidx.core.content.ContextCompat.checkSelfPermission(this, android.Manifest.permission.POST_NOTIFICATIONS)
            != android.content.pm.PackageManager.PERMISSION_GRANTED
        ) {
            throw SecurityException("Notification permission is disabled")
        }
        val open = PendingIntent.getActivity(
            this,
            id,
            Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        val style = NotificationCompat.BigTextStyle()
            .bigText(text)
        val notification = NotificationCompat.Builder(this, channel)
            .setSmallIcon(R.drawable.ic_mark)
            .setContentTitle(title)
            .setContentText(text)
            .setStyle(style)
            .setCategory(NotificationCompat.CATEGORY_STATUS)
            .setContentIntent(open)
            .setAutoCancel(true)
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .extend(NotificationCompat.CarExtender())
            .build()
        NotificationManagerCompat.from(this).notify(id, notification)
        Prefs.setLastAlert(this, title, text)
    }

    private fun cancelNotice(id: Int) {
        try {
            NotificationManagerCompat.from(this).cancel(id)
        } catch (_: Exception) {
            // Ignore
        }
    }

    fun postTest(kind: String) {
        val now = System.currentTimeMillis() / 1000
        val start = now - (now % 300)
        val sample = JSONObject()
            .put("id", "test:$kind:$now")
            .put("event", kind)
            .put("order_id", "test-order")
            .put("slug", "btc-updown-5m-$start")
            .put("side", "UP")
            .put("fill_price", 0.53)
            .put("stake_usd", 50.0)
            .put("window_start_ts", start)
            .put("window_end_ts", start + 300)
            .put("won", kind == "resolve")
            .put("dry_run", true)
        if (kind == "resolve") sample.put("net_pnl_usd", 42.68)
        notifyEvent(sample)
    }

    fun postTestSummary() {
        val now = System.currentTimeMillis() / 1000
        val end = now - (now % 300)
        val events = listOf(
            fakeResolved("a", "UP", true, 42.68, end - 900, end - 600),
            fakeResolved("b", "DOWN", false, -51.52, end - 600, end - 300),
            fakeEntry("c", "UP", end - 300, end),
        )
        for (event in events) cancelNotice(idHash(NotifyCopy.notifyKey(event)))
        val summaryData = NotifyCopy.summary(this, events, events, null)
        if (summaryData.hasContent) {
            postSummaryNotice(summaryData)
        }
    }

    private fun fakeResolved(
        order: String,
        side: String,
        won: Boolean,
        pnl: Double,
        start: Long,
        end: Long,
    ): JSONObject {
        return JSONObject()
            .put("id", "test:resolve:$order")
            .put("event", "resolve")
            .put("order_id", "test-$order")
            .put("side", side)
            .put("won", won)
            .put("net_pnl_usd", pnl)
            .put("fill_price", 0.52)
            .put("stake_usd", 50.0)
            .put("window_start_ts", start)
            .put("window_end_ts", end)
            .put("dry_run", true)
    }

    private fun fakeEntry(order: String, side: String, start: Long, end: Long): JSONObject {
        return JSONObject()
            .put("id", "test:entry:$order")
            .put("event", "entry")
            .put("order_id", "test-$order")
            .put("side", side)
            .put("fill_price", 0.53)
            .put("stake_usd", 50.0)
            .put("window_start_ts", start)
            .put("window_end_ts", end)
            .put("dry_run", true)
    }

    private fun idHash(id: String): Int {
        val hash = id.hashCode() and 0x7fffffff
        return if (hash < 10) hash + 10 else hash
    }

    companion object {
        private const val SUMMARY_NOTIFY_ID = 884201
        private const val PAPER_SUMMARY_NOTIFY_ID = 884202
    }
}
