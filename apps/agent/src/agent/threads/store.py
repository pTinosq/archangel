"""Threads on disk: each one's title, summary and state, the orchestrator's history in it (Pydantic AI JSON, so a
thread resumes exactly), and a full-text index of what was said in each, for search. In the store's SQLite file,
in tables of their own."""

import re
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass

from pydantic_ai import ModelMessage, ModelMessagesTypeAdapter

from agent.history import recent
from agent.threads.text import lines

BEFORE = "Before threads"
"""The one conversation there was until threads, kept as the first."""
ADOPTED = 40
"""Messages of it kept: what the orchestrator saw of it then."""


@dataclass(frozen=True)
class Thread:
    id: int
    title: str
    summary: str
    """Written once it's finished (closed): what was asked, found, done, and left open. "" until then."""
    open: bool
    created: float
    updated: float
    """When something was last said in it."""
    size: int
    """How many tokens the orchestrator's last call in it took: its context, once it's full a continuation starts."""
    summarised: float
    """When the summary was written; 0 if never."""
    continues: int | None = None
    """The thread this one carries on from, once that one was full."""

    @property
    def stale(self) -> bool:
        """Said more in since its summary (or never summarised)."""
        return self.summarised < self.updated


@dataclass(frozen=True)
class Found:
    thread: Thread
    snippet: str
    """Where the words were found, matches in [brackets]."""


