package com.assistant.mobile

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.provider.Settings

object CommandRouter {
    fun handleCommand(context: Context, text: String): AssistantResponse {
        val normalized = text.trim().lowercase()
        if (normalized.isEmpty()) {
            return AssistantResponse("Please say a command.", false)
        }

        if (normalized.startsWith("open ") || normalized.startsWith("launch ") || normalized.startsWith("run ")) {
            val target = normalized.replaceFirst(Regex("^(open|launch|run)\\s+"), "").trim()
            if (target.contains("settings")) {
                return openSettings(context, target)
            }
            if (target.contains("youtube")) {
                openUrl(context, "https://www.youtube.com")
                return AssistantResponse("Opening YouTube.")
            }
            if (target.contains("spotify")) {
                openUrl(context, "https://open.spotify.com")
                return AssistantResponse("Opening Spotify.")
            }
            val opened = openAppByName(context, target)
            return if (opened) {
                AssistantResponse("Opening $target.")
            } else {
                AssistantResponse("I could not find an app named $target.")
            }
        }

        if (normalized.startsWith("search ")) {
            val query = normalized.removePrefix("search ").trim()
            if (query.isNotEmpty()) {
                openUrl(context, "https://www.google.com/search?q=${Uri.encode(query)}")
                return AssistantResponse("Searching for $query.")
            }
        }

        if (normalized.contains("wifi") || normalized.contains("bluetooth") || normalized.contains("airplane")) {
            return openSettings(context, normalized)
        }

        if (normalized.contains("open youtube")) {
            openUrl(context, "https://www.youtube.com")
            return AssistantResponse("Opening YouTube.")
        }

        return AssistantResponse("I can open apps, settings, and websites. Try saying open Spotify or open Wi-Fi settings.")
    }

    private fun openUrl(context: Context, url: String) {
        val intent = Intent(Intent.ACTION_VIEW, Uri.parse(url))
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        context.startActivity(intent)
    }

    private fun openSettings(context: Context, target: String): AssistantResponse {
        val action = when {
            target.contains("wifi") -> Settings.ACTION_WIFI_SETTINGS
            target.contains("bluetooth") -> Settings.ACTION_BLUETOOTH_SETTINGS
            target.contains("airplane") -> Settings.ACTION_AIRPLANE_MODE_SETTINGS
            target.contains("sound") || target.contains("volume") -> Settings.ACTION_SOUND_SETTINGS
            else -> Settings.ACTION_SETTINGS
        }
        val intent = Intent(action)
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        context.startActivity(intent)
        return AssistantResponse("Opening settings.")
    }

    private fun openAppByName(context: Context, query: String): Boolean {
        val pm = context.packageManager
        val apps = pm.getInstalledApplications(0)
        if (apps.isEmpty()) return false

        val cleanedQuery = query.lowercase()
        var bestMatchPackage: String? = null
        var bestScore = 0

        for (app in apps) {
            val label = pm.getApplicationLabel(app).toString()
            val normalizedLabel = label.lowercase()
            val score = matchScore(cleanedQuery, normalizedLabel)
            if (score > bestScore) {
                bestScore = score
                bestMatchPackage = app.packageName
            }
        }

        if (bestMatchPackage.isNullOrBlank() || bestScore < 2) {
            return false
        }

        val launchIntent = pm.getLaunchIntentForPackage(bestMatchPackage) ?: return false
        launchIntent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        context.startActivity(launchIntent)
        return true
    }

    private fun matchScore(query: String, label: String): Int {
        if (query == label) return 10
        if (label.contains(query)) return 6
        val queryTokens = query.split(" ").filter { it.isNotBlank() }
        val labelTokens = label.split(" ").filter { it.isNotBlank() }
        val overlap = queryTokens.count { token -> labelTokens.any { it.startsWith(token) } }
        return overlap
    }
}
