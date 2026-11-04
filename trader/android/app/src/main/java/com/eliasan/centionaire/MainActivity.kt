package com.eliasan.centionaire

import android.Manifest
import android.annotation.SuppressLint
import android.content.ActivityNotFoundException
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.Message
import android.webkit.JavascriptInterface
import android.webkit.WebChromeClient
import android.webkit.WebResourceRequest
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.activity.OnBackPressedCallback
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import com.eliasan.centionaire.databinding.ActivityMainBinding

class MainActivity : AppCompatActivity() {
    private lateinit var binding: ActivityMainBinding
    private var dashboardUrl: String = ""
    @Volatile private var sheetBlocksRefresh = false

    inner class JsBridge {
        @JavascriptInterface
        fun setSheetOpen(open: Boolean) {
            sheetBlocksRefresh = open
        }
    }
    private val foregroundHandler = Handler(Looper.getMainLooper())
    private val foregroundSyncTask = object : Runnable {
        override fun run() {
            WatchScheduler.silentCheckpoint(this@MainActivity)
            foregroundHandler.postDelayed(this, 30_000L)
        }
    }

    private val notifyPermission = registerForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { granted -> if (granted) WatchScheduler.requestCheck(this, immediate = true, force = true) }

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)

        if (Build.VERSION.SDK_INT >= 33 &&
            ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS)
            != PackageManager.PERMISSION_GRANTED
        ) {
            notifyPermission.launch(Manifest.permission.POST_NOTIFICATIONS)
        }

        val web = binding.webview
        web.addJavascriptInterface(JsBridge(), "Centionaire")
        web.settings.javaScriptEnabled = true
        web.settings.domStorageEnabled = true
        web.settings.cacheMode = WebSettings.LOAD_NO_CACHE
        web.settings.mixedContentMode = WebSettings.MIXED_CONTENT_ALWAYS_ALLOW
        web.settings.setSupportMultipleWindows(true)
        web.settings.javaScriptCanOpenWindowsAutomatically = true
        web.settings.setSupportZoom(false)
        web.settings.builtInZoomControls = false
        web.isFocusableInTouchMode = true
        web.webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(
                view: WebView,
                request: WebResourceRequest,
            ): Boolean = handleUrl(request.url)

            @Deprecated("Deprecated in Java")
            override fun shouldOverrideUrlLoading(view: WebView, url: String): Boolean =
                handleUrl(Uri.parse(url))

            override fun onPageFinished(view: WebView, url: String) {
                syncSwipe(url)
            }
        }
        web.webChromeClient = object : WebChromeClient() {
            override fun onCreateWindow(
                view: WebView,
                isDialog: Boolean,
                isUserGesture: Boolean,
                resultMsg: Message,
            ): Boolean {
                val pipe = WebView(view.context)
                pipe.webViewClient = object : WebViewClient() {
                    override fun shouldOverrideUrlLoading(
                        v: WebView,
                        request: WebResourceRequest,
                    ): Boolean {
                        web.loadUrl(request.url.toString())
                        return true
                    }

                    @Deprecated("Deprecated in Java")
                    override fun shouldOverrideUrlLoading(v: WebView, url: String): Boolean {
                        web.loadUrl(url)
                        return true
                    }
                }
                (resultMsg.obj as WebView.WebViewTransport).webView = pipe
                resultMsg.sendToTarget()
                return true
            }
        }
        binding.swipe.setColorSchemeColors(0xFF3DDC97.toInt())
        binding.swipe.setOnChildScrollUpCallback { _, _ ->
            sheetBlocksRefresh || web.canScrollVertically(-1)
        }
        binding.swipe.setOnRefreshListener {
            WatchScheduler.requestCheck(this, immediate = true, force = true)
            web.reload()
            binding.swipe.isRefreshing = false
        }
        onBackPressedDispatcher.addCallback(
            this,
            object : OnBackPressedCallback(true) {
                override fun handleOnBackPressed() {
                    handleBack()
                }
            },
        )
        loadDashboard()
    }

    override fun onResume() {
        super.onResume()
        binding.webview.onResume()
        binding.webview.resumeTimers()
        WatchScheduler.requestCheck(this, immediate = true, force = true)
        foregroundHandler.removeCallbacks(foregroundSyncTask)
        foregroundHandler.postDelayed(foregroundSyncTask, 30_000L)
        val url = Prefs.url(this)
        if (url.isNotBlank() && url != dashboardUrl) {
            loadDashboard(force = true)
        }
    }

    override fun onPause() {
        foregroundHandler.removeCallbacks(foregroundSyncTask)
        WatchScheduler.silentCheckpoint(this)
        binding.webview.onPause()
        binding.webview.pauseTimers()
        super.onPause()
    }

    private fun handleBack(): Boolean {
        val web = binding.webview
        if (web.canGoBack()) {
            web.goBack()
            return true
        }
        if (!isOnDashboard()) {
            loadDashboard(force = true)
            return true
        }
        moveTaskToBack(true)
        return true
    }

    private fun handleUrl(uri: Uri): Boolean {
        val scheme = uri.scheme.orEmpty().lowercase()
        if (scheme == "http" || scheme == "https") {
            if (isDashboardUrl(uri)) return false
            binding.webview.loadUrl(uri.toString())
            return true
        }
        if (scheme.isEmpty() || scheme == "about") return false
        openExternal(uri)
        return true
    }

    private fun isOnDashboard(): Boolean {
        val current = binding.webview.url.orEmpty()
        return current.isBlank() || isDashboardUrl(Uri.parse(current))
    }

    private fun isDashboardUrl(uri: Uri): Boolean {
        val dash = runCatching { Uri.parse(Prefs.url(this)) }.getOrNull() ?: return false
        val host = uri.host ?: return false
        val dashHost = dash.host ?: return false
        if (!host.equals(dashHost, ignoreCase = true)) return false
        return portOf(uri) == portOf(dash)
    }

    private fun portOf(uri: Uri): Int {
        if (uri.port != -1) return uri.port
        return if (uri.scheme.equals("https", ignoreCase = true)) 443 else 80
    }

    private fun syncSwipe(url: String?) {
        binding.swipe.isEnabled = url.isNullOrBlank() || isDashboardUrl(Uri.parse(url))
    }

    private fun openExternal(uri: Uri) {
        try {
            startActivity(Intent(Intent.ACTION_VIEW, uri))
        } catch (_: ActivityNotFoundException) {
            /* no browser */
        }
    }

    private fun loadDashboard(force: Boolean = false) {
        var url = Prefs.url(this)
        if (url.isBlank()) {
            Prefs.setUrl(this, App.DEFAULT_URL)
            url = Prefs.url(this)
        }
        dashboardUrl = url
        val current = binding.webview.url
        if (force || current.isNullOrBlank() || !isDashboardUrl(Uri.parse(current))) {
            binding.webview.loadUrl(url)
        }
        syncSwipe(url)
    }
}