class ThreadStore:
    def __init__(self, db: sqlite3.Connection, clock: Callable[[], float] = time.time):
        self._db = db
        self._clock = clock
        with db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS threads (
                    id INTEGER PRIMARY KEY, title TEXT NOT NULL, summary TEXT NOT NULL DEFAULT '',
                    open INTEGER NOT NULL DEFAULT 1, created REAL NOT NULL, updated REAL NOT NULL,
                    size INTEGER NOT NULL DEFAULT 0, summarised REAL NOT NULL DEFAULT 0, continues INTEGER
                );
                CREATE INDEX IF NOT EXISTS threads_by_update ON threads (updated);
                CREATE TABLE IF NOT EXISTS thread_history (thread INTEGER PRIMARY KEY, messages BLOB NOT NULL);
                CREATE VIRTUAL TABLE IF NOT EXISTS thread_text USING fts5(title, summary, said);
                """
            )
        self._adopt_old_history()

    def new(self, title: str, continues: int | None = None) -> Thread:
        now = self._clock()
        with self._db:
            cursor = self._db.execute(
                "INSERT INTO threads (title, created, updated, continues) VALUES (?, ?, ?, ?)",
                (title, now, now, continues),
            )
        self._index(cursor.lastrowid)
        return self.get(cursor.lastrowid)

    def get(self, id: int) -> Thread | None:
        row = self._db.execute(f"SELECT {_COLUMNS} FROM threads WHERE id = ?", (id,)).fetchone()
        return _thread(row) if row else None

    def recent(self, limit: int = 10) -> list[Thread]:
        """The threads said most recently in, newest first."""
        rows = self._db.execute(f"SELECT {_COLUMNS} FROM threads ORDER BY updated DESC, id DESC LIMIT ?", (limit,))
        return [_thread(row) for row in rows]

    def latest(self) -> Thread | None:
        return next(iter(self.recent(1)), None)

    def history(self, id: int) -> list[ModelMessage]:
        row = self._db.execute("SELECT messages FROM thread_history WHERE thread = ?", (id,)).fetchone()
        return ModelMessagesTypeAdapter.validate_json(row[0]) if row else []

    def save(self, id: int, messages: list[ModelMessage], size: int) -> None:
        """The thread's whole history after a turn, and how big the orchestrator's context was."""
        with self._db:
            self._db.execute(
                "INSERT OR REPLACE INTO thread_history VALUES (?, ?)",
                (id, ModelMessagesTypeAdapter.dump_json(messages)),
            )
            self._db.execute("UPDATE threads SET updated = ?, size = ? WHERE id = ?", (self._clock(), size, id))
        self._index(id)

    def touch(self, id: int) -> None:
        """Being said in now: a summary being written meanwhile mustn't close it under the owner."""
        with self._db:
            self._db.execute("UPDATE threads SET updated = ? WHERE id = ?", (self._clock(), id))

    def rename(self, id: int, title: str) -> None:
        with self._db:
            self._db.execute("UPDATE threads SET title = ? WHERE id = ?", (title, id))
        self._index(id)

    def reopen(self, id: int) -> None:
        with self._db:
            self._db.execute("UPDATE threads SET open = 1, updated = ? WHERE id = ?", (self._clock(), id))

    def close(self, id: int) -> None:
        with self._db:
            self._db.execute("UPDATE threads SET open = 0 WHERE id = ?", (id,))

    def summarise(self, id: int, title: str, summary: str, seen: float) -> None:
        """Its summary of everything said up to `seen` (its `updated` when read); closes it too, unless something was
        said in it since."""
        with self._db:
            self._db.execute(
                "UPDATE threads SET title = ?, summary = ?, summarised = ?, "
                "open = CASE WHEN updated <= ? THEN 0 ELSE open END WHERE id = ?",
                (title, summary, seen, seen, id),
            )
        self._index(id)

    def successor(self, id: int) -> Thread | None:
        """The thread carrying this one on, once it was full."""
        row = self._db.execute(
            f"SELECT {_COLUMNS} FROM threads WHERE continues = ? ORDER BY id DESC LIMIT 1", (id,)
        ).fetchone()
        return _thread(row) if row else None

    def to_summarise(self, quiet_since: float) -> list[Thread]:
        """Open threads nothing's been said in since `quiet_since`, and closed ones said more in since their summary.
        Oldest first."""
        rows = self._db.execute(
            f"SELECT {_COLUMNS} FROM threads WHERE (open = 1 AND updated < ?) OR (open = 0 AND summarised < updated) "
            "ORDER BY updated",
            (quiet_since,),
        )
        return [_thread(row) for row in rows]

    def search(self, words: str, limit: int = 8) -> list[Found]:
        """Threads mentioning any of `words` (or words starting with them), best match first."""
        terms = re.findall(r"\w+", words)
        if not terms:
            return []
        query = " OR ".join(f'"{term}"*' for term in terms)
        rows = self._db.execute(
            "SELECT rowid, snippet(thread_text, -1, '[', ']', '…', 12) FROM thread_text WHERE thread_text MATCH ? "
            "ORDER BY rank LIMIT ?",
            (query, limit),
        )
        return [Found(thread, snippet) for id, snippet in rows if (thread := self.get(id))]

    def _index(self, id: int) -> None:
        thread = self.get(id)
        said = "\n".join(lines(self.history(id)))
        with self._db:
            self._db.execute(
                "INSERT OR REPLACE INTO thread_text (rowid, title, summary, said) VALUES (?, ?, ?, ?)",
                (id, thread.title, thread.summary, said),
            )

    def _adopt_old_history(self) -> None:
        """Until threads, the conversation was one history: it becomes the first thread, open, so the deploy that
        brings threads loses nothing the orchestrator saw (its last messages), with everything said and every task
        until then filed under it. Summarised once it's been quiet, like any other. All at once or not at all: a
        crash halfway would adopt it twice."""
        if not self._db.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'history'").fetchone():
            return
        row = self._db.execute("SELECT messages FROM history").fetchone()
        id = None
        with self._db:
            if row:
                messages = recent(ModelMessagesTypeAdapter.validate_json(row[0]), ADOPTED)
                now = self._clock()
                id = self._db.execute(
                    "INSERT INTO threads (title, created, updated) VALUES (?, ?, ?)", (BEFORE, now, now)
                ).lastrowid
                self._db.execute(
                    "INSERT INTO thread_history VALUES (?, ?)", (id, ModelMessagesTypeAdapter.dump_json(messages))
                )
                for table in ("transcript", "tasks"):  # the store's, made (with their thread column) before this
                    if any(column == "thread" for _, column, *_ in self._db.execute(f"PRAGMA table_info({table})")):
                        self._db.execute(f"UPDATE {table} SET thread = ? WHERE thread IS NULL", (id,))
            self._db.execute("DROP TABLE history")
        if id:
            self._index(id)


_COLUMNS = "id, title, summary, open, created, updated, size, summarised, continues"


def _thread(row: tuple) -> Thread:
    id, title, summary, open, created, updated, size, summarised, continues = row
    return Thread(id, title, summary, bool(open), created, updated, size, summarised, continues)
