"""Discovery: A5B open specification discovery, A6 targeted attribution, A5A known-specification hunter."""

from __future__ import annotations

import asyncio
import logging
import re

from rapidfuzz import fuzz

from ..contracts.common import PRODUCT_LIKE_ROLES, EntityType, EvidenceRef, FactClass, Role
from ..contracts.document import BlockType
from ..contracts.entities import DefenceEntity, KnowledgeGraph
from ..contracts.facts import Applicability, CandidateFact, Condition
from ..ingest.measurements import find_measurements
from ..pipeline.render import Chunk, build_chunks, est_tokens, extractable_blocks, header, render_blocks
from ..pipeline.state import DocState
from ..retrieval import BlockIndex
from .base import Runtime, ask, bisect_on_overflow, find_quote, mention_scan, norm_name, norm_text, over_doc_budget
from .segments import context_notes, local_entities, related, structural_owner
from .prompts import A5A_TASK, A5B_TASK, A6_TASK
from .schemas import A6Out, FactOut, FactsOut
from .understanding import single_product

log = logging.getLogger(__name__)


def doc_header(st: DocState, block_ids: list[str] | None = None) -> str:
    """Document header; with ``block_ids`` the entity registry is limited to what is relevant to those blocks
    (large documents only — see segments.local_entities)."""
    only = local_entities(st, block_ids) if block_ids is not None else None
    return header(st.ingest.doc, st.page_map, st.kg, st.ingest.profile.language, only=only)


def evidence(st: DocState, block_ids: list[str]) -> str:
    """Evidence blocks with their section paths and the product map's notes (which product each part describes)."""
    return "# EVIDENCE\n" + render_blocks(st.ingest.doc, block_ids, page_map=st.page_map, notes=context_notes(st))


def catalogue_for(rt: Runtime, kg: KnowledgeGraph) -> str:
    cats: list[str] = []
    for e in kg.entities:
        if e.role in PRODUCT_LIKE_ROLES:
            for c in e.product_classes:
                if c.category not in cats:
                    cats.append(c.category)
    params = rt.onto.for_categories(cats) if cats else list(rt.onto.params.values())
    return "\n".join(f"{p.id} — {p.name}" + (f" [{p.dimension}]" if p.dimension else "") for p in params)


# ---------------------------------------------------------------------------------------------------
# FactOut -> CandidateFact
# ---------------------------------------------------------------------------------------------------
def _part_owner(st: DocState, block_ids: list[str]) -> str | None:
    """The product a part named in these blocks belongs to: the section's single owner from the product map, else the
    only product-like entity of the document."""
    bmap = st.ingest.doc.block_map()
    for b in block_ids:
        if b in bmap:
            owners = (st.segments or {}).get(bmap[b].section_id) or []
            if len(owners) == 1:
                return owners[0]
            break
    focal = [f for f in st.kg.focal_entity_ids if st.kg.get(f) is not None]
    if len(focal) == 1:
        return focal[0]
    prods = [e.entity_id for e in st.kg.entities
             if e.role in (Role.focal_product, Role.related_product) and not e.parent_entity_id]
    return prods[0] if len(prods) == 1 else None


def _ensure_entity(st: DocState, subject_text: str | None, block_ids: list[str] | None = None) -> str | None:
    """Map a free-text subject to a registry entity, creating one for unseen designations. A new subject without a
    model number ("Integrated DC/DC Converter" in a charger's spec table) is a part of the product that owns that
    part of the document: it becomes a component of it, so its values stay linked to the product."""
    if not subject_text or st.kg is None:
        return None
    key = norm_name(subject_text)
    best, score = None, 0.0
    for e in st.kg.entities:
        for n in [e.name, *e.aliases]:
            s = fuzz.ratio(key, norm_name(n))
            if s > score:
                best, score = e, s
    if best is not None and score >= 90:
        return best.entity_id
    if re.search(r"[A-Z0-9]", subject_text) and len(subject_text) <= 80:
        eid = f"E{len(st.kg.entities) + 1}"
        while st.kg.get(eid) is not None:
            eid = f"E{int(eid[1:]) + 1}"
        owner = None if re.search(r"\d", subject_text) else _part_owner(st, block_ids or [])
        ent = DefenceEntity(entity_id=eid, name=subject_text.strip(), entity_type=EntityType.product,
                            specific_model=owner is None, role=Role.component if owner else Role.related_product,
                            parent_entity_id=owner,
                            mention_block_ids=mention_scan(st.ingest.doc, [subject_text.strip()]), confidence=0.5,
                            note="created from a fact subject not in the A3 registry"
                                 + (f"; part of {owner}" if owner else ""))
        st.kg.entities.append(ent)
        return eid
    return None


