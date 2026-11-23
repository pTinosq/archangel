from dataclasses import dataclass


@dataclass(frozen=True)
class Mailbox:
    """Archangel's mailbox, from the environment."""

    address: str
    smtp: str
    """`smtp://user:pass@host:587` (STARTTLS) or `smtps://user:pass@host:465`; credentials percent-encoded."""
    secret: str
    """What the provider's webhook presents (`Authorization: Bearer …`, or `?token=…` where it can't set headers)."""
