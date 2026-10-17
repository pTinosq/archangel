package tech.arrakis.archangel.bridge

/** What the bridge can do to the phone. Coordinates are physical screen pixels. */
interface Device {
    /** [scale] in (0, 1] shrinks the image; 1 is full resolution. [quality] is JPEG quality, 1..100. */
    suspend fun screenshot(scale: Float, quality: Int): Screenshot
    suspend fun swipe(x1: Float, y1: Float, x2: Float, y2: Float, durationMs: Long)
    suspend fun type(text: String)
    fun key(key: GlobalKey)
    fun launch(packageName: String)
    fun uiTree(): UiTree
    fun apps(): Apps
}

/** An expected failure, reported to the caller with [code] (see [ErrorCode]). */
class CommandException(val code: String, message: String) : Exception(message)
