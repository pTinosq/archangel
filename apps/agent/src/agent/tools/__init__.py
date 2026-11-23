"""Quick tools (M15): live data the orchestrator answers from inline, in seconds, with no device and no browsing: TfL,
the weather, exchange rates, the time and dates. Each is a `Tool` listed in `BUILT_IN`; the owner switches them on and
off, and sets what they need, on the Connectors page. A tool that's off, or lacks a setting it requires, isn't there
for the orchestrator: it neither sees its functions nor is told of it.

The orchestrator's prompt says to prefer them: a tool that answers beats a web search, and a web search beats a
device. To add one, subclass `Tool` and list it here.
"""

import json
import logging
from collections.abc import Sequence
from typing import Any

import httpx
from pydantic_ai import RunContext
from pydantic_ai.toolsets import FunctionToolset, ToolsetTool, WrapperToolset

from agent import activity
from agent.store import Store
from agent.tools.currency import Currency
from agent.tools.dates import Dates
from agent.tools.email import Email
from agent.tools.tfl import TfL
from agent.tools.tool import Setting, Tool, ToolError
from agent.tools.weather import Weather

log = logging.getLogger(__name__)

BUILT_IN: Sequence[type[Tool]] = (TfL, Weather, Currency, Dates, Email)
TIMEOUT = 15  # seconds: a quick tool that is slow is no quicker than the web


class Tools:
    def __init__(self, store: Store, client: httpx.AsyncClient | None = None, kinds: Sequence[type[Tool]] = BUILT_IN):
        self._store = store
        client = client or httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True)
        self._tools = {kind.name: kind(client, lambda name=kind.name: self._values(name)) for kind in kinds}
        self._owner: dict[str, Tool] = {}  # each function's tool
        self._functions = FunctionToolset()
        for tool in self._tools.values():
            for function in tool.functions():
                if function.__name__ in self._owner:
                    raise ValueError(f"Two quick tools are called {function.__name__}")
                self._owner[function.__name__] = tool
                self._functions.add_function(function)
        self.toolset = _Quick(self._functions, self)

    def all(self) -> list[dict]:
        """Each tool for the panel: what it is, whether it's on, and its settings (a secret's value never leaves)."""
        return [
            {
                "name": tool.name,
                "title": tool.title,
                "about": tool.about,
                "enabled": self._enabled(tool),
                "active": self.active(tool),
                "settings": [
                    {
                        "key": s.key,
                        "label": s.label,
                        "secret": s.secret,
                        "required": s.required,
                        "items": s.items,
                        "set": bool(self._values(tool.name).get(s.key)),
                        "value": "" if s.secret or s.items else tool.setting(s.key),
                        "list": tool.items(s.key) if s.items else [],
                    }
                    for s in tool.settings
                ],
            }
            for tool in self._tools.values()
        ]

    def configure(
        self, name: str, enabled: bool | None = None, settings: dict[str, str | list[str]] | None = None
    ) -> None:
        """Switch a tool on or off and/or change its settings (only those given; an empty value clears one; a list
        setting is given whole)."""
        tool = self._get(name)
        known = {s.key: s for s in tool.settings}
        if unknown := set(settings or {}) - set(known):
            raise ValueError(f"{tool.title} has no setting {', '.join(sorted(unknown))}")
        given = {}
        for key, value in (settings or {}).items():
            if known[key].items != isinstance(value, list):
                raise ValueError(f"{known[key].label} is {'a list' if known[key].items else 'one value'}")
            given[key] = _tidy(value)
        state = self._store.tools().get(name, {})
        values = {**state.get("settings", {}), **given}
        state = {
            "enabled": self._enabled(tool) if enabled is None else enabled,
            "settings": {k: v for k, v in values.items() if v},
        }
        self._store.save_tool(name, state)

    def items(self, name: str, key: str) -> list[str]:
        """A list setting of a tool that's on (an off tool has nothing: it reads and allows nothing)."""
        tool = self._get(name)
        return tool.items(key) if self._enabled(tool) else []

    def active(self, tool: Tool) -> bool:
        """On, and set up: what the orchestrator gets."""
        values = self._values(tool.name)
        return self._enabled(tool) and all(values.get(s.key) for s in tool.settings if s.required)

    def describe(self) -> str:
        """What's there to answer from, for the orchestrator: empty if nothing is."""
        answering = {id(tool) for tool in self._owner.values()}  # a tool with no functions answers nothing
        return "; ".join(
            f"{tool.title} ({tool.about})"
            for tool in self._tools.values()
            if self.active(tool) and id(tool) in answering
        )

    def _enabled(self, tool: Tool) -> bool:
        return self._store.tools().get(tool.name, {}).get("enabled", True)

    def _values(self, name: str) -> dict[str, str | list[str]]:
        return self._store.tools().get(name, {}).get("settings", {})

    def _get(self, name: str) -> Tool:
        if name not in self._tools:
            raise KeyError(f"No quick tool called {name!r}")
        return self._tools[name]


def _tidy(value: str | list[str]) -> str | list[str]:
    if isinstance(value, str):
        return value.strip()
    kept = (item.strip() for item in value)
    return list(dict.fromkeys(item for item in kept if item))  # no blanks or repeats, in the owner's order


class _Quick(WrapperToolset):
    """The quick tools that are active now, each call shown in the cockpit as a request to its tool. A source that
    fails is an answer, not an error: the conversation goes on, and the orchestrator may try the web."""

    def __init__(self, wrapped: FunctionToolset, tools: Tools):
        super().__init__(wrapped)
        self._tools = tools

    async def get_tools(self, ctx: RunContext) -> dict[str, ToolsetTool]:
        return {
            name: tool
            for name, tool in (await super().get_tools(ctx)).items()
            if self._tools.active(self._tools._owner[name])
        }

    async def call_tool(self, name: str, tool_args: dict[str, Any], ctx: RunContext, tool: ToolsetTool) -> Any:
        source = self._tools._owner[name]
        call = f"{name} {json.dumps(tool_args, ensure_ascii=False)}"
        with activity.call(source.name, "service", call) as reply:
            try:
                answer = await super().call_tool(name, tool_args, ctx, tool)
            except ToolError as error:
                answer = str(error)
            except Exception as error:  # the source: down, or answering something unexpected
                log.exception("%s failed", source.title)
                answer = f"{source.title} isn't answering ({type(error).__name__}): try the web instead."
            reply(answer)
        return answer


__all__ = ["BUILT_IN", "Setting", "Tool", "ToolError", "Tools"]
