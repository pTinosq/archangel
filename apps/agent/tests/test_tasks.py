import asyncio

from fakes import FakeHelper, FakePilot, FakeReporter, FakeResearcher

from agent.messages import Address
from agent.runs import Answer
from agent.store import Status
from agent.tasks import Tasks

CLI = Address("cli", "local")


def _tasks(store, pilot=None, researcher=None, reporter=None) -> Tasks:
    return Tasks(store, pilot or FakePilot(), researcher or FakeResearcher(), reporter or FakeReporter(), FakeHelper())


def _notified(tasks: Tasks) -> list[str]:
    updates = []

    async def notify(event):
        updates.append(f"#{event.task.id} {event.task.status}: {event.task.report}")

    tasks.notify = notify
    return updates


def test_tasks_run_in_the_background_and_pause_for_the_owner(store):
    async def scenario():
        tasks = _tasks(store)
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
        tasks = _tasks(store)
        tasks.start("Post hello", CLI)
        await tasks.idle()

    asyncio.run(scenario())
    steps = store.steps(1)
    assert [(s.text, bool(s.screen)) for s in steps] == [
        ("Tapped the Post button.", True),
        ("needs approval: Approve: post it?", False),
    ]


def test_all_the_owner_says_to_a_task_stays_with_it_so_its_not_asked_again(store):
    async def scenario():
        pilot = FakePilot(hold=asyncio.Event())
        tasks = _tasks(store, pilot)
        tasks.start("Find flights", CLI)
        await asyncio.sleep(0)
        tasks.answer(1, Answer("Clear any old filters"))  # while it runs
        pilot.hold.set()
        await tasks.idle()
        tasks.answer(1, Answer("Yes, clear them all", approved=True))  # once it pauses
        await tasks.idle()
        assert pilot.said == ["Clear any old filters", "Yes, clear them all"]

    asyncio.run(scenario())


def test_only_open_tasks_take_answers(store):
    async def scenario():
        tasks = _tasks(store)
        assert tasks.answer(7, Answer("yes")) == "Task #7 isn't open."

    asyncio.run(scenario())


def test_a_running_task_hears_the_owner(store):
    async def scenario():
        pilot = FakePilot(hold=asyncio.Event())
        tasks = _tasks(store, pilot)
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
        tasks = _tasks(store, pilot)
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
        tasks = _tasks(store, FakePilot(hold=asyncio.Event()))
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
        tasks = _tasks(store, pilot)
        updates = _notified(tasks)
        tasks.start("Read the latest text", CLI)
        tasks.start("The best funds in 2026", CLI, "research")
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        report = "Report: /reports/2\ntldr: The best funds in 2026: the gist."
        assert updates == [f"#2 done: {report}"]
        assert store.task(2).kind == "research"
        assert "<p>All about The best funds in 2026.</p>" in store.page(2)
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
        tasks = _tasks(store, researcher=FakeResearcher(hold=asyncio.Event()))
        tasks.start("The best funds in 2026", CLI, "research")
        await asyncio.sleep(0)
        assert tasks.answer(1, Answer("only UK ones")).startswith("Task #1 (research) can't be told")
        tasks.cancel(1)
        await tasks.idle()

    asyncio.run(scenario())


def test_without_a_report_the_research_is_passed_on_as_it_is(store):
    class Broken(FakeReporter):
        async def write(self, topic, material):
            raise RuntimeError("down")

    async def scenario():
        tasks = _tasks(store, reporter=Broken())
        updates = _notified(tasks)
        tasks.start("The best funds in 2026", CLI, "research")
        await tasks.idle()
        assert updates == ["#1 done: All about The best funds in 2026."]
        assert store.page(1) is None

    asyncio.run(scenario())


def test_failed_research_is_a_failed_task(store):
    class Broken(FakeResearcher):
        async def research(self, question, deep=False):
            raise RuntimeError("OpenRouter is down")

    async def scenario():
        tasks = _tasks(store, researcher=Broken())
        updates = _notified(tasks)
        tasks.start("The best funds in 2026", CLI, "research")
        await tasks.idle()
        assert updates == ["#1 failed: Research failed: OpenRouter is down"]

    asyncio.run(scenario())


def test_tasks_start_briefed_by_the_context_twin_but_research_doesnt(store):
    briefed = []

    async def brief(goal):
        briefed.append(goal)
        return "Cheapest ride; 5 stars; no tip."

    async def scenario():
        pilot, researcher = FakePilot(), FakeResearcher()
        tasks = Tasks(store, pilot, researcher, FakeReporter(), FakeHelper(), brief=brief)
        tasks.start("Uber home", CLI)
        tasks.start("Best funds in 2026", CLI, "research")
        await tasks.idle()
        assert "Uber home" in pilot.calls[0][0] and "Cheapest ride; 5 stars; no tip." in pilot.calls[0][0]
        assert "Briefing from the context twin:\nCheapest ride; 5 stars; no tip." in [s.text for s in store.steps(1)]

        tasks.answer(1, Answer("yes", approved=True))  # resumed: it was told at the start
        await tasks.idle()
        assert briefed == ["Uber home"]

    asyncio.run(scenario())
