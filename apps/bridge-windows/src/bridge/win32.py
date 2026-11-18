"""The Windows calls the bridge needs, and nothing else: the screen, the mouse and keyboard, the desktop with input
(the lock screen and administrator prompts are a desktop of their own), and processes for the signed-in user.

Every handle-returning function has its types declared: ctypes' default int would cut 64-bit handles in half.
"""

import base64
import ctypes
import msvcrt
import os
import subprocess
import tempfile
from ctypes import wintypes

import pywintypes
import win32api
import win32con
import win32event
import win32job
import win32process
import win32profile
import win32security
import win32ts
from PIL import Image

from bridge import config
from bridge.device import CommandError, Ran
from bridge.protocol import ErrorCode

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

HDESK = wintypes.HANDLE
ULONG_PTR = ctypes.c_size_t


def _declare(function, restype, *argtypes):
    function.restype = restype
    function.argtypes = argtypes


_declare(user32.GetDC, wintypes.HDC, wintypes.HWND)
_declare(user32.ReleaseDC, ctypes.c_int, wintypes.HWND, wintypes.HDC)
_declare(user32.GetSystemMetrics, ctypes.c_int, ctypes.c_int)
_declare(user32.SetCursorPos, wintypes.BOOL, ctypes.c_int, ctypes.c_int)
_declare(user32.VkKeyScanW, ctypes.c_short, wintypes.WCHAR)
_declare(user32.MapVirtualKeyW, wintypes.UINT, wintypes.UINT, wintypes.UINT)
_declare(user32.OpenInputDesktop, HDESK, wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
_declare(user32.SetThreadDesktop, wintypes.BOOL, HDESK)
_declare(user32.CloseDesktop, wintypes.BOOL, HDESK)
_declare(
    user32.GetUserObjectInformationW,
    wintypes.BOOL,
    wintypes.HANDLE,
    ctypes.c_int,
    ctypes.c_void_p,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
)
_declare(gdi32.CreateCompatibleDC, wintypes.HDC, wintypes.HDC)
_declare(gdi32.CreateCompatibleBitmap, wintypes.HBITMAP, wintypes.HDC, ctypes.c_int, ctypes.c_int)
_declare(gdi32.SelectObject, wintypes.HGDIOBJ, wintypes.HDC, wintypes.HGDIOBJ)
_declare(
    gdi32.BitBlt,
    wintypes.BOOL,
    wintypes.HDC,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.HDC,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.DWORD,
)
_declare(gdi32.DeleteObject, wintypes.BOOL, wintypes.HGDIOBJ)
_declare(gdi32.DeleteDC, wintypes.BOOL, wintypes.HDC)

SM_CXSCREEN, SM_CYSCREEN = 0, 1
SRCCOPY, CAPTUREBLT = 0x00CC0020, 0x40000000
UOI_NAME = 2
GENERIC_ALL = 0x10000000


# DPI: without this, Windows scales everything to 96 dpi, and screenshots and clicks miss on a 125% display.


def dpi_aware() -> None:
    """Call first, before anything reads the screen."""
    try:
        user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):  # per monitor, v2
            return
    except AttributeError:
        pass
    try:
        ctypes.WinDLL("shcore").SetProcessDpiAwareness(2)
    except (AttributeError, OSError):
        user32.SetProcessDPIAware()


def screen_size() -> tuple[int, int]:
    """The primary display, in physical pixels; it's at (0, 0)."""
    return user32.GetSystemMetrics(SM_CXSCREEN), user32.GetSystemMetrics(SM_CYSCREEN)


def density() -> float:
    try:
        return round(user32.GetDpiForSystem() / 96, 3)
    except AttributeError:
        return 1.0


# The screen


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


_declare(
    gdi32.GetDIBits,
    ctypes.c_int,
    wintypes.HDC,
    wintypes.HBITMAP,
    wintypes.UINT,
    wintypes.UINT,
    ctypes.c_void_p,
    ctypes.POINTER(BITMAPINFO),
    wintypes.UINT,
)


