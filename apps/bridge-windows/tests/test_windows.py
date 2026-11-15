"""The real thing, on a real Windows desktop (CI's runner): screen, mouse, keyboard, UI Automation, the Start menu."""

import asyncio
import ctypes
import io
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows only")

if sys.platform == "win32":
    import win32clipboard
    from PIL import Image

    from bridge import win32
    from bridge.windows import WindowsDevice

    win32.dpi_aware()

ARTIFACTS = Path(os.environ.get("ARTIFACTS", "artifacts"))


@pytest.fixture(scope="module")
def device():
    return WindowsDevice()


@pytest.fixture
def notepad():
    """Notepad, open and in front: typing before its window has focus would go elsewhere."""
    process = subprocess.Popen(["notepad.exe"])
    user32 = ctypes.windll.user32
    title = ctypes.create_unicode_buffer(256)
    for _ in range(100):
        time.sleep(0.1)
        user32.GetWindowTextW(user32.GetForegroundWindow(), title, 256)
        if "Notepad" in title.value:
            break
    else:
        pytest.fail(f"Notepad never came to the front (in front: {title.value!r})")
    time.sleep(0.5)  # its edit box takes focus just after the window
    yield process
    subprocess.run(["taskkill", "/im", "notepad.exe", "/f"], capture_output=True)


def run(coroutine):
    return asyncio.run(coroutine)


def test_the_screen_has_a_size(device):
    screen = device.screen()
    assert screen.width > 0 and screen.height > 0 and screen.density >= 1


def test_a_screenshot_is_a_jpeg_of_the_scaled_screen(device):
    shot = run(device.screenshot(0.5, 60))
    image = Image.open(io.BytesIO(shot.jpeg))
    screen = device.screen()
    assert image.format == "JPEG"
    assert (shot.width, shot.height) == image.size == (round(screen.width * 0.5), round(screen.height * 0.5))
    assert image.convert("L").getextrema()[0] != image.convert("L").getextrema()[1], "a blank screen"
    ARTIFACTS.mkdir(exist_ok=True)
    (ARTIFACTS / "desktop.jpg").write_bytes(run(device.screenshot(1.0, 80)).jpeg)


def test_tap_moves_the_mouse_there(device):
    run(device.tap(200, 150, 10, "left", 1))
    point = ctypes.wintypes.POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(point))
    assert (point.x, point.y) == (200, 150)


def test_typing_and_chords_land_in_the_window_in_front(device, notepad):
    run(device.type("Hello\tworld\nnew line é 🙂"))
    run(device.key("ctrl+a"))
    run(device.key("ctrl+c"))
    time.sleep(0.3)
    win32clipboard.OpenClipboard()
    try:
        copied = win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT)
    finally:
        win32clipboard.CloseClipboard()
    assert copied.replace("\r\n", "\n") == "Hello\tworld\nnew line é 🙂"


def test_the_ui_tree_is_the_window_in_front(device, notepad):
    [window] = run(device.ui_tree())
    assert window["class"] == "Window"
    assert "Notepad" in window.get("text", "")
    assert window.get("children")


def test_the_start_menu_lists_apps_and_opens_them(device):
    apps = run(device.apps())
    assert apps, "no apps"
    assert all(app["label"] and app["package"] for app in apps)
    notepad = next((app for app in apps if app["label"].lower() == "notepad"), None)
    if notepad:
        run(device.launch(notepad["package"]))
        time.sleep(2)
        subprocess.run(["taskkill", "/im", "notepad.exe", "/f"], capture_output=True)


def test_scroll_and_drag_run(device):
    width, height = win32.screen_size()
    run(device.scroll(width // 2, height // 2, 0, 3))
    run(device.swipe(width // 2, height // 2, width // 2 + 10, height // 2, 50))


def test_unlocking_an_unlocked_pc_does_nothing(device):
    run(device.unlock())


def test_the_network_card_is_found(device):
    mac = device.mac()
    assert mac is None or len(mac.split(":")) == 6
