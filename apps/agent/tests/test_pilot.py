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
from agent.phone.pilot import Answer, Kit, Outcome, Pilot
from agent.phone.relay import PhoneError
from agent.phone.vision import Box

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


class FakeLocator:
    async def locate(self, screen: Image, target: str) -> Box | None:
        return Box(0.2, 0.4, 0.4, 0.6) if "button" in target else None


class FakeGuard:
    """Flags anything mentioning "Send"."""

    def __init__(self):
        self.checked: list[str] = []

    async def check(self, screen: Image, target: str, task: str) -> str | None:
        self.checked.append(target)
        return "send the message" if "Send" in target else None


class FakeInvestigator:
    def __init__(self, advice: str | None = "A dialog is covering it; close it first."):
        self.advice = advice
        self.asked: list[list[str]] = []

    async def investigate(self, goal: str, steps: list[str], screens: list[Image]) -> str | None:
        self.asked.append(steps)
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
    kit = Kit(phone, FakeLocator(), guard or FakeGuard(), investigator or FakeInvestigator(None))
    record = kwargs.pop("record", lambda text, screen: None)
    return asyncio.run(Pilot(model, kit).run("do it", record, **kwargs))


def test_taps_the_centre_of_the_located_box():
    phone = FakePhone()
    outcome = run(scripted(("tap", {"target": "the WhatsApp button"})), phone)

    assert phone.actions == ["tap 0.30,0.50"]
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
    assert phone.actions == ["launch com.whatsapp"]


def test_a_repeated_failure_gets_a_nudge():
    outcome = run(scripted(("type_text", {"text": "hi"}), ("type_text", {"text": "hi"})), FakePhone())
    assert "failed the same way twice" in outcome.report.split(" | ")[1]


def test_a_repeated_failure_gets_a_second_opinion():
    outcome = run(
        scripted(("type_text", {"text": "hi"}), ("type_text", {"text": "hi"})),
        FakePhone(),
        investigator=FakeInvestigator(),
    )
    assert outcome.report.split(" | ")[1].endswith(
        "Second opinion, from a model that looked at the recent screens: A dialog is covering it; close it first."
    )


def test_going_in_circles_gets_a_second_opinion():
    investigator = FakeInvestigator()
    outcome = run(scripted(*[("tap", {"target": "the Post button"})] * 3), FakePhone(), investigator=investigator)

    results = outcome.report.split(" | ")
    assert results[:2] == ["Tapped the Post button."] * 2
    assert "Second opinion" in results[2]
    assert investigator.asked == [["Tapped the Post button."] * 3]


def test_second_opinions_are_limited():
    investigator = FakeInvestigator()
    run(scripted(*[("look", {})] * 12), FakePhone(), investigator=investigator)
    assert len(investigator.asked) == 2


def test_scrolling_through_a_list_is_not_a_loop():
    investigator = FakeInvestigator()
    run(scripted(*[("scroll", {"direction": "down"})] * 4), FakePhone(), investigator=investigator)
    assert investigator.asked == []


def test_consecutive_failures_end_the_task_as_stuck():
    outcome = run(scripted(*[("type_text", {"text": "hi"})] * 5), FakePhone(), investigator=FakeInvestigator())
    assert outcome.status == "stuck"
    assert "5 actions in a row failed" in outcome.report
    assert outcome.report.endswith("What seems to be in the way: A dialog is covering it; close it first.")


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
    assert phone.actions == ["tap 0.30,0.50"]
    assert resumed.status == "done"
    assert guard.checked == ["tap the Send button"]  # approved calls aren't re-checked


def test_a_declined_tap_never_happens():
    phone = FakePhone()
    model = scripted(("tap", {"target": "the Send button"}))
    waiting = run(model, phone)

    resumed = run(model, phone, paused=waiting.run, answer=Answer("not yet"))

    assert phone.actions == []
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
