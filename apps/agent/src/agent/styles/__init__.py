"""How Archangel writes: one style per audience, kept as files beside this one, not in any prompt (M20,
`docs/styles.md`).

Each `<name>.md` is a profile: TOML between `+++` lines saying who it's for, then the style itself, as prose for a
model. `base.md` is under every profile (plain words, nothing padded, nothing said twice); `default.md` is what's used
when nothing else fits. Adding a style is adding a file; nothing else in the code changes. The owner's own, naming
real people or accounts, go in `data/styles/` (the volume), never the public repo. `python -m agent.styles` shows
which one an audience gets.

A profile may name where it applies (`via`: a channel or an app, "sms", "discord", "email"), who (`people`, by name;
`groups`: the owner's buckets of people, "uni friends"), and whether it's for the owner (`owner = true`). All it names
must fit. Of the profiles that fit, the most specific wins: one naming the person, then their group, then one for the
owner, then one naming where; the default only when none fit. Two profiles that could both win the same message are
refused at start, so the choice never depends on file order.

Who's in which group is the owner's life, so it's the context twin's, not a file's: a person's node linked (either way)
to a node named for the group. The twin also gives a person's aliases, so "Sam Taylor" finds a profile for "Sam". The
lookup is code, not a model call. Someone in two groups whose profiles tie gets the first by file name.

Each style's text is also a prompt ("style discord"), edited on the panel's Prompts tab: an edit wins from the next
message, the file (the owner's, else the built-in) stays the default, and who it's for stays in the file. The owner
can also ask for one message to sound different ("make it casual"): `asked`, for that message only.
"""

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic_ai.toolsets import FunctionToolset

from agent.twin.graph import Graph

if TYPE_CHECKING:
    from agent.prompts import Prompts

FOLDER = Path(__file__).parent
BASE, DEFAULT = "base", "default"
OWNER = "owner"
"""What `writing_style` is told for `to` when it's writing to the owner."""
_KEYS = {"about", "groups", "owner", "people", "via"}


def _clean(name: str) -> str:
    return " ".join(name.lower().split())


@dataclass(frozen=True)
class Audience:
    """Who a piece of writing is for, and where it's going."""

    owner: bool = False
    person: str = ""
    via: str = ""
    """The channel or app it goes through."""

    @classmethod
    def of(cls, to: str = "", via: str = "") -> "Audience":
        """From what a model says: `to` is "owner" or a name, or blank; `via` a channel or app, or blank."""
        owner = _clean(to) in (OWNER, f"the {OWNER}")
        return cls(owner, "" if owner else to.strip(), via.strip())

    def __str__(self) -> str:
        to = "the owner" if self.owner else self.person or "someone"
        return f"{to} via {self.via}" if self.via else to


@dataclass(frozen=True)
class Style:
    name: str
    about: str
    text: str
    owner: bool = False
    people: tuple[str, ...] = ()
    via: tuple[str, ...] = ()
    groups: tuple[str, ...] = ()

    @property
    def general(self) -> bool:
        """Names nothing: it fits every audience, which only the default may."""
        return not (self.owner or self.people or self.groups or self.via)

    @property
    def applies(self) -> str:
        """Who it's for, in a few words."""
        names = ["the owner"] if self.owner else []
        names += [f"to {', '.join(self.people)}"] if self.people else []
        names += [f"in {', '.join(self.groups)}"] if self.groups else []
        names += [f"via {', '.join(self.via)}"] if self.via else []
        return "; ".join(names) or "otherwise"

    def rank(
        self, audience: Audience, names: frozenset[str] = frozenset(), groups: frozenset[str] = frozenset()
    ) -> tuple[bool, ...] | None:
        """None if it doesn't fit; else what it names, most telling first, so that larger is more specific. `names`:
        the person's names (theirs and their aliases); `groups`: what they're in."""
        names = names or frozenset([_clean(audience.person)] if audience.person else [])
        if (self.owner and not audience.owner) or (self.people and not names & set(self.people)):
            return None
        if (self.groups and not groups & set(self.groups)) or (self.via and _clean(audience.via) not in self.via):
            return None
        return self._named()

    def _named(self) -> tuple[bool, ...]:
        return bool(self.people), bool(self.groups), self.owner, bool(self.via)

    def clashes(self, other: "Style") -> bool:
        """Could both win the same audience? Same things named, and every list shares a name. (Two groups can't be
        checked: who's in both is the twin's.)"""
        return (
            self._named() == other._named()
            and (not self.people or bool(set(self.people) & set(other.people)))
            and (not self.groups or bool(set(self.groups) & set(other.groups)))
            and (not self.via or bool(set(self.via) & set(other.via)))
        )


