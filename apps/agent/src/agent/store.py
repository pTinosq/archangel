"""Everything that must survive a restart: the one conversation with the owner, the transcript across channels,
every task and its steps, and edited prompts. SQLite, one file in data/.

The history is stored whole as Pydantic AI JSON, and a paused task with its run, so both resume exactly.
Images (screenshots, pictures in chat) are files beside it, deleted after IMAGES_KEPT: they're most of the bytes
and only matter while a task is fresh.
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

import pydantic_ai
from pydantic_ai import ConversationTypeAdapter, ModelMessage, ModelMessagesTypeAdapter

from agent.history import forget_old_screenshots
from agent.messages import Address, Image, Message

DATA = Path("data")
IMAGES_KEPT = timedelta(days=3)
_IMAGE_NAME = re.compile(r"[0-9a-f]{32}\.[a-z]+")


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


class Store:
    def __init__(self, directory: Path = DATA):
        self._images = directory / "images"
        self._images.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(directory / "archangel.db")
        self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS history (id INTEGER PRIMARY KEY CHECK (id = 1), messages BLOB);
            CREATE TABLE IF NOT EXISTS transcript (
                id INTEGER PRIMARY KEY, time REAL, channel TEXT, address TEXT, sender TEXT, text TEXT, images TEXT
            );
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY, goal TEXT, channel TEXT, address TEXT, status TEXT, report TEXT, run BLOB
            );
            CREATE TABLE IF NOT EXISTS steps (id INTEGER PRIMARY KEY, task INTEGER, time REAL, text TEXT, screen TEXT);
            CREATE INDEX IF NOT EXISTS steps_by_task ON steps (task);
            CREATE TABLE IF NOT EXISTS prompts (name TEXT PRIMARY KEY, text TEXT);
            """
        )
        self.forget_old_images()

    # The conversation

    def history(self) -> list[ModelMessage]:
        row = self._db.execute("SELECT messages FROM history").fetchone()
        return ModelMessagesTypeAdapter.validate_json(row[0]) if row else []

    def save_history(self, messages: list[ModelMessage]) -> None:
        with self._db:
            self._db.execute(
                "INSERT OR REPLACE INTO history VALUES (1, ?)", (ModelMessagesTypeAdapter.dump_json(messages),)
            )

    def add_line(self, message: Message, sender: str) -> None:
        images = json.dumps([self.save_image(image) for image in message.images])
        with self._db:
            self._db.execute(
                "INSERT INTO transcript (time, channel, address, sender, text, images) VALUES (?, ?, ?, ?, ?, ?)",
                (time.time(), message.address.channel, message.address.id, sender, message.text, images),
            )

    def lines(self, after: int = 0) -> list[Line]:
        rows = self._db.execute(
            "SELECT id, time, channel, sender, text, images FROM transcript WHERE id > ? ORDER BY id", (after,)
        )
        return [Line(*row[:-1], json.loads(row[-1])) for row in rows]

    # Tasks

    def add_task(self, goal: str, report_to: Address) -> Task:
        self.forget_old_images()  # tasks are where images pile up; check as each starts
        with self._db:
            cursor = self._db.execute(
                "INSERT INTO tasks (goal, channel, address, status, report) VALUES (?, ?, ?, ?, '')",
                (goal, report_to.channel, report_to.id, Status.QUEUED),
            )
        return Task(cursor.lastrowid, goal, report_to)

    def save_task(self, task: Task) -> None:
        # A paused run only needs the screen it stopped on; the rest would be dead weight in every save.
        run = replace(task.run, messages=forget_old_screenshots(task.run.messages, keep=1)) if task.run else None
        with self._db:
            self._db.execute(
                "UPDATE tasks SET status = ?, report = ?, run = ? WHERE id = ?",
                (task.status, task.report, run and ConversationTypeAdapter.dump_json(run), task.id),
            )

    def report_task_to(self, id: int, to: Address) -> None:
        with self._db:
            self._db.execute("UPDATE tasks SET channel = ?, address = ? WHERE id = ?", (to.channel, to.id, id))

    def task(self, id: int) -> Task | None:
        row = self._db.execute(f"SELECT {_TASK_COLUMNS} FROM tasks WHERE id = ?", (id,)).fetchone()
        return _task(row) if row else None

    def tasks(self, limit: int = 100) -> list[Task]:
        """Newest first."""
        rows = self._db.execute(f"SELECT {_TASK_COLUMNS} FROM tasks ORDER BY id DESC LIMIT ?", (limit,))
        return [_task(row) for row in rows]

    def interrupt_running(self) -> None:
        """At startup: tasks that were mid-flight when Archangel stopped can't continue on their own."""
        with self._db:
            self._db.execute(
                "UPDATE tasks SET status = ?, report = 'Archangel restarted while this was running.' "
                "WHERE status IN (?, ?)",
                (Status.INTERRUPTED, Status.QUEUED, Status.RUNNING),
            )

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


_TASK_COLUMNS = "id, goal, channel, address, status, report, run"


def _task(row: tuple) -> Task:
    id, goal, channel, address, status, report, run = row
    return Task(
        id,
        goal,
        Address(channel, address),
        Status(status),
        report,
        ConversationTypeAdapter.validate_json(run) if run else None,
    )
