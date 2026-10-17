import asyncio

import pydantic_ai

from agent.messages import Conversation
from agent.phone.pilot import Answer, Outcome
from agent.phone.relay import Screenshot
from agent.store import Status, Store
from agent.tasks import Tasks

CONVERSATION = Conversation("cli", "local")
SCREEN = Screenshot(b"jpg", 540, 1200)


class FakePilot:
    """Asks for approval first, then does whatever the owner said."""

    def __init__(self, hold: asyncio.Event | None = None):
        self.hold = hold
        self.calls: list[tuple[str, Answer | None]] = []

    async def run(self, goal, paused=None, answer=None, record=None) -> Outcome:
        self.calls.append((goal, answer))
        if self.hold:
            await self.hold.wait()
        if not paused:
            return Outcome("waiting", "Approve: post it?", run=pydantic_ai.Conversation(), screen=SCREEN)
        return Outcome("done" if answer.approved else "gave_up", f"Owner said {answer.text}.", screen=SCREEN)


def _notified(tasks: Tasks) -> list[str]:
    updates = []

    async def notify(task, screen):
        updates.append(f"#{task.id} {task.status}: {task.report}")

    tasks.notify = notify
    return updates


def test_tasks_run_in_the_background_and_pause_for_the_owner():
    async def scenario():
        store = Store(":memory:")
        tasks = Tasks(store, FakePilot())
        updates = _notified(tasks)

        task = tasks.start(CONVERSATION, "Post hello")
        assert task.status == Status.QUEUED  # returned before any phone work
        await tasks.idle()
        assert updates == ["#1 waiting: Approve: post it?"]
        assert store.task(1).run is not None  # resumable after a restart

        assert tasks.answer(1, Answer("yes", approved=True)) == "Task #1 continues."
        await tasks.idle()
        assert updates[-1] == "#1 done: Owner said yes."

    asyncio.run(scenario())


def test_only_waiting_tasks_take_answers():
    async def scenario():
        tasks = Tasks(Store(":memory:"), FakePilot())
        assert tasks.answer(7, Answer("yes")) == "Task #7 isn't waiting for an answer."

    asyncio.run(scenario())


def test_one_task_drives_the_phone_at_a_time():
    async def scenario():
        hold = asyncio.Event()
        pilot = FakePilot(hold)
        tasks = Tasks(Store(":memory:"), pilot)
        tasks.start(CONVERSATION, "first")
        tasks.start(CONVERSATION, "second")
        await asyncio.sleep(0)
        assert [goal for goal, _ in pilot.calls] == ["first"]
        hold.set()
        await tasks.idle()
        assert [goal for goal, _ in pilot.calls] == ["first", "second"]

    asyncio.run(scenario())


def test_cancelling_stops_a_running_task():
    async def scenario():
        store = Store(":memory:")
        tasks = Tasks(store, FakePilot(asyncio.Event()))
        updates = _notified(tasks)
        tasks.start(CONVERSATION, "forever")
        await asyncio.sleep(0)

        assert tasks.cancel(1) == "Task #1 cancelled."
        await tasks.idle()
        assert store.task(1).status == Status.CANCELLED
        assert updates == []
        assert tasks.cancel(1) == "Task #1 isn't running."

    asyncio.run(scenario())
