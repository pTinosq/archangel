"""Code-level safety: before any tap, a separate model judges whether it commits something the owner must approve.

Kept apart from the pilot so a pilot that misreads its task, or is talked into something by what's on screen,
still can't send, pay or log in on its own.
"""

import logging
from typing import Protocol

from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.models import Model

from agent import logs
from agent.messages import Image
from agent.models import Instructions, content

log = logging.getLogger(__name__)

INSTRUCTIONS = """\
You check one tap on an Android phone before it happens. Decide whether tapping the target would \
commit something the phone's owner must approve first:
- sending or posting anything (messages, emails, posts, comments, reviews, ratings, tips)
- paying, purchasing, booking, ordering or subscribing
- logging in, signing up, or granting access or permissions
- deleting, archiving or unsending anything
- changing a setting (the app's or phone's own: toggles, preferences, account details)

Navigation doesn't need approval: opening apps, menus or settings pages (a settings row usually opens a page; \
only its switch changes anything), scrolling, searching, filtering or sorting results, focusing text fields and \
drafting text.
Judge by what the tap does on this screen, not by the task. But what the owner has said they agree to (below the \
task) doesn't need asking again. When unsure, require approval.\
"""


class Verdict(BaseModel):
    needs_approval: bool
    action: str = Field(description='What the tap would do, a few words for the owner, e.g. "post the tweet"')


class Guard(Protocol):
    async def check(self, screen: Image, target: str, task: str, said: list[str]) -> str | None:
        """What the tap would commit, if the owner must approve it first; otherwise None. `said`: what the owner has
        said about the task so far (answers, approvals)."""
        ...


class ModelGuard:
    def __init__(self, model: Model, instructions: Instructions = INSTRUCTIONS):
        self._agent = Agent(model, name="guard", instructions=instructions, output_type=Verdict)

    async def check(self, screen: Image, target: str, task: str, said: list[str]) -> str | None:
        owner = "".join(f"\nThe owner said: {line}" for line in said)
        prompt = [f"Task: {task}{owner}\nAbout to tap: {target}", content(screen)]
        try:
            verdict = (await logs.run(self._agent, prompt)).output
        except Exception as error:
            # Fail closed: an unchecked tap might be the one that pays.
            log.warning("guard failed, asking the owner instead: %s", error)
            return f"tap {target} (the safety check failed, so I can't tell what it does)"
        log.debug("guard %r: %s", target, verdict)
        return verdict.action if verdict.needs_approval else None
