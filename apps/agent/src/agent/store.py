"""Everything that must survive a restart: the transcript across channels, every task and its steps, edited prompts,
connectors with their sign-ins, quick tools' settings, where the owner last was, and each device's latest hello. SQLite, one file in data/,
which the conversation's threads (`agent.threads`) and the context twin keep their own tables in.

A paused task is stored with its run as Pydantic AI JSON, so it resumes exactly.
Research reports' web pages are files beside it. Images (screenshots, pictures in chat) too, deleted after IMAGES_KEPT:
they're most of the bytes and only matter while a task is fresh.
"""

import json
import mimetypes
import re
import sqlite3
import time
import uuid
from dataclasses import dataclass, replace
from datetime import timedelta
from enum import StrEnum
from pathlib import Path
from typing import Literal

import pydantic_ai
from pydantic_ai import ConversationTypeAdapter

from agent.history import forget_old_screenshots
from agent.messages import Address, Image, Message

DATA = Path("data")
IMAGES_KEPT = timedelta(days=3)
_IMAGE_NAME = re.compile(r"[0-9a-f]{32}\.[a-z]+")

Kind = Literal["phone", "pc", "research", "deep research", "connectors", "code"]


class Status(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    NEEDS_APPROVAL = "needs approval"
    NEEDS_ANSWER = "needs answer"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"  # Archangel stopped mid-task

    @property
    def waiting(self) -> bool:
        """On the owner."""
        return self in (Status.NEEDS_APPROVAL, Status.NEEDS_ANSWER)

    @property
    def open(self) -> bool:
        return self in (Status.QUEUED, Status.RUNNING) or self.waiting


@dataclass
class Task:
    id: int
    goal: str
    report_to: Address
    """Where its updates go: where it was asked for, unless the owner wanted the results elsewhere."""
    status: Status = Status.QUEUED
    report: str = ""
    run: pydantic_ai.Conversation | None = None
    """The sub-agent's history, kept so a paused task resumes where it stopped."""
    kind: Kind = "phone"
    """Phone and PC tasks take turns on their device; the rest run beside them: research (deep or not) on the web,
    connectors in the owner's online services, and code on Archangel itself (a PR, via GitHub)."""
    public: bool = False
    """Its report opens for anyone with the link; otherwise only for the owner, signed in."""
    thread: int | None = None
    """The conversation thread that started it, where its updates go; None for tasks from before threads."""
    github_run: int | None = None
    """A code task's GitHub Actions run, once started: it carries on through Archangel's restarts, so the task does."""


@dataclass(frozen=True)
class Location:
    """Where the owner was when they last shared it."""

    lat: float
    lon: float
    accuracy: float
    """In metres."""
    address: str
    """The nearest street address, or "" if it couldn't be found."""
    time: float

    def __str__(self) -> str:
        at = f"{self.lat:.5f}, {self.lon:.5f}, within {self.accuracy:.0f} m"
        return f"{self.address} ({at})" if self.address else at


@dataclass(frozen=True)
class Step:
    """One thing a task did or saw, for watching it work."""

    id: int
    time: float
    text: str
    screen: str | None
    """The screenshot taken after it, if any: `Store.image(screen)`, until it expires."""


@dataclass(frozen=True)
class Line:
    """One message in the transcript, from either side, on any channel."""

    id: int
    time: float
    channel: str
    sender: str  # "owner" or "archangel"
    text: str
    images: list[str]
    thread: int | None
    """The conversation thread it's in, once known: an owner's message is filed when the orchestrator reads it."""


class Store:
    def __init__(self, directory: Path = DATA):
        self._images = directory / "images"
        self._images.mkdir(parents=True, exist_ok=True)
        self._pages = directory / "pages"
        self._pages.mkdir(exist_ok=True)
        self._db = sqlite3.connect(directory / "archangel.db")
        self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS transcript (
                id INTEGER PRIMARY KEY, time REAL, channel TEXT, address TEXT, sender TEXT, text TEXT, images TEXT
            );
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY, goal TEXT, channel TEXT, address TEXT, status TEXT, report TEXT, run BLOB
            );
            CREATE TABLE IF NOT EXISTS steps (id INTEGER PRIMARY KEY, task INTEGER, time REAL, text TEXT, screen TEXT);
            CREATE INDEX IF NOT EXISTS steps_by_task ON steps (task);
            CREATE TABLE IF NOT EXISTS prompts (name TEXT PRIMARY KEY, text TEXT);
            CREATE TABLE IF NOT EXISTS connectors (name TEXT PRIMARY KEY, data TEXT);
            CREATE TABLE IF NOT EXISTS tools (name TEXT PRIMARY KEY, data TEXT);
            CREATE TABLE IF NOT EXISTS location (
                id INTEGER PRIMARY KEY CHECK (id = 1), lat REAL, lon REAL, accuracy REAL, address TEXT, time REAL
            );
            CREATE TABLE IF NOT EXISTS devices (name TEXT PRIMARY KEY, platform TEXT, hello TEXT, time REAL);
            """
        )
        columns = {column for _, column, *_ in self._db.execute("PRAGMA table_info(tasks)")}
        lines = {column for _, column, *_ in self._db.execute("PRAGMA table_info(transcript)")}
        with self._db:
            if "kind" not in columns:  # added in M9
                self._db.execute("ALTER TABLE tasks ADD COLUMN kind TEXT NOT NULL DEFAULT 'phone'")
            if "public" not in columns:  # added with shared reports
                self._db.execute("ALTER TABLE tasks ADD COLUMN public INTEGER NOT NULL DEFAULT 0")
            if "thread" not in columns:  # added with threads (M18)
                self._db.execute("ALTER TABLE tasks ADD COLUMN thread INTEGER")
            if "github_run" not in columns:  # added so code tasks survive a deploy
                self._db.execute("ALTER TABLE tasks ADD COLUMN github_run INTEGER")
            if "thread" not in lines:
                self._db.execute("ALTER TABLE transcript ADD COLUMN thread INTEGER")
        self.forget_old_images()

    @property
    def db(self) -> sqlite3.Connection:
        """For a module that keeps its own tables in the one file (the twin's graph)."""
        return self._db

    # The transcript: what was said, both ways, on every channel

    def add_line(self, message: Message, sender: str) -> int:
        images = json.dumps([self.save_image(image) for image in message.images])
        with self._db:
            cursor = self._db.execute(
                "INSERT INTO transcript (time, channel, address, sender, text, images, thread) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    time.time(),
                    message.address.channel,
                    message.address.id,
                    sender,
                    message.text,
                    images,
                    message.thread,
                ),
            )
        return cursor.lastrowid

    def file_line(self, id: int, thread: int) -> None:
        """Puts a line in its thread, once the orchestrator knows which (an owner's message, after it's routed)."""
        with self._db:
            self._db.execute("UPDATE transcript SET thread = ? WHERE id = ?", (thread, id))

    def lines(self, after: int = 0, thread: int | None = None) -> list[Line]:
        """Every line after `after`, or only those in `thread`."""
        where, args = "id > ?", [after]
        if thread is not None:
            where, args = where + " AND thread = ?", [*args, thread]
        rows = self._db.execute(
            f"SELECT id, time, channel, sender, text, images, thread FROM transcript WHERE {where} ORDER BY id", args
        )
        return [
            Line(id, at, channel, sender, text, json.loads(images), in_thread)
            for id, at, channel, sender, text, images, in_thread in rows
        ]

    # Tasks

    def add_task(self, goal: str, report_to: Address, kind: Kind = "phone", thread: int | None = None) -> Task:
        self.forget_old_images()  # tasks are where images pile up; check as each starts
        with self._db:
            cursor = self._db.execute(
                "INSERT INTO tasks (goal, channel, address, status, report, kind, thread) VALUES (?, ?, ?, ?, '', ?, ?)",
                (goal, report_to.channel, report_to.id, Status.QUEUED, kind, thread),
            )
        return Task(cursor.lastrowid, goal, report_to, kind=kind, thread=thread)

    def save_task(self, task: Task) -> None:
        # A paused run only needs the screen it stopped on; the rest would be dead weight in every save.
        run = replace(task.run, messages=forget_old_screenshots(task.run.messages, keep=1)) if task.run else None
        with self._db:
            self._db.execute(
                "UPDATE tasks SET status = ?, report = ?, run = ? WHERE id = ?",
                (task.status, task.report, run and ConversationTypeAdapter.dump_json(run), task.id),
            )

    def set_github_run(self, id: int, run: int) -> None:
        with self._db:
            self._db.execute("UPDATE tasks SET github_run = ? WHERE id = ?", (run, id))

    def report_task_to(self, id: int, to: Address) -> None:
        with self._db:
            self._db.execute("UPDATE tasks SET channel = ?, address = ? WHERE id = ?", (to.channel, to.id, id))

    def task(self, id: int) -> Task | None:
        row = self._db.execute(f"SELECT {_TASK_COLUMNS} FROM tasks WHERE id = ?", (id,)).fetchone()
        return _task(row) if row else None

    def tasks(self, limit: int = 100, thread: int | None = None) -> list[Task]:
        """Newest first; only `thread`'s, if given."""
        where, args = ("WHERE thread = ?", [thread]) if thread is not None else ("", [])
        rows = self._db.execute(f"SELECT {_TASK_COLUMNS} FROM tasks {where} ORDER BY id DESC LIMIT ?", [*args, limit])
        return [_task(row) for row in rows]

    def interrupt_running(self) -> list[Task]:
        """At startup: tasks that were mid-flight when Archangel stopped can't continue on their own. Returns them, so
        whoever's waiting on one hears."""
        open = (Status.QUEUED, Status.RUNNING)
        ids = [id for (id,) in self._db.execute("SELECT id FROM tasks WHERE status IN (?, ?)", open)]
        with self._db:
            self._db.execute(
                "UPDATE tasks SET status = ?, report = 'Archangel restarted while this was running.' "
                "WHERE status IN (?, ?)",
                (Status.INTERRUPTED, *open),
            )
        return [self.task(id) for id in ids]

    def add_step(self, task: int, text: str, screen: Image | None = None) -> None:
        name = screen and self.save_image(screen)
        with self._db:
            self._db.execute(
                "INSERT INTO steps (task, time, text, screen) VALUES (?, ?, ?, ?)", (task, time.time(), text, name)
            )

    def steps(self, task: int, after: int = 0) -> list[Step]:
        rows = self._db.execute(
            "SELECT id, time, text, screen FROM steps WHERE task = ? AND id > ? ORDER BY id", (task, after)
        )
        return [Step(*row) for row in rows]

    # Pages: research reports as web pages, kept (they're small)

    def save_page(self, task: int, html: str) -> None:
        self._page(task).write_text(html)

    def make_public(self, task: int, public: bool) -> None:
        with self._db:
            self._db.execute("UPDATE tasks SET public = ? WHERE id = ?", (public, task))

    def has_page(self, task: int) -> bool:
        return self._page(task).exists()

    def page(self, task: int) -> str | None:
        path = self._page(task)
        return path.read_text() if path.exists() else None

    def _page(self, task: int) -> Path:
        return self._pages / f"{task}.html"

    # Images

    def save_image(self, image: Image) -> str:
        name = uuid.uuid4().hex + (mimetypes.guess_extension(image.media_type) or ".bin")
        (self._images / name).write_bytes(image.data)
        return name

    def image(self, name: str) -> Image | None:
        """None if it never existed or has expired. Safe with untrusted names."""
        path = self._images / name
        if not _IMAGE_NAME.fullmatch(name) or not path.exists():
            return None
        return Image(path.read_bytes(), mimetypes.guess_type(name)[0] or "application/octet-stream")

    def forget_old_images(self) -> None:
        cutoff = time.time() - IMAGES_KEPT.total_seconds()
        for path in self._images.iterdir():
            if path.stat().st_mtime < cutoff:
                path.unlink()

    # Prompts

    def prompts(self) -> dict[str, str]:
        """The prompts edited away from their defaults."""
        return dict(self._db.execute("SELECT name, text FROM prompts"))

    def save_prompt(self, name: str, text: str | None) -> None:
        """Save an edited prompt; None goes back to the default."""
        with self._db:
            if text is None:
                self._db.execute("DELETE FROM prompts WHERE name = ?", (name,))
            else:
                self._db.execute("INSERT OR REPLACE INTO prompts VALUES (?, ?)", (name, text))

    # Connectors

    def connectors(self) -> dict[str, dict]:
        """Each connector's settings and sign-in, by name (shaped by `Connectors`)."""
        return {name: json.loads(data) for name, data in self._db.execute("SELECT name, data FROM connectors")}

    def save_connector(self, name: str, data: dict | None) -> None:
        """None removes it."""
        with self._db:
            if data is None:
                self._db.execute("DELETE FROM connectors WHERE name = ?", (name,))
            else:
                self._db.execute("INSERT OR REPLACE INTO connectors VALUES (?, ?)", (name, json.dumps(data)))

    # Quick tools

    def tools(self) -> dict[str, dict]:
        """What the owner has set for each quick tool (on or off, its settings), by name (shaped by `Tools`)."""
        return {name: json.loads(data) for name, data in self._db.execute("SELECT name, data FROM tools")}

    def save_tool(self, name: str, data: dict) -> None:
        with self._db:
            self._db.execute("INSERT OR REPLACE INTO tools VALUES (?, ?)", (name, json.dumps(data)))

    # Where the owner is

    def location(self) -> Location | None:
        row = self._db.execute("SELECT lat, lon, accuracy, address, time FROM location").fetchone()
        return Location(*row) if row else None

    def save_location(self, location: Location) -> None:
        with self._db:
            self._db.execute(
                "INSERT OR REPLACE INTO location VALUES (1, ?, ?, ?, ?, ?)",
                (location.lat, location.lon, location.accuracy, location.address, location.time),
            )

    # Devices

    def save_device(self, hello: dict) -> None:
        """A device's latest hello, for while it's offline: a sleeping PC is woken by the card it named."""
        with self._db:
            self._db.execute(
                "INSERT OR REPLACE INTO devices VALUES (?, ?, ?, ?)",
                (hello["device"], hello.get("platform", "android"), json.dumps(hello), time.time()),
            )

    def last_device(self, platform: str) -> dict | None:
        """The hello of the device of `platform` seen most recently."""
        row = self._db.execute(
            "SELECT hello FROM devices WHERE platform = ? ORDER BY time DESC LIMIT 1", (platform,)
        ).fetchone()
        return json.loads(row[0]) if row else None


_TASK_COLUMNS = "id, goal, channel, address, status, report, run, kind, public, thread, github_run"


def _task(row: tuple) -> Task:
    id, goal, channel, address, status, report, run, kind, public, thread, github_run = row
    return Task(
        id,
        goal,
        Address(channel, address),
        Status(status),
        report,
        ConversationTypeAdapter.validate_json(run) if run else None,
        kind,
        bool(public),
        thread,
        github_run,
    )
