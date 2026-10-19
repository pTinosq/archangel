package tech.arrakis.archangel.bridge

import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.decodeFromJsonElement
import kotlinx.serialization.json.encodeToJsonElement
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive

/**
 * Runs commands one at a time and never throws: every frame gets a [Reply].
 * Replies are remembered by id, so a retried command is answered, not re-executed.
 */
class CommandExecutor(private val device: Device) {
    private val mutex = Mutex()
    private val recent = object : LinkedHashMap<String, Reply>() {
        override fun removeEldestEntry(eldest: Map.Entry<String, Reply>) = size > REMEMBERED_REPLIES
    }

    suspend fun execute(frame: String): Reply = mutex.withLock {
        val command = runCatching { BridgeJson.parseToJsonElement(frame).jsonObject }.getOrNull()
        val id = command?.get("id")?.jsonPrimitive?.contentOrNull
            ?: return Reply.failure("", ErrorCode.INVALID_COMMAND, "Expected a JSON object with an id")
        recent[id] ?: run(id, command).also { recent[id] = it }
    }

    private suspend fun run(id: String, frame: JsonObject): Reply = try {
        Reply.success(id, perform(BridgeJson.decodeFromJsonElement<Command>(frame)))
    } catch (e: CancellationException) {
        throw e
    } catch (e: CommandException) {
        Reply.failure(id, e.code, e.message.orEmpty())
    } catch (e: IllegalArgumentException) {
        Reply.failure(id, ErrorCode.INVALID_COMMAND, e.message.orEmpty())
    } catch (e: Exception) {
        Reply.failure(id, ErrorCode.INTERNAL, e.toString())
    }

    private suspend fun perform(command: Command): JsonElement? {
        when (command) {
            is Command.Screenshot -> {
                require(command.scale > 0f && command.scale <= 1f) { "scale must be in (0, 1]" }
                require(command.quality in 1..100) { "quality must be in 1..100" }
                return BridgeJson.encodeToJsonElement(device.screenshot(command.scale, command.quality))
            }
            is Command.UiTree -> return BridgeJson.encodeToJsonElement(device.uiTree())
            is Command.Apps -> return BridgeJson.encodeToJsonElement(device.apps())
            is Command.Tap -> device.swipe(command.x, command.y, command.x, command.y, command.durationMs)
            is Command.Swipe -> device.swipe(command.x1, command.y1, command.x2, command.y2, command.durationMs)
            is Command.Type -> device.type(command.text)
            is Command.Key -> device.key(command.key)
            is Command.Launch -> device.launch(command.packageName)
            is Command.Location -> {
                require((command.lat == null) == (command.lon == null)) { "lat and lon go together" }
                require(command.lat == null || command.lat in -90.0..90.0 && command.lon!! in -180.0..180.0) {
                    "lat must be in -90..90 and lon in -180..180"
                }
                device.location(command.lat, command.lon)
            }
        }
        return null
    }

    private companion object {
        const val REMEMBERED_REPLIES = 64
    }
}
