package com.eliasan.centionaire

import android.Manifest
import android.annotation.SuppressLint
import android.content.ActivityNotFoundException
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Message
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

    private val notifyPermission = registerForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { /* channel still works if denied until Android 13 */ }

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)
        binding.toolbar.inflateMenu(R.menu.main)
        binding.toolbar.setOnMenuItemClickListener {
            if (it.itemId == R.id.action_settings) {
                startActivity(Intent(this, SettingsActivity::class.java))
                true
            } else {
                false
            }
        }

        if (Build.VERSION.SDK_INT >= 33 &&
            ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS)
            != PackageManager.PERMISSION_GRANTED
        ) {
            notifyPermission.launch(Manifest.permission.POST_NOTIFICATIONS)
        }

        val web = binding.webview
        web.settings.javaScriptEnabled = true
        web.settings.domStorageEnabled = true
        web.settings.cacheMode = WebSettings.LOAD_NO_CACHE
        web.settings.mixedContentMode = WebSettings.MIXED_CONTENT_ALWAYS_ALLOW
        web.settings.setSupportMultipleWindows(true)
        web.settings.javaScriptCanOpenWindowsAutomatically = true
        web.webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(
                view: WebView,
                request: WebResourceRequest,
            ): Boolean = handleUrl(request.url)
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
                        handleUrl(request.url)
                        return true
                    }
                }
                (resultMsg.obj as WebView.WebViewTransport).webView = pipe
                resultMsg.sendToTarget()
                return true
            }
        }
        binding.swipe.setColorSchemeColors(0xFF3DDC97.toInt())
        binding.swipe.setOnRefreshListener {
            web.reload()
            binding.swipe.isRefreshing = false
        }
        onBackPressedDispatcher.addCallback(
            this,
            object : OnBackPressedCallback(true) {
                override fun handleOnBackPressed() {
                    if (web.canGoBack()) {
                        web.goBack()
                        return
                    }
                    val current = web.url.orEmpty()
                    if (current.isNotBlank() && !isDashboardUrl(Uri.parse(current))) {
                        loadDashboard(force = true)
                        return
                    }
                    isEnabled = false
                    onBackPressedDispatcher.onBackPressed()
                }
            },
        )
        loadDashboard()
        WatchService.start(this)
    }

    override fun onResume() {
        super.onResume()
        loadDashboard()
        WatchService.start(this)
    }

    private fun handleUrl(uri: Uri): Boolean {
        if (isDashboardUrl(uri)) return false
        openExternal(uri)
        return true
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

    private fun openExternal(uri: Uri) {
        try {
            startActivity(Intent(Intent.ACTION_VIEW, uri))
        } catch (_: ActivityNotFoundException) {
            /* no browser */
        }
    }

    private fun loadDashboard(force: Boolean = false) {
        val url = Prefs.url(this)
        if (url.isBlank()) {
            startActivity(Intent(this, SettingsActivity::class.java))
            return
        }
        val current = binding.webview.url
        if (force || current.isNullOrBlank() || !isDashboardUrl(Uri.parse(current))) {
            binding.webview.loadUrl(url)
        }
    }
}
