package tech.arrakis.archangel.bridge

import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import org.junit.Assert.assertEquals
import org.junit.Test

class ProtocolTest {
    @Test
    fun `decodes commands with defaults and renamed fields`() {
        assertEquals(
            Command.Swipe("1", 1f, 2f, 3f, 4f, durationMs = 300),
            BridgeJson.decodeFromString<Command>("""{"id":"1","type":"swipe","x1":1,"y1":2,"x2":3,"y2":4}"""),
        )
        assertEquals(
            Command.Launch("2", "com.android.settings"),
            BridgeJson.decodeFromString<Command>("""{"id":"2","type":"launch","package":"com.android.settings"}"""),
        )
        assertEquals(
            Command.Key("3", GlobalKey.QuickSettings),
            BridgeJson.decodeFromString<Command>("""{"id":"3","type":"key","key":"quick_settings"}"""),
        )
        assertEquals(
            Command.Key("4", GlobalKey.Enter),
            BridgeJson.decodeFromString<Command>("""{"id":"4","type":"key","key":"enter"}"""),
        )
    }

    @Test
    fun `replies omit empty fields`() {
        assertEquals("""{"id":"1","ok":true}""", BridgeJson.encodeToString(Reply.success("1", null)))
        assertEquals(
            """{"id":"1","ok":false,"error":{"code":"failed","message":"no"}}""",
            BridgeJson.encodeToString(Reply.failure("1", "failed", "no")),
        )
        assertEquals(
            """{"id":"1","ok":true,"data":{"a":1}}""",
            BridgeJson.encodeToString(Reply.success("1", buildJsonObject { put("a", JsonPrimitive(1)) })),
        )
    }

    @Test
    fun `hello carries its type`() {
        val hello = Hello("pixel-6", Hello.Screen(1080, 2400, 2.625f), "0.1.0")
        assertEquals(
            """{"device":"pixel-6","screen":{"width":1080,"height":2400,"density":2.625},"version":"0.1.0","type":"hello"}""",
            BridgeJson.encodeToString(hello),
        )
    }
}