_ODD_HYPHEN = re.compile("[\u2010\u2011\u2012\u2043\ufe63\uff0d\u00ad]")
_MEASURE = re.compile(r"^(?P<num>[<>≥≤~±+-]?\s*\d[\d.,]*(?:\s*(?:-|–|to|~|/|x|×|\.\.\.?|…)\s*\d[\d.,]*)*)\s*(?P<unit>[^\d\s][^\d]{0,29})$")
_SHORT_UNITS = {"in", "m", "h", "s", "g", "l", "t", "ft", "a", "v", "w", "c", "k"}  # also plain words / letters


def _unit_from_context(value: str, texts: list[str]) -> str | None:
    """For a measurement "<number> <unit>" not printed as one string: the number as printed, when one block prints the
    number and also its unit in its labels ("Depth — EU: 250; IMU: 259; Units: mm", "Velocity [fps]"). A one- or
    two-letter unit must appear as a label ("[in]", "(m)", "Units: h"), not as a word of the text."""
    m = _MEASURE.match(value.strip())
    if not m:
        return None
    num, unit = m.group("num").strip(), m.group("unit").strip()
    nn, nu = norm_text(num), norm_text(unit)
    if not nn or not nu or not re.search(r"[a-z%°µ]", nu) or len(nu.split()) > 3:
        return None
    num_rx = re.compile(r"(?<![\d.,])" + re.escape(nn) + r"(?![\d.,])")
    if nu in _SHORT_UNITS:
        unit_rx = re.compile(r"[\[(]\s*" + re.escape(nu) + r"\s*[\])]|units?\s*:?\s*" + re.escape(nu) + r"(?![a-z])")
    else:
        unit_rx = re.compile(r"(?<![a-z/])" + re.escape(nu) + r"(?![a-z/²³])")  # not part of "km/h"
    for t in texts:
        nt = norm_text(t)
        if num_rx.search(nt) and unit_rx.search(nt):
            mo = re.search(r"(?<![\d.,])" + re.escape(num) + r"(?![\d.,])", t)
            return mo.group(0) if mo else num
    return None


_TRIM_EDGE = " ,;:-/*()"


def _verbatim_head(value: str, texts: list[str]) -> tuple[str | None, str]:
    """Longest word-prefix of a measurement ``value`` found verbatim in one of ``texts`` that is still a measurement
    (a number with a unit or word). Text values are never cut (a shortened sentence can change its meaning).
    Returns (head, remaining words)."""
    words = value.split()
    if not re.search(r"\d", value):
        return None, ""
    norm_texts = [norm_text(t) for t in texts]
    for k in range(len(words) - 1, 0, -1):
        head = " ".join(words[:k]).strip(_TRIM_EDGE.replace("(", "").replace(")", ""))
        if head.count("(") != head.count(")"):
            continue
        if not (re.search(r"\d", head) and re.search(r"[^\d\s.,]", head)):
            continue
        nh = norm_text(head)
        if nh and any(nh in t for t in norm_texts):
            return head, " ".join(words[k:]).strip(_TRIM_EDGE)
    return None, ""


