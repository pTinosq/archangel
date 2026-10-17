import asyncio

import pydantic_ai
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

from agent.messages import Conversation, Image, Message, Text
from agent.orchestrator import Orchestrator
from agent.phone.pilot import Outcome
from agent.phone.relay import Screenshot
from agent.store import Store
from agent.tasks import Tasks

CONVERSATION = Conversation("cli", "local")


class FakePilot:
    def __init__(self, outcome: Outcome):
        self.outcome = outcome
        self.calls: list[tuple] = []

    async def run(self, goal, paused=None, answer=None, record=None) -> Outcome:
        self.calls.append((goal, answer))
        return self.outcome


class Owner:
    """Talks to an orchestrator and collects every reply, including those that arrive after tasks finish."""

    def __init__(self, respond, outcome: Outcome = Outcome("done", "WhatsApp is open.")):
        self.requests: list[list[ModelMessage]] = []
        self.pilot = FakePilot(outcome)
        self.tasks = Tasks(Store(":memory:"), self.pilot)

        def recording(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            self.requests.append(messages)
            return respond(messages, info)

        self.orchestrator = Orchestrator(FunctionModel(recording), Store(":memory:"), self.tasks)
        self.replies: list[Message] = []

    def says(self, *parts) -> list[Message]:
        async def reply(message: Message) -> None:
            self.replies.append(message)

        async def scenario():
            await self.orchestrator.handle(Message(CONVERSATION, parts), reply)
            await self.tasks.idle()

        asyncio.run(scenario())
        return self.replies


def ok(messages, info) -> ModelResponse:
    return ModelResponse(parts=[TextPart("ok")])


def prompt_of(messages: list[ModelMessage]) -> str:
    """The latest prompt's text, whether from the owner or a task update."""
    part = next(p for p in reversed(messages[-1].parts) if isinstance(p, UserPromptPart))
    return part.content if isinstance(part.content, str) else " ".join(i for i in part.content if isinstance(i, str))


def test_replies_with_model_output():
    assert Owner(ok).says(Text("hi")) == [Message(CONVERSATION, (Text("ok"),))]


def test_text_and_images_reach_the_model():
    owner = Owner(ok)
    owner.says(Image(b"png", "image/png"), Text("what is this?"))

    prompt = owner.requests[0][-1].parts[-1].content
    assert prompt == [BinaryContent(data=b"png", media_type="image/png"), "what is this?"]


def test_history_carries_over():
    owner = Owner(ok)
    owner.says(Text("first"))
    owner.says(Text("second"))
    assert len(owner.requests[1]) == 3  # first request, its response, second request


def starts_a_task_then_relays_updates(messages, info) -> ModelResponse:
    last = messages[-1].parts[-1]
    if isinstance(last, ToolReturnPart):
        return ModelResponse(parts=[TextPart(f"On it: {last.content}")])
    if "not from the owner" in prompt_of(messages):
        return ModelResponse(parts=[TextPart(f"Relayed: {prompt_of(messages)}")])
    return ModelResponse(parts=[ToolCallPart("start_task", {"goal": "Open WhatsApp"})])


def test_tasks_run_in_the_background_and_report_back():
    owner = Owner(starts_a_task_then_relays_updates)
    replies = owner.says(Text("open whatsapp"))

    assert owner.pilot.calls == [("Open WhatsApp", None)]
    assert [r.text for r in replies] == [
        "On it: Started task #1.",
        "Relayed: [Update on task #1, not from the owner] done: WhatsApp is open.",
    ]


def test_questions_reach_the_owner_with_a_screenshot():
    waiting = Outcome("waiting", "Approve: post the tweet?", pydantic_ai.Conversation(), Screenshot(b"jpg", 1, 1))
    replies = Owner(starts_a_task_then_relays_updates, waiting).says(Text("tweet hello"))

    assert replies[-1].parts[-1] == Image(b"jpg", "image/jpeg")


def test_only_the_owner_can_approve():
    def approves_on_its_own(messages, info) -> ModelResponse:
        last = messages[-1].parts[-1]
        if isinstance(last, ToolReturnPart):
            return ModelResponse(parts=[TextPart(str(last.content))])
        if "not from the owner" in prompt_of(messages):
            return ModelResponse(parts=[ToolCallPart("answer_task", {"id": 1, "answer": "yes", "approve": True})])
        return ModelResponse(parts=[ToolCallPart("start_task", {"goal": "Post hello"})])

    waiting = Outcome("waiting", "Approve: post?", pydantic_ai.Conversation())
    owner = Owner(approves_on_its_own, waiting)
    replies = owner.says(Text("post hello"))

    assert replies[-1].text == "Only the owner can approve. Ask them."
    assert len(owner.pilot.calls) == 1


def test_the_owner_can_approve():
    def relays_then_approves(messages, info) -> ModelResponse:
        last = messages[-1].parts[-1]
        if isinstance(last, ToolReturnPart):
            return ModelResponse(parts=[TextPart(str(last.content))])
        text = prompt_of(messages)
        if "not from the owner" in text:
            return ModelResponse(parts=[TextPart("Post it?")])
        if text == "yes":
            return ModelResponse(parts=[ToolCallPart("answer_task", {"id": 1, "answer": "yes", "approve": True})])
        return ModelResponse(parts=[ToolCallPart("start_task", {"goal": "Post hello"})])

    owner = Owner(relays_then_approves, Outcome("waiting", "Approve: post?", pydantic_ai.Conversation()))
    owner.says(Text("post hello"))
    owner.says(Text("yes"))

    assert owner.pilot.calls[-1][1].approved


def test_a_task_can_be_cancelled_from_its_own_update():
    def cancels_waiting_tasks(messages, info) -> ModelResponse:
        last = messages[-1].parts[-1]
        if isinstance(last, ToolReturnPart):
            return ModelResponse(parts=[TextPart(str(last.content))])
        if "not from the owner" in prompt_of(messages):
            return ModelResponse(parts=[ToolCallPart("cancel_task", {"id": 1})])
        return ModelResponse(parts=[ToolCallPart("start_task", {"goal": "Post hello"})])

    owner = Owner(cancels_waiting_tasks, Outcome("waiting", "Approve: post?", pydantic_ai.Conversation()))
    assert owner.says(Text("post hello"))[-1].text == "Task #1 cancelled."
