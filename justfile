set dotenv-load

bridge := "apps/bridge"
relay := "apps/relay"
agent := "apps/agent"
panel := "apps/panel"
port := "8080"
service := "tech.arrakis.archangel.bridge/.BridgeService"

default:
    @just --list

# Chat with Archangel in the terminal
agent:
    cd {{agent}} && uv run python -m agent

# The control panel on http://localhost:5173; needs `just agent` running
panel:
    cd {{panel}} && npm install --silent && npm run dev

# Run the relay locally, reloading on change
relay:
    cd {{relay}} && uv run uvicorn relay.app:create_app --factory --reload --host 0.0.0.0 --port {{port}}

# Build and install the debug app on the connected phone, then switch it on
bridge-install: && bridge-enable
    cd {{bridge}} && ./gradlew installDebug

# Switch the accessibility service on. Replaces any other enabled services.
[script]
bridge-enable:
    # Right after an install Android may drop the setting, so check it stuck.
    for _ in 1 2 3 4 5; do
        adb shell settings put secure enabled_accessibility_services {{service}}
        adb shell settings put secure accessibility_enabled 1
        sleep 1
        [ "$(adb shell settings get secure enabled_accessibility_services)" = "{{service}}" ] && exit 0
    done
    echo "Could not enable the accessibility service" >&2
    exit 1

# Point the phone's localhost at this machine over USB
reverse:
    adb reverse tcp:{{port}} tcp:{{port}}

# Expose the local relay publicly; set the app's URL to wss://<host>/ws/device
tunnel:
    ngrok http {{port}}

# Send a command, e.g. just send '{"type":"tap","x":540,"y":1200}'
send command device="pixel-6":
    @curl -s -X POST localhost:{{port}}/devices/{{device}}/commands \
        -H "Authorization: Bearer $ARCHANGEL_TOKEN" -H "Content-Type: application/json" -d '{{command}}'; echo

# Save the phone's screen to screenshot.jpg
screenshot device="pixel-6":
    @curl -sf -o screenshot.jpg localhost:{{port}}/devices/{{device}}/screenshot.jpg \
        -H "Authorization: Bearer $ARCHANGEL_TOKEN" && echo "screenshot.jpg"

test:
    cd {{agent}} && uv run pytest -q
    cd {{relay}} && uv run pytest -q
    cd {{panel}} && npm install --silent && npx tsc --noEmit
    cd {{bridge}} && ./gradlew testDebugUnitTest -q

# Follow the bridge's logs on the phone
logs:
    adb logcat -s Archangel
