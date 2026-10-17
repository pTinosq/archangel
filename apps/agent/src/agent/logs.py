"""Per-run logs for investigating what the agent did.

Each run writes logs/<start time>/agent.log (logs/latest points at the newest): every message, model tool call,
tool result and error. Screenshots are with each task's steps in the store, shown in the control panel.
The console only shows a one-line summary of phone actions and errors.
"""

import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic_ai import Agent, AgentRunResult, RetryPromptPart, TextPart, ToolCallPart, ToolReturnPart

LOGS = Path("logs")
KEPT_RUNS = 20
MAX_LOGGED_CHARS = 2000

log = logging.getLogger("agent")


def start() -> Path:
    """Start a run's log folder and send all `agent` logging there."""
    run = LOGS / datetime.now().strftime("%Y%m%d-%H%M%S")
    run.mkdir(parents=True, exist_ok=True)
    latest = LOGS / "latest"
    latest.unlink(missing_ok=True)
    latest.symlink_to(run.name)
    for old in sorted(p for p in LOGS.iterdir() if p.is_dir() and not p.is_symlink())[:-KEPT_RUNS]:
        shutil.rmtree(old)

    file = logging.FileHandler(run / "agent.log")
    file.setLevel(logging.DEBUG)
    file.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(_Brief())
    log.setLevel(logging.DEBUG)
    log.addHandler(file)
    log.addHandler(console)
    return run


async def run(agent: Agent, prompt: Any, **kwargs: Any) -> AgentRunResult:
    """`agent.run`, logging each step as it happens: tool calls, results, and calls the model got wrong."""
    async with agent.iter(prompt, **kwargs) as steps:
        async for node in steps:
            if Agent.is_call_tools_node(node):
                for part in node.model_response.parts:
                    if isinstance(part, ToolCallPart):
                        log.debug("call %s %s", part.tool_name, _clip(part.args))
                    elif isinstance(part, TextPart):
                        log.debug("text %s", _clip(part.content))
            elif Agent.is_model_request_node(node):
                for part in node.request.parts:
                    if isinstance(part, RetryPromptPart):
                        log.warning("invalid call %s: %s", part.tool_name, _clip(part.model_response()))
                    elif isinstance(part, ToolReturnPart):
                        log.debug("result %s: %s", part.tool_name, _clip(part.content))
    return steps.result


def _clip(value: object) -> str:
    text = str(value)
    return text if len(text) <= MAX_LOGGED_CHARS else text[:MAX_LOGGED_CHARS] + "…"


class _Brief(logging.Formatter):
    """One line per record, no tracebacks: details live in the log file."""

    def format(self, record: logging.LogRecord) -> str:
        message = record.getMessage()
        if record.levelno >= logging.ERROR:
            message += " (details: apps/agent/logs/latest/agent.log)"
        return f"  · {message}"
