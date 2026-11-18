"""The built exe against the real relay, on Windows: first in the foreground (`run`), then as the service, which must
start the bridge as SYSTEM in the desktop's session, and, with E2E_LOCK set, lock the PC and unlock it again. For CI
(it changes the signed-in account's password: never on a real PC); leaves screenshots in `artifacts/`.

    uv run python scripts/e2e.py dist/archangel-bridge/archangel-bridge.exe
"""

import getpass
import json
import os
import secrets
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

TOKEN = "e2e-token"
PORT = 8765
ARTIFACTS = Path("artifacts")
SERVICE_SETTINGS = Path(os.environ["PROGRAMDATA"]) / "Archangel" / "bridge.json"


def main(exe: str) -> None:
    ARTIFACTS.mkdir(exist_ok=True)
    relay = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "relay.app:create_app", "--factory", "--port", str(PORT)],
        env={**os.environ, "ARCHANGEL_TOKEN": TOKEN},
    )
    http = httpx.Client(base_url=f"http://127.0.0.1:{PORT}", headers={"Authorization": f"Bearer {TOKEN}"}, timeout=30)
    try:
        wait_for(lambda: http.get("/health").status_code == 200, "the relay")
        foreground(exe, http)
        as_service(exe, http)
    finally:
        relay.terminate()
    print("e2e: all good")


def foreground(exe: str, http: httpx.Client) -> None:
    settings = Path(tempfile.mkdtemp()) / "bridge.json"
    settings.write_text(json.dumps(relay_settings("e2e-run")))
    bridge = subprocess.Popen([exe, "run", "--config", str(settings)])
    try:
        hello = wait_for(lambda: device(http, "e2e-run"), "the foreground bridge to connect")
        print("hello:", hello)
        assert hello["platform"] == "windows" and hello["screen"]["width"] > 0
        exercise(http, "e2e-run", "run")
    finally:
        bridge.terminate()


def as_service(exe: str, http: httpx.Client) -> None:
    folder = SERVICE_SETTINGS.parent
    (folder / "logs").mkdir(parents=True, exist_ok=True)
    (folder / "trace").touch()  # stacks of every thread, should anything hang
    password = lockable() if os.environ.get("E2E_LOCK") else ""
    save_service_settings(password)
    try:
        step([exe, "install"])
        hello = wait_for(lambda: device(http, "e2e-service"), "the service's bridge to connect", 90)
        print("hello from the service:", hello)
        exercise(http, "e2e-service", "service")
        unlocked = command(http, "e2e-service", {"type": "unlock"})
        print("unlock while unlocked:", unlocked)
        assert unlocked["ok"], unlocked
        if password:
            lock_and_unlock(http, "e2e-service")
    finally:
        processes = ["tasklist", "/v", "/fi", "imagename eq archangel-bridge.exe"]
        for query in (["sc", "queryex", "ArchangelBridge"], processes):
            print(subprocess.run(query, capture_output=True, text=True).stdout)
        events = (
            "Get-WinEvent -FilterHashtable @{LogName='System'; ProviderName='Service Control Manager'} -MaxEvents 8 "
            "| Format-List TimeCreated, Message"
        )
        print(subprocess.run(["powershell", "-Command", events], capture_output=True, text=True).stdout)
        for log in sorted((folder / "logs").glob("*.log"), key=lambda log: not log.name.startswith("trace")):
            # A process still running may hold it open: cmd's type shares it where Python's open may not.
            shown = subprocess.run(["cmd", "/c", "type", str(log)], capture_output=True, text=True, errors="replace")
            print(f"--- {log.name}\n{(shown.stdout or shown.stderr)[-6000:]}")
        step([exe, "uninstall"])


def step(args: list[str]) -> None:
    """Runs one of the exe's own commands, saying how it went; a hang is reported, not waited out."""
    try:
        done = subprocess.run(args, timeout=90, capture_output=True, text=True)
        print(f"{' '.join(args[1:])}: exit {done.returncode} {done.stdout}{done.stderr}")
    except subprocess.TimeoutExpired:
        print(f"{' '.join(args[1:])}: still running after 90 s")


