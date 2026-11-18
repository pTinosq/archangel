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
from agent.devices import phone as phone_module
from agent.devices import pilot as pilot_module
from agent.devices.pc import PC
from agent.devices.phone import PHONE
from agent.devices.guard import ModelGuard
from agent.devices.pilot import Kit, Pilot
from agent.devices.relay import DeviceError
from agent.devices.vision import Box
from agent.runs import Answer, Outcome

SCREEN = Image(b"jpg", "image/jpeg")


@pytest.fixture(autouse=True)
def no_settling(monkeypatch):
    monkeypatch.setattr(pilot_module, "SETTLE_SECONDS", 0)
    monkeypatch.setattr(phone_module, "CLOSE_PAUSE_SECONDS", 0)


class FakePhone:
    label = "phone"

    def __init__(self):
        self.actions: list[str] = []

    async def screenshot(self) -> Image:
        return SCREEN

    async def tap(self, x: float, y: float, hold: bool = False) -> None:
        self.actions.append(f"tap {x:.2f},{y:.2f}")

    async def type(self, text: str) -> None:
        raise DeviceError("No focused input field")

    async def apps(self) -> dict[str, str]:
        return {"WhatsApp": "com.whatsapp"}

    async def launch(self, package: str) -> None:
        self.actions.append(f"launch {package}")

    async def swipe(self, *positions: float) -> None:
        self.actions.append("swipe")

    async def key(self, key: str) -> None:
        self.actions.append(f"key {key}")

    async def location(self, lat: float | None = None, lon: float | None = None) -> None:
        self.actions.append(f"location {lat},{lon}")


class FakePC(FakePhone):
    label = "PC"

    async def type(self, text: str) -> None:
        self.actions.append(f"type {text}")

    async def click(self, x: float, y: float, button: str = "left", double: bool = False) -> None:
        self.actions.append(f"click {x:.2f},{y:.2f} {button}{' double' if double else ''}")

    async def scroll(self, x: float, y: float, dx: int = 0, dy: int = 0) -> None:
        self.actions.append(f"scroll {x:.2f},{y:.2f} by {dx},{dy}")

    async def unlock(self) -> None:
        self.actions.append("unlock")

    async def powershell(self, script: str) -> dict:
        self.actions.append(f"powershell {script}")
        if "Start-Sleep" in script:
            return {"exitCode": None, "stdout": "", "stderr": ""}
        return {"exitCode": 0, "stdout": "x" * 5000 if "Get-Content" in script else "Notes.txt\n", "stderr": ""}


class FakeVision:
    async def find(self, screen: Image, target: str) -> Box | None:
        return Box(0.2, 0.4, 0.4, 0.6) if "button" in target else None


class FakeGuard:
    """Flags anything mentioning "Send", and deleting files."""

    def __init__(self):
        self.checked: list[str] = []
        self.screens: list[Image | None] = []

    async def check(self, screen: Image | None, action: str, task: str, said: list[str]) -> str | None:
        self.checked.append(action)
        self.screens.append(screen)
        return "send the message" if "Send" in action else "delete files" if "Remove-Item" in action else None


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
    platform = PC if isinstance(phone, FakePC) else PHONE
    return asyncio.run(Pilot(model, kit, platform).run("do it", record, **kwargs))


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
        "No app called 'Snapchat'. Nearest: WhatsApp.",
        "Opened whatsapp.",
    ]
    assert phone.actions == ["launch com.whatsapp", "key home"]


def test_an_app_is_found_by_part_of_its_name_when_only_one_has_it():
    phone = FakePhone()
    phone.apps = lambda: _apps({"Google Chrome": "chrome", "Chrome Remote Desktop": "remote", "Notepad": "notepad"})
    outcome = run(
        scripted(("open_app", {"name": "Chrome"}), ("open_app", {"name": "notepad"}), ("open_app", {"name": "Google"})),
        phone,
    )

    assert outcome.report.split(" | ")[0] == "No app called 'Chrome'. Nearest: Google Chrome, Chrome Remote Desktop, Notepad."
    assert phone.actions[:2] == ["launch notepad", "launch chrome"]


async def _apps(apps: dict[str, str]) -> dict[str, str]:
    return apps


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
    # As swiping it away in recents does, then home as the task ends.
    assert phone.actions == ["launch com.whatsapp", "key recents", "swipe", "key home", "key home"]


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


def test_a_faked_location_lasts_only_while_the_task_runs():
    phone = FakePhone()
    run(scripted(("set_location", {"lat": 51.5178, "lon": -0.0823})), phone)
    assert phone.actions == ["location 51.5178,-0.0823", "key home", "location None,None"]


