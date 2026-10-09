"""Quality gates: A10 coverage audit (+ targeted recovery), A11 anomaly rules, A12 independent verification."""

from __future__ import annotations

import asyncio
import re

from ..contracts.common import NON_PRODUCT_CLASSES, SPEC_CLASSES, Confidence, FactClass, OntologyStatus, VerificationStatus
from ..contracts.document import ScopeHint
from ..contracts.facts import CandidateFact
from ..ontology.values import dimension_of
from ..pipeline.render import Chunk, build_chunks, render_blocks
from ..pipeline.state import DocState
from .base import Runtime, ask, norm_text, value_in_blocks
from .discovery import doc_header, evidence, link_inventory, to_candidate
from .segments import related, structural_owner

_STRONG_STRUCTURE = {"table_row", "table_column", "section:heading", "section:page_header", "section:page_map"}
from .mapping import _dynamic
from .prompts import A10_TASK, A12_TASK
from .schemas import A10Out, A12Out

_AUDIT_ROLES = {"specifications", "variants", "product_overview", "features_capabilities", "comparison", "catalogue_listing"}


# ---------------------------------------------------------------------------------------------------
# A10
# ---------------------------------------------------------------------------------------------------
def _covered_items(st: DocState) -> set[str]:
    return {i for f in st.facts if f.rejection_reason != "split_into_parts" for i in f.inventory_item_ids}


async def a10_audit(rt: Runtime, st: DocState, only_chunks: set[str] | None = None) -> tuple[list[CandidateFact], set[str]]:
    """Returns (recovered facts, chunk ids that produced issues)."""
    doc = st.ingest.doc
    chunks = build_chunks(doc, st.page_map, max_tokens=rt.settings.section_chunk_tokens)
    covered = _covered_items(st)
    items_by_block: dict[str, list] = {}
    for it in st.ingest.inventory:
        if it.dismissed_reason or it.item_id in covered or it.item_id in st.item_dismissals:
            continue
        items_by_block.setdefault(it.block_id, []).append(it)
    facts_by_block: dict[str, list[CandidateFact]] = {}
    for f in st.facts:
        if f.rejection_reason == "split_into_parts":
            continue
        for e in f.evidence:
            facts_by_block.setdefault(e.block_id, []).append(f)
    emap = st.kg.entity_map()

    async def audit(c: Chunk) -> tuple[str, list[CandidateFact], int]:
        unmatched = [it for b in c.block_ids for it in items_by_block.get(b, [])]
        if not unmatched and not (set(c.roles) & _AUDIT_ROLES):
            return c.chunk_id, [], 0
        extracted, seen = [], set()
        for b in c.block_ids:
            for f in facts_by_block.get(b, []):
                if f.fact_id in seen:
                    continue
                seen.add(f.fact_id)
                owner = emap.get(f.owner_entity_id or f.subject_entity_id or "")
                extracted.append(f"{f.fact_id} | {owner.name if owner else '?'} | {f.source_parameter or f.parameter_hint} | "
                                 f"{f.value_text[:120]} | {','.join(e.block_id for e in f.evidence)}")
        un_lines = [f"{it.item_id} | {it.block_id} | {it.text[:100]}" for it in unmatched[:120]]
        task = A10_TASK.replace("{extracted}", "\n".join(extracted) or "(none)").replace("{unmatched}", "\n".join(un_lines) or "(none)")
        res = await ask(rt, agent="A10", role="verifier", header=doc_header(st, c.block_ids), evidence=evidence(st, c.block_ids),
                        task=task, schema=A10Out, doc_id=doc.doc_id, reasoning="medium", max_tokens=12000)
        recovered: list[CandidateFact] = []
        issues = 0
        valid_items = {it.item_id for it in unmatched}
        fmap = {f.fact_id: f for f in st.facts}
        for iss in res.parsed.issues:
            if iss.kind == "not_a_fact":
                if iss.item_id in valid_items:
                    st.item_dismissals[iss.item_id] = "not_technical"
                continue
            issues += 1
            if iss.kind == "wrong_owner" and iss.fact_id in fmap:
                f = fmap[iss.fact_id]
                f.anomalies.append(f"audit_wrong_owner: {iss.note[:120]}")
                continue
            if iss.fact is not None:
                cf = to_candidate(st, iss.fact, "coverage_recovery", c.section_ids[0] if c.section_ids else None, "A10", c.block_ids)
                if cf is None:
                    continue
                if iss.item_id in valid_items:
                    cf.inventory_item_ids.append(iss.item_id)
                if iss.kind in ("merged", "incomplete_value") and iss.fact_id in fmap:
                    old = fmap[iss.fact_id]
                    old.anomalies.append(f"audit_{iss.kind}")
                    cf.attribution_note = f"replaces {old.fact_id} ({iss.kind})"
                recovered.append(cf)
        return c.chunk_id, recovered, issues

    targets = [c for c in chunks if only_chunks is None or c.chunk_id in only_chunks]
    results = await asyncio.gather(*[audit(c) for c in targets], return_exceptions=True)
    new: list[CandidateFact] = []
    flagged: set[str] = set()
    for c, r in zip(targets, results):
        if isinstance(r, BaseException):
            st.errors.append(f"A10 {c.chunk_id}: {type(r).__name__}: {r}")
            continue
        cid, rec, n = r
        new.extend(rec)
        if n:
            flagged.add(cid)
    existing = {(f.subject_entity_id, norm_text(f.value_text), f.applicability.key()) for f in st.facts}
    new = [f for f in new if (f.subject_entity_id, norm_text(f.value_text), f.applicability.key()) not in existing]
    link_inventory(st, new)
    return new, flagged


