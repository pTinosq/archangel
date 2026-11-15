package tech.arrakis.archangel.bridge

import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetAddress
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

/** `aa:bb:cc:dd:ee:ff` or `aa-bb-…` to its 6 bytes. */
fun parseMac(text: String): ByteArray {
    val parts = text.trim().split(':', '-')
    require(parts.size == 6 && parts.all { it.length == 2 }) { "mac must be like aa:bb:cc:dd:ee:ff" }
    return ByteArray(6) { i ->
        parts[i].toIntOrNull(16)?.toByte() ?: throw IllegalArgumentException("mac must be hexadecimal: $text")
    }
}

/** Six 0xFF bytes, then the address sixteen times: what a sleeping network card listens for. */
fun magicPacket(mac: ByteArray): ByteArray {
    require(mac.size == 6)
    return ByteArray(6) { 0xFF.toByte() } + ByteArray(16 * 6) { mac[it % 6] }
}

/** To everyone on the phone's network, on both ports cards listen on. */
suspend fun sendMagicPacket(mac: ByteArray) = withContext(Dispatchers.IO) {
    val packet = magicPacket(mac)
    val everyone = InetAddress.getByName("255.255.255.255")
    try {
        DatagramSocket().use { socket ->
            socket.broadcast = true
            for (port in listOf(9, 7)) socket.send(DatagramPacket(packet, packet.size, everyone, port))
        }
    } catch (e: java.io.IOException) {
        throw CommandException(ErrorCode.FAILED, "Couldn't send the wake packet: ${e.message}")
    }
}
