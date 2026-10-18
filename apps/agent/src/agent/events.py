"""Everything that wakes the orchestrator. Each kind says what the model sees, where the answer goes by default,
and whether it carries the owner's authority. New triggers (a schedule, an incoming call) are new kinds here.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass

from agent.messages import Address, Image, Message, Part
from agent.store import Task


class Event(ABC):
    reply_to: Address
    """Where the orchestrator's answer goes unless it decides otherwise."""
    from_owner: bool
    """Only events from the owner may approve anything."""

    @abstractmethod
    def prompt(self) -> list[Part | str]:
        """What the orchestrator model is shown."""

    def attachments(self) -> tuple[Part, ...]:
        """Sent to the owner along with the answer."""
        return ()

    @abstractmethod
    def fallback(self, error: Exception) -> str:
        """What the owner hears if the orchestrator itself fails."""


@dataclass(frozen=True)
class OwnerMessage(Event):
    message: Message
    from_owner = True

    @property
    def reply_to(self) -> Address:
        return self.message.address

    def prompt(self) -> list[Part | str]:
        return [f"(via {self.message.address.channel})", *self.message.parts]

    def fallback(self, error: Exception) -> str:
        return "Sorry, something went wrong on my side. Could you say that again?"  # the error is in the log


@dataclass(frozen=True)
class TaskUpdate(Event):
    task: Task
    screen: Image | None = None
    from_owner = False

    @property
    def reply_to(self) -> Address:
        return self.task.report_to

    def prompt(self) -> list[Part | str]:
        task = self.task
        update = f"[Update on task #{task.id}, not from the owner; your reply goes via {task.report_to.channel}]"
        update += f" {task.status}: {task.report}"
        return [update, *self.attachments()]

    def attachments(self) -> tuple[Part, ...]:
        # The owner should see what they're asked to approve or answer, not just read about it.
        return (self.screen,) if self.screen and self.task.status.waiting else ()

    def fallback(self, error: Exception) -> str:
        return f"An update on \"{self.task.goal}\": {self.task.report}"
