"""Everything that must survive a restart: each conversation's history, every task and its steps,
the control panel's chat, and edited prompts.

SQLite, one file. Histories are stored whole as Pydantic AI JSON, so a paused sub-agent resumes exactly.
"""

import sqlite3
import time
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import pydantic_ai
from pydantic_ai import ConversationTypeAdapter, ModelMessage, ModelMessagesTypeAdapter

from agent.messages import Conversation

DATABASE = Path("data/archangel.db")


class Status(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING = "waiting"  # on the owner: a question or an approval
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"  # Archangel stopped mid-task

    @property
    def open(self) -> bool:
        return self in (Status.QUEUED, Status.RUNNING, Status.WAITING)


@dataclass
class Task:
    id: int
    conversation: Conversation
    goal: str
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
    screen: bool
    """Whether a screenshot was taken after it: `Store.screen(id)`."""


@dataclass(frozen=True)
class Line:
    """One message in a chat transcript."""

    id: int
    time: float
    sender: str  # "owner" or "archangel"
    text: str
    image: str | None
    """The image's media type, if it has one: `Store.line_image(id)`."""


class Store:
    def __init__(self, path: Path | str = DATABASE):
        if isinstance(path, Path):
            path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path)
        self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS histories (channel TEXT, id TEXT, messages BLOB, PRIMARY KEY (channel, id));
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY, channel TEXT, conversation TEXT, goal TEXT, status TEXT, report TEXT, run BLOB
            );
            CREATE TABLE IF NOT EXISTS steps (id INTEGER PRIMARY KEY, task INTEGER, time REAL, text TEXT, screen BLOB);
            CREATE INDEX IF NOT EXISTS steps_by_task ON steps (task);
            CREATE TABLE IF NOT EXISTS chat (
                id INTEGER PRIMARY KEY, channel TEXT, conversation TEXT, time REAL, sender TEXT, text TEXT,
                image BLOB, media_type TEXT
            );
            CREATE TABLE IF NOT EXISTS prompts (name TEXT PRIMARY KEY, text TEXT);
            """
        )

    def history(self, conversation: Conversation) -> list[ModelMessage]:
        row = self._db.execute(
            "SELECT messages FROM histories WHERE channel = ? AND id = ?", (conversation.channel, conversation.id)
        ).fetchone()
        return ModelMessagesTypeAdapter.validate_json(row[0]) if row else []

    def save_history(self, conversation: Conversation, messages: list[ModelMessage]) -> None:
        with self._db:
            self._db.execute(
                "INSERT OR REPLACE INTO histories VALUES (?, ?, ?)",
                (conversation.channel, conversation.id, ModelMessagesTypeAdapter.dump_json(messages)),
            )

    def add_task(self, conversation: Conversation, goal: str) -> Task:
        with self._db:
            cursor = self._db.execute(
                "INSERT INTO tasks (channel, conversation, goal, status, report) VALUES (?, ?, ?, ?, '')",
                (conversation.channel, conversation.id, goal, Status.QUEUED),
            )
        return Task(cursor.lastrowid, conversation, goal)

    def save_task(self, task: Task) -> None:
        run = ConversationTypeAdapter.dump_json(task.run) if task.run else None
        with self._db:
            self._db.execute(
                "UPDATE tasks SET status = ?, report = ?, run = ? WHERE id = ?",
                (task.status, task.report, run, task.id),
            )

    def task(self, id: int) -> Task | None:
        row = self._db.execute(f"SELECT {_TASK_COLUMNS} FROM tasks WHERE id = ?", (id,)).fetchone()
        return _task(row) if row else None

    def tasks(self, conversation: Conversation) -> list[Task]:
        rows = self._db.execute(
            f"SELECT {_TASK_COLUMNS} FROM tasks WHERE channel = ? AND conversation = ? ORDER BY id",
            (conversation.channel, conversation.id),
        )
        return [_task(row) for row in rows]

    def recent_tasks(self, limit: int = 100) -> list[Task]:
        """Newest first, across every conversation."""
        rows = self._db.execute(f"SELECT {_TASK_COLUMNS} FROM tasks ORDER BY id DESC LIMIT ?", (limit,))
        return [_task(row) for row in rows]

    def add_step(self, task: int, text: str, screen: bytes | None = None) -> None:
        with self._db:
            self._db.execute(
                "INSERT INTO steps (task, time, text, screen) VALUES (?, ?, ?, ?)", (task, time.time(), text, screen)
            )

    def steps(self, task: int, after: int = 0) -> list[Step]:
        rows = self._db.execute(
            "SELECT id, time, text, screen IS NOT NULL FROM steps WHERE task = ? AND id > ? ORDER BY id", (task, after)
        )
        return [Step(id, at, text, bool(screen)) for id, at, text, screen in rows]

    def screen(self, step: int) -> bytes | None:
        row = self._db.execute("SELECT screen FROM steps WHERE id = ?", (step,)).fetchone()
        return row[0] if row else None

    def add_line(
        self,
        conversation: Conversation,
        sender: str,
        text: str,
        image: bytes | None = None,
        media_type: str | None = None,
    ) -> None:
        with self._db:
            self._db.execute(
                "INSERT INTO chat (channel, conversation, time, sender, text, image, media_type) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (conversation.channel, conversation.id, time.time(), sender, text, image, media_type),
            )

    def lines(self, conversation: Conversation, after: int = 0) -> list[Line]:
        rows = self._db.execute(
            "SELECT id, time, sender, text, media_type FROM chat WHERE channel = ? AND conversation = ? AND id > ? "
            "ORDER BY id",
            (conversation.channel, conversation.id, after),
        )
        return [Line(*row) for row in rows]

    def line_image(self, line: int) -> tuple[bytes, str] | None:
        row = self._db.execute("SELECT image, media_type FROM chat WHERE id = ?", (line,)).fetchone()
        return (row[0], row[1]) if row and row[0] else None

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

    def interrupt_running(self) -> None:
        """At startup: tasks that were mid-flight when Archangel stopped can't continue on their own."""
        with self._db:
            self._db.execute(
                "UPDATE tasks SET status = ?, report = 'Archangel restarted while this was running.' "
                "WHERE status IN (?, ?)",
                (Status.INTERRUPTED, Status.QUEUED, Status.RUNNING),
            )


_TASK_COLUMNS = "id, channel, conversation, goal, status, report, run"


def _task(row: tuple) -> Task:
    id, channel, conversation, goal, status, report, run = row
    return Task(
        id,
        Conversation(channel, conversation),
        goal,
        Status(status),
        report,
        ConversationTypeAdapter.validate_json(run) if run else None,
    )
