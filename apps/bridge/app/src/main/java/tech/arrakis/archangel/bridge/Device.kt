package tech.arrakis.archangel.bridge

/** What the bridge can do to the phone. Coordinates are physical screen pixels. */
interface Device {
    /** [scale] in (0, 1] shrinks the image; 1 is full resolution. */
    suspend fun screenshot(scale: Float): Screenshot
    suspend fun swipe(x1: Float, y1: Float, x2: Float, y2: Float, durationMs: Long)
    fun type(text: String)
    fun key(key: GlobalKey)
    fun launch(packageName: String)
    fun uiTree(): UiTree
    fun apps(): Apps
}

/** An expected failure, reported to the caller with [code] (see [ErrorCode]). */
class CommandException(val code: String, message: String) : Exception(message)
