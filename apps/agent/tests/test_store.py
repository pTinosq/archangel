from pydantic_ai import ModelRequest

from agent.messages import Conversation
from agent.store import Status, Store

CONVERSATION = Conversation("cli", "local")


def test_history_round_trips():
    store = Store(":memory:")
    assert store.history(CONVERSATION) == []

    history = [ModelRequest.user_text_prompt("hi")]
    store.save_history(CONVERSATION, history)
    assert store.history(CONVERSATION)[0].parts[0].content == "hi"


def test_history_survives_a_restart(tmp_path):
    Store(tmp_path / "a.db").save_history(CONVERSATION, [ModelRequest.user_text_prompt("hi")])
    assert len(Store(tmp_path / "a.db").history(CONVERSATION)) == 1


def test_tasks_round_trip():
    store = Store(":memory:")
    task = store.add_task(CONVERSATION, "Book an Uber")
    task.status, task.report = Status.DONE, "Booked."
    store.save_task(task)

    assert store.task(task.id) == task
    assert store.tasks(CONVERSATION) == [task]


def test_running_tasks_are_interrupted_on_restart():
    store = Store(":memory:")
    running, waiting = store.add_task(CONVERSATION, "a"), store.add_task(CONVERSATION, "b")
    running.status, waiting.status = Status.RUNNING, Status.WAITING
    store.save_task(running)
    store.save_task(waiting)

    store.interrupt_running()

    assert [t.status for t in store.tasks(CONVERSATION)] == [Status.INTERRUPTED, Status.WAITING]
