"""A thread's history as plain lines: what the router reads of an unfinished thread, what the summariser condenses,
what search finds and `read_thread` shows. The model's own bookkeeping (its tool calls) becomes a short note."""

from pydantic_ai import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

RESULT_SHOWN = 300
"""A tool's result is clipped: a web page read in full would drown the conversation around it."""


def lines(messages: list[ModelMessage]) -> list[str]:
    """Oldest first. What arrived is shown as the orchestrator saw it ("(via sms) …", "[Update on task #4 …] …")."""
    found: list[str] = []
    for message in messages:
        if isinstance(message, ModelRequest):
            for part in message.parts:
                if isinstance(part, UserPromptPart):
                    found.append(_content(part.content))
                elif isinstance(part, ToolReturnPart):
                    found.append(f"  → {_clip(part.model_response_str())}")
        elif isinstance(message, ModelResponse):
            for part in message.parts:
                if isinstance(part, TextPart) and part.content.strip():
                    found.append(f"Archangel: {part.content.strip()}")
                elif isinstance(part, ToolCallPart):
                    found.append(f"  (Archangel used {part.tool_name} {_clip(part.args_as_json_str())})")
    return found


def _content(content: object) -> str:
    if isinstance(content, str):
        return content
    return " ".join(item if isinstance(item, str) else "[image]" for item in content)  # text and pictures


def _clip(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= RESULT_SHOWN else text[: RESULT_SHOWN - 1] + "…"


def ago(then: float, now: float) -> str:
    """ "just now", "12 min ago", "3 h 5 min ago", "8 days ago"."""
    minutes = round((now - then) / 60)
    if minutes < 1:
        return "just now"
    if minutes < 60:
        return f"{minutes} min ago"
    if minutes < 24 * 60:
        return f"{minutes // 60} h {minutes % 60} min ago"
    days = minutes // (24 * 60)
    return "1 day ago" if days == 1 else f"{days} days ago"
