from agent import roadmap
from agent.roadmap import Item, parse

from test_panel import Panel

DOC = """# Roadmap

| # | Milestone | You can… | Proves |
|---|---|---|---|
| M1 ✅ | Chat | talk | a brain |
| M2 | Files | hand it a PDF | a folder |
| M2a ✅ | Sub step | one | two |
| Later | Telegram | message it | another channel |

## M2 requirements (files) (from the owner)

Prose is not an item.

- **Stores** what it's given.
- Reads it.

**Plan (sketch)**
- Use `tools`.

## Cleanup (done)

- Belongs to no milestone.

## M1 (done)

- Shipped.
"""


def test_milestones_come_with_status_and_items():
    result = parse(DOC)
    assert [(m.id, m.status, m.name) for m in result.milestones] == [
        ("M1", "done", "Chat"),
        ("M2", "open", "Files"),
        ("M2a", "done", "Sub step"),
        ("Later", "later", "Telegram"),
    ]
    one, two = result.milestones[:2]
    assert (two.can, two.proves) == ("hand it a PDF", "a folder")
    assert one.items == [Item("Shipped.")]
    assert two.items == [
        Item("**Stores** what it's given."),
        Item("Reads it."),
        Item("Use `tools`.", "Plan (sketch)"),
    ]
    assert result.notes == []


def test_a_section_for_an_unlisted_milestone_is_ignored():
    result = parse(DOC.replace("| M2 | Files | hand it a PDF | a folder |\n", ""))
    assert [m.id for m in result.milestones] == ["M1", "M2a", "Later"]
    assert all(not m.items or m.id == "M1" for m in result.milestones)


def test_malformed_rows_are_skipped_and_said_so():
    broken = DOC.replace("| M2a ✅ | Sub step | one | two |", "| not a milestone | x | y | z |\n| M3 |\n| M1 | Again | | |")
    result = parse(broken)
    assert [m.id for m in result.milestones] == ["M1", "M2", "Later"]
    assert len(result.notes) == 3
    assert result.milestones[0].name == "Chat"


def test_a_short_row_keeps_what_it_has():
    result = parse("| # | Milestone |\n|---|---|\n| M7 ✅ | Deploy |\n")
    assert [(m.id, m.name, m.can, m.proves) for m in result.milestones] == [("M7", "Deploy", "", "")]


def test_nothing_usable_degrades_to_a_note():
    for text in ["", "no table here", "| a |\n|---|\n| b |", "## M1\n- orphan"]:
        result = parse(text)
        assert result.milestones == []
        assert result.notes


def test_an_unreadable_document_is_a_note_not_an_error(tmp_path):
    missing = roadmap.read(tmp_path / "nope.md")
    assert missing.milestones == [] and "nope.md" in missing.notes[0]
    binary = tmp_path / "bad.md"
    binary.write_bytes(b"\xff\xfe\x00")
    assert roadmap.read(binary).notes


def test_the_real_roadmap_parses_cleanly():
    result = roadmap.read()
    assert result.notes == []
    assert len(result.milestones) > 20
    assert {m.status for m in result.milestones} == {"done", "open", "later"}
    assert all(m.name for m in result.milestones)
    assert any(m.items for m in result.milestones)


def test_the_panel_serves_the_document_parsed(store, monkeypatch, tmp_path):
    doc = tmp_path / "roadmap.md"
    doc.write_text(DOC)
    monkeypatch.setattr(roadmap, "DOCUMENT", doc)
    served = Panel(store).call("GET", "/api/roadmap").json()
    assert [m["id"] for m in served["milestones"]] == ["M1", "M2", "M2a", "Later"]
    assert served["milestones"][1]["items"][2] == {"text": "Use `tools`.", "group": "Plan (sketch)"}
    assert served["notes"] == []
