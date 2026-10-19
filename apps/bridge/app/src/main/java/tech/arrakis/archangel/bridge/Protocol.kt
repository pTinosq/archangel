package tech.arrakis.archangel.bridge

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonElement

/** Wire types. The contract lives in docs/protocol.md; keep the two in step. */
val BridgeJson = Json {
    ignoreUnknownKeys = true
    encodeDefaults = true
    explicitNulls = false
}

@Serializable
sealed interface Command {
    val id: String

    @Serializable
    @SerialName("screenshot")
    data class Screenshot(override val id: String, val scale: Float = 1f, val quality: Int = 80) : Command

    @Serializable
    @SerialName("tap")
    data class Tap(override val id: String, val x: Float, val y: Float, val durationMs: Long = 50) : Command

    @Serializable
    @SerialName("swipe")
    data class Swipe(
        override val id: String,
        val x1: Float,
        val y1: Float,
        val x2: Float,
        val y2: Float,
        val durationMs: Long = 300,
    ) : Command

    @Serializable
    @SerialName("type")
    data class Type(override val id: String, val text: String) : Command

    @Serializable
    @SerialName("key")
    data class Key(override val id: String, val key: GlobalKey) : Command

    @Serializable
    @SerialName("launch")
    data class Launch(override val id: String, @SerialName("package") val packageName: String) : Command

    @Serializable
    @SerialName("ui_tree")
    data class UiTree(override val id: String) : Command

    @Serializable
    @SerialName("apps")
    data class Apps(override val id: String) : Command

    @Serializable
    @SerialName("location")
    data class Location(override val id: String, val lat: Double? = null, val lon: Double? = null) : Command
}

@Serializable
enum class GlobalKey {
    @SerialName("back") Back,
    @SerialName("home") Home,
    @SerialName("recents") Recents,
    @SerialName("notifications") Notifications,
    @SerialName("quick_settings") QuickSettings,
    @SerialName("lock") Lock,
    /** Not global: the focused field's IME action (search, send, next). */
    @SerialName("enter") Enter,
}

@Serializable
data class Reply(val id: String, val ok: Boolean, val data: JsonElement? = null, val error: Error? = null) {
    @Serializable
    data class Error(val code: String, val message: String)

    companion object {
        fun success(id: String, data: JsonElement?) = Reply(id, ok = true, data = data)
        fun failure(id: String, code: String, message: String) = Reply(id, ok = false, error = Error(code, message))
    }
}

object ErrorCode {
    const val INVALID_COMMAND = "invalid_command"
    const val NOT_FOUND = "not_found"
    const val FAILED = "failed"
    const val INTERNAL = "internal"
}

@Serializable
data class Hello(val device: String, val screen: Screen, val version: String, val type: String = "hello") {
    @Serializable
    data class Screen(val width: Int, val height: Int, val density: Float)
}

@Serializable
data class Screenshot(val width: Int, val height: Int, val jpeg: String)

@Serializable
data class UiTree(val windows: List<Node>)

@Serializable
data class Apps(val apps: List<App>)

@Serializable
data class App(val label: String, @SerialName("package") val packageName: String)

@Serializable
data class Node(
    @SerialName("class") val className: String,
    val text: String? = null,
    val desc: String? = null,
    val id: String? = null,
    val bounds: List<Int>,
    val flags: List<String>? = null,
    val children: List<Node>? = null,
)