def capture() -> Image.Image:
    """The primary display as it is now, on the calling thread's desktop. GDI rather than desktop duplication:
    it works on the lock screen too."""
    width, height = screen_size()
    screen = user32.GetDC(None)
    if not screen:
        raise _failed("Couldn't reach the screen")
    memory = gdi32.CreateCompatibleDC(screen)
    bitmap = gdi32.CreateCompatibleBitmap(screen, width, height)
    try:
        previous = gdi32.SelectObject(memory, bitmap)
        copied = gdi32.BitBlt(memory, 0, 0, width, height, screen, 0, 0, SRCCOPY | CAPTUREBLT)
        gdi32.SelectObject(memory, previous)
        if not copied:
            raise _failed("Couldn't capture the screen")
        info = BITMAPINFO()
        info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        info.bmiHeader.biWidth = width
        info.bmiHeader.biHeight = -height  # top-down rows
        info.bmiHeader.biPlanes = 1
        info.bmiHeader.biBitCount = 32
        pixels = ctypes.create_string_buffer(width * height * 4)
        if gdi32.GetDIBits(memory, bitmap, 0, height, pixels, ctypes.byref(info), 0) != height:
            raise _failed("Couldn't read the screen")
        return Image.frombuffer("RGB", (width, height), pixels, "raw", "BGRX", 0, 1)
    finally:
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(memory)
        user32.ReleaseDC(None, screen)


# The mouse and keyboard


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.LONG),  # signed: the wheel turns both ways
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUT_UNION)]


_declare(user32.SendInput, wintypes.UINT, wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)

INPUT_MOUSE, INPUT_KEYBOARD = 0, 1
MOUSE_DOWN = {"left": 0x0002, "right": 0x0008}
MOUSE_UP = {"left": 0x0004, "right": 0x0010}
MOUSEEVENTF_WHEEL, MOUSEEVENTF_HWHEEL = 0x0800, 0x1000
WHEEL_DELTA = 120
KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP, KEYEVENTF_UNICODE = 0x0001, 0x0002, 0x0004
VK_SHIFT, VK_CONTROL, VK_MENU, VK_RETURN, VK_TAB = 0x10, 0x11, 0x12, 0x0D, 0x09


def _send(*inputs: INPUT) -> None:
    array = (INPUT * len(inputs))(*inputs)
    if user32.SendInput(len(inputs), array, ctypes.sizeof(INPUT)) != len(inputs):
        # Blocked: another desktop has input, or the window in front runs as administrator (and we don't).
        raise _failed("Windows blocked the input")


def move(x: int, y: int) -> None:
    if not user32.SetCursorPos(x, y):
        raise _failed("Couldn't move the mouse")


def button(name: str, down: bool) -> None:
    _send(INPUT(INPUT_MOUSE, _INPUT_UNION(mi=MOUSEINPUT(dwFlags=(MOUSE_DOWN if down else MOUSE_UP)[name]))))


def wheel(notches: int, horizontal: bool) -> None:
    flags = MOUSEEVENTF_HWHEEL if horizontal else MOUSEEVENTF_WHEEL
    _send(INPUT(INPUT_MOUSE, _INPUT_UNION(mi=MOUSEINPUT(mouseData=notches * WHEEL_DELTA, dwFlags=flags))))


def key(vk: int, down: bool, extended: bool = False) -> None:
    flags = (0 if down else KEYEVENTF_KEYUP) | (KEYEVENTF_EXTENDEDKEY if extended else 0)
    scan = user32.MapVirtualKeyW(vk, 0)  # some screens (the lock screen among them) read scan codes
    _send(INPUT(INPUT_KEYBOARD, _INPUT_UNION(ki=KEYBDINPUT(wVk=vk, wScan=scan, dwFlags=flags))))


def unicode(text: str) -> None:
    """Types `text` as characters, whatever the keyboard layout. Shortcuts don't fire."""
    units = text.encode("utf-16-le")
    for i in range(0, len(units), 2):  # UTF-16 code units: an emoji is two
        unit = int.from_bytes(units[i : i + 2], "little")
        for up in (0, KEYEVENTF_KEYUP):
            _send(INPUT(INPUT_KEYBOARD, _INPUT_UNION(ki=KEYBDINPUT(wScan=unit, dwFlags=KEYEVENTF_UNICODE | up))))


