"""What flows between the owner and Archangel, independent of any medium or model."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Address:
    """Where a message comes from or goes: a channel, and who on it (a chat id, a phone number, an email)."""

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
    address: Address
    parts: tuple[Part, ...]
    answering: bool = False
    """A reply to what the owner just said, not news: a channel they've since left (a call) may drop it."""
    ends: bool = False
    """The last word: a call hangs up once it's said."""

    @property
    def text(self) -> str:
        return "\n".join(part.text for part in self.parts if isinstance(part, Text))

    @property
    def images(self) -> list[Image]:
        return [part for part in self.parts if isinstance(part, Image)]
