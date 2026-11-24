"""The roadmap as data, read from docs/roadmap.md, the one place it's written.

The panel's Roadmap page is a view of that document, parsed on every request: nothing is copied from it. The
document's shape: a table of milestones (`| M18 ✅ | Name | You can… | Proves |`; "Later" rows are ideas with no
number), then `## M9 requirements…` or `## M8 (done)` sections whose bullets are that milestone's items. Whatever
doesn't fit is skipped and said so in `Roadmap.notes`; parsing never raises.
"""

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

# Beside the agent's source in a checkout; the Dockerfile copies the document to the same place under /app.
DOCUMENT = Path(__file__).parents[4] / "docs" / "roadmap.md"

_ID = re.compile(r"M\d+[a-z]*|Later", re.IGNORECASE)
_HEADING = re.compile(r"##\s+(M\d+[a-z]*)\b", re.IGNORECASE)
_BOLD_LINE = re.compile(r"\*\*(.+?)\*\*:?")


@dataclass
class Item:
    text: str
    """Markdown, as written."""
    group: str = ""
    """The bold line it sits under ("Plan (sketch, undecided)"); "" if none."""


@dataclass
class Milestone:
    id: str
    """"M18a", or "Later" for an idea with no number yet."""
    name: str
    status: str
    """"done" (✅ after its id), "later" (a Later row), else "open"."""
    can: str = ""
    proves: str = ""
    items: list[Item] = field(default_factory=list)


@dataclass
class Roadmap:
    milestones: list[Milestone] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    """What couldn't be read, for the page to show instead of breaking."""

    def as_json(self) -> dict:
        return asdict(self)


def read(path: Path | None = None) -> Roadmap:
    path = path or DOCUMENT
    try:
        return parse(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as error:
        return Roadmap(notes=[f"Couldn't read {path.name}: {error.__class__.__name__}"])


def parse(text: str) -> Roadmap:
    roadmap = Roadmap()
    by_id: dict[str, Milestone] = {}
    section: Milestone | None = None
    group = ""
    in_table = False
    for number, line in enumerate(text.splitlines(), 1):
        line = line.rstrip()
        if line.startswith("##"):
            heading = _HEADING.match(line)
            section = by_id.get(heading.group(1).upper() if heading else "")
            group = ""
            in_table = False
        elif line.startswith("|"):
            if not roadmap.milestones and not in_table:
                in_table = True  # the header and its rule come first; they carry no milestone
                continue
            if set(line) <= set("|-: "):
                continue
            milestone = _row(line)
            if not milestone:
                roadmap.notes.append(f"Line {number}: a table row without a milestone, skipped")
            elif milestone.id.upper() in by_id:
                roadmap.notes.append(f"Line {number}: {milestone.id} is listed twice, the first kept")
            else:
                roadmap.milestones.append(milestone)
                if milestone.id != "Later":
                    by_id[milestone.id.upper()] = milestone
        elif section and (item := re.match(r"[-*]\s+(.+)", line)):
            section.items.append(Item(item.group(1).strip(), group))
        elif section and (bold := _BOLD_LINE.fullmatch(line.strip())):
            group = bold.group(1)
    if not roadmap.milestones:
        roadmap.notes.append("No milestones found in the roadmap")
    return roadmap


def _row(line: str) -> Milestone | None:
    cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
    if len(cells) < 2 or not cells[1]:
        return None
    first = cells[0].split()
    if not first or not _ID.fullmatch(first[0]):
        return None
    id = "Later" if first[0].lower() == "later" else "M" + first[0][1:].lower()
    status = "later" if id == "Later" else "done" if "✅" in cells[0] else "open"
    cells += [""] * (4 - len(cells))
    return Milestone(id, cells[1], status, cells[2], cells[3])
