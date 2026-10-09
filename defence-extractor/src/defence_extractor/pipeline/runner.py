"""Stage functions + local async runner. The same stage functions back the Temporal activities.

Every stage reads/writes DocState (persisted after each stage) -> idempotent, resumable per document.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Awaitable, Callable

from ..config import Settings
from ..contracts.common import StageTiming, Usage
from ..ingest import ingest
from ..ingest.corpus import DocRef
from ..llm import BudgetExceeded
from .state import STAGES, DocState

log = logging.getLogger(__name__)


async def _stage_ingest(rt, st: DocState) -> None:
    def work():
        if st.ref.format == "pdf":  # long PDFs: page windows, one docling conversion at a time, cached per window
            return ingest(st.ref, pdf_ocr=rt.settings.pdf_ocr, pdf_threads=rt.settings.pdf_threads,
                          cache_dir=rt.settings.var_dir / "pdf_windows", window_pages=rt.settings.pdf_window_pages)
        return ingest(st.ref)

    st.ingest = await asyncio.to_thread(work)


async def _stage_vision(rt, st):
    from ..agents.vision import a0_vision

    await a0_vision(rt, st)


async def _stage_product_map(rt, st):
    from ..agents.segments import build_product_map

    build_product_map(rt, st)


async def _stage_page_map(rt, st):
    from ..agents.understanding import a2_page_map

    st.page_map = await a2_page_map(rt, st)


async def _stage_entities(rt, st):
    from ..agents.understanding import a3_entities

    st.kg = await a3_entities(rt, st)


async def _stage_roles(rt, st):
    from ..agents.understanding import a4_roles

    st.kg = await a4_roles(rt, st)


async def _stage_references(rt, st):
    from ..agents.understanding import a7_references

    st.kg = await a7_references(rt, st)


async def _stage_discovery(rt, st):
    from ..agents.discovery import a5b_open_discovery

    st.facts = await a5b_open_discovery(rt, st)


async def _stage_attribution(rt, st):
    from ..agents.discovery import a6_attribution

    await a6_attribution(rt, st)


async def _stage_known_hunt(rt, st):
    from ..agents.discovery import a5a_known_hunt, designation_specs

    from ..agents.base import over_doc_budget

    st.facts += designation_specs(st)
    if over_doc_budget(rt, st):
        st.errors.append(f"A5A: skipped (document budget ${rt.settings.doc_budget_usd} reached)")
        return
    st.facts += await a5a_known_hunt(rt, st)


async def _stage_mapping(rt, st):
    from ..agents.mapping import a8_mapping

    await a8_mapping(rt, st)


async def _stage_structuring(rt, st):
    from ..agents.mapping import a9_structuring

    st.facts += a9_structuring(rt, st)


async def _stage_audit(rt, st):
    from ..agents.discovery import a6_attribution
    from ..agents.mapping import a8_mapping, a9_structuring
    from ..agents.quality import a10_audit

    from ..contracts.common import PRODUCT_LIKE_ROLES

    has_products = any(e.role in PRODUCT_LIKE_ROLES for e in (st.kg.entities if st.kg else []))
    covered = {i for f in st.facts for i in f.inventory_item_ids}
    open_items = [it for it in st.ingest.inventory if not it.dismissed_reason and it.in_content_scope and it.item_id not in covered]
    if not has_products and not st.facts and len(open_items) < 3:
        # genuinely no product on the page (e.g. a history article): nothing to audit
        for it in st.ingest.inventory:
            if not it.dismissed_reason:
                st.item_dismissals.setdefault(it.item_id, "no_product_on_page")
        return
    from ..agents.base import over_doc_budget

    flagged: set[str] | None = None
    for rnd in range(rt.settings.max_recovery_rounds):
        if over_doc_budget(rt, st):
            st.errors.append(f"A10: audit round {rnd + 1} skipped (document budget ${rt.settings.doc_budget_usd} reached)")
            break
        new, flagged_now = await a10_audit(rt, st, only_chunks=flagged)
        st.audit_rounds = rnd + 1
        if not new:
            break
        st.facts += new
        st.recovered_fact_ids += [f.fact_id for f in new]
        await a6_attribution(rt, st)
        await a8_mapping(rt, st, new)
        st.facts += a9_structuring(rt, st)
        if not flagged_now:
            break
        flagged = flagged_now


async def _stage_anomaly(rt, st):
    from ..agents.quality import a11_anomalies

    a11_anomalies(rt, st)


async def _stage_verification(rt, st):
    from ..agents.quality import a12_verify

    await a12_verify(rt, st)


STAGE_FUNCS: dict[str, Callable[..., Awaitable[None]]] = {
    "ingest": _stage_ingest, "vision": _stage_vision, "page_map": _stage_page_map, "entities": _stage_entities,
    "roles": _stage_roles, "references": _stage_references, "product_map": _stage_product_map,
    "discovery": _stage_discovery, "attribution": _stage_attribution,
    "known_hunt": _stage_known_hunt, "mapping": _stage_mapping, "structuring": _stage_structuring,
    "audit": _stage_audit, "anomaly": _stage_anomaly, "verification": _stage_verification,
}


def _delta(a: Usage, b: Usage) -> Usage:
    d = Usage()
    for f in ("calls", "prompt_tokens", "completion_tokens", "reasoning_tokens", "cached_tokens", "cache_hits"):
        setattr(d, f, getattr(b, f) - getattr(a, f))
    d.cost_usd = round(b.cost_usd - a.cost_usd, 6)
    return d


async def run_stage(rt, st: DocState, stage: str, runs_dir: Path) -> None:
    if stage in st.done:
        return
    before = rt.gw.by_doc.get(st.ref.doc_id, Usage()).model_copy()
    t0 = time.time()
    await STAGE_FUNCS[stage](rt, st)
    after = rt.gw.by_doc.get(st.ref.doc_id, Usage())
    st.timings.append(StageTiming(stage=stage, seconds=round(time.time() - t0, 2), usage=_delta(before, after)))
    st.done.append(stage)
    st.save(runs_dir)


def result_path(runs_dir: Path, run_id: str, doc_id: str) -> Path:
    return DocState.path_for(runs_dir, run_id, doc_id) / "result.json"


async def assemble(rt, st: DocState, runs_dir: Path) -> dict:
    from ..agents.assembly import a13_assemble

    usage = Usage()
    for t in st.timings:
        usage.add(t.usage)
    duration = sum(t.seconds for t in st.timings)
    res = a13_assemble(rt, st, usage, rt.gw.providers_by_doc.get(st.ref.doc_id, {}), duration)
    p = result_path(runs_dir, st.run_id, st.ref.doc_id)
    p.write_text(res.model_dump_json(indent=1, exclude_none=False), encoding="utf-8")
    if "assembly" not in st.done:
        st.done.append("assembly")
    st.status = "completed"
    st.save(runs_dir)
    return summarize(st, res)


def summarize(st: DocState, res) -> dict:
    accepted = sum(len(p.specifications) for p in res.products)
    text_facts = sum(len(p.capabilities) + len(p.features) + len(p.technologies) + len(p.components) + len(p.compatibility)
                     + len(p.missions) + len(p.targets) for p in res.products)
    dyn = sum(1 for p in res.products for s in p.specifications if s.parameter.status.value == "dynamic")
    return {
        "doc_id": st.ref.doc_id, "level": st.ref.level, "format": st.ref.format, "status": st.status,
        "products": len(res.products), "focal": len(st.kg.focal_entity_ids) if st.kg else 0,
        "specs": accepted, "dynamic_specs": dyn, "text_facts": text_facts,
        "unresolved": sum(len(p.unresolved) for p in res.products) + len(res.unattributed),
        "ledger": res.ledger_summary.model_dump(), "coverage": res.coverage.model_dump(),
        "cost_usd": res.execution.usage.cost_usd, "llm_calls": res.execution.usage.calls,
        "seconds": res.execution.duration_s, "errors": len(st.errors),
    }


async def run_document(rt, ref: DocRef, run_id: str, runs_dir: Path, resume: bool = True) -> dict:
    st = DocState.load(runs_dir, run_id, ref.doc_id) if resume else None
    if st is None:
        st = DocState(run_id=run_id, ref=ref)
    if st.status == "completed" and result_path(runs_dir, run_id, ref.doc_id).exists():
        res = json.loads(result_path(runs_dir, run_id, ref.doc_id).read_text())
        return {"doc_id": ref.doc_id, "status": "completed", "cached": True, "cost_usd": res["execution"]["usage"]["cost_usd"]}
    st.status = "running"
    for stage in STAGES[:-1]:
        try:
            await run_stage(rt, st, stage, runs_dir)
        except BudgetExceeded as e:
            st.status = "budget_exhausted"
            st.errors.append(f"{stage}: {e}")
            st.save(runs_dir)
            raise
        except Exception as e:
            log.exception("stage %s failed for %s", stage, ref.doc_id)
            st.errors.append(f"{stage}: {type(e).__name__}: {str(e)[:300]}")
            if stage in ("ingest", "page_map", "entities", "roles", "discovery"):
                st.status = "failed"
                st.save(runs_dir)
                return {"doc_id": ref.doc_id, "status": "failed", "stage": stage, "error": str(e)[:300]}
            st.done.append(stage)  # non-critical stage: continue, error recorded
            st.save(runs_dir)
    return await assemble(rt, st, runs_dir)


async def run_batch(rt, refs: list[DocRef], run_id: str, runs_dir: Path, concurrency: int = 4,
                    on_done: Callable[[dict], None] | None = None) -> list[dict]:
    sem = asyncio.Semaphore(concurrency)
    stop = asyncio.Event()
    out: list[dict] = []

    async def one(ref: DocRef):
        if stop.is_set():
            return
        async with sem:
            if stop.is_set():
                return
            try:
                s = await run_document(rt, ref, run_id, runs_dir)
            except BudgetExceeded as e:
                stop.set()
                s = {"doc_id": ref.doc_id, "status": "budget_exhausted", "error": str(e)}
            except Exception as e:  # pragma: no cover
                s = {"doc_id": ref.doc_id, "status": "failed", "error": f"{type(e).__name__}: {e}"}
            out.append(s)
            if on_done:
                on_done(s)

    await asyncio.gather(*[one(r) for r in refs])
    return out


def build_runtime(settings: Settings, run_id: str, budget_cap: float | None = None):
    """Create gateway + budget + cache + ontology. Caller must ``async with rt.gw``."""
    from ..agents.base import Runtime
    from ..llm import BudgetGuard, Gateway, ResponseCache
    from ..ontology import load_ontology

    run_dir = settings.runs_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    budget = BudgetGuard(settings.openrouter_base_url, settings.openrouter_api_key, reserve_usd=settings.budget_reserve_usd,
                         run_cap_usd=budget_cap)
    cache = ResponseCache(settings.var_dir / "llm_cache.sqlite")
    gw = Gateway(settings, run_dir / "llm_calls.jsonl", budget=budget, cache=cache)
    gw.seed_spend_from_log()  # per-document spend (for the per-document cap) survives worker restarts
    return Runtime(settings=settings, gw=gw, onto=load_ontology())