def to_candidate(st: DocState, f: FactOut, method: str, section_id: str | None, agent: str,
                 allowed_blocks: list[str] | None = None) -> CandidateFact | None:
    doc = st.ingest.doc
    bmap = doc.block_map()
    value = (f.value_text or "").strip()
    if not value or len(value) > 600:
        return None
    blocks = [b for b in f.block_ids if b in bmap][:4]
    texts = [bmap[b].text for b in [*blocks, *(allowed_blocks or [])] if b in bmap]
    if _ODD_HYPHEN.search(value) and not any(_ODD_HYPHEN.search(t) for t in texts):
        # the verifier model writes non-breaking hyphens (U+2011) where the page has "-": use the page's characters
        value = _ODD_HYPHEN.sub(lambda m: "" if m.group(0) == "\u00ad" else "-", value)
    refs = find_quote(doc, blocks, value)
    if not any(r.quote for r in refs) and allowed_blocks:
        # the model cited the wrong block: look for the value inside the evidence it was shown
        for bid in allowed_blocks:
            if norm_text(value) in norm_text(bmap[bid].text):
                refs = find_quote(doc, [bid], value) + refs
                break
    conds = [Condition(dimension=c.dimension.strip()[:60], value_text=c.value_text.strip()[:200])
             for c in f.conditions if c.dimension and c.value_text]
    anomalies = []
    num = None if any(r.quote for r in refs) else _unit_from_context(value, texts)
    if num:
        # a table cell with its unit in the row / column label ("IMU: 259; Units: mm" -> "259 mm"): the number is
        # quoted verbatim, the unit comes from the label, and the verifier checks the composition
        src = [b for b in [*blocks, *(allowed_blocks or [])] if b in bmap and norm_text(num) in norm_text(bmap[b].text)]
        refs = find_quote(doc, src[:1], num) + [r for r in refs if r.block_id not in src[:1]]
        anomalies.append("unit_from_context")
    elif not any(r.quote for r in refs):
        # the model glued a condition onto the value and dropped what sat between them on the page
        # ("1,009 km (545 nm)* / 913 km (493 nm)** with standard tanks" -> "1,009 km (545 nm) with standard tanks"):
        # keep the longest verbatim head that still carries the measurement, the rest becomes a condition
        head, rest = _verbatim_head(value, texts)
        if head:
            refs = find_quote(doc, blocks, head)
            if not any(r.quote for r in refs) and allowed_blocks:
                refs = find_quote(doc, [b for b in allowed_blocks if norm_text(head) in norm_text(bmap[b].text)][:1], head) + refs
            value = head
            anomalies.append("value_trimmed")
            if rest:
                conds.append(Condition(dimension="condition", value_text=rest[:200]))
        else:
            anomalies.append("value_not_in_evidence")
    subj = f.subject_id if f.subject_id and st.kg.get(f.subject_id) else None
    if subj is None and f.subject_text:
        subj = _ensure_entity(st, f.subject_text, blocks or list(allowed_blocks or []))
    variant_id = None
    for c in conds:
        for e in st.kg.entities:
            if e.role == Role.variant and norm_name(c.value_text) in {norm_name(e.name), *[norm_name(a) for a in e.aliases]}:
                variant_id = e.entity_id
    return CandidateFact(
        fact_id=st.next_fact_id(), method=method, section_id=section_id or (bmap[blocks[0]].section_id if blocks else None),
        subject_entity_id=subj, subject_text=f.subject_text, source_parameter=(f.label or None),
        parameter_hint=re.sub(r"[^a-z0-9_]+", "_", (f.parameter or "").lower()).strip("_") or None,
        value_text=value, fact_class=f.fact_class,
        applicability=Applicability(variant_entity_id=variant_id, conditions=conds),
        evidence=[r for r in refs if r.block_id], anomalies=anomalies, agents=[agent],
    )


def link_inventory(st: DocState, facts: list[CandidateFact]) -> None:
    """Attach inventory items to facts by span overlap / value containment in the same block."""
    by_block: dict[str, list] = {}
    for it in st.ingest.inventory:
        by_block.setdefault(it.block_id, []).append(it)
    for f in facts:
        nv = norm_text(f.value_text)
        for ev in f.evidence:
            for it in by_block.get(ev.block_id, []):
                if it.item_id in f.inventory_item_ids:
                    continue
                overlap = (ev.char_start is not None and ev.char_end is not None
                           and it.char_start < ev.char_end and ev.char_start < it.char_end)
                contained = norm_text(it.text) and (norm_text(it.text) in nv or nv in norm_text(it.text))
                if overlap or contained:
                    f.inventory_item_ids.append(it.item_id)


