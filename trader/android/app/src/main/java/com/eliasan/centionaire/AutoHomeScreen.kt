package com.eliasan.centionaire

import android.os.Handler
import android.os.Looper
import androidx.car.app.CarContext
import androidx.car.app.Screen
import androidx.car.app.model.Action
import androidx.car.app.model.ActionStrip
import androidx.car.app.model.ItemList
import androidx.car.app.model.ListTemplate
import androidx.car.app.model.Row
import androidx.car.app.model.Template
import androidx.lifecycle.DefaultLifecycleObserver
import androidx.lifecycle.LifecycleOwner

class AutoHomeScreen(carContext: CarContext) : Screen(carContext) {
    private val handler = Handler(Looper.getMainLooper())
    private val refresh = object : Runnable {
        override fun run() {
            invalidate()
            handler.postDelayed(this, 5_000)
        }
    }

    init {
        lifecycle.addObserver(object : DefaultLifecycleObserver {
            override fun onStart(owner: LifecycleOwner) {
                handler.removeCallbacks(refresh)
                handler.post(refresh)
            }

            override fun onStop(owner: LifecycleOwner) {
                handler.removeCallbacks(refresh)
            }
        })
    }

    override fun onGetTemplate(): Template {
        val state = Prefs.liveState(carContext).ifBlank { "waiting" }
        val slug = Prefs.liveSlug(carContext).ifBlank { "no window" }
        val side = Prefs.liveSide(carContext).ifBlank { "—" }
        val lastTitle = Prefs.lastTitle(carContext).ifBlank { "No fills yet" }
        val lastText = Prefs.lastText(carContext).ifBlank { "Alerts show here and as Auto messages" }

        val rows = ItemList.Builder()
            .addItem(
                Row.Builder()
                    .setTitle(state.uppercase())
                    .addText("$side · $slug")
                    .build(),
            )
            .addItem(
                Row.Builder()
                    .setTitle(lastTitle)
                    .addText(lastText)
                    .build(),
            )
            .build()

        return ListTemplate.Builder()
            .setTitle(carContext.getString(R.string.app_name))
            .setHeaderAction(Action.APP_ICON)
            .setSingleList(rows)
            .setActionStrip(
                ActionStrip.Builder()
                    .addAction(
                        Action.Builder()
                            .setTitle(carContext.getString(R.string.auto_refresh))
                            .setOnClickListener { invalidate() }
                            .build(),
                    )
                    .build(),
            )
            .build()
    }
}
