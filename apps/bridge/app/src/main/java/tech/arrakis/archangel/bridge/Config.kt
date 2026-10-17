package tech.arrakis.archangel.bridge

import android.content.Context
import android.content.SharedPreferences
import android.os.Build
import kotlinx.coroutines.channels.awaitClose
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.callbackFlow
import kotlinx.coroutines.flow.conflate
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.serialization.Serializable

/** What the connection uses: the relay it dials and the name it gives. */
@Serializable
data class Config(val url: String, val token: String, val device: String)

@Serializable
data class Relay(val url: String, val token: String)

enum class Mode { Dev, Prod }

/** Two relays, switched in one tap: the laptop's while developing, the deployed one the rest of the time. */
@Serializable
data class BridgeSettings(val mode: Mode, val dev: Relay, val prod: Relay, val device: String) {
    val relay: Relay get() = if (mode == Mode.Dev) dev else prod
    val config: Config get() = Config(relay.url, relay.token, device)

    /** These settings with the current mode's relay replaced. */
    fun withRelay(relay: Relay): BridgeSettings = if (mode == Mode.Dev) copy(dev = relay) else copy(prod = relay)

    companion object {
        /** Saved settings. Saved before there were modes, it's one relay: prod if it's `wss://`, else dev. */
        fun decode(json: String, defaults: BridgeSettings): BridgeSettings =
            runCatching { BridgeJson.decodeFromString<BridgeSettings>(json) }.getOrElse {
                val old = BridgeJson.decodeFromString<Config>(json)
                val relay = Relay(old.url, old.token)
                if (old.url.startsWith("wss://")) defaults.copy(mode = Mode.Prod, prod = relay, device = old.device)
                else defaults.copy(dev = relay, device = old.device)
            }
    }
}

/** Persists [BridgeSettings] as a single value, so one save is one change. */
class ConfigStore(context: Context) {
    private val prefs = context.getSharedPreferences("bridge", Context.MODE_PRIVATE)

    fun load(): BridgeSettings = prefs.getString(KEY, null)?.let { BridgeSettings.decode(it, defaults) } ?: defaults

    fun save(settings: BridgeSettings) = prefs.edit().putString(KEY, BridgeJson.encodeToString(settings)).apply()

    /** The current config, then each change to it. */
    val changes: Flow<Config> = callbackFlow {
        val listener = SharedPreferences.OnSharedPreferenceChangeListener { _, key ->
            if (key == KEY) trySend(load().config)
        }
        prefs.registerOnSharedPreferenceChangeListener(listener)
        send(load().config)
        awaitClose { prefs.unregisterOnSharedPreferenceChangeListener(listener) }
    }.conflate().distinctUntilChanged()

    private companion object {
        const val KEY = "config"

        val defaults = BridgeSettings(
            mode = Mode.Dev,
            dev = Relay(BuildConfig.DEFAULT_URL, BuildConfig.DEFAULT_TOKEN),
            prod = Relay("", ""),
            device = Build.MODEL.lowercase().replace(Regex("[^a-z0-9]+"), "-").trim('-'),
        )
    }
}