def layout_key(char: str) -> tuple[int, list[int]] | None:
    """The key that types `char` on the current layout, and the modifiers it needs; None if there's none."""
    found = user32.VkKeyScanW(char)
    if found == -1:
        return None
    vk, state = found & 0xFF, (found >> 8) & 0xFF
    modifiers = [vk for bit, vk in ((1, VK_SHIFT), (2, VK_CONTROL), (4, VK_MENU)) if state & bit]
    return vk, modifiers


def press(vk: int, modifiers: list[int] = (), extended: bool = False) -> None:
    for modifier in modifiers:
        key(modifier, True)
    try:
        key(vk, True, extended)
        key(vk, False, extended)
    finally:
        for modifier in reversed(modifiers):
            key(modifier, False)


# The desktop with input


def _desktop_name(handle) -> str:
    buffer = ctypes.create_unicode_buffer(256)
    if not user32.GetUserObjectInformationW(handle, UOI_NAME, buffer, ctypes.sizeof(buffer), None):
        return ""
    return buffer.value


class Desktop:
    """Keeps the calling thread on whichever desktop has input: Default normally, Winlogon while locked or during an
    administrator prompt. Only SYSTEM may open Winlogon. One per thread, and the thread must own no windows."""

    def __init__(self):
        self._handle = None
        self.name = ""

    def follow(self) -> str | None:
        """The input desktop's name, now this thread's; None if it can't be opened (locked, and not SYSTEM)."""
        handle = user32.OpenInputDesktop(0, False, GENERIC_ALL)
        if not handle:
            return None
        name = _desktop_name(handle)
        if self._handle and name == self.name:
            user32.CloseDesktop(handle)
            return name
        if not user32.SetThreadDesktop(handle):
            user32.CloseDesktop(handle)
            return None
        if self._handle:
            user32.CloseDesktop(self._handle)
        self._handle, self.name = handle, name
        return name


# Settings


def settings_store(path) -> config.Store:
    """The settings file, its secret encrypted for this machine: the administrator who saves it and the service
    (SYSTEM) both read it, and a copy taken elsewhere doesn't."""
    import win32crypt

    machine = 0x4  # CRYPTPROTECT_LOCAL_MACHINE
    return config.Store(
        path,
        protect=lambda data: win32crypt.CryptProtectData(data, None, None, None, None, machine),
        unprotect=lambda data: win32crypt.CryptUnprotectData(data, None, None, None, 0)[1],
    )


# Sessions and the signed-in user


def session() -> int:
    """This process's Windows session: the service's child runs in the console's."""
    return win32ts.ProcessIdToSessionId(win32api.GetCurrentProcessId())


def console_session() -> int | None:
    """The session at the physical screen, None while there's none (switching users)."""
    found = win32ts.WTSGetActiveConsoleSessionId()
    return None if found == 0xFFFFFFFF else found


def is_system() -> bool:
    token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32security.TOKEN_QUERY)
    user, _ = win32security.GetTokenInformation(token, win32security.TokenUser)
    return user == win32security.CreateWellKnownSid(win32security.WinLocalSystemSid)


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except OSError:
        return False


def lock_screen_up(in_session: int) -> bool:
    """The lock or sign-in screen is showing in `in_session` (LogonUI runs only then; not for an administrator
    prompt, which is consent.exe)."""
    processes = win32ts.WTSEnumerateProcesses(win32ts.WTS_CURRENT_SERVER_HANDLE, 1, 0)
    return any(sid == in_session and name.lower() == "logonui.exe" for sid, _, name, _ in processes)


def run_as_user(args: list[str]) -> None:
    """Starts `args`, without waiting, as the user signed in to this session, not as the bridge (SYSTEM, under the
    service): apps it opens are theirs."""
    if not is_system():
        subprocess.Popen(args, creationflags=subprocess.CREATE_NO_WINDOW)
        return
    user = _user_token()
    try:
        process, thread, _, _ = win32process.CreateProcessAsUser(
            user, None, subprocess.list2cmdline(args), None, None, False, *_as_user(user, win32con.CREATE_NO_WINDOW)
        )
    finally:
        user.Close()
    thread.Close()
    process.Close()


OUTPUT_BYTES = 32_000
"""Of each of stdout and stderr; the rest is cut."""


