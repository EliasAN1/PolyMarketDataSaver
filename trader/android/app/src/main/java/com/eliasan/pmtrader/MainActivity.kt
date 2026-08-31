package com.eliasan.pmtrader

import android.Manifest
import android.annotation.SuppressLint
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.webkit.WebChromeClient
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import com.eliasan.pmtrader.databinding.ActivityMainBinding

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
        web.webViewClient = WebViewClient()
        web.webChromeClient = WebChromeClient()
        binding.swipe.setColorSchemeColors(0xFF3DDC97.toInt())
        binding.swipe.setOnRefreshListener {
            web.reload()
            binding.swipe.isRefreshing = false
        }
        loadDashboard()
        WatchService.start(this)
    }

    override fun onResume() {
        super.onResume()
        loadDashboard()
        WatchService.start(this)
    }

    private fun loadDashboard() {
        val url = Prefs.url(this)
        if (url.isBlank()) {
            startActivity(Intent(this, SettingsActivity::class.java))
            return
        }
        val current = binding.webview.url
        if (current.isNullOrBlank() || !current.startsWith(url)) {
            binding.webview.loadUrl(url)
        }
    }
}
