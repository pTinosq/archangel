"""The reporter: turns material about a topic (research, so far) into a short report to read on a phone, and a
tldr for the orchestrator to pass on with its link. The model writes the pages; the layout is ours, the same every
time: portrait pages like a PDF's, phone-shaped (9:16).
"""

from html import escape

from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.models import Model

from agent import logs
from agent.models import Instructions

INSTRUCTIONS = """Write a report on the topic from the material, for a busy person reading on a phone, in pages \
like a PDF's, each one phone screen: about 120 words, or a short table. The answer first, then what backs it up, \
sources as links on the last page. Keep what matters; add nothing."""


class Report(BaseModel):
    tldr: str = Field(description="The answer in a sentence or two, to text the owner with the link.")
    title: str
    pages: list[str] = Field(description="Each page's HTML: headings, paragraphs, lists, tables, links. No styles.")

    @property
    def html(self) -> str:
        pages = "".join(
            f'<section class="page">{page}<footer>{n} / {len(self.pages)}</footer></section>'
            for n, page in enumerate(self.pages, 1)
        )
        return PAGE.format(title=escape(self.title), pages=pages)


class Reporter:
    def __init__(self, model: Model, instructions: Instructions = INSTRUCTIONS):
        self._agent = Agent(model, name="reporter", instructions=instructions, output_type=Report)

    async def write(self, topic: str, material: str) -> Report:
        return (await logs.run(self._agent, f"Topic: {topic}\n\nMaterial:\n{material}")).output


# A page is 9:16 at least, and grows rather than cut off what doesn't fit; wide tables scroll inside it.
PAGE = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title><style>
:root{{color-scheme:light dark;--bg:#e6e8ec;--page:#fff;--fg:#14161b;--muted:#5b6270;--line:#e3e5ea;--accent:#0b5fbe}}
@media (prefers-color-scheme:dark){{:root{{--bg:#000;--page:#17191e;--fg:#e8eaef;--muted:#a3aab7;--line:#2a2f38;\
--accent:#79b6ff}}}}
*{{box-sizing:border-box}}
body{{margin:0;padding:12px;background:var(--bg);color:var(--fg);-webkit-text-size-adjust:100%;\
font:16px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}}
.page{{display:flex;flex-direction:column;width:min(100%,30rem);aspect-ratio:9/16;margin:0 auto 12px;\
padding:28px 22px 16px;background:var(--page);border-radius:6px;box-shadow:0 1px 4px #0003;overflow-wrap:break-word}}
footer{{margin-top:auto;padding-top:16px;color:var(--muted);font-size:.8rem;text-align:center}}
h1{{font-size:1.45rem;line-height:1.25;margin:0 0 .8rem}} h2{{font-size:1.15rem;margin:0 0 .6rem}}
h3{{font-size:1rem;margin:1rem 0 .3rem}} p{{margin:.5rem 0}} ul,ol{{margin:.5rem 0;padding-left:1.2rem}}
li{{margin:.25rem 0}} a{{color:var(--accent);overflow-wrap:anywhere}}
table{{display:block;overflow-x:auto;border-collapse:collapse;font-size:.88rem;margin:.6rem 0}}
th,td{{padding:6px 8px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}
th{{color:var(--muted);font-weight:600}}
</style></head><body>{pages}</body></html>
"""
