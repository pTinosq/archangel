"""Every prompt Archangel runs on, by name. The defaults live in the code beside their agents; edits made in the
control panel live in the store and win. An edit applies from the agent's next run, no restart.
"""

from collections.abc import Callable
from dataclasses import dataclass

from agent import orchestrator
from agent.phone import guard, investigator, pilot, vision
from agent.store import Store

DEFAULTS = {
    "orchestrator": orchestrator.INSTRUCTIONS,
    "pilot": pilot.INSTRUCTIONS,
    "guard": guard.INSTRUCTIONS,
    "investigator": investigator.INSTRUCTIONS,
    "locator": vision.LOCATOR_INSTRUCTIONS,
}


@dataclass(frozen=True)
class Prompt:
    name: str
    text: str
    default: str

    @property
    def edited(self) -> bool:
        return self.text != self.default


class Prompts:
    def __init__(self, store: Store):
        self._store = store
        self._edits = store.prompts()  # in memory: agents read prompts on every run

    def __getitem__(self, name: str) -> str:
        return self._edits.get(name, DEFAULTS[name])

    def live(self, name: str) -> Callable[[], str]:
        """For an agent's `instructions`: the prompt as it is when each run starts."""
        _known(name)  # at startup, not mid-run
        return lambda: self[name]

    def get(self, name: str) -> Prompt:
        return Prompt(name, self[name], DEFAULTS[name])

    def all(self) -> list[Prompt]:
        return [self.get(name) for name in DEFAULTS]

    def edit(self, name: str, text: str | None) -> None:
        """Replace a prompt; None (or the default itself) restores the default."""
        _known(name)
        if text is None or text == DEFAULTS[name]:
            self._edits.pop(name, None)
            self._store.save_prompt(name, None)
        else:
            self._edits[name] = text
            self._store.save_prompt(name, text)


def _known(name: str) -> None:
    if name not in DEFAULTS:
        raise KeyError(f"No prompt called {name!r}")
