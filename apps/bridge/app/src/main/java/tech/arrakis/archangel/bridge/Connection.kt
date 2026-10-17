package tech.arrakis.archangel.bridge

import android.util.Log
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import kotlin.math.min
import kotlin.random.Random
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.awaitCancellation
import kotlinx.coroutines.cancelChildren
import kotlinx.coroutines.channels.ReceiveChannel
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.withTimeoutOrNull
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener

sealed interface Status {
    data object Off : Status
    data object Unconfigured : Status
    data object Connecting : Status
    data object Connected : Status
    data class Retrying(val reason: String, val delayMs: Long) : Status
}

/**
 * Keeps one WebSocket to the relay open until cancelled, reconnecting with backoff.
 * Each incoming frame is passed to [handle] and its result sent back.
 */
class Connection(
    private val config: Config,
    private val hello: String,
    private val networkAvailable: ReceiveChannel<Unit>,
    private val handle: suspend (String) -> String,
) {
    suspend fun run(): Nothing {
        try {
            if (config.url.isBlank()) {
                update(Status.Unconfigured)
                awaitCancellation()
            }
            var failures = 0
            while (true) {
                update(Status.Connecting)
                val session = session()
                failures = if (session.opened) 1 else failures + 1
                val delayMs = backoffMillis(failures)
                update(Status.Retrying(session.reason, delayMs))
                withTimeoutOrNull(delayMs) { networkAvailable.receive() }
            }
        } finally {
            update(Status.Off)
        }
    }

    private class Session(val opened: Boolean, val reason: String)

    private suspend fun session(): Session = coroutineScope {
        val request = runCatching {
            Request.Builder().url(config.url).header("Authorization", "Bearer ${config.token}").build()
        }.getOrElse { return@coroutineScope Session(opened = false, reason = "Invalid URL") }

        val opened = AtomicBoolean()
        val ended = CompletableDeferred<String>()
        val socket = client.newWebSocket(request, object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                opened.set(true)
                webSocket.send(hello)
                update(Status.Connected)
            }

            override fun onMessage(webSocket: WebSocket, text: String) {
                launch { webSocket.send(handle(text)) }
            }

            override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
                webSocket.close(NORMAL_CLOSURE, null)
                ended.complete("Closed by relay ($code)")
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                ended.complete(response?.let { "HTTP ${it.code}" } ?: t.message ?: t.javaClass.simpleName)
            }
        })

        try {
            Session(opened.get(), ended.await())
        } finally {
            socket.cancel()
            // Commands still queued belong to a dead connection; the relay has already given up on them.
            coroutineContext.cancelChildren()
        }
    }

    companion object {
        private const val TAG = "Archangel"
        private const val NORMAL_CLOSURE = 1000

        private val client = OkHttpClient.Builder().pingInterval(15, TimeUnit.SECONDS).build()
        private val mutableStatus = MutableStateFlow<Status>(Status.Off)

        val status: StateFlow<Status> = mutableStatus.asStateFlow()

        private fun update(status: Status) {
            Log.i(TAG, status.toString())
            mutableStatus.value = status
        }
    }
}

private const val MIN_BACKOFF_MS = 1_000L
private const val MAX_BACKOFF_MS = 30_000L

/** Exponential backoff with jitter: 1s, 2s, 4s… capped at 30s, each scaled by 50–100%. */
internal fun backoffMillis(failures: Int, random: Random = Random.Default): Long {
    val ceiling = min(MAX_BACKOFF_MS, MIN_BACKOFF_MS shl (failures - 1).coerceIn(0, 5))
    return (ceiling * random.nextDouble(0.5, 1.0)).toLong()
}
