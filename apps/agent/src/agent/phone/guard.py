"""Code-level safety: before any tap, a separate model judges whether it commits something the owner must approve.

Kept apart from the pilot so a pilot that misreads its task, or is talked into something by what's on screen,
still can't send, pay or log in on its own.
"""

import logging
from typing import Protocol

from pydantic import BaseModel
from pydantic_ai import Agent, BinaryContent
from pydantic_ai.models import Model

from agent.phone.relay import Screenshot

log = logging.getLogger(__name__)

INSTRUCTIONS = """\
You check one tap on an Android phone before it happens. Decide whether tapping the target would \
commit something the phone's owner must approve first:
- sending or posting anything (messages, emails, posts, comments, reviews, ratings, tips)
- paying, purchasing, booking, ordering or subscribing
- logging in, signing up, or granting access or permissions
- deleting, archiving or unsending anything
- changing a setting (toggles, preferences, account details)

Navigation, opening things, scrolling, searching, selecting text fields and drafting text don't need approval.
Judge by what the tap does on this screen, not by the task. When unsure, require approval.\
"""


class Verdict(BaseModel):
    needs_approval: bool
    action: str
    """What the tap would do, phrased for the owner, e.g. "post the tweet 'hello world' publicly"."""


class Guard(Protocol):
    async def check(self, screen: Screenshot, target: str, task: str) -> str | None:
        """What the tap would commit, if the owner must approve it first; otherwise None."""
        ...


class ModelGuard:
    def __init__(self, model: Model):
        self._agent = Agent(model, instructions=INSTRUCTIONS, output_type=Verdict)

    async def check(self, screen: Screenshot, target: str, task: str) -> str | None:
        prompt = [
            f"Task: {task}\nAbout to tap: {target}",
            BinaryContent(data=screen.jpeg, media_type="image/jpeg"),
        ]
        try:
            verdict = (await self._agent.run(prompt)).output
        except Exception as error:
            # Fail closed: an unchecked tap might be the one that pays.
            log.warning("guard failed, asking the owner instead: %s", error)
            return f"tap {target} (the safety check failed, so I can't tell what it does)"
        log.debug("guard %r: %s", target, verdict)
        return verdict.action if verdict.needs_approval else None
