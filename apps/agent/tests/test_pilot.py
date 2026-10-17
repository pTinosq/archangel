import asyncio

import pytest
from pydantic_ai import (
    BinaryContent,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from agent.history import forget_old_screenshots
from agent.phone import pilot as pilot_module
from agent.phone.pilot import Pilot
from agent.phone.relay import PhoneError, Screenshot
from agent.phone.vision import Box


@pytest.fixture(autouse=True)
def no_settling(monkeypatch):
    monkeypatch.setattr(pilot_module, "SETTLE_SECONDS", 0)


class FakePhone:
    def __init__(self):
        self.actions: list[str] = []

    async def screenshot(self) -> Screenshot:
        return Screenshot(b"jpg", 540, 1200)

    async def tap(self, x: float, y: float, hold: bool = False) -> None:
        self.actions.append(f"tap {x:.2f},{y:.2f}")

    async def type(self, text: str) -> None:
        raise PhoneError("No focused input field")

    async def apps(self) -> dict[str, str]:
        return {"WhatsApp": "com.whatsapp"}

    async def launch(self, package: str) -> None:
        self.actions.append(f"launch {package}")


class FakeLocator:
    async def locate(self, screenshot: Screenshot, target: str) -> Box | None:
        return Box(0.2, 0.4, 0.4, 0.6) if "WhatsApp" in target else None


def scripted(*calls: tuple[str, dict]):
    """A model that makes `calls` in order, then reports every tool result it saw."""
    seen: list[str] = []

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        for part in messages[-1].parts:
            if isinstance(part, ToolReturnPart):
                seen.append(str(part.content))
        step = sum(isinstance(m, ModelResponse) for m in messages)
        if step < len(calls):
            return ModelResponse(parts=[ToolCallPart(*calls[step])])
        return ModelResponse(parts=[TextPart(" | ".join(seen))])

    return FunctionModel(respond)


def run(model, phone: FakePhone) -> str:
    return asyncio.run(Pilot(model, phone, FakeLocator()).run("do it"))


def test_taps_the_centre_of_the_located_box():
    phone = FakePhone()
    report = run(scripted(("tap", {"target": "the WhatsApp icon"})), phone)

    assert phone.actions == ["tap 0.30,0.50"]
    assert report == "Tapped the WhatsApp icon."


def test_problems_are_reported_back_to_the_model():
    phone = FakePhone()
    report = run(
        scripted(
            ("tap", {"target": "a unicorn"}),
            ("type_text", {"text": "hi"}),
            ("open_app", {"name": "Snapchat"}),
            ("open_app", {"name": "whatsapp"}),
        ),
        phone,
    )

    assert report.split(" | ") == [
        "Couldn't find a unicorn on the screen.",
        "Failed: No focused input field",
        "No app called 'Snapchat'. Installed: WhatsApp.",
        "Opened whatsapp.",
    ]
    assert phone.actions == ["launch com.whatsapp"]


def test_gives_up_after_the_step_limit(monkeypatch):
    monkeypatch.setattr(pilot_module, "MAX_STEPS", 3)
    forever = FunctionModel(lambda messages, info: ModelResponse(parts=[ToolCallPart("look", {})]))
    assert run(forever, FakePhone()) == "Gave up after 3 steps without finishing."


def test_unexpected_failures_are_reported_not_raised():
    invalid = FunctionModel(lambda messages, info: ModelResponse(parts=[ToolCallPart("scroll", {"direction": "sideways"})]))
    report = run(invalid, FakePhone())
    assert report.startswith("The phone task failed unexpectedly (UnexpectedModelBehavior")


def test_only_recent_screenshots_stay_in_context():
    def screen(n: int) -> ModelRequest:
        return ModelRequest(parts=[UserPromptPart([f"screen {n}", BinaryContent(b"jpg", media_type="image/jpeg")])])

    trimmed = forget_old_screenshots([screen(1), screen(2), screen(3)])

    images = [any(isinstance(item, BinaryContent) for item in m.parts[0].content) for m in trimmed]
    assert images == [False, True, True]
    assert trimmed[0].parts[0].content == ["screen 1", "(earlier screenshot)"]
