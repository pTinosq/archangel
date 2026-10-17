package tech.arrakis.archangel.bridge

import android.accessibilityservice.AccessibilityService
import android.net.ConnectivityManager
import android.net.Network
import android.util.Log
import android.view.WindowManager
import android.view.accessibility.AccessibilityEvent
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.launch

/**
 * The bridge's only long-lived component. Android keeps enabled accessibility services running,
 * so this owns the relay connection for exactly as long as the user has the bridge switched on.
 */
class BridgeService : AccessibilityService() {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private val networkAvailable = Channel<Unit>(Channel.CONFLATED)
    private val networkCallback = object : ConnectivityManager.NetworkCallback() {
        override fun onAvailable(network: Network) {
            networkAvailable.trySend(Unit)
        }
    }

    override fun onServiceConnected() {
        getSystemService(ConnectivityManager::class.java).registerDefaultNetworkCallback(networkCallback)
        val executor = CommandExecutor(AndroidDevice(this))
        val handle: suspend (String) -> String = { frame ->
            val reply = executor.execute(frame)
            if (!reply.ok) Log.w(TAG, "${reply.id}: ${reply.error}")
            BridgeJson.encodeToString(reply)
        }
        scope.launch {
            ConfigStore(this@BridgeService).changes.collectLatest { config ->
                Connection(config, BridgeJson.encodeToString(hello(config)), networkAvailable, handle).run()
            }
        }
    }

    override fun onDestroy() {
        getSystemService(ConnectivityManager::class.java).unregisterNetworkCallback(networkCallback)
        scope.cancel()
        super.onDestroy()
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent) = Unit

    override fun onInterrupt() = Unit

    private fun hello(config: Config): Hello {
        val bounds = getSystemService(WindowManager::class.java).maximumWindowMetrics.bounds
        val screen = Hello.Screen(bounds.width(), bounds.height(), resources.displayMetrics.density)
        return Hello(config.device, screen, BuildConfig.VERSION_NAME)
    }

    private companion object {
        const val TAG = "Archangel"
    }
}
