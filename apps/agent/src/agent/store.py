"""Everything that must survive a restart: each conversation's history and every task.

SQLite, one file. Histories are stored whole as Pydantic AI JSON, so a paused sub-agent resumes exactly.
"""

import sqlite3
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