# ---------------------------------------------------------------------------------------------------
# A11 (deterministic rules)
# ---------------------------------------------------------------------------------------------------
_PLACEHOLDER = re.compile(r"(?i)^\s*(?:0+(?:[.,]0+)?\s*[a-zA-Z%°]*|-|–|—|n/?a|tbd|tba|xx+|\?)\s*$")


def a11_anomalies(rt: Runtime, st: DocState) -> None:
    doc = st.ingest.doc
    bmap = doc.block_map()
    lang = st.ingest.profile.language
    focal = set(st.kg.focal_entity_ids)
    groups: dict[tuple, list[CandidateFact]] = {}
    for f in st.facts:
        if f.rejection_reason == "split_into_parts":
            continue
        blocks = [bmap[e.block_id] for e in f.evidence if e.block_id in bmap]
        if _PLACEHOLDER.match(f.value_text):
            f.anomalies.append("placeholder")
        if any(b.attributes.get("counter_attr") for b in blocks):
            f.anomalies.append("value_from_counter_attribute")
        p = rt.onto.get(f.parameter.id) if f.parameter else None
        if p is not None and p.dimension and p.type == "number":
            vd = dimension_of(f.value_text, lang)
            if vd is None and f.fact_class not in NON_PRODUCT_CLASSES:
                f.anomalies.append(f"non_numeric_value_for:{p.id}")
                f.parameter = _dynamic(f)  # unknown is better than wrong
                f.mapping_confidence = min(f.mapping_confidence, 0.7)
                if f.fact_class in (FactClass.technical_specification, FactClass.performance_specification):
                    f.fact_class = FactClass.capability
            elif vd is not None and vd != p.dimension and not (p.dimension == "count"):
                f.anomalies.append(f"dimension_mismatch:{p.id}")
                f.parameter = _dynamic(f)
                f.mapping_confidence = min(f.mapping_confidence, 0.7)
        if blocks and all(b.scope in (ScopeHint.related_content, ScopeHint.sidebar) for b in blocks) and f.owner_entity_id in focal:
            f.anomalies.append("evidence_only_in_related_content")
        if f.parameter is not None and f.parameter.status != OntologyStatus.dynamic and f.owner_entity_id:
            groups.setdefault((f.owner_entity_id, f.parameter.id, f.applicability.key()), []).append(f)
    for (_o, pid, _a), fs in groups.items():
        p = rt.onto.get(pid)
        if p is None or p.multi or len(fs) < 2:
            continue
        vals = {norm_text(f.value_text) for f in fs}
        if len(vals) > 1 and p.type == "number":
            for f in fs:
                f.anomalies.append(f"conflicting_values:{pid}")
    for f in st.facts:
        f.anomalies = list(dict.fromkeys(f.anomalies))


