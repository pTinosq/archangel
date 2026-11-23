"""The reporter: turns material about a topic (research, so far) into a short report to read on a phone, and a
tldr for the orchestrator to pass on with its link. The model writes the pages; the layout is ours, the same every
time: portrait pages like a PDF's, phone-shaped (9:16).
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from html import escape, unescape
from html.parser import HTMLParser

from pydantic import BaseModel, Field, PrivateAttr
from pydantic_ai import Agent, RunContext, ToolReturn
from pydantic_ai.models import Model

from agent import logs
from agent.messages import Image
from agent.models import Instructions, content
from agent.research import Researcher

MAX_IMAGES = 8
EXTENSIONS = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif"}

INSTRUCTIONS = """Write a report on the topic from the material, for a busy person reading on a phone, in pages \
like a PDF's, each one phone screen: about 120 words, or a short table. The answer first, then what backs it up, \
sources as links on the last page. Each page opens with a short heading; the title and tldr are on a cover, so \
don't repeat them. Keep what matters; add nothing.

Where a picture of the thing helps (a product, a place), add a few with your tools: look for one with \
`search_images`, or among a page's with `page_images`, or take one the owner sent, then `add_image` (it shows you \
the picture: keep it only if it is what you wanted). It gives the name to use as `<img src="name" alt="what it \
shows">` in a page, near what it shows. Only pictures you added show. If none can be found or fetched, write the \
report without; never invent an address."""


class Report(BaseModel):
    tldr: str = Field(description="The answer in a sentence or two, to text the owner with the link.")
    title: str
    pages: list[str] = Field(
        description="Each page's HTML: headings, paragraphs, lists, tables, links, and `<img>` of added pictures. "
        "No styles."
    )
    _images: dict[str, Image] = PrivateAttr(default_factory=dict)

    @property
    def images(self) -> dict[str, Image]:
        """The pictures the pages use, by the names they're written with."""
        used = set().union(*(_sources(page) for page in self.pages))
        return {name: image for name, image in self._images.items() if name in used}

    def attach(self, images: dict[str, Image]) -> "Report":
        self._images = images
        return self

    @property
    def html(self) -> str:
        return self.page("")

    def page(self, images_at: str) -> str:
        """The finished HTML. A picture is served from `images_at` + its name; any `<img>` that isn't one of ours
        is dropped, so the page never reaches for anything else."""
        names = set(self._images)
        body = [_pictures(page, names, images_at) for page in self.pages]
        title, total = escape(self.title), len(self.pages) + 1
        cover = (
            f'<p class="brand">Archangel</p><p class="kind">Research report</p>'
            f"<h1>{title}</h1>"
            f'<div class="summary"><p class="label">In short</p><p>{escape(self.tldr)}</p></div>'
            f'<p class="date">{date.today():%-d %B %Y}</p>'
        )
        pages = [f'<section class="page cover">{cover}{_footer(1, total)}</section>'] + [
            f'<section class="page"><p class="running">{title}</p>{page}{_footer(n, total)}</section>'
            for n, page in enumerate(body, 2)
        ]
        return (
            '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            f"<title>{title}</title><style>{STYLE}</style></head><body>{''.join(pages)}</body></html>"
        )


@dataclass
class Gathering:
    """The pictures one report has so far."""

    researcher: Researcher | None
    supplied: Sequence[Image]
    kept: dict[str, Image] = field(default_factory=dict)


async def search_images(ctx: RunContext[Gathering], query: str) -> str:
    """Pictures from a web search: addresses, each with what it shows. Try several searches for different things."""
    return await ctx.deps.researcher.image_search(query) if ctx.deps.researcher else "No web access."


async def page_images(ctx: RunContext[Gathering], url: str) -> str:
    """The pictures on a web page (a product's page, say): addresses."""
    return await ctx.deps.researcher.page_images(url) if ctx.deps.researcher else "No web access."


