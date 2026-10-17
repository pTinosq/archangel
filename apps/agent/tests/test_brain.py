import asyncio

from pydantic_ai import BinaryContent, ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from agent.brain import Brain
from agent.messages import Conversation, Image, Message, Text
from agent.threads import Threads

CONVERSATION = Conversation("cli", "local")


class Recorder:
    """A model that answers "ok" and records what it was sent."""

    def __init__(self):
        self.requests: list[list[ModelMessage]] = []
        self.model = FunctionModel(self.respond)

    def respond(self, messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        self.requests.append(messages)
        return ModelResponse(parts=[TextPart("ok")])


def send(brain: Brain, *parts) -> list[Message]:
    replies: list[Message] = []

    async def reply(message: Message) -> None:
        replies.append(message)

    asyncio.run(brain.handle(Message(CONVERSATION, parts), reply))
    return replies


def test_replies_with_model_output():
    brain = Brain(Recorder().model, Threads())
    assert send(brain, Text("hi")) == [Message(CONVERSATION, (Text("ok"),))]


def test_text_and_images_reach_the_model():
    recorder = Recorder()
    send(Brain(recorder.model, Threads()), Image(b"png", "image/png"), Text("what is this?"))

    prompt = recorder.requests[0][-1].parts[-1].content
    assert prompt == [BinaryContent(data=b"png", media_type="image/png"), "what is this?"]


def test_history_carries_over():
    recorder = Recorder()
    brain = Brain(recorder.model, Threads())
    send(brain, Text("first"))
    send(brain, Text("second"))

    assert len(recorder.requests[1]) == 3  # first request, its response, second request
