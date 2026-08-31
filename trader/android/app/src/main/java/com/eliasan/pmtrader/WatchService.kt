package com.eliasan.pmtrader

import android.app.Notification
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import android.os.PowerManager
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.util.concurrent.Executors
import java.util.concurrent.ScheduledFuture
import java.util.concurrent.TimeUnit

class WatchService : Service() {
    private val executor = Executors.newSingleThreadScheduledExecutor()
    private var wakeLock: PowerManager.WakeLock? = null
    private var job: ScheduledFuture<*>? = null
    private var seeded = false

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        val pm = getSystemService(POWER_SERVICE) as PowerManager
        wakeLock = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "pmtrader:watch").apply {
            setReferenceCounted(false)
            acquire()
        }
        startForegroundWatch()
        job = executor.scheduleWithFixedDelay({ tick() }, 0, 5, TimeUnit.SECONDS)
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        startForegroundWatch()
        when (intent?.getStringExtra(EXTRA_TEST)) {
            "entry" -> postTest("entry")
            "resolve" -> postTest("resolve")
        }
        return START_STICKY
    }

    override fun onDestroy() {
        job?.cancel(true)
        executor.shutdownNow()
        if (wakeLock?.isHeld == true) wakeLock?.release()
        super.onDestroy()
    }

    private fun startForegroundWatch() {
        val open = PendingIntent.getActivity(
            this,
            0,
            Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        val notification: Notification = NotificationCompat.Builder(this, PmApp.CHANNEL_WATCH)
            .setSmallIcon(R.drawable.ic_mark)
            .setContentTitle(getString(R.string.app_name))
            .setContentText(getString(R.string.watching))
            .setContentIntent(open)
            .setOngoing(true)
            .setSilent(true)
            .build()
        if (Build.VERSION.SDK_INT >= 34) {
            startForeground(1, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC)
        } else {
            startForeground(1, notification)
        }
    }

    private fun tick() {
        val base = Prefs.url(this)
        if (base.isBlank()) return
        try {
            val body = httpGet("$base/api/notify") ?: return
            val json = JSONObject(body)
            val events = json.optJSONArray("events") ?: return
            val seen = Prefs.seenIds(this)
            if (!seeded && seen.isEmpty()) {
                for (i in 0 until events.length()) {
                    val id = events.optJSONObject(i)?.optString("id").orEmpty()
                    if (id.isNotEmpty()) seen.add(id)
                }
                Prefs.setSeenIds(this, seen)
                seeded = true
                return
            }
            seeded = true
            var changed = false
            for (i in 0 until events.length()) {
                val event = events.optJSONObject(i) ?: continue
                val id = event.optString("id")
                if (id.isEmpty() || id in seen) continue
                seen.add(id)
                changed = true
                notifyEvent(event)
            }
            if (changed) Prefs.setSeenIds(this, seen)
        } catch (_: Exception) {
            // Keep watching; next tick retries.
        }
    }

    private fun notifyEvent(event: JSONObject) {
        val kind = event.optString("event")
        val slug = event.optString("slug").ifBlank { "window" }
        val side = event.optString("side").uppercase()
        val dry = event.optBoolean("dry_run")
        val prefix = if (dry) "DRY " else ""
        val (channel, title, text) = when (kind) {
            "entry" -> {
                val price = event.optDouble("fill_price", Double.NaN)
                val px = if (price.isNaN()) "" else " @ ${"%.3f".format(price)}"
                Triple(
                    PmApp.CHANNEL_TRADES,
                    "${prefix}Trade $side",
                    "$slug$px",
                )
            }
            "resolve" -> {
                val won = event.optBoolean("won")
                val pnl = event.optDouble("net_pnl_usd", Double.NaN)
                val pnlTxt = if (pnl.isNaN()) "" else " PnL ${"%.2f".format(pnl)}"
                Triple(
                    PmApp.CHANNEL_RESULTS,
                    if (won) "${prefix}WIN $side" else "${prefix}LOSS $side",
                    "$slug$pnlTxt",
                )
            }
            else -> return
        }
        val open = PendingIntent.getActivity(
            this,
            idHash(event.optString("id")),
            Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        val notification = NotificationCompat.Builder(this, channel)
            .setSmallIcon(R.drawable.ic_mark)
            .setContentTitle(title)
            .setContentText(text)
            .setContentIntent(open)
            .setAutoCancel(true)
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .build()
        try {
            NotificationManagerCompat.from(this).notify(idHash(event.optString("id")), notification)
        } catch (_: SecurityException) {
            // POST_NOTIFICATIONS denied
        }
    }

    fun postTest(kind: String) {
        val sample = JSONObject()
            .put("id", "test:$kind:${System.currentTimeMillis()}")
            .put("event", kind)
            .put("slug", "test-window")
            .put("side", "UP")
            .put("fill_price", 0.35)
            .put("won", kind == "resolve")
            .put("net_pnl_usd", if (kind == "resolve") 65.0 else Double.NaN)
            .put("dry_run", true)
        notifyEvent(sample)
    }

    private fun idHash(id: String): Int = id.hashCode() and 0x7fffffff

    private fun httpGet(url: String): String? {
        val conn = URL(url).openConnection() as HttpURLConnection
        conn.connectTimeout = 8000
        conn.readTimeout = 8000
        conn.requestMethod = "GET"
        conn.setRequestProperty("Accept", "application/json")
        conn.instanceFollowRedirects = true
        return try {
            if (conn.responseCode !in 200..299) null
            else conn.inputStream.bufferedReader().use { it.readText() }
        } finally {
            conn.disconnect()
        }
    }

    companion object {
        const val EXTRA_TEST = "test_kind"

        fun start(context: Context, testKind: String? = null) {
            val intent = Intent(context, WatchService::class.java)
            if (testKind != null) intent.putExtra(EXTRA_TEST, testKind)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                context.startForegroundService(intent)
            } else {
                context.startService(intent)
            }
        }
    }
}
