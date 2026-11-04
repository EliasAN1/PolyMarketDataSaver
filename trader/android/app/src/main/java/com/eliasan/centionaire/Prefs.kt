package com.eliasan.centionaire

import android.content.Context

object Prefs {
    fun url(context: Context): String {
        val raw = context.getSharedPreferences(App.PREFS, Context.MODE_PRIVATE)
            .getString(App.KEY_URL, App.DEFAULT_URL)
            .orEmpty()
            .trim()
        return raw.trimEnd('/')
    }

    fun setUrl(context: Context, value: String) {
        val normalized = value.trim().trimEnd('/')
        val changed = normalized != url(context)
        val editor = context.getSharedPreferences(App.PREFS, Context.MODE_PRIVATE).edit()
            .putString(App.KEY_URL, normalized)
        if (changed) {
            editor.remove(App.KEY_SEEN).remove(App.KEY_SEEN_ORDERED).remove(App.KEY_LAST_POLL)
        }
        editor.apply()
    }

    fun seenIds(context: Context): MutableSet<String> {
        val prefs = context.getSharedPreferences(App.PREFS, Context.MODE_PRIVATE)
        val ordered = prefs.getString(App.KEY_SEEN_ORDERED, null)
        if (ordered != null) {
            return linkedSetOf<String>().apply {
                addAll(ordered.split('\n').filter { it.isNotEmpty() })
            }
        }
        return linkedSetOf<String>().apply {
            addAll(prefs.getStringSet(App.KEY_SEEN, emptySet()).orEmpty())
        }
    }

    fun setSeenIds(context: Context, ids: Set<String>) {
        val list = ids.toList()
        val trimmed = if (list.size > 500) list.takeLast(400) else list
        context.getSharedPreferences(App.PREFS, Context.MODE_PRIVATE)
            .edit()
            .putString(App.KEY_SEEN_ORDERED, trimmed.joinToString("\n"))
            .remove(App.KEY_SEEN)
            .apply()
    }

    @android.annotation.SuppressLint("ApplySharedPref") // Worker thread; persist before JobService finishes.
    fun setCheckpoint(context: Context, ids: Set<String>, pollMs: Long) {
        context.getSharedPreferences(App.PREFS, Context.MODE_PRIVATE)
            .edit()
            .putString(App.KEY_SEEN_ORDERED, ids.toList().takeLast(500).joinToString("\n"))
            .remove(App.KEY_SEEN)
            .putLong(App.KEY_LAST_POLL, pollMs)
            .commit()
    }

    fun setLive(context: Context, state: String?, slug: String?, side: String?) {
        context.getSharedPreferences(App.PREFS, Context.MODE_PRIVATE)
            .edit()
            .putString(App.KEY_LIVE_STATE, state.orEmpty())
            .putString(App.KEY_LIVE_SLUG, slug.orEmpty())
            .putString(App.KEY_LIVE_SIDE, side.orEmpty())
            .apply()
    }

    fun setLastAlert(context: Context, title: String, text: String) {
        context.getSharedPreferences(App.PREFS, Context.MODE_PRIVATE)
            .edit()
            .putString(App.KEY_LAST_TITLE, title)
            .putString(App.KEY_LAST_TEXT, text)
            .apply()
    }

    fun liveState(context: Context): String =
        context.getSharedPreferences(App.PREFS, Context.MODE_PRIVATE).getString(App.KEY_LIVE_STATE, "").orEmpty()

    fun liveSlug(context: Context): String =
        context.getSharedPreferences(App.PREFS, Context.MODE_PRIVATE).getString(App.KEY_LIVE_SLUG, "").orEmpty()

    fun liveSide(context: Context): String =
        context.getSharedPreferences(App.PREFS, Context.MODE_PRIVATE).getString(App.KEY_LIVE_SIDE, "").orEmpty()

    fun lastTitle(context: Context): String =
        context.getSharedPreferences(App.PREFS, Context.MODE_PRIVATE).getString(App.KEY_LAST_TITLE, "").orEmpty()

    fun lastText(context: Context): String =
        context.getSharedPreferences(App.PREFS, Context.MODE_PRIVATE).getString(App.KEY_LAST_TEXT, "").orEmpty()

    fun lastPollMs(context: Context): Long =
        context.getSharedPreferences(App.PREFS, Context.MODE_PRIVATE).getLong(App.KEY_LAST_POLL, 0L)

    fun setLastPollMs(context: Context, value: Long) {
        context.getSharedPreferences(App.PREFS, Context.MODE_PRIVATE)
            .edit()
            .putLong(App.KEY_LAST_POLL, value)
            .apply()
    }
}
