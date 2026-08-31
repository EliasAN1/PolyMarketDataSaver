package com.eliasan.centionaire

import android.os.Bundle
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import com.eliasan.centionaire.databinding.ActivitySettingsBinding

class SettingsActivity : AppCompatActivity() {
    private lateinit var binding: ActivitySettingsBinding

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivitySettingsBinding.inflate(layoutInflater)
        setContentView(binding.root)
        binding.urlInput.setText(Prefs.url(this).ifBlank { App.DEFAULT_URL })
        binding.saveBtn.setOnClickListener {
            var url = binding.urlInput.text.toString().trim()
            if (url.isNotEmpty() && !url.startsWith("http://") && !url.startsWith("https://")) {
                url = "https://$url"
            }
            Prefs.setUrl(this, url)
            WatchService.start(this)
            Toast.makeText(this, "Saved", Toast.LENGTH_SHORT).show()
            finish()
        }
        binding.testTradeBtn.setOnClickListener {
            WatchService.start(this, "entry")
            Toast.makeText(this, "Trade ping sent", Toast.LENGTH_SHORT).show()
        }
        binding.testResultBtn.setOnClickListener {
            WatchService.start(this, "resolve")
            Toast.makeText(this, "Result ping sent", Toast.LENGTH_SHORT).show()
        }
    }
}
