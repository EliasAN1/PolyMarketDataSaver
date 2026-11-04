package com.eliasan.centionaire

import android.app.Application
import android.app.NotificationChannel
import android.app.NotificationManager
import android.os.Build

class App : Application() {
    override fun onCreate() {
        super.onCreate()
        val manager = getSystemService(NotificationManager::class.java)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            manager.deleteNotificationChannel("watch")
            manager.deleteNotificationChannel(CHANNEL_WATCH)
            manager.createNotificationChannel(
                NotificationChannel(CHANNEL_TRADES, getString(R.string.trades_channel), NotificationManager.IMPORTANCE_HIGH).apply {
                    description = "Fills and order sends"
                },
            )
            manager.createNotificationChannel(
                NotificationChannel(CHANNEL_RESULTS, getString(R.string.results_channel), NotificationManager.IMPORTANCE_HIGH).apply {
                    description = "Window settlement win/loss"
                },
            )
        }
        WatchScheduler.initialize(this)
    }

    companion object {
        const val CHANNEL_WATCH = "watch_min"
        const val CHANNEL_TRADES = "trades"
        const val CHANNEL_RESULTS = "results"
        const val PREFS = "centionaire"
        const val KEY_URL = "server_url"
        const val KEY_SEEN = "seen_event_ids"
        const val KEY_SEEN_ORDERED = "seen_event_ids_ordered"
        const val KEY_LIVE_STATE = "live_state"
        const val KEY_LIVE_SLUG = "live_slug"
        const val KEY_LIVE_SIDE = "live_side"
        const val KEY_LAST_TITLE = "last_title"
        const val KEY_LAST_TEXT = "last_text"
        const val KEY_LAST_POLL = "last_poll_ms"
        const val DEFAULT_URL = "https://ip-172-31-28-129.tail86f11c.ts.net"
    }
}
