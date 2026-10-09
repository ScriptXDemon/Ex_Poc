"""Smoke test: one structured-output call per model role; reports provider, tokens, reasoning tokens and cost."""

from __future__ import annotations

import asyncio
import json
import sys

from pydantic import BaseModel

from defence_extractor.config import get_settings
from defence_extractor.llm import BudgetGuard, Gateway


class Fact(BaseModel):
    product: str
    parameter: str
    value_text: str


class Out(BaseModel):
    facts: list[Fact]


PASSAGE = ("The Tarn launcher fires the Orel-X missile, which reaches 300 km. The Zorya-7 has a 3-man crew and fires up to "
           "9 rpm. Estonia has 24 Tarn on order.")


async def main() -> None:
    s = get_settings()
    budget = BudgetGuard(s.openrouter_base_url, s.openrouter_api_key, reserve_usd=s.budget_reserve_usd)
    await budget.refresh()
    print("budget before:", budget.snapshot())
    async with Gateway(s, "var/smoke_calls.jsonl", budget=budget) as gw:
        for role, reasoning in (("main", "off"), ("main", "low"), ("verifier", "medium")):
            r = await gw.call(agent=f"smoke_{role}_{reasoning}", role=role, reasoning=reasoning, schema=Out,
                              system="Extract product specifications as JSON. Values verbatim. Order quantities are not specs.",
                              user=f"PASSAGE: {PASSAGE}\nReturn facts.", max_tokens=1500)
            print(f"\n[{role}/{reasoning}] provider={r.provider} prompt={r.usage.prompt_tokens} completion={r.usage.completion_tokens} "
                  f"reasoning={r.usage.reasoning_tokens} cost=${r.usage.cost_usd:.5f} latency={r.latency_s:.1f}s")
            print(json.dumps(r.parsed.model_dump(), indent=1)[:800])
    print("\nbudget after:", budget.snapshot(), file=sys.stdout)


if __name__ == "__main__":
    asyncio.run(main())
