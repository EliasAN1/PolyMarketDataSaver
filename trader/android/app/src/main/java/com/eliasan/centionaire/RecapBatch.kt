package com.eliasan.centionaire

import org.json.JSONObject

object RecapBatch {
    fun select(events: List<JSONObject>, seen: Set<String>, lastPoll: Long): List<JSONObject> {
        // Seed only on first installation, never after process death.
        if (lastPoll <= 0L) return emptyList()
        val since = lastPoll - 15_000L
        val batchIds = mutableSetOf<String>()
        return events.filter {
            val id = it.optString("id")
            val ts = it.optLong("ts", 0L) * 1000L
            id.isNotEmpty() && id !in seen && batchIds.add(id) &&
                !it.optBoolean("unmatched") &&
                it.optString("event") in setOf("entry", "resolve") &&
                (ts == 0L || ts >= since)
        }
    }
}
