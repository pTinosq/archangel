"""The Windows service, so the bridge runs from boot, whoever's signed in, and reaches the lock screen.

A service lives in session 0, which has no screen. So the service only supervises: it starts the bridge itself as
SYSTEM in the session at the physical screen (the console), and starts it again when that session changes (signing
out, switching users) or the bridge exits. As SYSTEM there, the bridge may follow input onto the lock screen.
"""

import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import servicemanager
import win32api
import win32con
import win32event
import win32process
import win32security
import win32service
import win32serviceutil

from bridge import config, win32

log = logging.getLogger(__name__)

NAME = "ArchangelBridge"
DISPLAY_NAME = "Archangel bridge"
DESCRIPTION = "Lets Archangel operate this PC: dials the relay and runs its commands, the lock screen included."
CHECK_SECONDS = 2
RESTART_AFTER_SECONDS = 10
"""A bridge that exits sooner is failing at startup: don't spin."""
STILL_ACTIVE = 259


def installed_folder() -> Path:
    return Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Archangel"


def command(mode: str) -> list[str]:
    """How to start the bridge in `mode`: the built exe, or Python while developing."""
    if getattr(sys, "frozen", False):
        return [sys.executable, mode]
    return [sys.executable, "-m", "bridge", mode]


class Service(win32serviceutil.ServiceFramework):
    _svc_name_ = NAME
    _svc_display_name_ = DISPLAY_NAME
    _svc_description_ = DESCRIPTION

    def __init__(self, args):
        super().__init__(args)
        self._stop = win32event.CreateEvent(None, True, False, None)

    def SvcStop(self):
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        win32event.SetEvent(self._stop)

    def SvcDoRun(self):
        try:
            supervise(self._stop)
        except Exception:
            log.exception("The service failed")
            raise


def host() -> None:
    """The service's entry point, when Windows starts it."""
    servicemanager.Initialize()
    servicemanager.PrepareToHostSingle(Service)
    servicemanager.StartServiceCtrlDispatcher()


def supervise(stop) -> None:
    child = None
    child_session = None
    started = 0.0
    log.info("Service started")
    while win32event.WaitForSingleObject(stop, CHECK_SECONDS * 1000) == win32event.WAIT_TIMEOUT:
        session = win32.console_session()
        alive = child is not None and win32process.GetExitCodeProcess(child) == STILL_ACTIVE
        if alive and child_session == session:
            continue
        if child is not None:
            if alive:
                log.info("Session %s → %s: restarting the bridge there", child_session, session)
                win32process.TerminateProcess(child, 0)
            else:
                log.warning("The bridge exited (%s)", win32process.GetExitCodeProcess(child))
            child.Close()
            child = None
        if session is None or time.monotonic() - started < RESTART_AFTER_SECONDS:
            continue
        try:
            child, child_session, started = _start_in(session), session, time.monotonic()
        except Exception:
            log.exception("Couldn't start the bridge in session %s", session)
            started = time.monotonic()
    if child is not None:
        win32process.TerminateProcess(child, 0)
        child.Close()
    log.info("Service stopped")


def _start_in(session: int):
    """The bridge, as SYSTEM, in `session`: our own token, moved there."""
    own = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.MAXIMUM_ALLOWED)
    token = win32security.DuplicateTokenEx(
        own, win32security.SecurityImpersonation, win32con.MAXIMUM_ALLOWED, win32security.TokenPrimary
    )
    win32security.SetTokenInformation(token, win32security.TokenSessionId, session)
    startup = win32process.STARTUPINFO()
    startup.lpDesktop = "winsta0\\default"
    process, thread, pid, _ = win32process.CreateProcessAsUser(
        token,
        None,
        subprocess.list2cmdline(command("session")),
        None,
        None,
        False,
        win32con.CREATE_NO_WINDOW,
        None,
        None,
        startup,
    )
    thread.Close()
    token.Close()
    own.Close()
    log.info("Bridge started in session %s (pid %s)", session, pid)
    return process


