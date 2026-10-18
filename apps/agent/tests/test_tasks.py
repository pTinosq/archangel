import asyncio

from fakes import FakePilot

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
        tasks = Tasks(store, FakePilot())
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
        tasks = Tasks(store, FakePilot())
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
        tasks = Tasks(store, FakePilot())
        assert tasks.answer(7, Answer("yes")) == "Task #7 isn't open."

    asyncio.run(scenario())


def test_a_running_task_hears_the_owner(store):
    async def scenario():
        pilot = FakePilot(hold=asyncio.Event())
        tasks = Tasks(store, pilot)
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
        tasks = Tasks(store, pilot)
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
        tasks = Tasks(store, FakePilot(hold=asyncio.Event()))
        updates = _notified(tasks)
        tasks.start("forever", CLI)
        await asyncio.sleep(0)

        assert tasks.cancel(1) == "Task #1 cancelled."
        await tasks.idle()
        assert store.task(1).status == Status.CANCELLED
        assert updates == []
        assert tasks.cancel(1) == "Task #1 isn't running."

    asyncio.run(scenario())