# ---------------------------------------------------------------------------------------------------
# A12
# ---------------------------------------------------------------------------------------------------
_NEWS_TYPES = {"defence_news", "press_release", "technical_article"}
_NEWS_NOTE = ("\nThis document is a news / analysis article: accept only stated characteristics of a product (specifications, "
              "capabilities, components, armament, compatibility). What a ship, unit or force did, where it is based or deployed, "
              "its readiness, exercises, incidents, schedules, orders and fleet numbers are not_a_spec.")


def _gate(st: DocState, f: CandidateFact) -> str | None:
    """Deterministic verification gates; returns a rejection reason or None."""
    doc = st.ingest.doc
    bmap = doc.block_map()
    if not f.evidence or not all(e.block_id in bmap for e in f.evidence):
        return "unsupported"
    if not value_in_blocks(doc, [e.block_id for e in f.evidence], f.value_text):
        return "unsupported"
    if "placeholder" in f.anomalies:
        return "placeholder"
    return None


async def a12_verify(rt: Runtime, st: DocState) -> None:
    doc = st.ingest.doc
    bmap = doc.block_map()
    emap = st.kg.entity_map()
    todo: list[CandidateFact] = []
    for f in st.facts:
        if f.verification != VerificationStatus.pending:
            continue
        if f.rejection_reason == "split_into_parts":
            f.verification = VerificationStatus.rejected
            continue
        if f.fact_class == FactClass.procurement_information:
            f.verification, f.rejection_reason = VerificationStatus.rejected, "procurement_information"
            continue
        if f.fact_class == FactClass.marketing_claim:
            f.verification, f.rejection_reason = VerificationStatus.rejected, "marketing_only"
            continue
        if f.fact_class == FactClass.operational_event:
            f.verification, f.rejection_reason = VerificationStatus.rejected, "operational_event"
            continue
        reason = _gate(st, f)
        if reason:
            f.verification, f.rejection_reason = VerificationStatus.rejected, reason
            f.confidence = Confidence.combine(0.0, f.owner_confidence, f.mapping_confidence, 0.0)
            continue
        if f.owner_entity_id is None:
            f.verification, f.rejection_reason = VerificationStatus.unresolved, "no_owner"
            continue
        todo.append(f)
    if not todo:
        return
    todo.sort(key=lambda f: (f.owner_entity_id or "", f.fact_id))  # owners grouped, but batches span owners
    n = rt.settings.verify_batch_size
    batches: list[list[CandidateFact]] = [todo[i : i + n] for i in range(0, len(todo), n)]

    async def verify(batch: list[CandidateFact]) -> None:
        ev_ids = sorted({e.block_id for f in batch for e in f.evidence if e.block_id in bmap}, key=lambda x: bmap[x].order)
        lines = []
        for f in batch:
            owner = emap.get(f.owner_entity_id)
            p = rt.onto.get(f.parameter.id) if f.parameter else None
            pdesc = f"{p.id} ({p.name}: {p.description[:120]})" if p else (f.parameter.id if f.parameter else "-")
            cond = "; ".join(f"{c.dimension}={c.value_text}" for c in f.applicability.conditions)
            lines.append(f"{f.fact_id} | owner: {owner.entity_id + ' ' + owner.name if owner else '?'} | parameter: {pdesc} | "
                         f"label: {f.source_parameter or '-'} | value: {f.value_text}" + (f" | conditions: {cond}" if cond else "")
                         + f" | evidence: {', '.join(e.block_id for e in f.evidence)}")
        task = A12_TASK.replace("{facts}", "\n".join(lines))
        if st.page_map is not None and st.page_map.page_type in _NEWS_TYPES:
            task += _NEWS_NOTE
        res = await ask(rt, agent="A12", role="verifier", header=doc_header(st, ev_ids), evidence=evidence(st, ev_ids),
                        task=task, schema=A12Out, doc_id=doc.doc_id, reasoning="medium", max_tokens=10000)
        verdicts = {v.fact_id: v for v in res.parsed.verdicts}
        for f in batch:
            v = verdicts.get(f.fact_id)
            f.agents.append("A12")
            if v is None:
                f.verification, f.verification_note = VerificationStatus.unresolved, "verifier returned no verdict"
                continue
            att = min(f.owner_confidence or 0.9, max(0.0, min(1.0, v.attribution_confidence)))
            vmap = max(0.0, min(1.0, v.mapping_confidence))
            if f.fact_class not in SPEC_CLASSES:
                mapc = 1.0  # capabilities/features/... have no parameter slot to get wrong
            elif f.parameter is not None and f.parameter.status == OntologyStatus.dynamic:
                mapc = max(f.mapping_confidence, vmap)  # dynamic = "not forced into a field"; wrong_parameter handles misses
            else:
                mapc = min(f.mapping_confidence or 0.9, vmap)
            val = max(0.0, min(1.0, v.value_confidence))
            f.verification_note = (v.note or "")[:300] or None
            ev_conf = 0.85 if any(bmap[e.block_id].attributes.get("vision") for e in f.evidence if e.block_id in bmap) else 1.0
            if v.verdict == "supported":
                f.confidence = Confidence.combine(ev_conf, att, mapc, val)
                o = f.confidence.overall
                f.verification = (VerificationStatus.verified if o >= 0.9 else
                                  VerificationStatus.verified_low_confidence if o >= 0.7 else VerificationStatus.unresolved)
            elif v.verdict == "wrong_parameter":
                f.parameter = _dynamic(f)
                f.confidence = Confidence.combine(1.0, att, 0.75, val)
                f.verification = VerificationStatus.verified_low_confidence if min(att, val) >= 0.7 else VerificationStatus.unresolved
                f.anomalies.append("verifier_wrong_parameter")
            elif v.verdict == "wrong_owner":
                so, src = structural_owner(st, f)
                co = v.correct_owner_id if v.correct_owner_id in emap and v.correct_owner_id != f.owner_entity_id else None
                cur = emap.get(f.owner_entity_id or "")
                # a part's values (controller, battery, camera, launcher) sit inside its product's section: the section
                # label alone does not move them up to the product (seen on a drone page: the verifier, shown
                # "describes: <drone>", pulled the controller's battery and size onto the drone)
                part_to_parent = bool(co and cur is not None and cur.parent_entity_id == co and src.startswith("section"))
                if part_to_parent:
                    f.verification = VerificationStatus.verified_low_confidence if min(mapc, val) >= 0.7 else VerificationStatus.unresolved
                    f.confidence = Confidence.combine(1.0, 0.7, mapc, val)
                    f.anomalies.append("verifier_suggests_parent")
                elif co and so and related(st, co, so) and src in _STRONG_STRUCTURE:
                    # (continuation / lead-mention owners are inferences, not the document's structure)
                    # the verifier's owner agrees with the document's structure (table row / column, section):
                    # move the value to that product instead of abstaining
                    f.owner_entity_id = co
                    f.confidence = Confidence.combine(1.0, 0.8, mapc, val)
                    f.verification = VerificationStatus.verified_low_confidence if min(mapc, val) >= 0.7 else VerificationStatus.unresolved
                    f.attribution_note = f"owner corrected by the verifier, consistent with {src}"
                    f.anomalies.append("owner_corrected")
                else:
                    if co:
                        f.alternative_owner_ids = list(dict.fromkeys([co, *f.alternative_owner_ids]))
                    f.verification, f.rejection_reason = VerificationStatus.unresolved, "belongs_to_other_entity"
                    f.confidence = Confidence.combine(1.0, 0.3, mapc, val)
            elif v.verdict == "not_a_spec":
                f.verification, f.rejection_reason = VerificationStatus.rejected, "not_technical"
            elif v.verdict == "unsupported":
                f.verification, f.rejection_reason = VerificationStatus.rejected, "unsupported"
            else:
                f.verification = VerificationStatus.unresolved
                f.confidence = Confidence.combine(1.0, att, mapc, val)

    results = await asyncio.gather(*[verify(b) for b in batches], return_exceptions=True)
    for b, r in zip(batches, results):
        if isinstance(r, BaseException):
            st.errors.append(f"A12: {type(r).__name__}: {r}")
            for f in b:
                if f.verification == VerificationStatus.pending:
                    f.verification, f.verification_note = VerificationStatus.unresolved, "verifier call failed"
