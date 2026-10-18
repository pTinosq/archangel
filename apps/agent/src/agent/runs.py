"""A sub-agent's run as a task sees it: how it ended, and pausing for the owner (an approval, or a question) and
carrying on with their answer. Shared by the sub-agents that can pause: the pilot and the helper."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

import pydantic_ai
from pydantic_ai import DeferredToolRequests, DeferredToolResults, ToolDenied

from agent.messages import Image


@dataclass(frozen=True)
class Answer:
    """The owner's reply to a paused task: free text, and for an approval, yes or no."""

    text: str
    approved: bool = False


@dataclass(frozen=True)
class Outcome:
    status: Literal["done", "gave_up", "stuck", "failed", "needs_approval", "needs_answer"]
    report: str
    """What happened; when it needs the owner, what they're being asked."""
    run: pydantic_ai.Conversation | None = None
    """Set when it needs the owner: pass back to the sub-agent with their answer to continue."""
    screen: Image | None = None


Record = Callable[[str, Image | None], None]
"""Hears each step of a run as it happens, with the screen it left, if any, for watching the task live."""


def answered(answer: Answer | None) -> str:
    if not answer:
        return ""
    return ("Approved" if answer.approved else "Owner replied") + (f": {answer.text}" if answer.text else ".")


def question(requests: DeferredToolRequests) -> str:
    asks = [f"Approve: {requests.metadata[call.tool_call_id]['action']}?" for call in requests.approvals]
    asks += [requests.metadata[call.tool_call_id]["question"] for call in requests.calls]
    return "\n".join(asks)


def results(paused: pydantic_ai.Conversation, answer: Answer | None) -> DeferredToolResults:
    requests = paused.deferred_tool_requests or DeferredToolRequests()
    answer = answer or Answer("")
    no = ToolDenied(f"The owner said no: {answer.text}" if answer.text else "The owner said no.")
    return requests.build_results(
        approvals={call.tool_call_id: answer.approved or no for call in requests.approvals},
        calls={call.tool_call_id: f"The owner replied: {answer.text}" for call in requests.calls},
    )
