"""The time anywhere, UK bank holidays (gov.uk), days between dates, and arithmetic done exactly: what a model gets
wrong when it does it in its head."""

import ast
import operator
from collections.abc import Awaitable, Callable
from datetime import date, datetime
from zoneinfo import ZoneInfo

from agent.schedule import TIMEZONE
from agent.tools import places
from agent.tools.tool import Tool, ToolError

BANK_HOLIDAYS = "https://www.gov.uk/bank-holidays.json"
REGIONS = ("england-and-wales", "scotland", "northern-ireland")
MAX_POWER = 1000  # an exponent past this is a way to make the agent compute for minutes

OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}


class Dates(Tool):
    name = "dates"
    title = "Time and dates"
    about = "the time in any place, UK bank holidays, days between dates, exact arithmetic and percentages"

    def functions(self) -> list[Callable[..., Awaitable[str]]]:
        return [self.time_in, self.bank_holidays, self.days_between, self.calculate]

    async def time_in(self, place: str) -> str:
        """The time now in a place ("Tokyo", "New York"), and how far it is from the owner's. Not for the owner's own
        time, which you're always told."""
        found = await places.find(self, place)
        if found.timezone == "auto":
            raise ToolError("Give a place by name: coordinates don't say which time zone.")
        there, here = datetime.now(ZoneInfo(found.timezone)), datetime.now(TIMEZONE)
        hours = (there.utcoffset() - here.utcoffset()).total_seconds() / 3600
        gap = f"{abs(hours):g} h {'ahead of' if hours > 0 else 'behind'} the owner's" if hours else "the owner's time"
        return f"{found.name}: {there:%A %-d %B, %H:%M} ({found.timezone}), {gap}."

    async def bank_holidays(self, region: str = "england-and-wales", count: int = 5) -> str:
        """The next UK bank holidays (`region`: england-and-wales, scotland or northern-ireland). Not other
        countries' holidays: search the web for those."""
        if region not in REGIONS:
            raise ToolError(f"The regions are {', '.join(REGIONS)}.")
        events = (await self.get(BANK_HOLIDAYS))[region]["events"]
        today = datetime.now(TIMEZONE).date().isoformat()
        coming = [e for e in events if e["date"] >= today][: max(1, min(count, 20))]
        if not coming:
            return "gov.uk lists no more bank holidays yet."
        return "\n".join(f"{date.fromisoformat(e['date']):%a %-d %b %Y}: {e['title']}" for e in coming)

    async def days_between(self, start: str, end: str = "") -> str:
        """Exact days between two dates (YYYY-MM-DD); `end` defaults to today, the owner's. For "how many days until
        Christmas", "how long since…"; negative if `end` is before `start`."""
        try:
            first = date.fromisoformat(start)
            last = date.fromisoformat(end) if end else datetime.now(TIMEZONE).date()
        except ValueError as error:
            raise ToolError(f"Dates are YYYY-MM-DD: {error}") from error
        days = (last - first).days
        return f"{first:%a %-d %b %Y} to {last:%a %-d %b %Y}: {days} days ({days // 7} weeks and {days % 7} days)."

    async def calculate(self, expression: str) -> str:
        """Exact arithmetic: + - * / // % ** and brackets, e.g. "1234.5 * 1.2 / 3" or "18% of 64" as "64 * 0.18".
        Use it for any sum whose answer they'll rely on (splitting a bill, a conversion, a total)."""
        try:
            value = _evaluate(ast.parse(expression.strip(), mode="eval").body)
        except (SyntaxError, ValueError, TypeError, ZeroDivisionError, OverflowError) as error:
            raise ToolError(f"Couldn't work out {expression!r}: {error or type(error).__name__}.") from error
        shown = round(value, 10) if isinstance(value, float) else value  # not 0.30000000000000004
        return f"{expression.strip()} = {shown:,}"


def _evaluate(node: ast.expr) -> int | float:
    match node:
        case ast.Constant(value=int() | float() as number) if not isinstance(number, bool):
            return number
        case ast.UnaryOp(op=ast.USub(), operand=operand):
            return -_evaluate(operand)
        case ast.UnaryOp(op=ast.UAdd(), operand=operand):
            return _evaluate(operand)
        case ast.BinOp(left=left, op=op, right=right) if type(op) in OPERATORS:
            a, b = _evaluate(left), _evaluate(right)
            if isinstance(op, ast.Pow) and abs(b) > MAX_POWER:
                raise ValueError("that power is too large")
            return OPERATORS[type(op)](a, b)
    raise ValueError("only numbers and + - * / // % ** are understood")
