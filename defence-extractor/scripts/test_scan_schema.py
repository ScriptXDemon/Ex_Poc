"""Experiment: does a 'scan first' field prevent the empty-list collapse of thinking-off structured output?
usage: python scripts/test_scan_schema.py <run_id> <doc_id> [repeats]
"""

from __future__ import annotations

import asyncio
import sys

from pydantic import BaseModel

from defence_extractor.agents.base import Runtime
from defence_extractor.agents.discovery import catalogue_for, doc_header
from defence_extractor.agents.prompts import A5B_TASK, SHARED_RULES
from defence_extractor.agents.schemas import FactOut, FactsOut
from defence_extractor.config import get_settings
from defence_extractor.llm import BudgetGuard, Gateway
from defence_extractor.ontology import load_ontology
from defence_extractor.pipeline.render import build_chunks, render_blocks
from defence_extractor.pipeline.state import DocState


class ScanFactsOut(BaseModel):
    fact_blocks: list[str]
    facts: list[FactOut]


async def main() -> None:
    run_id, doc_id = sys.argv[1], sys.argv[2]
    reps = int(sys.argv[3]) if len(sys.argv) > 3 else 3
    s = get_settings()
    s.llm_cache = False
    st = DocState.load(s.runs_dir, run_id, doc_id)
    doc = st.ingest.doc
    async with Gateway(s, s.var_dir / "scan_test.jsonl", budget=BudgetGuard(s.openrouter_base_url, s.openrouter_api_key)) as gw:
        rt = Runtime(settings=s, gw=gw, onto=load_ontology())
        chunk = build_chunks(doc, st.page_map, s.section_chunk_tokens)[0]
        ev = "# EVIDENCE\n" + render_blocks(doc, chunk.block_ids, page_map=st.page_map)
        base = A5B_TASK.replace("{catalogue}", catalogue_for(rt, st.kg))
        scan_task = base + "\nFirst list in fact_blocks the ids of every evidence block that states a product fact, then extract the facts."
        for label, task, schema in (("plain", base, FactsOut), ("scan-first", scan_task, ScanFactsOut)):
            for i in range(reps):
                user = doc_header(st) + "\n\n" + ev + "\n\n" + task
                r = await gw.call(agent=f"scan_{label}", role="main", system=SHARED_RULES, user=user + f"\n(run {i})",
                                  schema=schema, doc_id=doc_id, reasoning="off", max_tokens=8000)
                print(f"{label:10} try {i}: facts={len(r.parsed.facts)} out_tokens={r.usage.completion_tokens}")


if __name__ == "__main__":
    asyncio.run(main())
