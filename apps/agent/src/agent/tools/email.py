"""Email's settings on the Connectors page, beside the quick tools': who Archangel reads mail from. It answers
nothing itself (the channel is `channels/email.py`), so the orchestrator is never told it's something to answer from."""

from collections.abc import Awaitable, Callable

from agent.tools.tool import Setting, Tool


class Email(Tool):
    name = "email"
    title = "Email"
    about = "who Archangel reads mail from, at its own address; anyone else's is ignored"
    settings = (Setting("allowed", "Allowed senders (the first is where Archangel writes to you)", items=True),)

    def functions(self) -> list[Callable[..., Awaitable[str]]]:
        return []
