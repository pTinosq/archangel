"""Exchange rates (Frankfurter: the European Central Bank's daily reference rates, free, no key)."""

import re
from collections.abc import Awaitable, Callable

import httpx

from agent.tools.tool import Tool, ToolError

FRANKFURTER = "https://api.frankfurter.dev/v1"


class Currency(Tool):
    name = "currency"
    title = "Exchange rates"
    about = "convert between currencies at the latest (or a past day's) rate"

    def functions(self) -> list[Callable[..., Awaitable[str]]]:
        return [self.convert_currency]

    async def convert_currency(self, amount: float, from_currency: str, to_currency: str, on: str = "") -> str:
        """Convert money between currencies (ISO codes: GBP, EUR, USD, JPY…) at the ECB's rate, which is set once each
        working day: good for "how much is 50 euros", not the rate a bank or card will give, nor crypto or markets.
        `on`: a past day, YYYY-MM-DD."""
        base, quote = (code.strip().upper() for code in (from_currency, to_currency))
        for code in (base, quote):
            if not re.fullmatch(r"[A-Z]{3}", code):
                raise ToolError(f"{code!r} isn't a currency code: use GBP, EUR, USD…")
        if amount <= 0:
            raise ToolError("The amount must be more than zero.")
        if base == quote:
            return f"{amount:,} {base} is {amount:,} {quote}."
        try:
            data = await self.get(f"{FRANKFURTER}/{on.strip() or 'latest'}", base=base, symbols=quote, amount=amount)
        except httpx.HTTPStatusError as error:
            if error.response.status_code in (404, 422):  # a code or date it doesn't have
                raise ToolError(f"No rate for {base} to {quote}{f' on {on}' if on else ''}.") from error
            raise
        converted = data["rates"][quote]
        rate = f"1 {base} = {converted / amount:.4f} {quote}"
        return f"{amount:,} {base} = {converted:,.2f} {quote} (ECB rate of {data['date']}: {rate})."
