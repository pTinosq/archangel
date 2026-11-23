"""What a quick tool is: a few functions the orchestrator calls, each answering from one live source in seconds."""

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, ClassVar

import httpx

Config = Callable[[], Mapping[str, str | list[str]]]
"""A tool's settings as the owner has them now (read at each call: they change from the panel while it runs)."""


class ToolError(Exception):
    """Something the model should be told plainly and can act on ("no such line", "no place called that"): the
    message is its answer. Anything else going wrong is a source being down, reported without the details."""


@dataclass(frozen=True)
class Setting:
    """One thing the owner may enter on the Connectors page for a tool."""

    key: str
    label: str
    secret: bool = False
    required: bool = False
    """The tool is off until it's set; otherwise it works without."""
    items: bool = False
    """A list of strings (addresses, say) the owner adds to and removes from, not one value."""


class Tool(ABC):
    """A source of live answers. Subclass it, list it in `BUILT_IN` (`agent.tools`), and it shows on the Connectors
    page, switched on or off by the owner, with its settings; the orchestrator gets its functions while it's on.

    The functions are async and return text, the answer in the words the orchestrator will pass on. Their names are
    the orchestrator's tool names (so unique across tools), their docstrings its description of them: write them as
    for someone who must pick the right tool, saying what each is for and what it is not.
    """

    name: ClassVar[str]
    """Short and lowercase; what the owner's settings are kept under."""
    title: ClassVar[str]
    about: ClassVar[str]
    """What it answers, in a line: the owner reads it on the panel, and the orchestrator is told it every turn to
    choose between this and the web or a device."""
    settings: ClassVar[tuple[Setting, ...]] = ()

    def __init__(self, http: httpx.AsyncClient, config: Config):
        self._http, self._config = http, config

    @abstractmethod
    def functions(self) -> list[Callable[..., Awaitable[str]]]:
        """The orchestrator's tools."""

    def setting(self, key: str) -> str:
        value = self._config().get(key, "")
        return value if isinstance(value, str) else ""

    def items(self, key: str) -> list[str]:
        """A list setting, in the order the owner keeps it."""
        value = self._config().get(key, [])
        return list(value) if isinstance(value, list) else []

    async def get(self, url: str, *, accept: tuple[int, ...] = (), **params: Any) -> Any:
        """The JSON at `url`; a source that errors raises, which the caller turns into an answer. `accept`: statuses
        besides success that carry an answer (a 300 listing the places a name could mean)."""
        response = await self._http.get(url, params={k: v for k, v in params.items() if v is not None})
        if response.status_code not in accept:
            response.raise_for_status()
        return response.json()
