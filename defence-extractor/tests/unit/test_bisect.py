import asyncio

import pytest

from defence_extractor.agents.base import bisect_on_overflow
from defence_extractor.llm import SchemaError


def test_overflowing_chunk_is_split_until_it_fits():
    calls = []

    async def run(ids):
        calls.append(list(ids))
        if len(ids) > 2:
            raise SchemaError("cut off")
        return ids

    out = asyncio.run(bisect_on_overflow(run, ["b1", "b2", "b3", "b4", "b5"]))
    assert [b for part in out for b in part] == ["b1", "b2", "b3", "b4", "b5"]
    assert all(len(p) <= 2 for p in out)


def test_single_block_overflow_is_reported():
    async def run(ids):
        raise SchemaError("cut off")

    with pytest.raises(SchemaError):
        asyncio.run(bisect_on_overflow(run, ["b1"]))


def test_dense_blocks_are_chunked_by_item_count():
    from defence_extractor.contracts.document import BlockType, ScopeHint, SemanticBlock, SemanticDocument
    from defence_extractor.pipeline.render import build_chunks

    blocks = [SemanticBlock(block_id=f"b{i}", type=BlockType.paragraph, text="Weight: 8 kg; Length: 1,020 mm", order=i,
                            section_id="s0", scope=ScopeHint.main) for i in range(10)]
    doc = SemanticDocument(doc_id="D", format="pdf", content_sha256="x", parser_version="t", blocks=blocks)
    assert len(build_chunks(doc, None, max_tokens=3500)) == 1
    chunks = build_chunks(doc, None, max_tokens=3500, max_items=6, items={b.block_id: 2 for b in blocks})
    assert [len(c.block_ids) for c in chunks] == [3, 3, 3, 1]


def test_doc_budget_survives_restarts(tmp_path):
    import json
    from types import SimpleNamespace

    from defence_extractor.agents.base import doc_spent, over_doc_budget
    from defence_extractor.config import Settings
    from defence_extractor.contracts.common import Usage
    from defence_extractor.llm import Gateway

    log = tmp_path / "llm_calls.jsonl"
    log.write_text("\n".join(json.dumps(r) for r in [
        {"doc_id": "D", "cost_usd": 0.10}, {"doc_id": "D", "cost_usd": 0.25}, {"doc_id": "X", "cost_usd": 1.0},
        {"doc_id": "D", "cache_hit": True}]) + "\n")
    gw = Gateway(Settings(), log)          # a fresh worker process
    gw.seed_spend_from_log()
    rt = SimpleNamespace(gw=gw, settings=SimpleNamespace(doc_budget_usd=0.40))
    st = SimpleNamespace(run_id="R", ref=SimpleNamespace(doc_id="D"))
    assert abs(doc_spent(rt, st) - 0.35) < 1e-9 and not over_doc_budget(rt, st)
    gw._account("D", Usage(calls=1, cost_usd=0.06))
    assert over_doc_budget(rt, st)
