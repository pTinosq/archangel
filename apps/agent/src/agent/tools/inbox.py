"""Who Archangel reads email from (M21). Not a source of answers: a quick tool only so the owner finds its setting
beside the others on the Connectors page. The email channel reads it at each email, so a change applies at once."""

import re
from collections.abc import Awaitable, Callable

from agent.tools.tool import Setting, Tool

ADDRESS = re.compile(r"[^\s,;<>()\[\]\"']+@[^\s,;<>()\[\]\"']+")


class Inbox(Tool):
    name = "inbox"
    title = "Email senders"
    about = "the addresses Archangel reads email from; mail from any other sender is dropped unread"
    settings = (Setting("senders", "Addresses to read email from, separated by commas or new lines"),)

    def functions(self) -> list[Callable[..., Awaitable[str]]]:
        return []


def addresses(raw: str) -> frozenset[str]:
    """The addresses in a setting's text, lower case. Empty means no one: nothing is read."""
    return frozenset(found.lower().rstrip(".") for found in ADDRESS.findall(raw))
