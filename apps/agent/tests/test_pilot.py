import asyncio

import pytest
from pydantic_ai import (
    BinaryContent,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from agent.history import forget_old_screenshots
from agent.messages import Image
from agent.phone import pilot as pilot_module
from agent.phone.pilot import Kit, Pilot
from agent.phone.relay import PhoneError
from agent.phone.vision import Box
from agent.runs import Answer, Outcome

SCREEN = Image(b"jpg", "image/jpeg")


@pytest.fixture(autouse=True)
def no_settling(monkeypatch):
    monkeypatch.setattr(pilot_module, "SETTLE_SECONDS", 0)


class FakePhone:
    def __init__(self):
        self.actions: list[str] = []

    async def screenshot(self) -> Image:
        return SCREEN

    async def tap(self, x: float, y: float, hold: bool = False) -> None:
        self.actions.append(f"tap {x:.2f},{y:.2f}")

    async def type(self, text: str) -> None:
        raise PhoneError("No focused input field")

    async def apps(self) -> dict[str, str]:
        return {"WhatsApp": "com.whatsapp"}

    async def launch(self, package: str) -> None:
        self.actions.append(f"launch {package}")

    async def swipe(self, *positions: float) -> None:
        self.actions.append("swipe")

    async def key(self, key: str) -> None:
        self.actions.append(f"key {key}")

    async def close(self, package: str) -> None:
        self.actions.append(f"close {package}")


class FakeVision:
    async def find(self, screen: Image, target: str) -> Box | None:
        return Box(0.2, 0.4, 0.4, 0.6) if "button" in target else None


class FakeGuard:
    """Flags anything mentioning "Send"."""

    def __init__(self):
        self.checked: list[str] = []

    async def check(self, screen: Image, action: str, task: str, said: list[str]) -> str | None:
        self.checked.append(action)
        return "send the message" if "Send" in action else None


class FakeInvestigator:
    def __init__(self, advice: str | None = None):
        self.advice = advice

    async def investigate(self, goal: str, steps: list[str], screens: list[Image]) -> str | None:
        return self.advice


def scripted(*calls: tuple[str, dict]):
    """A model that makes `calls` in order, then finishes, reporting every tool result it saw."""
    seen: list[str] = []

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        for part in messages[-1].parts:
            if isinstance(part, ToolReturnPart):
                seen.append(str(part.content))
        step = sum(isinstance(m, ModelResponse) for m in messages)
        if step < len(calls):
            return ModelResponse(parts=[ToolCallPart(*calls[step])])
        return ModelResponse(parts=[ToolCallPart("done", {"report": " | ".join(seen)})])

    return FunctionModel(respond)


def run(
    model, phone: FakePhone, guard: FakeGuard | None = None, investigator: FakeInvestigator | None = None, **kwargs
) -> Outcome:
    kit = Kit(phone, FakeVision(), guard or FakeGuard(), investigator or FakeInvestigator())
    record = kwargs.pop("record", lambda text, screen: None)
    return asyncio.run(Pilot(model, kit).run("do it", record, **kwargs))


def test_taps_the_centre_of_the_located_box():
    phone = FakePhone()
    outcome = run(scripted(("tap", {"target": "the WhatsApp button"})), phone)

    assert phone.actions == ["tap 0.30,0.50", "key home"]
    assert outcome == Outcome("done", "Tapped the WhatsApp button.", screen=SCREEN)


def test_problems_are_reported_back_to_the_model():
    phone = FakePhone()
    outcome = run(
        scripted(
            ("tap", {"target": "a unicorn"}),
            ("type_text", {"text": "hi"}),
            ("open_app", {"name": "Snapchat"}),
            ("open_app", {"name": "whatsapp"}),
        ),
        phone,
    )

    assert outcome.report.split(" | ") == [
        "Couldn't find a unicorn on the screen.",
        "Failed: No focused input field",
        "No app called 'Snapchat'. Installed: WhatsApp.",
        "Opened whatsapp.",
    ]
    assert phone.actions == ["launch com.whatsapp", "key home"]


def test_the_model_can_give_up():
    model = FunctionModel(
        lambda messages, info: ModelResponse(parts=[ToolCallPart("give_up", {"reason": "No signal"})])
    )
    assert run(model, FakePhone()).status == "gave_up"


def test_gives_up_after_the_step_limit(monkeypatch):
    monkeypatch.setattr(pilot_module, "MAX_STEPS", 3)
    forever = FunctionModel(lambda messages, info: ModelResponse(parts=[ToolCallPart("look", {})]))
    assert run(forever, FakePhone()).report == "Gave up after 3 steps without finishing."


def test_unexpected_failures_are_reported_not_raised():
    invalid = FunctionModel(
        lambda messages, info: ModelResponse(parts=[ToolCallPart("scroll", {"direction": "sideways"})])
    )
    outcome = run(invalid, FakePhone())
    assert outcome.status == "failed"
    assert outcome.report.startswith("Failed unexpectedly (UnexpectedModelBehavior")


def test_consequential_taps_wait_for_approval_then_tap_once():
    phone, guard = FakePhone(), FakeGuard()
    model = scripted(("tap", {"target": "the Send button"}))

    waiting = run(model, phone, guard)
    assert waiting.status == "needs_approval"
    assert waiting.report == "Approve: send the message?"
    assert phone.actions == []

    resumed = run(model, phone, guard, paused=waiting.run, answer=Answer("yes", approved=True))
    assert phone.actions == ["tap 0.30,0.50", "key home"]
    assert resumed.status == "done"
    assert guard.checked == ["tap the Send button"]  # approved calls aren't re-checked


def test_a_declined_tap_never_happens():
    phone = FakePhone()
    model = scripted(("tap", {"target": "the Send button"}))
    waiting = run(model, phone)

    resumed = run(model, phone, paused=waiting.run, answer=Answer("not yet"))

    assert phone.actions == ["key home"]  # the declined tap never happened
    assert resumed.report == "The owner said no: not yet"


def test_questions_round_trip_to_the_owner():
    model = scripted(("ask_owner", {"question": "Which Alex?"}))
    waiting = run(model, FakePhone())
    assert (waiting.status, waiting.report) == ("needs_answer", "Which Alex?")

    resumed = run(model, FakePhone(), paused=waiting.run, answer=Answer("Alex Smith"))
    assert resumed.report == "The owner replied: Alex Smith"


def test_only_recent_screenshots_stay_in_context():
    def screen(n: int) -> ModelRequest:
        return ModelRequest(parts=[UserPromptPart([f"screen {n}", BinaryContent(b"jpg", media_type="image/jpeg")])])

    trimmed = forget_old_screenshots([screen(1), screen(2), screen(3)])

    images = [any(isinstance(item, BinaryContent) for item in m.parts[0].content) for m in trimmed]
    assert images == [False, True, True]
    assert trimmed[0].parts[0].content == ["screen 1", "(earlier screenshot)"]


def test_every_step_is_recorded_with_its_screen():
    steps = []
    record = lambda text, screen: steps.append((text, screen is not None))  # noqa: E731
    run(
        scripted(("tap", {"target": "the WhatsApp button"}), ("tap", {"target": "a unicorn"})),
        FakePhone(),
        record=record,
    )
    assert steps == [
        ("Started.", True),
        ("Tapped the WhatsApp button.", True),
        ("Couldn't find a unicorn on the screen.", False),
    ]


def test_done_goes_home_unless_asked_to_leave_it_open():
    phone = FakePhone()
    model = FunctionModel(
        lambda m, i: ModelResponse(parts=[ToolCallPart("done", {"report": "Open.", "leave_open": True})])
    )
    assert run(model, phone).status == "done" and phone.actions == []


def test_close_app_closes_it_by_name():
    phone = FakePhone()
    run(scripted(("close_app", {"name": "whatsapp"})), phone)
    assert phone.actions == ["close com.whatsapp", "key home"]


def test_unlock_swipes_instead_of_pressing_a_key():
    phone = FakePhone()
    run(scripted(("press", {"key": "unlock"})), phone)
    assert phone.actions == ["swipe", "key home"]


def test_a_second_opinion_when_the_pilot_asks():
    investigator = FakeInvestigator("Close the dialog first.")
    outcome = run(
        scripted(("second_opinion", {"question": "Why won't it close?"})), FakePhone(), investigator=investigator
    )
    assert outcome.report == "Close the dialog first."


def test_the_owner_s_word_reaches_a_running_pilot():
    inbox = ["You can tap Continue"]
    outcome = run(scripted(("press", {"key": "back"})), FakePhone(), inbox=inbox)
    assert "The owner says, meanwhile: You can tap Continue" in outcome.report
    assert inbox == []
