"""archangel-bridge.exe [settings | run [--config FILE] | session | service | install | uninstall]

No argument: the settings window (as administrator: it installs the service). `run`: in the foreground, as you, for
trying it out (no lock screen). `session` is the bridge as the service starts it; `service` is Windows starting the
service.
"""

import argparse
import asyncio
import contextlib
import faulthandler
import logging
import sys
from pathlib import Path

from bridge import app, config


def main() -> None:
    parser = argparse.ArgumentParser(prog="archangel-bridge")
    parser.add_argument("mode", nargs="?", default="settings")
    parser.add_argument("--config", type=Path, help="settings file for `run` (default: the service's)")
    args = parser.parse_args()

    from bridge import win32  # Windows only, so not at the top: the tests import this package anywhere

    win32.dpi_aware()  # before anything reads the screen
    folder = config.folder()
    if (folder / "trace").exists():  # for a hang: every thread's stack, every 15 s
        trace = open(folder / "logs" / f"trace-{args.mode}.log", "a")  # noqa: SIM115 (open for the process's life)
        faulthandler.dump_traceback_later(15, repeat=True, file=trace)
    # Each mode logs to its own file: the service and its bridge run with no screen to show anything on.
    app.start_logging(None if args.mode == "run" else folder / "logs" / f"{LOGS.get(args.mode, args.mode)}.log")
    try:
        match args.mode:
            case "settings":
                from bridge import settings

                settings.open_window()
            case "run":
                _bridge(win32.settings_store(args.config or folder / "bridge.json"), None)
            case "session":
                _bridge(win32.settings_store(folder / "bridge.json"), folder / "status.json")
            case "service":
                from bridge import service

                service.host()
            case "install" | "uninstall":
                from bridge import service

                service.install() if args.mode == "install" else service.uninstall()
            case _:
                parser.error(f"unknown mode {args.mode!r}")
    except Exception:
        # Logged, never left to escape: the windowed exe would show it in a dialog, and under the service no one can
        # ever close that dialog, so the process would hang there.
        logging.getLogger("bridge").exception("%s failed", args.mode)
        sys.exit(1)


LOGS = {"session": "bridge", "install": "service", "uninstall": "service"}


def _bridge(store: config.Store, status: Path | None) -> None:
    from bridge.windows import WindowsDevice

    settings = store.load()
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(app.serve(settings, WindowsDevice(settings.secret), status))


if __name__ == "__main__":
    sys.exit(main())