def dedupe(facts: list[CandidateFact]) -> list[CandidateFact]:
    seen: dict[tuple, CandidateFact] = {}
    out = []
    for f in facts:
        key = (f.subject_entity_id or norm_name(f.subject_text or ""), norm_text(f.value_text),
               norm_text(f.source_parameter or f.parameter_hint or ""), f.applicability.key())
        if key in seen:
            keep = seen[key]
            for ev in f.evidence:
                if ev.block_id not in {e.block_id for e in keep.evidence}:
                    keep.evidence.append(ev)
            keep.inventory_item_ids = list(dict.fromkeys(keep.inventory_item_ids + f.inventory_item_ids))
            continue
        seen[key] = f
        out.append(f)
    return out


# ---------------------------------------------------------------------------------------------------
# A5B
# ---------------------------------------------------------------------------------------------------
async def a5b_open_discovery(rt: Runtime, st: DocState) -> list[CandidateFact]:
    doc = st.ingest.doc
    content_items = {}
    for it in st.ingest.inventory:
        if not it.dismissed_reason:
            content_items[it.block_id] = content_items.get(it.block_id, 0) + 1
    # ~70 value items -> ~100 facts -> ~9k output tokens: dense catalogue chunks must not overflow max_tokens
    chunks = build_chunks(doc, st.page_map, max_tokens=rt.settings.section_chunk_tokens, max_items=70, items=content_items)
    task = A5B_TASK.replace("{catalogue}", catalogue_for(rt, st.kg))

    async def run(c: Chunk) -> list[CandidateFact]:
        async def one(ids: list[str]) -> list[CandidateFact]:
            part = c if ids == c.block_ids else Chunk(chunk_id=c.chunk_id, section_ids=c.section_ids, block_ids=ids,
                                                      tokens=sum(est_tokens(bm[b].text) for b in ids if b in bm), roles=c.roles)
            return await run_chunk(part)
        groups = await bisect_on_overflow(one, c.block_ids)
        return [f for g in groups for f in g]

    bm = doc.block_map()

    async def run_chunk(c: Chunk) -> list[CandidateFact]:
        ev = evidence(st, c.block_ids)
        hdr = doc_header(st, c.block_ids)
        res = await ask(rt, agent="A5B", role="main", header=hdr, evidence=ev, task=task, schema=FactsOut,
                        doc_id=doc.doc_id, reasoning="off", max_tokens=12000)
        # empty-output guard: thinking-off + strict JSON occasionally collapses to {"facts": []}; if the
        # deterministic inventory says this chunk holds technical values, retry once with light thinking
        substantive = sum(content_items.get(b, 0) for b in c.block_ids) >= 2 or (
            c.tokens >= 250 and set(c.roles) & {"specifications", "product_overview", "features_capabilities", "variants"})
        if not res.parsed.facts and substantive:
            res = await ask(rt, agent="A5B:retry_thinking", role="main", header=hdr, evidence=ev, task=task,
                            schema=FactsOut, doc_id=doc.doc_id, reasoning="low", max_tokens=16000)
        out = []
        for f in res.parsed.facts:
            cf = to_candidate(st, f, "open_discovery", c.section_ids[0] if c.section_ids else None, "A5B", c.block_ids)
            if cf is not None:
                out.append(cf)
        return out

    # chunks start in document order, a few at a time, and stop starting once the document's budget is spent
    # (a 1,500-block catalogue would otherwise cost as much as a hundred ordinary pages)
    gate = asyncio.Semaphore(max(1, rt.settings.chunk_concurrency))
    skipped: list[str] = []

    async def guarded(c: Chunk) -> list[CandidateFact]:
        async with gate:
            if over_doc_budget(rt, st):
                skipped.append(c.chunk_id)
                st.skipped_block_ids.extend(c.block_ids)
                return []
            return await run(c)

    # spend where the specifications are: chunks start densest-first (value items per token), so when a document's
    # budget runs out the unread part is narrative, not specification tables
    order = sorted(range(len(chunks)), key=lambda i: -(sum(content_items.get(b, 0) for b in chunks[i].block_ids) / max(1, chunks[i].tokens)))
    done = await asyncio.gather(*[guarded(chunks[i]) for i in order], return_exceptions=True)
    results = [None] * len(chunks)
    for i, r in zip(order, done):
        results[i] = r
    if skipped:
        st.errors.append(f"A5B: document budget ${rt.settings.doc_budget_usd} reached; {len(skipped)} of {len(chunks)} "
                         f"chunks not processed ({skipped[0]}..{skipped[-1]})")
    facts: list[CandidateFact] = []
    for c, r in zip(chunks, results):
        if isinstance(r, BaseException):
            st.errors.append(f"A5B chunk {c.chunk_id}: {type(r).__name__}: {r}")
            continue
        facts.extend(r)
    facts = dedupe(facts)
    link_inventory(st, facts)
    st.discovered_inventory_ids = sorted({i for f in facts for i in f.inventory_item_ids})
    return facts