async def add_image(ctx: RunContext[Gathering], source: str, alt: str) -> ToolReturn | str:
    """Fetch a picture to use: `source` is its web address, or "owner 1", "owner 2"… for one the owner sent. `alt`:
    what it shows, in a few words. You see it, and are given its name for the page."""
    gathering = ctx.deps
    if len(gathering.kept) >= MAX_IMAGES:
        return f"That's enough pictures ({MAX_IMAGES})."
    if match := re.fullmatch(r"owner (\d+)", source.strip()):
        n = int(match[1])
        image = gathering.supplied[n - 1] if 0 < n <= len(gathering.supplied) else None
    else:
        image = await gathering.researcher.image(source.strip()) if gathering.researcher else None
    if not image or image.media_type not in EXTENSIONS:
        return f"Couldn't get {source}: not a picture, not public, too big or gone. Try another, or go without."
    name = f"img{len(gathering.kept) + 1}{EXTENSIONS[image.media_type]}"
    gathering.kept[name] = image
    return ToolReturn(f"Added as {name} ({alt}). Look at it: if it isn't right, don't use it.", content=[content(image)])


class Reporter:
    def __init__(self, model: Model, instructions: Instructions = INSTRUCTIONS, researcher: Researcher | None = None):
        """`researcher`: where pictures are looked for and fetched; without it, only the owner's can be used."""
        self._researcher = researcher
        self._agent = Agent(
            model,
            name="reporter",
            instructions=instructions,
            output_type=Report,
            tools=[search_images, page_images, add_image],
            deps_type=Gathering,
        )

    async def write(self, topic: str, material: str, supplied: Sequence[Image] = ()) -> Report:
        """`supplied`: pictures the owner sent, which it may use as "owner 1", "owner 2"…"""
        owner = f"\n\nThe owner sent {len(supplied)} pictures: owner 1 to owner {len(supplied)}." if supplied else ""
        gathering = Gathering(self._researcher, supplied)
        prompt = f"Topic: {topic}\n\nMaterial:\n{material}{owner}"
        return (await logs.run(self._agent, prompt, deps=gathering)).output.attach(gathering.kept)


_IMG = re.compile(r"""<img\b(?:[^>"']|"[^"]*"|'[^']*')*>""", re.I)  # a `>` inside a quoted value isn't its end
_ATTRIBUTE = re.compile(r"""\b(src|alt)\s*=\s*(?:"([^"]*)"|'([^']*)')""", re.I)


def _attributes(tag: str) -> dict[str, str]:
    return {name.lower(): unescape(double or single) for name, double, single in _ATTRIBUTE.findall(tag)}


def _sources(page: str) -> set[str]:
    return {_attributes(tag).get("src", "") for tag in _IMG.findall(page)}


def _pictures(page: str, names: set[str], at: str) -> str:
    def figure(tag: re.Match) -> str:
        attributes = _attributes(tag[0])
        if (src := attributes.get("src", "")) not in names:
            return ""
        alt = escape(attributes.get("alt", ""))
        caption = f"<figcaption>{alt}</figcaption>" if alt else ""
        return f'<figure><img src="{escape(at + src)}" alt="{alt}" loading="lazy">{caption}</figure>'

    return _IMG.sub(figure, page)


def text_of(html: str) -> str:
    """A saved report's words, for Archangel to read: one line per paragraph, list item or table row, links with
    their address. Only the finished HTML is kept, so this is what makes every stored report readable."""
    reader = _Text()
    reader.feed(html)
    lines = ("".join(reader.out).split("\n"))
    return "\n".join(line for line in (" ".join(line.split()) for line in lines) if line)


class _Text(HTMLParser):
    BLOCKS = {"p", "h1", "h2", "h3", "li", "tr", "section", "table"}
    LAYOUT = {"style", "title", "footer"}  # and the running head: not the report's content

    def __init__(self):
        super().__init__()
        self.out: list[str] = []
        self._skipping: str | None = None  # the tag being skipped; its own kind nests no deeper in our pages
        self._hrefs: list[str | None] = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if not self._skipping and (tag in self.LAYOUT or "running" in (attrs.get("class") or "").split()):
            self._skipping = tag
        if tag == "a":
            self._hrefs.append(attrs.get("href"))
        if tag in self.BLOCKS:
            self.out.append("\n")
        if tag in ("td", "th"):
            self.out.append(" | ")

    def handle_endtag(self, tag):
        if tag == "a" and self._hrefs and (href := self._hrefs.pop()) and not self._skipping:
            self.out.append(f" ({href})")
        if tag == self._skipping:
            self._skipping = None
        if tag in self.BLOCKS:
            self.out.append("\n")

    def handle_data(self, data):
        if not self._skipping:
            self.out.append(data)


