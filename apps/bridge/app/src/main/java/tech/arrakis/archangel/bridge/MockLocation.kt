package tech.arrakis.archangel.bridge

import android.annotation.SuppressLint
import android.content.Context
import android.location.Location
import android.location.LocationManager
import android.location.provider.ProviderProperties
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.os.SystemClock

/**
 * Makes the phone say it's somewhere else, for apps that only use GPS ("near me"). The bridge must be the mock
 * location app (Developer options). The position is resent every second: apps ask for fresh fixes and drop stale ones.
 */
class MockLocation(context: Context) {
    private val manager = context.getSystemService(LocationManager::class.java)
    private val handler = Handler(Looper.getMainLooper())
    private var at: Pair<Double, Double>? = null
    private val repeat = object : Runnable {
        override fun run() {
            val (lat, lon) = at ?: return
            runCatching { providers.forEach { manager.setTestProviderLocation(it, fix(it, lat, lon)) } }
            handler.postDelayed(this, REPEAT_MS)
        }
    }
    private val providers = buildList {
        add(LocationManager.GPS_PROVIDER)
        add(LocationManager.NETWORK_PROVIDER)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) add(LocationManager.FUSED_PROVIDER)
    }

    fun set(lat: Double, lon: Double) {
        try {
            if (at == null) providers.forEach(::add)
            at = lat to lon
            providers.forEach { manager.setTestProviderLocation(it, fix(it, lat, lon)) }
        } catch (_: SecurityException) {
            stop()
            throw CommandException(
                ErrorCode.FAILED,
                "The bridge isn't the mock location app: Developer options → Select mock location app → Archangel Bridge",
            )
        }
        handler.removeCallbacks(repeat)
        handler.postDelayed(repeat, REPEAT_MS)
    }

    /** Back to where the phone really is. */
    fun stop() {
        handler.removeCallbacks(repeat)
        at = null
        providers.forEach { runCatching { manager.removeTestProvider(it) } }
    }

    // The replacement takes a ProviderProperties object, API 31; minSdk is 30. Its int constants are inlined, so fine.
    @Suppress("DEPRECATION")
    @SuppressLint("InlinedApi")
    private fun add(provider: String) {
        manager.addTestProvider(
            provider, false, false, false, false, true, true, true,
            ProviderProperties.POWER_USAGE_LOW, ProviderProperties.ACCURACY_FINE,
        )
        manager.setTestProviderEnabled(provider, true)
    }

    private fun fix(provider: String, lat: Double, lon: Double) = Location(provider).apply {
        latitude = lat
        longitude = lon
        accuracy = ACCURACY_M
        time = System.currentTimeMillis()
        elapsedRealtimeNanos = SystemClock.elapsedRealtimeNanos()
    }

    private companion object {
        const val REPEAT_MS = 1000L
        const val ACCURACY_M = 10f
    }
}
