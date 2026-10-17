import asyncio

from pydantic_ai import BinaryContent, ModelMessage, ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from agent.brain import Brain
from agent.messages import Conversation, Image, Message, Text
from agent.phone.pilot import Outcome
from agent.store import Store

CONVERSATION = Conversation("cli", "local")


class Recorder:
    """A model that answers "ok" and records what it was sent."""

    def __init__(self):
        self.requests: list[list[ModelMessage]] = []
        self.model = FunctionModel(self.respond)

    def respond(self, messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        self.requests.append(messages)
        return ModelResponse(parts=[TextPart("ok")])


class FakePilot:
    def __init__(self):
        self.tasks: list[str] = []

    async def run(self, task: str) -> Outcome:
        self.tasks.append(task)
        return Outcome("done", "WhatsApp is open.")


def brain(model) -> Brain:
    return Brain(model, Store(":memory:"), FakePilot())


def send(brain: Brain, *parts) -> list[Message]:
    replies: list[Message] = []

    async def reply(message: Message) -> None:
        replies.append(message)

    asyncio.run(brain.handle(Message(CONVERSATION, parts), reply))
    return replies


def test_replies_with_model_output():
    assert send(brain(Recorder().model), Text("hi")) == [Message(CONVERSATION, (Text("ok"),))]


def test_text_and_images_reach_the_model():
    recorder = Recorder()
    send(brain(recorder.model), Image(b"png", "image/png"), Text("what is this?"))

    prompt = recorder.requests[0][-1].parts[-1].content
    assert prompt == [BinaryContent(data=b"png", media_type="image/png"), "what is this?"]


def test_history_carries_over():
    recorder = Recorder()
    agent = brain(recorder.model)
    send(agent, Text("first"))
    send(agent, Text("second"))

    assert len(recorder.requests[1]) == 3  # first request, its response, second request


def test_phone_tasks_go_to_the_pilot():
    pilot = FakePilot()

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        last = messages[-1].parts[-1]
        if isinstance(last, ToolReturnPart):
            return ModelResponse(parts=[TextPart(f"Done: {last.content}")])
        return ModelResponse(parts=[ToolCallPart("use_phone", {"task": "Open WhatsApp"})])

    replies = send(Brain(FunctionModel(respond), Store(":memory:"), pilot), Text("open whatsapp"))

    assert pilot.tasks == ["Open WhatsApp"]
    assert replies[0].text == "Done: WhatsApp is open."
