"""Re-issue one agent prompt for a document (cache disabled) and print the prompt tail + raw output.

usage: python scripts/debug_call.py <run_id> <doc_id> A5B [--reasoning off|low] [--show-prompt]
"""

from __future__ import annotations

import argparse
import asyncio
import json

from defence_extractor.agents.base import Runtime
from defence_extractor.agents.discovery import catalogue_for, doc_header
from defence_extractor.agents.prompts import A3_TASK, A5B_TASK, SHARED_RULES
from defence_extractor.agents.schemas import A3Out, FactsOut
from defence_extractor.config import get_settings
from defence_extractor.llm import BudgetGuard, Gateway
from defence_extractor.ontology import load_ontology
from defence_extractor.pipeline.render import build_chunks, render_blocks
from defence_extractor.pipeline.state import DocState


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_id")
    ap.add_argument("doc_id")
    ap.add_argument("agent")
    ap.add_argument("--reasoning", default="off")
    ap.add_argument("--show-prompt", action="store_true")
    a = ap.parse_args()
    s = get_settings()
    s.llm_cache = False
    st = DocState.load(s.runs_dir, a.run_id, a.doc_id)
    doc = st.ingest.doc
    budget = BudgetGuard(s.openrouter_base_url, s.openrouter_api_key)
    async with Gateway(s, s.var_dir / "debug_calls.jsonl", budget=budget) as gw:
        rt = Runtime(settings=s, gw=gw, onto=load_ontology())
        chunks = build_chunks(doc, st.page_map, s.section_chunk_tokens)
        ev = "# EVIDENCE\n" + render_blocks(doc, chunks[0].block_ids, page_map=st.page_map)
        if a.agent == "A5B":
            task, schema = A5B_TASK.replace("{catalogue}", catalogue_for(rt, st.kg)), FactsOut
        else:
            task, schema = A3_TASK, A3Out
        user = doc_header(st) + "\n\n" + ev + "\n\n" + task
        if a.show_prompt:
            print(user[:6000])
        r = await gw.call(agent=f"debug_{a.agent}", role="main", system=SHARED_RULES, user=user, schema=schema,
                          doc_id=a.doc_id, reasoning=a.reasoning, max_tokens=8000)
        print(json.dumps(r.parsed.model_dump(), indent=1)[:4000])
        print("usage:", r.usage)


if __name__ == "__main__":
    asyncio.run(main())