# ---------------------------------------------------------------------------------------------------
# A6
# ---------------------------------------------------------------------------------------------------
def _ambiguous(st: DocState, f: CandidateFact, product_ids: set[str], mentions: dict[str, set[str]]) -> bool:
    if f.subject_entity_id is None:
        return True
    blocks = {e.block_id for e in f.evidence}
    mentioned = {eid for eid in product_ids if mentions.get(eid, set()) & blocks}
    if len(mentioned - {f.subject_entity_id}) >= 1 and f.subject_entity_id not in mentioned:
        return True
    if len(mentioned) >= 2:
        return True
    for ref in st.kg.references:
        if ref.block_id in blocks and ref.confidence < 0.75:
            return True
    return False


async def a6_attribution(rt: Runtime, st: DocState) -> None:
    kg, doc = st.kg, st.ingest.doc
    sp = single_product(kg)
    if sp is not None:  # one product on the page: subject or the focal product owns every fact
        for f in st.facts:
            if f.owner_entity_id is None:
                f.owner_entity_id = f.subject_entity_id or sp
                f.owner_confidence = 0.95 if f.subject_entity_id in (None, sp) else 0.85
        return
    product_ids = {e.entity_id for e in kg.entities if e.role in PRODUCT_LIKE_ROLES}
    mentions = {e.entity_id: set(e.mention_block_ids) for e in kg.entities}
    todo = [f for f in st.facts if f.owner_entity_id is None and f.fact_class not in (FactClass.procurement_information,)]
    bmap = doc.block_map()
    amb: list[CandidateFact] = []
    for f in todo:
        so, src = structural_owner(st, f)
        subj = f.subject_entity_id
        if so is not None and src.startswith("table"):
            # the row label / column header names the product: it owns the cell unless the subject is its own part
            keep_subject = subj is not None and subj != so and related(st, subj, so)
            f.owner_entity_id = subj if keep_subject else so
            f.owner_confidence = 0.95
            f.attribution_note = f"structure: {src}"
            continue
        if so is not None:
            ev_blocks = {e.block_id for e in f.evidence}
            if subj is None or related(st, subj, so):
                f.owner_entity_id = subj or so
                f.owner_confidence = 0.92 if subj else 0.85
                f.attribution_note = f"structure: {src}"
                continue
            if mentions.get(subj, set()) & ev_blocks:  # the evidence itself names another product: the extractor's call
                f.owner_entity_id, f.owner_confidence = subj, 0.85
                continue
            f.alternative_owner_ids = [so]
            amb.append(f)  # the subject is neither named here nor related to this part's product: ask
            continue
        if _ambiguous(st, f, product_ids, mentions):
            amb.append(f)
        else:
            f.owner_entity_id, f.owner_confidence = subj, 0.9
    if not amb:
        return
    gate = asyncio.Semaphore(6)  # batches are independent: a 1,400-fact catalogue must not decide them one at a time

    async def decide(batch: list[CandidateFact]) -> None:
        async with gate:
            await _decide(batch)

    async def _decide(batch: list[CandidateFact]) -> None:
        ev_ids: list[str] = []
        for f in batch:
            for e in f.evidence:
                b = bmap.get(e.block_id)
                if b is None:
                    continue
                for x in doc.blocks[max(0, b.order - 2) : b.order + 2]:
                    if x.block_id not in ev_ids and x.type != BlockType.metadata:
                        ev_ids.append(x.block_id)
        ev_ids = sorted(ev_ids, key=lambda x: bmap[x].order)
        lines = []
        for f in batch:
            subj = f.subject_entity_id or f"(text: {f.subject_text})"
            hint = f" | this part describes: {', '.join(f.alternative_owner_ids)}" if f.alternative_owner_ids else ""
            lines.append(f"{f.fact_id} | proposed: {subj} | {f.source_parameter or f.parameter_hint} = {f.value_text} | "
                         f"blocks {', '.join(e.block_id for e in f.evidence)}{hint}")
        try:
            res = await ask(rt, agent="A6", role="main", header=doc_header(st, ev_ids), evidence=evidence(st, ev_ids),
                            task=A6_TASK.replace("{facts}", "\n".join(lines)), schema=A6Out, doc_id=doc.doc_id,
                            reasoning="low", max_tokens=6000)
        except Exception as e:
            st.errors.append(f"A6: {type(e).__name__}: {e}")
            for f in batch:
                f.owner_entity_id, f.owner_confidence = f.subject_entity_id, 0.6
            return
        dec = {d.fact_id: d for d in res.parsed.decisions}
        for f in batch:
            d = dec.get(f.fact_id)
            if d is None:
                f.owner_entity_id, f.owner_confidence = f.subject_entity_id, 0.6
                continue
            owner = d.owner_id if d.owner_id and kg.get(d.owner_id) else None
            f.owner_entity_id = owner
            f.owner_confidence = max(0.0, min(1.0, d.confidence)) if owner else 0.0
            f.alternative_owner_ids = [a for a in d.alternative_ids if kg.get(a) and a != owner]
            f.attribution_note = d.reason[:200]
            f.agents.append("A6")

    await asyncio.gather(*[decide(amb[i : i + 40]) for i in range(0, len(amb), 40)])


