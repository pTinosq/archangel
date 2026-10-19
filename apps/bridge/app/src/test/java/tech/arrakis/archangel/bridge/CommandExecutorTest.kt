package tech.arrakis.archangel.bridge

import kotlinx.coroutines.test.runTest
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class CommandExecutorTest {
    private val device = FakeDevice()
    private val executor = CommandExecutor(device)

    @Test
    fun `tap is a zero-length swipe`() = runTest {
        val reply = executor.execute("""{"id":"1","type":"tap","x":10,"y":20}""")

        assertEquals(Reply("1", ok = true), reply)
        assertEquals(listOf("swipe 10.0,20.0 -> 10.0,20.0 in 50ms"), device.calls)
    }

    @Test
    fun `screenshot returns its data`() = runTest {
        val reply = executor.execute("""{"id":"1","type":"screenshot","quality":30}""")

        assertEquals("q30", reply.data!!.jsonObject["jpeg"]!!.jsonPrimitive.content)
    }

    @Test
    fun `screenshot scale and quality are validated`() = runTest {
        val reply = executor.execute("""{"id":"1","type":"screenshot","scale":0.5}""")
        assertEquals("50", reply.data!!.jsonObject["width"]!!.jsonPrimitive.content)

        assertError("invalid_command", executor.execute("""{"id":"2","type":"screenshot","scale":0}"""))
        assertError("invalid_command", executor.execute("""{"id":"3","type":"screenshot","scale":1.5}"""))
        assertError("invalid_command", executor.execute("""{"id":"4","type":"screenshot","quality":0}"""))
    }

    @Test
    fun `apps lists launchable apps`() = runTest {
        val reply = executor.execute("""{"id":"1","type":"apps"}""")
        assertEquals("""{"apps":[{"label":"WhatsApp","package":"com.whatsapp"}]}""", reply.data.toString())
    }

    @Test
    fun `repeated id replays the reply without executing again`() = runTest {
        val frame = """{"id":"1","type":"key","key":"home"}"""

        val first = executor.execute(frame)
        val second = executor.execute(frame)

        assertEquals(first, second)
        assertEquals(listOf("key Home"), device.calls)
    }

    @Test
    fun `location fakes a place, or stops`() = runTest {
        assertEquals(Reply("1", ok = true), executor.execute("""{"id":"1","type":"location","lat":51.5,"lon":-0.08}"""))
        executor.execute("""{"id":"2","type":"location"}""")
        assertEquals(listOf("location 51.5,-0.08", "location null,null"), device.calls)

        assertError("invalid_command", executor.execute("""{"id":"3","type":"location","lat":51.5}"""))
        assertError("invalid_command", executor.execute("""{"id":"4","type":"location","lat":95,"lon":0}"""))
    }

    @Test
    fun `unknown command is invalid`() = runTest {
        assertError("invalid_command", executor.execute("""{"id":"1","type":"dance"}"""))
    }

    @Test
    fun `missing params are invalid`() = runTest {
        assertError("invalid_command", executor.execute("""{"id":"1","type":"tap","x":10}"""))
    }

    @Test
    fun `frame without id is invalid`() = runTest {
        assertError("invalid_command", executor.execute("""{"type":"tap"}"""))
        assertError("invalid_command", executor.execute("not json"))
    }

    @Test
    fun `device failures keep their code`() = runTest {
        assertError("not_found", executor.execute("""{"id":"1","type":"launch","package":"nope"}"""))
    }

    @Test
    fun `unexpected exceptions become internal errors`() = runTest {
        assertError("internal", executor.execute("""{"id":"1","type":"ui_tree"}"""))
    }

    private fun assertError(code: String, reply: Reply) {
        assertTrue(!reply.ok)
        assertNull(reply.data)
        assertEquals(code, reply.error?.code)
    }
}

private class FakeDevice : Device {
    val calls = mutableListOf<String>()

    override suspend fun screenshot(scale: Float, quality: Int) =
        Screenshot((100 * scale).toInt(), (200 * scale).toInt(), "q$quality")

    override suspend fun swipe(x1: Float, y1: Float, x2: Float, y2: Float, durationMs: Long) {
        calls += "swipe $x1,$y1 -> $x2,$y2 in ${durationMs}ms"
    }

    override suspend fun type(text: String) {
        calls += "type $text"
    }

    override fun key(key: GlobalKey) {
        calls += "key $key"
    }

    override fun launch(packageName: String) = throw CommandException(ErrorCode.NOT_FOUND, packageName)

    override fun uiTree() = throw IllegalStateException("boom")

    override fun apps() = Apps(listOf(App("WhatsApp", "com.whatsapp")))

    override fun location(lat: Double?, lon: Double?) {
        calls += "location $lat,$lon"
    }
}
