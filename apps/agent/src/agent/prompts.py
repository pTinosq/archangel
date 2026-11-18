"""Every prompt Archangel runs on, by name. The defaults live in the code beside their agents; edits made in the
control panel live in the store and win. An edit applies from the agent's next run, no restart.

The writing styles are prompts too ("style discord"), added by `Styles`: their files are the defaults and say who
each is for; only the text is edited here.
"""

from collections.abc import Callable
from dataclasses import dataclass

from agent import helper, orchestrator, reports
from agent.devices import guard, investigator, pc, phone, vision
from agent.store import Store
from agent.threads import router, summariser
from agent.twin import reader, writer

DEFAULTS = {
    "orchestrator": orchestrator.INSTRUCTIONS,
    "phone pilot": phone.INSTRUCTIONS,
    "pc pilot": pc.INSTRUCTIONS,
    "guard": guard.INSTRUCTIONS,
    "investigator": investigator.INSTRUCTIONS,
    "vision": vision.INSTRUCTIONS,
    "reporter": reports.INSTRUCTIONS,
    "helper": helper.INSTRUCTIONS,
    "twin reader": reader.INSTRUCTIONS,
    "twin writer": writer.INSTRUCTIONS,
    "thread router": router.INSTRUCTIONS,
    "thread summariser": summariser.INSTRUCTIONS,
}


@dataclass(frozen=True)
class Prompt:
    name: str
    text: str
    default: str
    about: str = ""
    """Who it's for, for the prompts that aren't an agent's (the styles)."""

    @property
    def edited(self) -> bool:
        return self.text != self.default


class Prompts:
    def __init__(self, store: Store):
        self._store = store
        self._defaults = dict(DEFAULTS)
        self._about: dict[str, str] = {}
        self._edits = store.prompts()  # in memory: agents read prompts on every run
        if "pilot" in self._edits:  # its name before there was a PC
            self.edit("phone pilot", self._edits.pop("pilot"))
            store.save_prompt("pilot", None)

    def __getitem__(self, name: str) -> str:
        return self._edits.get(name, self._defaults[name])

    def add(self, name: str, default: str, about: str = "") -> None:
        """A prompt whose default isn't in the code above: a style, from its file."""
        self._defaults[name] = default
        self._about[name] = about

    def live(self, name: str) -> Callable[[], str]:
        """For an agent's `instructions`: the prompt as it is when each run starts."""
        self._known(name)  # at startup, not mid-run
        return lambda: self[name]

    def get(self, name: str) -> Prompt:
        self._known(name)
        return Prompt(name, self[name], self._defaults[name], self._about.get(name, ""))

    def all(self) -> list[Prompt]:
        return [self.get(name) for name in self._defaults]

    def edit(self, name: str, text: str | None) -> None:
        """Replace a prompt; None (or the default itself) restores the default."""
        self._known(name)
        if text is None or text == self._defaults[name]:
            self._edits.pop(name, None)
            self._store.save_prompt(name, None)
        else:
            self._edits[name] = text
            self._store.save_prompt(name, text)

    def _known(self, name: str) -> None:
        if name not in self._defaults:
            raise KeyError(f"No prompt called {name!r}")