def run(args: list[str], timeout: float, as_user: bool = True) -> Ran:
    """Runs `args` until it exits, or for `timeout` seconds and then stops it and everything it started. As the user
    signed in to this session (when the bridge is SYSTEM, under the service) unless not `as_user`, in their home.

    Output goes to files, not pipes: an app it opens inherits them, and a pipe would stay open as long as the app.
    """
    user = _user_token() if as_user and is_system() else None
    flags = win32con.CREATE_NO_WINDOW | win32con.CREATE_SUSPENDED  # suspended until it's in the job
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        startup = win32process.STARTUPINFO()
        startup.dwFlags = win32con.STARTF_USESTDHANDLES
        startup.hStdOutput, startup.hStdError = _inheritable(out), _inheritable(err)
        command = subprocess.list2cmdline(args)
        try:
            if user:
                process, thread, _, _ = win32process.CreateProcessAsUser(
                    user, None, command, None, None, True, *_as_user(user, flags, startup)
                )
            else:
                home = os.path.expanduser("~") if as_user else None
                process, thread, _, _ = win32process.CreateProcess(
                    None, command, None, None, True, flags, None, home, startup
                )
        except pywintypes.error as error:
            raise CommandError(ErrorCode.FAILED, f"Couldn't start {args[0]}: {error.strerror}") from error
        finally:
            if user:
                user.Close()
        # A job holds it and all it starts, so a timeout stops the lot. Not killed when the job closes: what it
        # opened (an app, a folder) stays open.
        job = win32job.CreateJobObject(None, "")
        try:
            try:
                win32job.AssignProcessToJobObject(job, process)
                win32process.ResumeThread(thread)
            except pywintypes.error:
                win32process.TerminateProcess(process, 1)
                raise
            exited = win32event.WaitForSingleObject(process, int(timeout * 1000)) == win32event.WAIT_OBJECT_0
            if not exited:
                win32job.TerminateJobObject(job, 1)
                win32event.WaitForSingleObject(process, 5000)
            code = win32process.GetExitCodeProcess(process) if exited else None
        finally:
            job.Close()
            thread.Close()
            process.Close()
        return Ran(code, _read(out), _read(err))


def _user_token():
    try:
        return win32ts.WTSQueryUserToken(session())
    except pywintypes.error as error:
        if error.winerror == 1008:  # ERROR_NO_TOKEN
            raise CommandError(ErrorCode.FAILED, "Nobody is signed in to the PC: unlock it first") from error
        raise


def _as_user(user, flags: int, startup=None) -> tuple:
    """The rest of CreateProcessAsUser's arguments for `user`: their environment, home, and desktop."""
    startup = startup or win32process.STARTUPINFO()
    startup.lpDesktop = "winsta0\\default"
    environment = win32profile.CreateEnvironmentBlock(user, False)
    home = win32profile.GetUserProfileDirectory(user)
    return flags | win32con.CREATE_UNICODE_ENVIRONMENT, environment, home, startup


def _inheritable(file) -> int:
    handle = msvcrt.get_osfhandle(file.fileno())
    os.set_handle_inheritable(handle, True)
    return handle


def _read(file) -> str:
    size = os.fstat(file.fileno()).st_size
    file.seek(0)
    text = file.read(OUTPUT_BYTES).decode("utf-8", errors="replace")
    return text + (f"\n… ({size - OUTPUT_BYTES} more bytes cut)" if size > OUTPUT_BYTES else "")


def powershell(script: str, timeout: float = 30, as_user: bool = True) -> Ran:
    """Runs a PowerShell `script`, as the signed-in user unless not `as_user`."""
    # Passed encoded, so no quote in it can break the command line; UTF-8 out, and no progress bars in it.
    encoded = base64.b64encode(script.encode()).decode()
    wrapper = (
        "[Console]::OutputEncoding = [Text.Encoding]::UTF8; $ProgressPreference = 'SilentlyContinue'; "
        f"& ([ScriptBlock]::Create([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{encoded}'))))"
    )
    args = ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", wrapper]
    return run(args, timeout, as_user)


def _failed(what: str) -> CommandError:
    return CommandError(ErrorCode.FAILED, f"{what} (Windows error {ctypes.get_last_error()})")