# Installing (from the settings window, as administrator)


def state() -> str:
    """running, stopped, starting, stopping, or not installed."""
    try:
        status = win32serviceutil.QueryServiceStatus(NAME)[1]
    except win32service.error:
        return "not installed"
    return {
        win32service.SERVICE_RUNNING: "running",
        win32service.SERVICE_STOPPED: "stopped",
        win32service.SERVICE_START_PENDING: "starting",
        win32service.SERVICE_STOP_PENDING: "stopping",
    }.get(status, "busy")


def install() -> None:
    """Copies the bridge (its folder: the exe and its libraries) to Program Files, where the service runs it from,
    then registers the service and (re)starts it. Run again, it updates: a newer download replaces what's installed."""
    if not getattr(sys, "frozen", False):
        raise RuntimeError("Only the built exe can be a service: python.exe can't host one")
    stop()
    source, target = Path(sys.executable).parent, installed_folder()
    if source.resolve() != target.resolve():
        shutil.copytree(source, target, dirs_exist_ok=True)
    binary = subprocess.list2cmdline([str(target / Path(sys.executable).name), "service"])
    manager = win32service.OpenSCManager(None, None, win32service.SC_MANAGER_ALL_ACCESS)
    try:
        try:
            handle = win32service.OpenService(manager, NAME, win32service.SERVICE_ALL_ACCESS)
            win32service.ChangeServiceConfig(
                handle,
                win32service.SERVICE_WIN32_OWN_PROCESS,
                win32service.SERVICE_AUTO_START,
                win32service.SERVICE_ERROR_NORMAL,
                binary,
                None,
                0,
                None,
                None,  # LocalSystem
                None,
                DISPLAY_NAME,
            )
        except win32service.error:
            handle = win32service.CreateService(
                manager,
                NAME,
                DISPLAY_NAME,
                win32service.SERVICE_ALL_ACCESS,
                win32service.SERVICE_WIN32_OWN_PROCESS,
                win32service.SERVICE_AUTO_START,
                win32service.SERVICE_ERROR_NORMAL,
                binary,
                None,
                0,
                None,
                None,  # LocalSystem
                None,
            )
        try:
            win32service.ChangeServiceConfig2(handle, win32service.SERVICE_CONFIG_DESCRIPTION, DESCRIPTION)
            restart = (win32service.SC_ACTION_RESTART, 5000)
            win32service.ChangeServiceConfig2(
                handle,
                win32service.SERVICE_CONFIG_FAILURE_ACTIONS,
                {"ResetPeriod": 24 * 3600, "RebootMsg": "", "Command": "", "Actions": [restart, restart, restart]},
            )
        finally:
            win32service.CloseServiceHandle(handle)
    finally:
        win32service.CloseServiceHandle(manager)
    secure(config.folder())
    win32serviceutil.StartService(NAME)
    log.info("Service installed and started: %s", binary)


def stop() -> None:
    if state() in ("not installed", "stopped"):
        return
    try:
        win32serviceutil.StopService(NAME)
    except win32service.error:
        pass
    for _ in range(60):  # it stops its bridge first
        if state() in ("stopped", "not installed"):
            return
        time.sleep(0.5)


def uninstall() -> None:
    stop()
    try:
        win32serviceutil.RemoveService(NAME)
    except win32service.error:
        pass
    log.info("Service removed")


def secure(folder: Path) -> None:
    """Only SYSTEM and administrators may read the folder: the settings hold the relay's token and the PIN. Set on
    the folder alone; Windows passes it down to what's inside (`/T` would put folder-only grants on files, leaving
    them readable by no one)."""
    folder.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["icacls", str(folder), "/inheritance:r", "/grant:r", "*S-1-5-18:(OI)(CI)F", "*S-1-5-32-544:(OI)(CI)F"],
        check=True,
        capture_output=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
