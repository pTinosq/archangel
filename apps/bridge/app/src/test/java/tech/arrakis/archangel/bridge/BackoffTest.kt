package tech.arrakis.archangel.bridge

import kotlin.random.Random
import org.junit.Assert.assertEquals
import org.junit.Test

class BackoffTest {
    private val ceiling = object : Random() {
        override fun nextBits(bitCount: Int) = 0
        override fun nextDouble(from: Double, until: Double) = until
    }

    @Test
    fun `doubles from one second up to thirty`() {
        val delays = (1..7).map { backoffMillis(it, ceiling) }
        assertEquals(listOf(1_000L, 2_000L, 4_000L, 8_000L, 16_000L, 30_000L, 30_000L), delays)
    }
}
