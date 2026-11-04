package com.eliasan.centionaire

import android.app.job.JobScheduler
import android.content.Context
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import android.net.NetworkRequest
import android.os.Handler
import android.os.Looper
import android.util.Log
import java.util.concurrent.Executors
import java.util.concurrent.Future

object WatchScheduler {
    private const val JOB_ID = 884200
    private val executor = Executors.newSingleThreadExecutor()
    private val handler = Handler(Looper.getMainLooper())
    private var callback: ConnectivityManager.NetworkCallback? = null
    private var pending: Runnable? = null
    private var lastAttempt = 0L // Accessed only on the single worker thread.
    private var lastServer = ""

    fun initialize(context: Context) {
        val app = context.applicationContext
        // Cancel any legacy JobScheduler periodic job so Android never wakes up on a background timer.
        cancelScheduled(app)
        if (callback != null) return
        val connectivity = app.getSystemService(ConnectivityManager::class.java)
        val listener = object : ConnectivityManager.NetworkCallback() {
            override fun onAvailable(network: Network) {
                // VPN came up; debounce 2s for Tailscale routes/DNS, then force check.
                requestCheck(app, immediate = false, force = true)
            }
        }
        try {
            // Passive observation does not keep the process or CPU alive.
            connectivity.registerNetworkCallback(vpnRequest(), listener)
            callback = listener
        } catch (e: RuntimeException) {
            Log.w("CentionaireSync", "VPN callback unavailable", e)
        }
    }

    fun cancelScheduled(context: Context) {
        try {
            context.getSystemService(JobScheduler::class.java).cancel(JOB_ID)
        } catch (e: Exception) {
            Log.w("CentionaireSync", "Could not cancel JobScheduler job", e)
        }
    }

    fun ensureScheduled(context: Context) {
        cancelScheduled(context)
    }

    fun requestCheck(context: Context, immediate: Boolean = false, force: Boolean = false) {
        val app = context.applicationContext
        handler.post {
            pending?.let { handler.removeCallbacks(it) }
            val request = Runnable {
                pending = null
                submit(app, force = force)
            }
            if (immediate) {
                request.run()
            } else {
                pending = request
                // Allow Tailscale to install its routes and DNS configuration.
                handler.postDelayed(request, 2_000L)
            }
        }
    }

    fun submit(
        context: Context,
        network: Network? = null,
        force: Boolean = false,
        finished: () -> Unit = {},
    ): Future<*> {
        val app = context.applicationContext
        return executor.submit {
            try {
                val now = android.os.SystemClock.elapsedRealtime()
                val base = Prefs.url(app)
                if (canConnect(app) && (force || base != lastServer || lastAttempt == 0L || now - lastAttempt >= 5_000L)) {
                    lastAttempt = now
                    lastServer = base
                    WatchSync(app).check(network)
                }
            } catch (e: Exception) {
                Log.w("CentionaireSync", "Report check failed", e)
            } finally {
                finished()
            }
        }
    }

    fun silentCheckpoint(context: Context) {
        val app = context.applicationContext
        executor.submit {
            try {
                if (canConnect(app)) {
                    WatchSync(app).checkpointSilently()
                }
            } catch (e: Exception) {
                Log.d("CentionaireSync", "Silent checkpoint failed", e)
            }
        }
    }

    fun test(context: Context, kind: String) {
        val app = context.applicationContext
        executor.execute {
            try {
                val sync = WatchSync(app)
                if (kind == "summary") sync.postTestSummary() else sync.postTest(kind)
            } catch (e: Exception) {
                Log.w("CentionaireSync", "Test notification unavailable", e)
            }
        }
    }

    fun canConnect(context: Context): Boolean = connectionNetwork(context) != null

    fun connectionNetwork(context: Context): Network? {
        val connectivity = context.getSystemService(ConnectivityManager::class.java)
        if (!requiresVpn(Prefs.url(context))) return connectivity.activeNetwork
        @Suppress("DEPRECATION")
        return connectivity.allNetworks.firstOrNull {
            connectivity.getNetworkCapabilities(it)?.hasTransport(NetworkCapabilities.TRANSPORT_VPN) == true
        }
    }

    internal fun requiresVpn(url: String): Boolean {
        val host = runCatching { java.net.URI(url).host?.lowercase() }.getOrNull() ?: return false
        if (host.endsWith(".ts.net")) return true
        val octets = host.split('.').mapNotNull { it.toIntOrNull() }
        return (octets.size == 4 && octets[0] == 100 && octets[1] in 64..127) ||
            host.removePrefix("[").startsWith("fd7a:115c:a1e0:")
    }

    private fun vpnRequest(): NetworkRequest = NetworkRequest.Builder()
        .removeCapability(NetworkCapabilities.NET_CAPABILITY_NOT_VPN)
        .addTransportType(NetworkCapabilities.TRANSPORT_VPN)
        .build()
}