def exercise(http: httpx.Client, name: str, label: str) -> None:
    shot = http.get(f"/devices/{name}/screenshot.jpg")
    assert shot.status_code == 200, shot.text
    (ARTIFACTS / f"{label}.jpg").write_bytes(shot.content)
    apps = command(http, name, {"type": "apps"})
    assert apps["ok"], apps
    print(f"{len(apps['data']['apps'])} apps, e.g.", apps["data"]["apps"][:5])
    tree = command(http, name, {"type": "ui_tree"})
    assert tree["ok"], tree
    assert command(http, name, {"type": "key", "key": "esc"})["ok"]
    assert not command(http, name, {"type": "location", "lat": 1, "lon": 2})["ok"]
    ran = as_user(http, name)
    print("powershell:", ran)
    assert ran["data"]["exitCode"] == 2, ran


def as_user(http: httpx.Client, name: str) -> dict:
    """A powershell command, which must run as the signed-in user: never as SYSTEM, the service's bridge."""
    ran = command(http, name, {"type": "powershell", "script": "[Environment]::UserName; exit 2"})
    assert ran["ok"] and ran["data"]["stdout"].strip().lower() == getpass.getuser().lower(), ran
    return ran


def lockable() -> str:
    """Gives the signed-in account a password the bridge can type, and lets the lock screen take it without
    Ctrl+Alt+Del (Windows Server asks for it; home PCs don't, and the bridge can't send it)."""
    password = f"Arch-{secrets.token_hex(8)}-9!"
    subprocess.run(["net", "user", getpass.getuser(), password], check=True, capture_output=True)
    policy = r"HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System"
    subprocess.run(["reg", "add", policy, "/v", "DisableCAD", "/t", "REG_DWORD", "/d", "1", "/f"], check=True)
    return password


def save_service_settings(secret: str) -> None:
    from bridge.config import Relay, Settings
    from bridge.win32 import settings_store

    relay = Relay(f"ws://127.0.0.1:{PORT}/ws/device", TOKEN)
    settings_store(SERVICE_SETTINGS).save(Settings(prod=relay, device="e2e-service", secret=secret))


def lock_and_unlock(http: httpx.Client, name: str) -> None:
    assert command(http, name, {"type": "key", "key": "lock"})["ok"]
    wait_for(lock_screen_up, "the lock screen", 20)
    time.sleep(3)  # its picture slides in
    locked = http.get(f"/devices/{name}/screenshot.jpg")
    assert locked.status_code == 200, locked.text  # the service sees the lock screen
    (ARTIFACTS / "locked.jpg").write_bytes(locked.content)
    print("the lock screen, as the service saw it:", describe(locked.content))
    print("powershell while locked:", as_user(http, name))
    unlocked = command(http, name, {"type": "unlock"})
    print("unlock on the lock screen:", unlocked)
    assert unlocked["ok"], unlocked
    assert not lock_screen_up(), "unlock said ok, but the lock screen is still up"
    time.sleep(2)
    desktop = http.get(f"/devices/{name}/screenshot.jpg").content
    (ARTIFACTS / "unlocked.jpg").write_bytes(desktop)
    print("the desktop again:", describe(desktop))
    again = command(http, name, {"type": "unlock"})
    assert again["ok"], again  # unlocked now: nothing to do


def lock_screen_up() -> bool:
    """LogonUI runs only while the lock (or sign-in) screen is showing."""
    found = subprocess.run(["tasklist", "/fi", "imagename eq LogonUI.exe"], capture_output=True, text=True).stdout
    return "LogonUI.exe" in found


def describe(jpeg: bytes) -> str:
    """Enough to tell a real screen from a black one, without downloading it."""
    import io

    from PIL import Image, ImageStat

    image = Image.open(io.BytesIO(jpeg)).convert("L")
    stat = ImageStat.Stat(image)
    return f"{image.size[0]}x{image.size[1]}, brightness {stat.mean[0]:.0f}, contrast {stat.stddev[0]:.0f}"


def relay_settings(name: str) -> dict:
    return {"mode": "prod", "prod": {"url": f"ws://127.0.0.1:{PORT}/ws/device", "token": TOKEN}, "device": name}


def device(http: httpx.Client, name: str) -> dict | None:
    return next((hello for hello in http.get("/devices").json() if hello["device"] == name), None)


def command(http: httpx.Client, name: str, body: dict) -> dict:
    response = http.post(f"/devices/{name}/commands", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def wait_for(check, what: str, seconds: float = 30):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            if found := check():
                return found
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    raise SystemExit(f"e2e: gave up waiting for {what}")


if __name__ == "__main__":
    main(sys.argv[1])
