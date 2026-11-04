package com.eliasan.centionaire

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class RecapBatchTest {
    private fun event(id: String, ts: Long, kind: String = "resolve", unmatched: Boolean = false) =
        JSONObject().put("id", id).put("ts", ts).put("event", kind).put("unmatched", unmatched)

    @Test fun firstConnectionSeedsHistoryWithoutAlerts() {
        assertTrue(RecapBatch.select(listOf(event("old", 100)), emptySet(), 0).isEmpty())
    }

    @Test fun reconnectIncludesSeveralHoursOfResults() {
        val rows = listOf(event("a", 1100), event("b", 8000), event("c", 15000, "entry"))
        assertEquals(rows, RecapBatch.select(rows, emptySet(), 1_000_000))
    }

    @Test fun processRestartWithCheckpointDoesNotReseed() {
        val row = event("new", 1100)
        assertEquals(listOf(row), RecapBatch.select(listOf(row), emptySet(), 1_000_000))
    }

    @Test fun repeatedResponsesAndDuplicateRowsDoNotRepeatAlerts() {
        val old = event("seen", 1100)
        val fresh = event("new", 1200)
        assertEquals(listOf(fresh), RecapBatch.select(listOf(old, fresh, fresh), setOf("seen"), 1_000_000))
        assertTrue(RecapBatch.select(listOf(old, fresh), setOf("seen", "new"), 1_000_000).isEmpty())
    }

    @Test fun trimmedHistoryAndUnmatchedRowsAreExcludedButBoundaryEventsSurvive() {
        val boundary = event("boundary", 990)
        val rows = listOf(event("old", 900), event("paper", 1100, unmatched = true),
            event("noise", 1100, "error"), boundary)
        assertEquals(listOf(boundary), RecapBatch.select(rows, emptySet(), 1_000_000))
    }

    @Test fun largeCatchUpIsNotTruncatedByClient() {
        val rows = (1..700).map { event("event-$it", 1000L + it) }
        assertEquals(700, RecapBatch.select(rows, emptySet(), 1_000_000).size)
    }
}
