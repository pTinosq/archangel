"""What flows between channels and the brain, independent of any medium or model."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Conversation:
    channel: str
    id: str


@dataclass(frozen=True)
class Text:
    text: str


@dataclass(frozen=True)
class Image:
    data: bytes
    media_type: str


Part = Text | Image


@dataclass(frozen=True)
class Message:
    conversation: Conversation
    parts: tuple[Part, ...]

    @property
    def text(self) -> str:
        return "\n".join(part.text for part in self.parts if isinstance(part, Text))
