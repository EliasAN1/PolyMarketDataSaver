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
        context.getSharedPreferences(App.PREFS, Context.MODE_PRIVATE)
            .edit()
            .putString(App.KEY_URL, value.trim())
            .apply()
    }

    fun seenIds(context: Context): MutableSet<String> {
        return context.getSharedPreferences(App.PREFS, Context.MODE_PRIVATE)
            .getStringSet(App.KEY_SEEN, emptySet())
            ?.toMutableSet()
            ?: mutableSetOf()
    }

    fun setSeenIds(context: Context, ids: Set<String>) {
        val trimmed = if (ids.size > 300) ids.toList().takeLast(200).toSet() else ids
        context.getSharedPreferences(App.PREFS, Context.MODE_PRIVATE)
            .edit()
            .putStringSet(App.KEY_SEEN, trimmed)
            .apply()
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
}
