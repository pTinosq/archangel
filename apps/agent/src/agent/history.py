"""Keeping model context small: long histories and old screenshots cost tokens and add little."""

from dataclasses import replace

from pydantic_ai import BinaryContent, ModelMessage, ModelRequest, UserPromptPart

KEPT_MESSAGES = 100
"""A backstop: a conversation thread is carried on in a new one at its size in tokens (threads.FULL), usually long
before this many messages."""
KEPT_SCREENSHOTS = 2


def recent(messages: list[ModelMessage], keep: int = KEPT_MESSAGES) -> list[ModelMessage]:
    """The last turns that fit in `keep` messages, cut where a turn starts so tool calls keep their results."""
    if len(messages) <= keep:
        return messages
    for start in range(len(messages) - keep, len(messages)):
        if _starts_turn(messages[start]):
            return messages[start:]
    return messages[-1:]


def forget_old_screenshots(messages: list[ModelMessage], keep: int = KEPT_SCREENSHOTS) -> list[ModelMessage]:
    """Keep only the latest `keep` images."""
    kept = 0
    trimmed: list[ModelMessage] = []
    for message in reversed(messages):
        if isinstance(message, ModelRequest):
            parts = []
            for part in message.parts:
                if isinstance(part, UserPromptPart) and _has_image(part):
                    kept += 1
                    if kept > keep:
                        part = replace(part, content=[_drop_image(item) for item in part.content])
                parts.append(part)
            message = replace(message, parts=parts)
        trimmed.append(message)
    return trimmed[::-1]


def _starts_turn(message: ModelMessage) -> bool:
    return isinstance(message, ModelRequest) and any(isinstance(part, UserPromptPart) for part in message.parts)


def _has_image(part: UserPromptPart) -> bool:
    return not isinstance(part.content, str) and any(isinstance(item, BinaryContent) for item in part.content)


def _drop_image(item):
    return "(earlier screenshot)" if isinstance(item, BinaryContent) else item