def parse(name: str, source: str) -> Style:
    """A profile from its file's text. Raises `ValueError` saying what's wrong: a typo in a key must not pass as
    a style that quietly never matches."""
    head, found, text = source.removeprefix("+++\n").partition("\n+++\n")
    if not (source.startswith("+++\n") and found):
        raise ValueError(f"{name}: starts with TOML between +++ lines")
    try:
        settings = tomllib.loads(head)
    except tomllib.TOMLDecodeError as error:
        raise ValueError(f"{name}: {error}") from error
    if unknown := settings.keys() - _KEYS:
        raise ValueError(f"{name}: unknown {', '.join(sorted(unknown))} (known: {', '.join(sorted(_KEYS))})")
    if not isinstance(about := settings.get("about"), str) or not about:
        raise ValueError(f"{name}: `about` says in a line what it's for")
    if not isinstance(owner := settings.get("owner", False), bool):
        raise ValueError(f"{name}: `owner` is true or false")
    lists = {}
    for key in ("people", "groups", "via"):
        values = settings.get(key, [])
        if not (isinstance(values, list) and all(isinstance(v, str) and v.strip() for v in values)):
            raise ValueError(f"{name}: `{key}` is a list of names")
        lists[key] = tuple(_clean(v) for v in values)
    if not text.strip():
        raise ValueError(f"{name}: nothing written")
    return Style(name, about, text.strip(), owner, **lists)


class Styles:
    """The profiles in a folder: pick one for an audience, and say how to write for it."""

    def __init__(
        self,
        folder: Path = FOLDER,
        own: Path | None = None,
        twin: Graph | None = None,
        prompts: "Prompts | None" = None,
    ):
        """`own`: the owner's folder (on the volume, never in the repo), for styles that name real people or accounts.
        A file there adds a style, or replaces the built-in one of its name. `twin`: who's in which group. `prompts`:
        where each style's text can be edited, its file the default."""
        self._twin = twin
        found = {
            path.stem: parse(path.stem, path.read_text())
            for where in (folder, own)
            if where and where.is_dir()
            for path in sorted(where.glob("*.md"))
        }
        for required in (BASE, DEFAULT):
            if required not in found:
                raise ValueError(f"{folder}: {required}.md is missing")
        self.base = found.pop(BASE)
        if self.base.owner or self.base.people or self.base.via:
            raise ValueError("base: is under every style, so names no audience")
        self.default = found.pop(DEFAULT)
        if not self.default.general:
            raise ValueError("default: fits when nothing else does, so names no audience")
        self.profiles = sorted(found.values(), key=lambda style: style.name)  # a tie goes to the first by name
        for style in self.profiles:
            if style.general:
                raise ValueError(f"{style.name}: names no one (owner, people, groups, via); only the default may")
        for i, style in enumerate(self.profiles):
            for other in self.profiles[i + 1 :]:
                if style.clashes(other):
                    raise ValueError(f"{style.name} and {other.name} could both be chosen for one audience")
        self._prompts = prompts
        for style in self.all if prompts is not None else []:
            prompts.add(prompt(style.name), style.text, f"{style.about} ({self._applies(style)})")

    @property
    def all(self) -> list[Style]:
        """The base, the profiles, then the default."""
        return [self.base, *self.profiles, self.default]

    def text(self, style: Style) -> str:
        """Its text as edited on the panel, else its file's."""
        return self._prompts[prompt(style.name)] if self._prompts else style.text

    def _applies(self, style: Style) -> str:
        return "under all of them" if style is self.base else style.applies

    def choose(self, audience: Audience) -> Style:
        """The most specific profile that fits, else the default."""
        names, groups = self._known(audience.person)
        fits = [(rank, style) for style in self.profiles if (rank := style.rank(audience, names, groups)) is not None]
        return max(fits, key=lambda fit: fit[0])[1] if fits else self.default

    def _known(self, person: str) -> tuple[frozenset[str], frozenset[str]]:
        """What the twin knows of `person`: their names, and the names of every node they're linked to (their groups,
        among others: only those a profile names matter)."""
        if not (person.strip() and self._twin and (node := self._twin.find(person))):
            return frozenset([_clean(person)] if person.strip() else []), frozenset()
        linked = [link.target for link in self._twin.links_from(node.name)]
        linked += [link.source for link in self._twin.links_to(node.name)]
        nodes = [other for name in linked if (other := self._twin.find(name))]
        groups = {_clean(n) for other in nodes for n in (other.name, *other.aliases)}
        return frozenset(_clean(n) for n in (person, node.name, *node.aliases)), frozenset(groups)

    def guidance(self, audience: Audience, asked: str = "") -> str:
        """How to write for them: the base, then the profile chosen, then what the owner asked of this one message."""
        style = self.choose(audience)
        text = f'Writing style "{style.name}", for {audience}:\n\n{self.text(self.base)}\n\n{self.text(style)}'
        if asked.strip():
            text += f'\n\nFor this message only, the owner asked: "{asked.strip()}". Where that differs, do as asked.'
        return text

    def describe(self) -> str:
        """Every profile and who it's for, for whoever tunes them."""
        return "\n".join(f"{style.name}: {style.about} ({self._applies(style)})" for style in self.all)

    @property
    def tools(self) -> FunctionToolset:
        """`writing_style`, for any agent that writes to someone for the owner."""

        async def writing_style(to: str = "", via: str = "", asked: str = "") -> str:
            """How to write a message, email or post for someone: call it before you write one, then write as it
            says. `to`: "owner" for the owner, else the person's name (blank if you don't know); `via`: the app or
            channel it goes through ("Discord", "email", "Slack", "sms"); `asked`: the owner's words on how this one
            should sound ("make it casual"), if they said. Not for your own replies in this conversation, which come
            with their style."""
            return self.guidance(Audience.of(to, via), asked)

        return FunctionToolset([writing_style])


def prompt(name: str) -> str:
    """A style's name among the prompts."""
    return f"style {name}"
