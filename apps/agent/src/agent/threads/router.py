"""The router: before the orchestrator reads what the owner said, a quick model puts it in a thread, an earlier one
it carries on or a new one, so the orchestrator sees that thread's history and nothing else. It reads the recent
threads (summaries, or how unfinished ones end) and searches the rest when the owner refers back further."""

import time
from datetime import date

from pydantic import BaseModel, Field
from pydantic_ai import Agent, ModelRetry
from pydantic_ai.models import Model

from agent import logs
from agent.models import Instructions
from agent.threads.store import Found, Thread, ThreadStore
from agent.threads.text import ago, lines

INSTRUCTIONS = """You sort what the owner says to Archangel, their assistant, into conversation threads, one per \
topic, so Archangel answers with that thread in mind and nothing else. Choose the thread this carries on, or none \
for something new (then give it a title of a few words). A reply to Archangel's last words ("yes", "the second one") \
carries that thread on. They refer to something not listed? search_threads; when several match, the recent one is \
likelier. `closes` only when it asks for nothing at all: a thanks or goodbye that wraps its thread up."""

ENDING_SHOWN = 4
"""Lines of an unfinished thread the router sees: enough to tell whether this answers it."""
LINE_SHOWN = 240


class Choice(BaseModel):
    thread: int | None = Field(description="The thread it carries on; null for a new one")
    title: str = Field("", description="A new thread's title, a few words")
    closes: bool = Field(False, description="Only a thanks or goodbye that asks for nothing")


class Router:
    def __init__(self, model: Model, store: ThreadStore, instructions: Instructions = INSTRUCTIONS):
        self._store = store

        async def search_threads(words: str) -> str:
            """Every thread ever, by words in it ("india flights"), best match first."""
            return search(store, words, time.time())

        self._agent = Agent(
            model,
            name="thread router",
            output_type=Choice,
            instructions=[instructions, lambda: f"Today is {date.today():%A %d %B %Y}."],
            tools=[search_threads],
            retries=2,
        )

        @self._agent.output_validator
        async def known(choice: Choice) -> Choice:
            if choice.thread is not None and not store.get(choice.thread):
                raise ModelRetry(f"There's no thread #{choice.thread}: choose a listed or found one, or null.")
            if choice.thread is None and not choice.title.strip():
                raise ModelRetry("A new thread needs a title.")
            return choice

    async def choose(self, said: str, recent: list[Thread]) -> Choice:
        """`recent`: newest first; the first is where the conversation is, what Archangel said last."""
        now = time.time()
        if recent:
            here, *others = recent
            menu = f"Where the conversation is (Archangel's last words):\n{self._describe(here, now)}"
            if others:
                menu += "\n\nOther recent threads, newest first:\n" + "\n".join(self._describe(t, now) for t in others)
        else:
            menu = "No threads yet."
        result = await logs.run(self._agent, f"{menu}\n\nThe owner says {said}")
        return result.output

    def _describe(self, thread: Thread, now: float) -> str:
        head = f'#{thread.id} "{thread.title}" · {"open" if thread.open else "closed"} · {ago(thread.updated, now)}'
        if thread.summary and not thread.stale:
            return f"{head}\n  summary: {thread.summary}"
        ending = [_clip(line) for line in lines(self._store.history(thread.id))[-ENDING_SHOWN:]]
        return f"{head}\n" + "\n".join(f"  {line}" for line in ending) if ending else head


def search(store: ThreadStore, words: str, now: float) -> str:
    """Threads mentioning `words`, for a model: each with its summary, or where the words are if it has none."""
    found = store.search(words)
    listed = (f'#{f.thread.id} "{f.thread.title}" · {ago(f.thread.updated, now)}: {_shown(f)}' for f in found)
    return "\n".join(listed) or "No thread mentions that."


def _shown(found: Found) -> str:
    return found.thread.summary if found.thread.summary and not found.thread.stale else found.snippet


def _clip(line: str) -> str:
    line = " ".join(line.split())
    return line if len(line) <= LINE_SHOWN else line[: LINE_SHOWN - 1] + "…"
