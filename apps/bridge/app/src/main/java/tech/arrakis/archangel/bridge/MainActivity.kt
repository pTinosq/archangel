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
    initial: Config,
    onSave: (Config) -> Unit,
    onOpenAccessibility: () -> Unit,
    onOpenBattery: () -> Unit,
) {
    val status by Connection.status.collectAsState()
    var config by remember { mutableStateOf(initial) }

    Column(
        Modifier.safeDrawingPadding().padding(24.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        Text("Archangel Bridge", style = MaterialTheme.typography.headlineMedium)
        Text(status.label, style = MaterialTheme.typography.titleMedium)
        OutlinedTextField(
            value = config.url,
            onValueChange = { config = config.copy(url = it.trim()) },
            label = { Text("Relay URL") },
            singleLine = true,
            modifier = Modifier.fillMaxWidth(),
        )
        OutlinedTextField(
            value = config.token,
            onValueChange = { config = config.copy(token = it.trim()) },
            label = { Text("Token") },
            singleLine = true,
            visualTransformation = PasswordVisualTransformation(),
            modifier = Modifier.fillMaxWidth(),
        )
        OutlinedTextField(
            value = config.device,
            onValueChange = { config = config.copy(device = it.trim()) },
            label = { Text("Device name") },
            singleLine = true,
            modifier = Modifier.fillMaxWidth(),
        )
        Button(onClick = { onSave(config) }, modifier = Modifier.fillMaxWidth()) { Text("Save & reconnect") }
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
