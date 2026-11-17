import sqlite3

from pydantic_ai import ModelMessagesTypeAdapter, ModelRequest, ModelResponse, TextPart

from agent.threads.store import BEFORE, ThreadStore


class Clock:
    def __init__(self, now: float = 1000.0):
        self.now = now

    def __call__(self) -> float:
        return self.now


def turn(said: str, answer: str) -> list:
    return [ModelRequest.user_text_prompt(f"(via sms) {said}"), ModelResponse(parts=[TextPart(answer)])]


def test_a_thread_keeps_its_history_and_size():
    store = ThreadStore(sqlite3.connect(":memory:"))
    thread = store.new("Flights to India")
    store.save(thread.id, turn("flights to Delhi?", "From £540."), size=1200)
    assert store.history(thread.id)[1].parts[0].content == "From £540."
    assert store.get(thread.id).size == 1200
    assert store.history(store.new("Other").id) == []


def test_the_newest_said_in_comes_first():
    clock = Clock()
    store = ThreadStore(sqlite3.connect(":memory:"), clock)
    first, _ = store.new("First"), store.new("Second")
    clock.now += 60
    store.save(first.id, turn("more", "ok"), size=10)
    assert [t.title for t in store.recent()] == ["First", "Second"]
    assert store.latest().id == first.id


def test_search_finds_words_and_their_beginnings_best_first():
    store = ThreadStore(sqlite3.connect(":memory:"))
    india = store.new("Flights to India")
    store.save(india.id, turn("flights to Delhi in March?", "Return from £540 with Air India."), size=10)
    store.save(store.new("Birthday").id, turn("text Sam happy birthday", "Sent."), size=10)
    [found] = store.search("delhi")
    assert found.thread.id == india.id and "[Delhi]" in found.snippet
    assert [f.thread.title for f in store.search("Ind")] == ["Flights to India"]  # a word's beginning
    assert store.search("") == store.search("?!") == []  # nothing to look for, and no query syntax let through


def test_a_summary_closes_a_thread_unless_something_was_said_since():
    clock = Clock()
    store = ThreadStore(sqlite3.connect(":memory:"), clock)
    thread = store.new("Flights")
    store.save(thread.id, turn("flights?", "£540."), size=10)
    as_of = clock.now
    clock.now += 5
    store.save(thread.id, turn("and hotels?", "Looking."), size=20)  # while the summary was being written
    store.summarise(thread.id, "Flights and hotels", "Flights £540.", as_of)
    assert store.get(thread.id).open and store.get(thread.id).stale
    store.summarise(thread.id, "Flights and hotels", "Flights £540; hotels next.", clock.now)
    summed = store.get(thread.id)
    assert not summed.open and not summed.stale and summed.title == "Flights and hotels"
    assert store.search("hotels")[0].thread.summary == "Flights £540; hotels next."


def test_quiet_open_threads_and_stale_closed_ones_are_to_summarise():
    clock = Clock()
    store = ThreadStore(sqlite3.connect(":memory:"), clock)
    quiet, fresh_summary, stale_summary = store.new("Quiet"), store.new("Summed up"), store.new("Said more since")
    for thread in (fresh_summary, stale_summary):
        store.summarise(thread.id, thread.title, "…", clock.now)
    clock.now += 10
    store.save(stale_summary.id, turn("one more thing", "ok"), size=10)
    busy = store.new("Busy")
    assert [t.id for t in store.to_summarise(quiet_since=clock.now - 5)] == [quiet.id, stale_summary.id]
    assert busy.id not in [t.id for t in store.to_summarise(quiet_since=clock.now - 5)]


def test_the_conversation_before_threads_becomes_the_first():
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE history (id INTEGER PRIMARY KEY CHECK (id = 1), messages BLOB)")
    db.execute("INSERT INTO history VALUES (1, ?)", (ModelMessagesTypeAdapter.dump_json(turn("hi", "Hello.")),))
    store = ThreadStore(db)
    [thread] = store.recent()
    assert (thread.title, thread.open) == (BEFORE, True)
    assert store.history(thread.id)[1].parts[0].content == "Hello."
    assert store.search("hello")[0].thread.id == thread.id
    assert not db.execute("SELECT 1 FROM sqlite_master WHERE name = 'history'").fetchone()
    assert ThreadStore(db).recent() == [store.get(thread.id)]  # once only


def test_with_no_conversation_before_there_are_no_threads():
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE history (id INTEGER PRIMARY KEY CHECK (id = 1), messages BLOB)")
    assert ThreadStore(db).recent() == []
