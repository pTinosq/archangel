import asyncio

import pydantic_ai
from fakes import SCREEN, FakeChannel, FakePilot
from pydantic_ai import (
    BinaryContent,
    ModelMessage,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from agent.events import OwnerMessage
from agent.gateway import Gateway
from agent.messages import Image, Message, Text
from agent.orchestrator import Orchestrator
from agent.phone.pilot import Outcome
from agent.tasks import Tasks


class Owner:
    """Talks to an orchestrator over two channels and collects every reply, including those after tasks finish."""

    def __init__(self, store, respond, outcome: Outcome = Outcome("done", "WhatsApp is open.")):
        self.requests: list[list[ModelMessage]] = []
        self.pilot = FakePilot(outcome)
        self.tasks = Tasks(store, self.pilot)
        self.cli, self.email = FakeChannel("cli"), FakeChannel("email")
        self.store = store

        def recording(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            self.requests.append(messages)
            return respond(messages, info)

        gateway = Gateway([self.cli, self.email], store)
        self.orchestrator = Orchestrator(FunctionModel(recording), store, self.tasks, gateway)

    def says(self, *parts, via: FakeChannel | None = None) -> list[Message]:
        async def scenario():
            await self.orchestrator.handle(OwnerMessage(Message((via or self.cli).owner, parts)))
            await self.tasks.idle()

        asyncio.run(scenario())
        return self.cli.sent


def ok(messages, info) -> ModelResponse:
    return ModelResponse(parts=[TextPart("ok")])


def prompt_of(messages: list[ModelMessage]) -> str:
    """The latest prompt's text, whether from the owner or a task update."""
    part = next(p for p in reversed(messages[-1].parts) if isinstance(p, UserPromptPart))
    return part.content if isinstance(part.content, str) else " ".join(i for i in part.content if isinstance(i, str))


def tool_result_or(respond):
    """Says each tool's result back as the reply; otherwise `respond(prompt)`."""

    def model(messages, info) -> ModelResponse:
        last = messages[-1].parts[-1]
        if isinstance(last, ToolReturnPart):
            return ModelResponse(parts=[TextPart(str(last.content))])
        return respond(prompt_of(messages))

    return model


def call(tool: str, **args) -> ModelResponse:
    return ModelResponse(parts=[ToolCallPart(tool, args)])


def say(text: str) -> ModelResponse:
    return ModelResponse(parts=[TextPart(text)])


def test_replies_go_back_where_the_owner_wrote(store):
    owner = Owner(store, ok)
    owner.says(Text("hi"), via=owner.email)
    assert (owner.cli.sent, owner.email.sent) == ([], [Message(owner.email.owner, (Text("ok"),), answering=True)])


def test_the_model_sees_the_channel_text_and_images(store):
    owner = Owner(store, ok)
    owner.says(Image(b"png", "image/png"), Text("what is this?"))

    assert owner.requests[0][-1].parts[-1].content == [
        "(via cli)",
        BinaryContent(data=b"png", media_type="image/png"),
        "what is this?",
    ]


def test_one_conversation_across_channels(store):
    owner = Owner(store, ok)
    owner.says(Text("first"))
    owner.says(Text("second"), via=owner.email)
    assert len(owner.requests[1]) == 3  # first request, its response, second request


def starts_a_task_then_relays_updates(prompt: str) -> ModelResponse:
    if "not from the owner" in prompt:
        return say(f"Relayed: {prompt}")
    if "email" in prompt:
        return call("start_task", goal="Open WhatsApp", report_via="email")
    return call("start_task", goal="Open WhatsApp")


def test_tasks_run_in_the_background_and_report_back(store):
    owner = Owner(store, tool_result_or(starts_a_task_then_relays_updates))
    replies = owner.says(Text("open whatsapp"))

    assert owner.pilot.calls == [("Open WhatsApp", None)]
    assert [r.text for r in replies] == [
        "Started task #1.",
        "Relayed: [Update on task #1, not from the owner] done: WhatsApp is open.",
    ]


def test_a_task_just_started_isnt_mistaken_for_an_old_one(store):
    owner = Owner(store, tool_result_or(starts_a_task_then_relays_updates))
    owner.says(Text("open whatsapp"))

    # The call writing the reply sees the tasks as the message found them; start_task's result says what's new.
    replying = owner.requests[1]
    assert "Tasks when this arrived: none." in replying[-1].instructions
    assert "Started task #1." in str(replying[-1].parts[-1].content)


def test_task_results_can_go_to_another_channel(store):
    owner = Owner(store, tool_result_or(starts_a_task_then_relays_updates))
    owner.says(Text("open whatsapp and email me"))

    assert [r.text for r in owner.cli.sent] == ["Started task #1."]
    assert owner.email.sent[0].text.startswith("Relayed: [Update on task #1")


def test_a_started_task_can_report_elsewhere(store):
    def redirects(prompt):
        if "not from the owner" in prompt:
            return say("Done.")
        if "email" in prompt:
            return call("redirect_task", id=1, channel="email")
        return call("start_task", goal="Book a taxi")

    owner = Owner(store, tool_result_or(redirects))
    owner.pilot.hold = asyncio.Event()

    async def scenario():
        await owner.orchestrator.handle(OwnerMessage(Message(owner.cli.owner, (Text("book a taxi"),))))
        await owner.orchestrator.handle(OwnerMessage(Message(owner.cli.owner, (Text("email me when it's done"),))))
        owner.pilot.hold.set()
        await owner.tasks.idle()

    asyncio.run(scenario())
    assert [r.text for r in owner.cli.sent] == ["Started task #1.", "Task #1 will report via email."]
    assert [m.text for m in owner.email.sent] == ["Done."]


def test_the_model_can_message_another_channel(store):
    owner = Owner(store, tool_result_or(lambda prompt: call("send", channel="email", text="Your summary")))
    replies = owner.says(Text("email me a summary"))

    assert [m.text for m in owner.email.sent] == ["Your summary"]
    assert [r.text for r in replies] == ["Sent via email."]
    assert [(line.channel, line.sender) for line in store.lines()] == [("email", "archangel"), ("cli", "archangel")]


def test_questions_reach_the_owner_with_a_screenshot(store):
    waiting = Outcome("needs_approval", "Approve: post the tweet?", pydantic_ai.Conversation(), SCREEN)
    replies = Owner(store, tool_result_or(starts_a_task_then_relays_updates), waiting).says(Text("tweet hello"))

    assert replies[-1].parts[-1] == SCREEN


def test_only_the_owner_can_approve(store):
    def approves_on_its_own(prompt):
        if "not from the owner" in prompt:
            return call("answer_task", id=1, answer="yes", approve=True)
        return call("start_task", goal="Post hello")

    owner = Owner(
        store,
        tool_result_or(approves_on_its_own),
        Outcome("needs_approval", "Approve: post?", pydantic_ai.Conversation()),
    )
    replies = owner.says(Text("post hello"))

    assert replies[-1].text == "Only the owner can approve. Ask them."
    assert len(owner.pilot.calls) == 1


def test_the_owner_can_approve(store):
    def relays_then_approves(prompt):
        if "not from the owner" in prompt:
            return say("Post it?")
        if prompt.endswith("yes"):
            return call("answer_task", id=1, answer="yes", approve=True)
        return call("start_task", goal="Post hello")

    owner = Owner(
        store,
        tool_result_or(relays_then_approves),
        Outcome("needs_approval", "Approve: post?", pydantic_ai.Conversation()),
    )
    owner.says(Text("post hello"))
    owner.says(Text("yes"))

    assert owner.pilot.calls[-1][1].approved


def test_a_task_can_be_cancelled_from_its_own_update(store):
    def cancels_waiting_tasks(prompt):
        if "not from the owner" in prompt:
            return call("cancel_task", id=1)
        return call("start_task", goal="Post hello")

    owner = Owner(
        store,
        tool_result_or(cancels_waiting_tasks),
        Outcome("needs_approval", "Approve: post?", pydantic_ai.Conversation()),
    )
    assert owner.says(Text("post hello"))[-1].text == "Task #1 cancelled."


def test_failures_reach_the_owner(store):
    def broken(messages, info):
        raise RuntimeError("boom")

    assert Owner(store, broken).says(Text("hi"))[-1].text == "Sorry, something went wrong on my side. Could you say that again?"
