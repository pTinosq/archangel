package tech.arrakis.archangel.bridge

import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.provider.Settings
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.SegmentedButton
import androidx.compose.material3.SegmentedButtonDefaults
import androidx.compose.material3.SingleChoiceSegmentedButtonRow
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        val store = ConfigStore(this)
        setContent {
            MaterialTheme {
                Surface(Modifier.fillMaxSize()) {
                    BridgeScreen(
                        initial = store.load(),
                        onSave = store::save,
                        onOpenAccessibility = { startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS)) },
                        onOpenBattery = {
                            val uri = Uri.fromParts("package", packageName, null)
                            startActivity(Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, uri))
                        },
                    )
                }
            }
        }
    }
}

@Composable
private fun BridgeScreen(
    initial: BridgeSettings,
    onSave: (BridgeSettings) -> Unit,
    onOpenAccessibility: () -> Unit,
    onOpenBattery: () -> Unit,
) {
    val status by Connection.status.collectAsState()
    var settings by remember { mutableStateOf(initial) }

    Column(
        Modifier.safeDrawingPadding().padding(24.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        Text("Archangel Bridge", style = MaterialTheme.typography.headlineMedium)
        Text(status.label, style = MaterialTheme.typography.titleMedium)
        SingleChoiceSegmentedButtonRow(Modifier.fillMaxWidth()) {
            Mode.entries.forEachIndexed { i, mode ->
                SegmentedButton(
                    selected = settings.mode == mode,
                    onClick = {
                        settings = settings.copy(mode = mode)
                        onSave(settings) // switching is the point: reconnect straight away
                    },
                    shape = SegmentedButtonDefaults.itemShape(i, Mode.entries.size),
                ) { Text(mode.name) }
            }
        }
        OutlinedTextField(
            value = settings.relay.url,
            onValueChange = { settings = settings.withRelay(settings.relay.copy(url = it.trim())) },
            label = { Text("Relay URL") },
            singleLine = true,
            modifier = Modifier.fillMaxWidth(),
        )
        OutlinedTextField(
            value = settings.relay.token,
            onValueChange = { settings = settings.withRelay(settings.relay.copy(token = it.trim())) },
            label = { Text("Token") },
            singleLine = true,
            visualTransformation = PasswordVisualTransformation(),
            modifier = Modifier.fillMaxWidth(),
        )
        OutlinedTextField(
            value = settings.device,
            onValueChange = { settings = settings.copy(device = it.trim()) },
            label = { Text("Device name") },
            singleLine = true,
            modifier = Modifier.fillMaxWidth(),
        )
        Button(onClick = { onSave(settings) }, modifier = Modifier.fillMaxWidth()) { Text("Save & reconnect") }
        OutlinedButton(onClick = onOpenAccessibility, modifier = Modifier.fillMaxWidth()) {
            Text("Accessibility settings")
        }
        OutlinedButton(onClick = onOpenBattery, modifier = Modifier.fillMaxWidth()) {
            Text("Allow running in background")
        }
    }
}

private val Status.label: String
    get() = when (this) {
        Status.Off -> "Off — enable Archangel Bridge in Accessibility settings"
        Status.Unconfigured -> "Set a relay URL to connect"
        Status.Connecting -> "Connecting…"
        Status.Connected -> "Connected"
        is Status.Retrying -> "Retrying in ${delayMs / 1000}s — $reason"
    }
