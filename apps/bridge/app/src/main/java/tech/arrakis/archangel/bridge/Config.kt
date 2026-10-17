package tech.arrakis.archangel.bridge

import android.content.Context
import android.content.SharedPreferences
import android.os.Build
import kotlinx.coroutines.channels.awaitClose
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.callbackFlow
import kotlinx.coroutines.flow.conflate
import kotlinx.serialization.Serializable

@Serializable
data class Config(val url: String, val token: String, val device: String)

/** Persists [Config] as a single value, so one save is one change. */
class ConfigStore(context: Context) {
    private val prefs = context.getSharedPreferences("bridge", Context.MODE_PRIVATE)

    fun load(): Config = prefs.getString(KEY, null)?.let(BridgeJson::decodeFromString) ?: defaults

    fun save(config: Config) = prefs.edit().putString(KEY, BridgeJson.encodeToString(config)).apply()

    /** The current config, then every saved change. */
    val changes: Flow<Config> = callbackFlow {
        val listener = SharedPreferences.OnSharedPreferenceChangeListener { _, key ->
            if (key == KEY) trySend(load())
        }
        prefs.registerOnSharedPreferenceChangeListener(listener)
        send(load())
        awaitClose { prefs.unregisterOnSharedPreferenceChangeListener(listener) }
    }.conflate()

    private companion object {
        const val KEY = "config"

        val defaults = Config(
            url = BuildConfig.DEFAULT_URL,
            token = BuildConfig.DEFAULT_TOKEN,
            device = Build.MODEL.lowercase().replace(Regex("[^a-z0-9]+"), "-").trim('-'),
        )
    }
}
