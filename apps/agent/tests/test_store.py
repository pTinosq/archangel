import os
import time

import pydantic_ai
from pydantic_ai import BinaryContent, ModelRequest, UserPromptPart

from agent.messages import Address, Image, Message, Text
from agent.store import IMAGES_KEPT, Status, Store

CLI = Address("cli", "local")


def test_one_history_survives_a_restart(tmp_path):
    assert Store(tmp_path).history() == []
    Store(tmp_path).save_history([ModelRequest.user_text_prompt("hi")])
    assert Store(tmp_path).history()[0].parts[0].content == "hi"


def test_tasks_round_trip(store):
    task = store.add_task("Book an Uber", CLI)
    task.status, task.report = Status.DONE, "Booked."
    store.save_task(task)

    assert store.task(task.id) == task
    assert store.tasks() == [task]


def test_running_tasks_are_interrupted_on_restart(store):
    running, waiting = store.add_task("a", CLI), store.add_task("b", CLI)
    running.status, waiting.status = Status.RUNNING, Status.NEEDS_APPROVAL
    store.save_task(running)
    store.save_task(waiting)

    store.interrupt_running()

    assert [t.status for t in store.tasks()] == [Status.NEEDS_APPROVAL, Status.INTERRUPTED]


def test_paused_runs_keep_only_the_latest_screen(store):
    def screen(n):
        return ModelRequest(parts=[UserPromptPart([f"screen {n}", BinaryContent(b"jpg", media_type="image/jpeg")])])

    task = store.add_task("a", CLI)
    task.status, task.run = Status.NEEDS_APPROVAL, pydantic_ai.Conversation(messages=[screen(1), screen(2)])
    store.save_task(task)

    kept = store.task(task.id).run.messages
    earlier, latest = (m.parts[0].content[1] for m in kept)
    assert (earlier, latest.data) == ("(earlier screenshot)", b"jpg")


def test_transcript_keeps_both_sides_with_images(store):
    store.add_line(Message(CLI, (Text("hi"),)), "owner")
    store.add_line(Message(Address("panel", "owner"), (Text("Post?"), Image(b"png", "image/png"))), "archangel")

    owner, archangel = store.lines()
    assert (owner.channel, owner.sender, owner.text, owner.images) == ("cli", "owner", "hi", [])
    assert store.image(archangel.images[0]) == Image(b"png", "image/png")
    assert store.lines(after=owner.id) == [archangel]


def test_steps_and_their_screens(store):
    store.add_step(1, "Started.", Image(b"jpg", "image/jpeg"))
    store.add_step(1, "done: ok")
    store.add_step(2, "other task")

    started, finished = store.steps(1)
    assert store.image(started.screen) == Image(b"jpg", "image/jpeg")
    assert (finished.text, finished.screen) == ("done: ok", None)


def test_images_expire(store):
    name = store.save_image(Image(b"jpg", "image/jpeg"))
    old = time.time() - IMAGES_KEPT.total_seconds() - 60
    os.utime(store._images / name, (old, old))

    store.forget_old_images()

    assert store.image(name) is None


def test_image_names_cant_escape_the_folder(store):
    assert store.image("../archangel.db") is None
