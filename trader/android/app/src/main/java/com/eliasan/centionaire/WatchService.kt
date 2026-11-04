package com.eliasan.centionaire

import android.app.job.JobParameters
import android.app.job.JobService
import android.os.Handler
import android.os.Looper
import java.util.concurrent.Future

/** Android owns the wake lock only for the duration of this short job. */
class WatchService : JobService() {
    private val handler = Handler(Looper.getMainLooper())
    private var task: Future<*>? = null
    private var active: JobParameters? = null

    override fun onStartJob(params: JobParameters): Boolean {
        active = params
        val network = if (android.os.Build.VERSION.SDK_INT >= 28) params.network else null
        task = WatchScheduler.submit(this, network) {
            handler.post {
                if (active === params) {
                    active = null
                    jobFinished(params, false)
                }
            }
        }
        return true
    }

    override fun onStopJob(params: JobParameters): Boolean {
        active = null
        task?.cancel(true)
        return false // The persisted periodic job supplies the next attempt.
    }

    override fun onDestroy() {
        active = null
        task?.cancel(true)
        handler.removeCallbacksAndMessages(null)
        super.onDestroy()
    }
}
