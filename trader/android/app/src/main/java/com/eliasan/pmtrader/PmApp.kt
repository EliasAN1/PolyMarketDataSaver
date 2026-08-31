package com.eliasan.pmtrader

import android.app.Application
import android.app.NotificationChannel
import android.app.NotificationManager
import android.os.Build

class PmApp : Application() {
    override fun onCreate() {
        super.onCreate()
        val manager = getSystemService(NotificationManager::class.java)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            manager.createNotificationChannel(
                NotificationChannel(CHANNEL_WATCH, getString(R.string.watch_channel), NotificationManager.IMPORTANCE_LOW),
            )
            manager.createNotificationChannel(
                NotificationChannel(CHANNEL_TRADES, getString(R.string.trades_channel), NotificationManager.IMPORTANCE_HIGH).apply {
                    description = "FAK fills and order sends"
                },
            )
            manager.createNotificationChannel(
                NotificationChannel(CHANNEL_RESULTS, getString(R.string.results_channel), NotificationManager.IMPORTANCE_HIGH).apply {
                    description = "Window settlement win/loss"
                },
            )
        }
    }

    companion object {
        const val CHANNEL_WATCH = "watch"
        const val CHANNEL_TRADES = "trades"
        const val CHANNEL_RESULTS = "results"
        const val PREFS = "pmtrader"
        const val KEY_URL = "server_url"
        const val KEY_SEEN = "seen_event_ids"
        const val DEFAULT_URL = "https://drelias.tail86f11c.ts.net/"
    }
}
