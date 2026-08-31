package com.eliasan.centionaire

import android.content.Intent
import androidx.car.app.CarAppService
import androidx.car.app.Session
import androidx.car.app.validation.HostValidator

class AutoService : CarAppService() {
    override fun createHostValidator(): HostValidator =
        HostValidator.ALLOW_ALL_HOSTS_VALIDATOR

    override fun onCreateSession(): Session = AutoSession()
}

class AutoSession : Session() {
    override fun onCreateScreen(intent: Intent) = AutoHomeScreen(carContext)
}
