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
        val reply = executor.execute("""{"id":"1","type":"screenshot"}""")

        assertEquals("abc", reply.data!!.jsonObject["jpeg"]!!.jsonPrimitive.content)
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

    override suspend fun screenshot() = Screenshot(1, 1, "abc")

    override suspend fun swipe(x1: Float, y1: Float, x2: Float, y2: Float, durationMs: Long) {
        calls += "swipe $x1,$y1 -> $x2,$y2 in ${durationMs}ms"
    }

    override fun type(text: String) {
        calls += "type $text"
    }

    override fun key(key: GlobalKey) {
        calls += "key $key"
    }

    override fun launch(packageName: String) = throw CommandException(ErrorCode.NOT_FOUND, packageName)

    override fun uiTree() = throw IllegalStateException("boom")
}