# ---------------------------------------------------------------------------------------------------
# A5A
# ---------------------------------------------------------------------------------------------------
def _found_params(st: DocState, eid: str) -> set[str]:
    out = set()
    for f in st.facts:
        if f.owner_entity_id == eid:
            for k in (f.parameter.id if f.parameter else None, f.parameter_hint):
                if k:
                    out.add(k)
            for p in rt_lookup(st, f.source_parameter):
                out.add(p)
    return out


def rt_lookup(st: DocState, label: str | None) -> list[str]:
    from ..ontology import load_ontology

    return [p.id for p in load_ontology().lookup_label(label)] if label else []


async def a5a_known_hunt(rt: Runtime, st: DocState) -> list[CandidateFact]:
    doc, kg = st.ingest.doc, st.kg
    blocks = [b for b in extractable_blocks(doc, st.page_map) if b.type != BlockType.heading]
    if not blocks:
        return []
    index = BlockIndex([b.block_id for b in blocks], [b.text for b in blocks])
    bmap = doc.block_map()
    owners = {f.owner_entity_id for f in st.facts}
    targets = [e for e in kg.entities if e.product_classes and (
        e.entity_id in kg.focal_entity_ids or (e.role == Role.related_product and e.entity_id in owners))]
    targets = targets[:4]
    new: list[CandidateFact] = []

    async def hunt(e: DefenceEntity) -> list[CandidateFact]:
        cats = [c.category for c in e.product_classes]
        expected = rt.onto.core_for(cats)
        found = _found_params(st, e.entity_id)
        missing = [p for p in expected if p not in found]
        if not missing:
            return []
        scope = set(e.mention_block_ids)
        if single_product(kg) == e.entity_id or not scope:
            scope = {b.block_id for b in blocks}
        else:  # blocks in sections where the product is mentioned
            secs = {bmap[b].section_id for b in scope if b in bmap}
            scope |= {b.block_id for b in blocks if b.section_id in secs}
        ev: list[str] = []
        lines = []
        for pid in missing:
            p = rt.onto.get(pid)
            q = " ".join([p.name, *p.aliases[:6]])
            hits = [h for h in index.search(q, k=4, allowed=scope) if find_measurements(bmap[h[0]].text) or p.type == "text"]
            if not hits:
                continue
            lines.append(f"- {p.id}: {p.name} — {p.description}")
            for bid, _ in hits[:3]:
                if bid not in ev:
                    ev.append(bid)
        if not lines:
            return []
        tokens = 0
        kept = []
        for bid in ev:
            t = est_tokens(bmap[bid].text)
            if tokens + t > 3000:
                break
            kept.append(bid)
            tokens += t
        kept = sorted(kept, key=lambda x: bmap[x].order)
        task = A5A_TASK.replace("{product}", e.name).replace("{product_id}", e.entity_id).replace("{parameters}", "\n".join(lines))
        res = await ask(rt, agent="A5A", role="main", header=doc_header(st, kept), evidence=evidence(st, kept),
                        task=task, schema=FactsOut, doc_id=doc.doc_id, reasoning="off", max_tokens=4000)
        out = []
        for f in res.parsed.facts:
            if not f.subject_id:
                f.subject_id = e.entity_id
            cf = to_candidate(st, f, "known_spec_hunter", None, "A5A", kept)
            if cf is not None:
                out.append(cf)
        return out

    results = await asyncio.gather(*[hunt(e) for e in targets], return_exceptions=True)
    for e, r in zip(targets, results):
        if isinstance(r, BaseException):
            st.errors.append(f"A5A {e.entity_id}: {type(r).__name__}: {r}")
            continue
        new.extend(r)
    existing = {(f.owner_entity_id or f.subject_entity_id, norm_text(f.value_text)) for f in st.facts}
    new = [f for f in dedupe(new) if (f.subject_entity_id, norm_text(f.value_text)) not in existing]
    link_inventory(st, new)
    for f in new:
        f.owner_entity_id = f.subject_entity_id
        f.owner_confidence = 0.85
    return new


