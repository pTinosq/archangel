package tech.arrakis.archangel.bridge

import org.junit.Assert.assertEquals
import org.junit.Test

class BridgeSettingsTest {
    private val local = Relay("ws://localhost:8080/ws/device", "dev-token")
    private val defaults = BridgeSettings(Mode.Dev, local, Relay("", ""), "pixel-6")

    @Test
    fun `each mode keeps its own relay`() {
        val cloud = Relay("wss://relay.example/ws/device", "prod-token")
        val settings = defaults.copy(mode = Mode.Prod).withRelay(cloud)

        assertEquals(Config(cloud.url, cloud.token, "pixel-6"), settings.config)
        assertEquals(Config(local.url, local.token, "pixel-6"), settings.copy(mode = Mode.Dev).config)
    }

    @Test
    fun `round-trips`() {
        assertEquals(defaults, BridgeSettings.decode(BridgeJson.encodeToString(defaults), defaults))
    }

    @Test
    fun `a relay saved before modes lands in the mode its URL belongs to`() {
        val deployed = BridgeSettings.decode("""{"url":"wss://relay.example/ws/device","token":"t","device":"phone"}""", defaults)
        assertEquals(defaults.copy(mode = Mode.Prod, prod = Relay("wss://relay.example/ws/device", "t"), device = "phone"), deployed)

        val local = BridgeSettings.decode("""{"url":"ws://10.0.0.2:8080/ws/device","token":"t","device":"phone"}""", defaults)
        assertEquals(defaults.copy(dev = Relay("ws://10.0.0.2:8080/ws/device", "t"), device = "phone"), local)
    }
}