def _footer(n: int, total: int) -> str:
    return f"<footer><span>Archangel</span><span>{n} / {total}</span></footer>"


# Like a printed report, shrunk to a phone, in the panel's look: black, mono, one acid accent. Portrait 9:16 pages
# that grow rather than cut off what doesn't fit; wide tables scroll inside. System fonts only: the page may load
# nothing.
STYLE = """
*{box-sizing:border-box}
html{color-scheme:dark;-webkit-text-size-adjust:100%}
body{margin:0;padding:16px 12px;background:#000;color:#e9e9e4;
font:14px/1.62 ui-monospace,"SF Mono","JetBrains Mono",Menlo,Consolas,"Roboto Mono",monospace}
::selection{background:#c6ff3d;color:#000}
.page{display:flex;flex-direction:column;width:min(100%,30rem);aspect-ratio:9/16;margin:0 auto 16px;
padding:24px 22px 14px;background:#070707;border:1px solid #1f1f1f;overflow-wrap:break-word;
background-image:repeating-linear-gradient(to bottom,#ffffff05 0 1px,transparent 1px 3px)}
.running{margin:0 0 20px;padding-bottom:8px;border-bottom:1px solid #1f1f1f;color:#8b8b84;font-size:.7rem;
text-transform:uppercase;letter-spacing:.08em;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
footer{display:flex;justify-content:space-between;margin-top:auto;padding-top:8px;border-top:1px solid #1f1f1f;
color:#8b8b84;font-size:.7rem;text-transform:uppercase;letter-spacing:.08em}
h1,h2{margin:0 0 .9rem;color:#c6ff3d;font-size:1rem;line-height:1.35;text-transform:uppercase;letter-spacing:.05em}
h1::before,h2::before{content:"> "}
h3{margin:1.2rem 0 .3rem;font-size:.85rem;text-transform:uppercase;letter-spacing:.06em}
p{margin:.55rem 0} strong{color:#fff}
ul,ol{margin:.55rem 0;padding-left:1.2rem} li{margin:.3rem 0} li::marker{color:#c6ff3d}
ul{list-style:square}
a{color:#c6ff3d;overflow-wrap:anywhere;text-decoration:underline dashed 1px;text-underline-offset:3px}
table{display:block;overflow-x:auto;border-collapse:collapse;margin:.8rem 0;font-size:.8rem;
font-variant-numeric:tabular-nums;border:1px solid #343434}
th,td{padding:6px 9px;text-align:left;vertical-align:top}
th{background:#c6ff3d;color:#000;font-size:.7rem;text-transform:uppercase;letter-spacing:.05em}
td{border-bottom:1px solid #1f1f1f}
figure{margin:.8rem 0}
figure img{display:block;max-width:100%;max-height:13rem;margin:0 auto;object-fit:contain;background:#fff}
figcaption{margin-top:.3rem;color:#8b8b84;font-size:.7rem;text-align:center}
.cover{border-top:4px solid #c6ff3d}
.brand{margin:0;color:#c6ff3d;font-weight:700;font-size:.8rem;text-transform:uppercase;letter-spacing:.2em}
.brand::after{content:"_";animation:blink 1.1s steps(1) infinite}
@keyframes blink{50%{opacity:0}}
.kind{margin:auto 0 .8rem;color:#8b8b84;font-size:.7rem;text-transform:uppercase;letter-spacing:.14em}
.cover h1{margin:0 0 1.6rem;color:#fff;font-size:1.85rem;line-height:1.1;letter-spacing:0}
.cover h1::before{content:none}
.summary{padding:12px 14px;border:1px solid #5d7a12;background:#c6ff3d0d}
.summary .label{margin:0 0 .3rem;color:#c6ff3d;font-size:.7rem;text-transform:uppercase;letter-spacing:.14em}
.summary p:last-child{margin:0}
.date{margin:1.2rem 0 auto;color:#8b8b84;font-size:.75rem;text-transform:uppercase;letter-spacing:.08em}
"""
