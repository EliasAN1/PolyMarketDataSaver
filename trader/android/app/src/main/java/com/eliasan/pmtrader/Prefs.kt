package com.eliasan.pmtrader

import android.content.Context

object Prefs {
    fun url(context: Context): String {
        val raw = context.getSharedPreferences(PmApp.PREFS, Context.MODE_PRIVATE)
            .getString(PmApp.KEY_URL, PmApp.DEFAULT_URL)
            .orEmpty()
            .trim()
        return raw.trimEnd('/')
    }

    fun setUrl(context: Context, value: String) {
        context.getSharedPreferences(PmApp.PREFS, Context.MODE_PRIVATE)
            .edit()
            .putString(PmApp.KEY_URL, value.trim())
            .apply()
    }

    fun seenIds(context: Context): MutableSet<String> {
        return context.getSharedPreferences(PmApp.PREFS, Context.MODE_PRIVATE)
            .getStringSet(PmApp.KEY_SEEN, emptySet())
            ?.toMutableSet()
            ?: mutableSetOf()
    }

    fun setSeenIds(context: Context, ids: Set<String>) {
        val trimmed = if (ids.size > 300) ids.toList().takeLast(200).toSet() else ids
        context.getSharedPreferences(PmApp.PREFS, Context.MODE_PRIVATE)
            .edit()
            .putStringSet(PmApp.KEY_SEEN, trimmed)
            .apply()
    }
}
