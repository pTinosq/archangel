"""The context twin's graph: nodes with markdown pages, linked with previews. See docs/twin.md.

The only code on the twin's tables. Models judge what goes where; this keeps what they can't be trusted to: one node
per name or alias, every edit recorded with the owner's words, and previews that went stale when their target
changed found, not forgotten.
"""

import sqlite3
import time
from dataclasses import dataclass

ROOT = "You"


@dataclass(frozen=True)
class Node:
    id: int
    name: str
    page: str
    """Markdown. Empty: a router, only a name and links."""
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class Link:
    source: str
    target: str
    relation: str
    preview: str
    """A few words on what's at the target, written from the source's side."""


@dataclass(frozen=True)
class Change:
    time: float
    node: str
    what: str
    before: str
    after: str
    said: str
    """The owner's words it came from, quoted."""
    origin: str
    """Where they said it: "chat", "task 12", "panel"."""


class Graph:
    def __init__(self, db: sqlite3.Connection, clock=time.time):
        self._db, self._clock = db, clock
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS twin_nodes (id INTEGER PRIMARY KEY, name TEXT, page TEXT, updated REAL);
            CREATE TABLE IF NOT EXISTS twin_names (name TEXT PRIMARY KEY COLLATE NOCASE, node INTEGER);
            CREATE TABLE IF NOT EXISTS twin_links (
                source INTEGER, target INTEGER, relation TEXT, preview TEXT, updated REAL, PRIMARY KEY (source, target)
            );
            CREATE INDEX IF NOT EXISTS twin_links_to ON twin_links (target);
            CREATE TABLE IF NOT EXISTS twin_changes (
                id INTEGER PRIMARY KEY, time REAL, node TEXT, what TEXT, before TEXT, after TEXT, said TEXT, origin TEXT
            );
            """
        )
        if not self.find(ROOT):
            self.add(ROOT, origin="start")

    # Reading

    def find(self, name: str) -> Node | None:
        """By name or alias, any case."""
        row = self._db.execute(
            "SELECT n.id, n.name, n.page FROM twin_names m JOIN twin_nodes n ON n.id = m.node WHERE m.name = ?",
            (name.strip(),),
        ).fetchone()
        return self._node(row) if row else None

    def open(self, name: str) -> str:
        """A node as a model reads it: its page, then where it links, each with its preview."""
        node = self._get(name)
        lines = [f"# {node.name}" + (f" (also: {', '.join(node.aliases)})" if node.aliases else "")]
        lines.append(node.page.strip() or "(a router: no page, only links)")
        links = self.links_from(node.name)
        if links:
            lines.append("")
            lines += [f"→ {link.target} ({link.relation}): {link.preview}" for link in links]
        linked_from = [link.source for link in self.links_to(node.name)]
        if linked_from:
            lines.append(f"\nLinked from: {', '.join(linked_from)}")
        return "\n".join(lines)

    def nodes(self) -> list[Node]:
        return [self._node(row) for row in self._db.execute("SELECT id, name, page FROM twin_nodes ORDER BY id")]

    def links(self) -> list[Link]:
        return self._links("")

    def links_from(self, name: str) -> list[Link]:
        return self._links("WHERE l.source = ?", self._get(name).id)

    def links_to(self, name: str) -> list[Link]:
        return self._links("WHERE l.target = ?", self._get(name).id)

    def stale(self) -> list[Link]:
        """Links whose target's page changed after their preview was written."""
        return self._links("WHERE l.updated < t.updated")

    def history(self, name: str | None = None) -> list[Change]:
        """Newest first; one node's, or everything's."""
        where, args = ("WHERE node = ?", (self._get(name).name,)) if name else ("", ())
        rows = self._db.execute(
            f"SELECT time, node, what, before, after, said, origin FROM twin_changes {where} ORDER BY id DESC", args
        )
        return [Change(*row) for row in rows]

    # Editing. Each takes the owner's words it comes from, and where they were said.

    def add(self, name: str, page: str = "", *, said: str = "", origin: str = "panel") -> Node:
        name = name.strip()
        if not name:
            raise ValueError("A node needs a name.")
        self._free(name)
        with self._db:
            id = self._db.execute(
                "INSERT INTO twin_nodes (name, page, updated) VALUES (?, ?, ?)", (name, page, self._clock())
            ).lastrowid
            self._db.execute("INSERT INTO twin_names VALUES (?, ?)", (name, id))
            self._record(name, "added", "", page, said, origin)
        return self._get(name)

    def write(self, name: str, page: str, *, said: str = "", origin: str = "panel") -> Node:
        """Replaces the page. Previews of links to it are stale until rewritten."""
        node = self._get(name)
        with self._db:
            self._db.execute("UPDATE twin_nodes SET page = ?, updated = ? WHERE id = ?", (page, self._clock(), node.id))
            self._record(node.name, "page", node.page, page, said, origin)
        return self._get(name)

    def alias(self, name: str, alias: str, *, said: str = "", origin: str = "panel") -> None:
        node, alias = self._get(name), alias.strip()
        self._free(alias)
        with self._db:
            self._db.execute("INSERT INTO twin_names VALUES (?, ?)", (alias, node.id))
            self._record(node.name, "alias", "", alias, said, origin)

    def rename(self, name: str, new: str, *, said: str = "", origin: str = "panel") -> None:
        node, new = self._get(name), new.strip()
        if node.name == ROOT:
            raise ValueError(f"{ROOT} stays {ROOT}.")
        if new.casefold() != node.name.casefold():
            self._free(new)
        with self._db:
            self._db.execute("DELETE FROM twin_names WHERE name = ?", (node.name,))
            self._db.execute("INSERT INTO twin_names VALUES (?, ?)", (new, node.id))
            self._db.execute("UPDATE twin_nodes SET name = ? WHERE id = ?", (new, node.id))
            self._db.execute("UPDATE twin_changes SET node = ? WHERE node = ?", (new, node.name))
            self._record(new, "renamed", node.name, new, said, origin)

    def link(
        self, source: str, target: str, relation: str, preview: str, *, said: str = "", origin: str = "panel"
    ) -> None:
        """Adds the link, or rewrites its relation and preview."""
        a, b = self._get(source), self._get(target)
        if a.id == b.id:
            raise ValueError("A node can't link to itself.")
        with self._db:
            self._db.execute(
                "INSERT OR REPLACE INTO twin_links VALUES (?, ?, ?, ?, ?)",
                (a.id, b.id, relation.strip(), preview.strip(), self._clock()),
            )
            self._record(a.name, f"link → {b.name}", "", f"{relation}: {preview}", said, origin)

    def unlink(self, source: str, target: str, *, said: str = "", origin: str = "panel") -> None:
        a, b = self._get(source), self._get(target)
        with self._db:
            self._db.execute("DELETE FROM twin_links WHERE source = ? AND target = ?", (a.id, b.id))
            self._record(a.name, f"unlink → {b.name}", "", "", said, origin)

    def merge(self, name: str, into: str, *, said: str = "", origin: str = "panel") -> None:
        """One thing under two names: `name`'s names and links move to `into`, its page is dropped (write the
        combined page to `into` first)."""
        gone, kept = self._get(name), self._get(into)
        if gone.id == kept.id or gone.name == ROOT:
            raise ValueError(f"Can't merge {gone.name} into {kept.name}.")
        with self._db:
            for end, other in (("source", "target"), ("target", "source")):  # skip links that would duplicate or loop
                self._db.execute(
                    f"""UPDATE OR IGNORE twin_links SET {end} = ? WHERE {end} = ? AND {other} != ?""",
                    (kept.id, gone.id, kept.id),
                )
            self._db.execute("DELETE FROM twin_links WHERE source = ? OR target = ?", (gone.id, gone.id))
            self._db.execute("UPDATE twin_names SET node = ? WHERE node = ?", (kept.id, gone.id))
            self._db.execute("DELETE FROM twin_nodes WHERE id = ?", (gone.id,))
            self._record(kept.name, f"merged {gone.name}", gone.page, "", said, origin)

    def delete(self, name: str, *, said: str = "", origin: str = "panel") -> None:
        node = self._get(name)
        if node.name == ROOT:
            raise ValueError(f"{ROOT} can't be deleted.")
        with self._db:
            self._db.execute("DELETE FROM twin_links WHERE source = ? OR target = ?", (node.id, node.id))
            self._db.execute("DELETE FROM twin_names WHERE node = ?", (node.id,))
            self._db.execute("DELETE FROM twin_nodes WHERE id = ?", (node.id,))
            self._record(node.name, "deleted", node.page, "", said, origin)

    # Inside

    def _get(self, name: str) -> Node:
        node = self.find(name)
        if not node:
            raise ValueError(f"No node called {name!r}.")
        return node

    def _free(self, name: str) -> None:
        taken = self.find(name)
        if taken:
            raise ValueError(f"{name!r} is already {taken.name}: link to it, or merge.")

    def _node(self, row: tuple) -> Node:
        id, name, page = row
        aliases = self._db.execute("SELECT name FROM twin_names WHERE node = ? AND name != ? ORDER BY name", (id, name))
        return Node(id, name, page, tuple(alias for (alias,) in aliases))

    def _links(self, where: str, *args) -> list[Link]:
        rows = self._db.execute(
            f"""SELECT s.name, t.name, l.relation, l.preview FROM twin_links l
                JOIN twin_nodes s ON s.id = l.source JOIN twin_nodes t ON t.id = l.target {where}
                ORDER BY s.name, t.name""",
            args,
        )
        return [Link(*row) for row in rows]

    def _record(self, node: str, what: str, before: str, after: str, said: str, origin: str) -> None:
        self._db.execute(
            "INSERT INTO twin_changes (time, node, what, before, after, said, origin) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (self._clock(), node, what, before, after, said, origin),
        )