def test_a_paused_task_gives_the_phone_back_where_it_is():
    phone = FakePhone()
    model = scripted(("set_location", {"lat": 51.5, "lon": -0.08}), ("tap", {"target": "the Send button"}))
    assert run(model, phone).status == "needs_approval"
    assert phone.actions == ["location 51.5,-0.08", "location None,None"]


def test_a_pc_clicks_and_never_goes_home():
    pc = FakePC()
    outcome = run(scripted(("click", {"target": "the Start button", "button": "right"})), pc)
    assert pc.actions == ["click 0.30,0.50 right"]
    assert outcome.report == "Right-clicked the Start button."


def test_a_consequential_click_waits_for_approval():
    pc, guard = FakePC(), FakeGuard()
    waiting = run(scripted(("click", {"target": "the Send button", "double": True})), pc, guard)
    assert waiting.status == "needs_approval" and pc.actions == []
    assert guard.checked == ["double-click the Send button"]


def test_moving_around_isnt_checked_but_other_keys_are():
    pc, guard = FakePC(), FakeGuard()
    run(
        scripted(("press", {"keys": "ctrl+L"}), ("press", {"keys": "Tab"}), ("press", {"keys": "ctrl+enter"})),
        pc,
        guard,
    )
    assert guard.checked == ["press ctrl+enter"]
    assert pc.actions == ["key ctrl+L", "key Tab", "key ctrl+enter"]


def test_scroll_over_a_described_area():
    pc = FakePC()
    run(
        scripted(
            ("scroll", {"direction": "down", "within": "the message button list"}), ("scroll", {"direction": "up"})
        ),
        pc,
    )
    assert pc.actions == ["scroll 0.30,0.50 by 0,5", "scroll 0.50,0.50 by 0,-5"]


def test_a_pc_it_unlocked_is_locked_again_when_the_task_ends_or_pauses():
    pc = FakePC()
    run(scripted(("unlock", {}), ("type_text", {"text": "hi"})), pc)
    assert pc.actions == ["unlock", "type hi", "key lock"]

    pc = FakePC()
    waiting = run(scripted(("unlock", {}), ("click", {"target": "the Send button"})), pc)
    assert waiting.status == "needs_approval"
    assert pc.actions == ["unlock", "key lock"]


def test_a_pc_task_that_cant_reach_it_says_which_device():
    class Asleep(FakePC):
        async def screenshot(self):
            raise DeviceError("The PC has been offline for 150 s")

    outcome = run(scripted(), Asleep())
    assert outcome == Outcome("failed", "Couldn't use the PC: The PC has been offline for 150 s")


def test_a_command_runs_once_checked_without_the_screen():
    pc, guard = FakePC(), FakeGuard()
    outcome = run(scripted(("run_command", {"command": "Get-ChildItem Documents"})), pc, guard)
    assert pc.actions == ["powershell Get-ChildItem Documents"]
    assert guard.checked == ["run this PowerShell command on the PC: Get-ChildItem Documents"]
    assert guard.screens == [None]
    assert outcome.report == "Exit code 0.\nOutput:\nNotes.txt"


def test_a_consequential_command_shows_the_owner_what_would_run():
    pc, guard = FakePC(), FakeGuard()
    model = scripted(("run_command", {"command": "Remove-Item Downloads\\old -Recurse"}))
    waiting = run(model, pc, guard)
    assert waiting.status == "needs_approval" and pc.actions == []
    assert waiting.report == "Approve: delete files, running `Remove-Item Downloads\\old -Recurse`?"

    run(model, pc, guard, paused=waiting.run, answer=Answer("yes", approved=True))
    assert pc.actions == ["powershell Remove-Item Downloads\\old -Recurse"]


def test_a_commands_long_output_is_cut_and_a_slow_one_says_it_was_stopped():
    outcome = run(
        scripted(("run_command", {"command": "Get-Content big.log"}), ("run_command", {"command": "Start-Sleep 60"})),
        FakePC(),
    )
    long, slow = outcome.report.split(" | ")
    assert long.endswith("\n… (1000 more characters)") and len(long) < 4100
    assert slow == "Stopped: still running after the time limit."


def test_the_guard_judges_a_command_without_a_screen():
    seen = []

    def judge(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen.extend(part.content for part in messages[0].parts if isinstance(part, UserPromptPart))
        return ModelResponse(parts=[ToolCallPart("final_result", {"needs_approval": False, "action": "list files"})])

    guard = ModelGuard(FunctionModel(judge))
    assert asyncio.run(guard.check(None, "run this PowerShell command on the PC: ls", "tidy up", [])) is None
    [prompt] = seen
    assert not any(isinstance(part, BinaryContent) for part in prompt)
