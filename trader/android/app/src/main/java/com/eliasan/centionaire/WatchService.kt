package com.eliasan.centionaire

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
        wakeLock = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "centionaire:watch").apply {
            setReferenceCounted(false)
            acquire()
        }
        startForegroundWatch()
        job = executor.scheduleWithFixedDelay({ tick() }, 0, 5, TimeUnit.SECONDS)
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        startForegroundWatch()
        try {
            when (intent?.getStringExtra(EXTRA_TEST)) {
                "entry" -> postTest("entry")
                "resolve" -> postTest("resolve")
            }
        } catch (_: Exception) {
            // Test ping must never take down the watcher.
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
        val notification: Notification = NotificationCompat.Builder(this, App.CHANNEL_WATCH)
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
            val live = json.optJSONObject("live")
            if (live != null) {
                Prefs.setLive(
                    this,
                    live.optString("state"),
                    live.optString("slug"),
                    live.optString("side"),
                )
            }
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
        val side = event.optString("side").uppercase().ifBlank { "—" }
        val dry = event.optBoolean("dry_run")
        val prefix = if (dry) "DRY " else ""
        val (channel, title, text) = when (kind) {
            "entry" -> Triple(
                App.CHANNEL_TRADES,
                "${prefix}Bought $side",
                fillLine(event),
            )
            "resolve" -> {
                val won = event.optBoolean("won")
                Triple(
                    App.CHANNEL_RESULTS,
                    "${prefix}$side ${if (won) "won" else "lost"}",
                    pnlLine(event),
                )
            }
            else -> return
        }
        Prefs.setLastAlert(this, title, text)
        val open = PendingIntent.getActivity(
            this,
            idHash(event.optString("id")),
            Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        val person = androidx.core.app.Person.Builder()
            .setName(getString(R.string.app_name))
            .setKey("centionaire")
            .setImportant(true)
            .build()
        val style = NotificationCompat.MessagingStyle(person)
            .setConversationTitle(title)
            .addMessage(text, System.currentTimeMillis(), person)
        val notification = NotificationCompat.Builder(this, channel)
            .setSmallIcon(R.drawable.ic_mark)
            .setContentTitle(title)
            .setContentText(text)
            .setStyle(style)
            .setCategory(NotificationCompat.CATEGORY_MESSAGE)
            .setContentIntent(open)
            .setAutoCancel(true)
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .extend(NotificationCompat.CarExtender())
            .build()
        try {
            NotificationManagerCompat.from(this).notify(idHash(event.optString("id")), notification)
        } catch (_: SecurityException) {
            // POST_NOTIFICATIONS denied
        }
    }

    private fun fillLine(event: JSONObject): String {
        val parts = mutableListOf<String>()
        val price = event.optDouble("fill_price", Double.NaN)
        if (!price.isNaN() && price > 0) {
            parts.add("${"%.1f".format(java.util.Locale.US, price * 100)}¢")
        }
        val stake = event.optDouble("stake_usd", Double.NaN)
        if (!stake.isNaN() && stake > 0) {
            parts.add("$${trimMoney(stake)}")
        }
        return parts.joinToString(" · ").ifBlank { "filled" }
    }

    private fun pnlLine(event: JSONObject): String {
        val pnl = event.optDouble("net_pnl_usd", Double.NaN)
        if (pnl.isNaN()) return "settled"
        val sign = if (pnl >= 0) "+" else "−"
        return "$sign$${trimMoney(kotlin.math.abs(pnl))}"
    }

    private fun trimMoney(value: Double): String {
        val text = "%.2f".format(java.util.Locale.US, value)
        return if (text.endsWith(".00")) text.dropLast(3) else text
    }

    private fun postTest(kind: String) {
        val sample = JSONObject()
            .put("id", "test:$kind:${System.currentTimeMillis()}")
            .put("event", kind)
            .put("slug", "test-window")
            .put("side", "UP")
            .put("fill_price", 0.35)
            .put("stake_usd", 50.0)
            .put("won", kind == "resolve")
            .put("dry_run", true)
        if (kind == "resolve") sample.put("net_pnl_usd", 65.0)
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
