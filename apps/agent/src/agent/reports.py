"""The reporter: turns material about a topic (research, so far) into a short report to read on a phone, and a
tldr for the orchestrator to pass on with its link. The model writes the pages; the layout is ours, the same every
time: portrait pages like a PDF's, phone-shaped (9:16).
"""

from datetime import date
from html import escape

from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.models import Model

from agent import logs
from agent.models import Instructions

INSTRUCTIONS = """Write a report on the topic from the material, for a busy person reading on a phone, in pages \
like a PDF's, each one phone screen: about 120 words, or a short table. The answer first, then what backs it up, \
sources as links on the last page. Each page opens with a short heading; the title and tldr are on a cover, so \
don't repeat them. Keep what matters; add nothing."""


class Report(BaseModel):
    tldr: str = Field(description="The answer in a sentence or two, to text the owner with the link.")
    title: str
    pages: list[str] = Field(description="Each page's HTML: headings, paragraphs, lists, tables, links. No styles.")

    @property
    def html(self) -> str:
        title, total = escape(self.title), len(self.pages) + 1
        cover = (
            f'<p class="brand">Archangel</p><p class="kind">Research report</p>'
            f"<h1>{title}</h1>"
            f'<div class="summary"><p class="label">In short</p><p>{escape(self.tldr)}</p></div>'
            f'<p class="date">{date.today():%-d %B %Y}</p>'
        )
        pages = [f'<section class="page cover">{cover}{_footer(1, total)}</section>'] + [
            f'<section class="page"><p class="running">{title}</p>{page}{_footer(n, total)}</section>'
            for n, page in enumerate(self.pages, 2)
        ]
        return (
            '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            f"<title>{title}</title><style>{STYLE}</style></head><body>{''.join(pages)}</body></html>"
        )


class Reporter:
    def __init__(self, model: Model, instructions: Instructions = INSTRUCTIONS):
        self._agent = Agent(model, name="reporter", instructions=instructions, output_type=Report)

    async def write(self, topic: str, material: str) -> Report:
        return (await logs.run(self._agent, f"Topic: {topic}\n\nMaterial:\n{material}")).output


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