def evidence_refs(f: CandidateFact) -> list[EvidenceRef]:
    return f.evidence


_DESIG_CAL = re.compile(
    r"(?<![\w.])(\d{1,3}(?:[.,]\d{1,2})?\s?mm(?:\s?[x×]\s?\d{2,3}(?:\s?mm)?)?|\d{1,2}(?:[.,]\d{1,2})?\s?[x×]\s?\d{2,3}\s?mm"
    r"|\.\d{2,3}\s?(?:cal\.?|BMG|ACP|Win(?:chester)?|WM|Mag(?:num)?|NATO|Lapua))", re.IGNORECASE)


def designation_specs(st: DocState) -> list[CandidateFact]:
    """Deterministic rule: a calibre stated in a focal product's own designation ("7.62 mm x 51 Ball 11 Long Range",
    "K9 155mm howitzer") is that product's calibre. Evidence is the verbatim text in the title/heading block."""
    doc, kg = st.ingest.doc, st.kg
    out: list[CandidateFact] = []
    for eid in kg.focal_entity_ids:
        e = kg.get(eid)
        if e is None:
            continue
        has = any((f.owner_entity_id or f.subject_entity_id) == eid and
                  ("calibre" in (f.parameter_hint or "") or (f.parameter is not None and f.parameter.id == "calibre"))
                  and f.verification.value != "rejected" for f in st.facts)
        if has:
            continue
        for name in [e.name, *e.aliases]:
            m = _DESIG_CAL.search(name or "")
            if not m:
                continue
            val = m.group(1).strip()
            blocks = [b for b in doc.blocks if val in b.text and (b.source == "title" or b.type == BlockType.heading or name in b.text)]
            blocks = blocks or [b for b in doc.blocks if val in b.text][:1]
            if not blocks:
                continue
            b = blocks[0]
            i = b.text.find(val)
            out.append(CandidateFact(
                fact_id=st.next_fact_id(), method="designation", section_id=b.section_id, subject_entity_id=eid,
                owner_entity_id=eid, owner_confidence=0.9, source_parameter=None, parameter_hint="calibre",
                value_text=val, fact_class=FactClass.technical_specification,
                evidence=[EvidenceRef(block_id=b.block_id, quote=val, char_start=i, char_end=i + len(val))],
                agents=["designation-rule"]))
            break
    link_inventory(st, out)
    return out
