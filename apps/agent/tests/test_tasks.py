import asyncio

from fakes import FakePilot, FakeReporter, FakeResearcher

from agent.messages import Address
from agent.phone.pilot import Answer
from agent.store import Status
from agent.tasks import Tasks

CLI = Address("cli", "local")


def _notified(tasks: Tasks) -> list[str]:
    updates = []

    async def notify(event):
        updates.append(f"#{event.task.id} {event.task.status}: {event.task.report}")

    tasks.notify = notify
    return updates


def test_tasks_run_in_the_background_and_pause_for_the_owner(store):
    async def scenario():
        tasks = Tasks(store, FakePilot(), FakeResearcher(), FakeReporter())
        updates = _notified(tasks)

        task = tasks.start("Post hello", CLI)
        assert task.status == Status.QUEUED  # returned before any phone work
        await tasks.idle()
        assert updates == ["#1 needs approval: Approve: post it?"]
        assert store.task(1).run is not None  # resumable after a restart

        assert tasks.answer(1, Answer("yes", approved=True)) == "Task #1 continues."
        await tasks.idle()
        assert updates[-1] == "#1 done: Owner said yes."
        assert store.task(1).run is None

    asyncio.run(scenario())


def test_steps_are_recorded(store):
    async def scenario():
        tasks = Tasks(store, FakePilot(), FakeResearcher(), FakeReporter())
        tasks.start("Post hello", CLI)
        await tasks.idle()

    asyncio.run(scenario())
    steps = store.steps(1)
    assert [(s.text, bool(s.screen)) for s in steps] == [
        ("Tapped the Post button.", True),
        ("needs approval: Approve: post it?", False),
    ]


def test_only_open_tasks_take_answers(store):
    async def scenario():
        tasks = Tasks(store, FakePilot(), FakeResearcher(), FakeReporter())
        assert tasks.answer(7, Answer("yes")) == "Task #7 isn't open."

    asyncio.run(scenario())


def test_a_running_task_hears_the_owner(store):
    async def scenario():
        pilot = FakePilot(hold=asyncio.Event())
        tasks = Tasks(store, pilot, FakeResearcher(), FakeReporter())
        tasks.start("Read the latest text", CLI)
        await asyncio.sleep(0)
        assert tasks.answer(1, Answer("You can tap Continue")) == "Passed on to task #1; it sees it on its next step."
        assert pilot.inbox == ["You can tap Continue"]
        pilot.hold.set()
        await tasks.idle()

    asyncio.run(scenario())


def test_one_task_drives_the_phone_at_a_time(store):
    async def scenario():
        hold = asyncio.Event()
        pilot = FakePilot(hold=hold)
        tasks = Tasks(store, pilot, FakeResearcher(), FakeReporter())
        tasks.start("first", CLI)
        tasks.start("second", CLI)
        await asyncio.sleep(0)
        assert [goal for goal, _ in pilot.calls] == ["first"]
        hold.set()
        await tasks.idle()
        assert [goal for goal, _ in pilot.calls] == ["first", "second"]

    asyncio.run(scenario())


def test_cancelling_stops_a_running_task(store):
    async def scenario():
        tasks = Tasks(store, FakePilot(hold=asyncio.Event()), FakeResearcher(), FakeReporter())
        updates = _notified(tasks)
        tasks.start("forever", CLI)
        await asyncio.sleep(0)

        assert tasks.cancel(1) == "Task #1 cancelled."
        await tasks.idle()
        assert store.task(1).status == Status.CANCELLED
        assert updates == []
        assert tasks.cancel(1) == "Task #1 isn't running."

    asyncio.run(scenario())


def test_research_runs_beside_a_phone_task(store):
    async def scenario():
        pilot = FakePilot(hold=asyncio.Event())
        tasks = Tasks(store, pilot, FakeResearcher(), FakeReporter())
        updates = _notified(tasks)
        tasks.start("Read the latest text", CLI)
        tasks.start("The best funds in 2026", CLI, "research")
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        report = "Report: /reports/2\ntldr: The best funds in 2026: the gist."
        assert updates == [f"#2 done: {report}"]
        assert store.task(2).kind == "research"
        assert store.page(2) == "<p>All about The best funds in 2026.</p>"
        assert [s.text for s in store.steps(2)] == [
            "Researching.",
            "All about The best funds in 2026.",
            "Writing the report.",
            f"done: {report}",
        ]
        pilot.hold.set()
        await tasks.idle()

    asyncio.run(scenario())


def test_research_cant_be_told_anything_midway(store):
    async def scenario():
        tasks = Tasks(store, FakePilot(), FakeResearcher(hold=asyncio.Event()), FakeReporter())
        tasks.start("The best funds in 2026", CLI, "research")
        await asyncio.sleep(0)
        assert tasks.answer(1, Answer("only UK ones")).startswith("Task #1 is research")
        tasks.cancel(1)
        await tasks.idle()

    asyncio.run(scenario())


def test_without_a_report_the_research_is_passed_on_as_it_is(store):
    class Broken(FakeReporter):
        async def write(self, topic, material):
            raise RuntimeError("down")

    async def scenario():
        tasks = Tasks(store, FakePilot(), FakeResearcher(), Broken())
        updates = _notified(tasks)
        tasks.start("The best funds in 2026", CLI, "research")
        await tasks.idle()
        assert updates == ["#1 done: All about The best funds in 2026."]
        assert store.page(1) is None

    asyncio.run(scenario())


def test_failed_research_is_a_failed_task(store):
    class Broken(FakeResearcher):
        async def research(self, question):
            raise RuntimeError("OpenRouter is down")

    async def scenario():
        tasks = Tasks(store, FakePilot(), Broken(), FakeReporter())
        updates = _notified(tasks)
        tasks.start("The best funds in 2026", CLI, "research")
        await tasks.idle()
        assert updates == ["#1 failed: Research failed: OpenRouter is down"]

    asyncio.run(scenario())
