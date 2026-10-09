"""Page understanding agents: A2 page map, A3 entity discovery, A4 roles/relations/classes, A7 references."""

from __future__ import annotations

import asyncio
import re

from ..contracts.common import PRODUCT_LIKE_ROLES, EntityType, Predicate, Role
from ..contracts.document import NON_CONTENT_SCOPES, BlockType, ScopeHint
from ..contracts.entities import DefenceEntity, KnowledgeGraph, ProductClass, ReferenceLink, RelationEdge
from ..contracts.page import PageMap, SectionAssessment, SectionRole
from ..pipeline.render import build_chunks, est_tokens, header, outline, outline_sections, render_blocks
from ..pipeline.state import DocState
from .base import Runtime, ask, bisect_on_overflow, mention_scan, norm_name
from .prompts import A2_TASK, A3_TASK, A4_TASK, A7_TASK
from .schemas import A2Out, A3Out, A4Out, A7Out

_SCOPE_ROLE = {
    ScopeHint.navigation: SectionRole.navigation,
    ScopeHint.footer: SectionRole.footer,
    ScopeHint.header: SectionRole.navigation,
    ScopeHint.cookie: SectionRole.contact_legal,
    ScopeHint.form: SectionRole.contact_legal,
    ScopeHint.related_content: SectionRole.related_products,
}


# ---------------------------------------------------------------------------------------------------
# A2
# ---------------------------------------------------------------------------------------------------
A2_WINDOW = 150


async def a2_page_map(rt: Runtime, st: DocState) -> PageMap:
    """Page map over ALL sections: long documents are classified in windows of A2_WINDOW sections (the first window
    also decides page type and scope), so no part of a 300-page catalogue is left without a role or subjects."""
    doc, prof = st.ingest.doc, st.ingest.profile
    hdr = header(doc, None, None, prof.language)
    ids = outline_sections(doc)
    windows = [ids[i:i + A2_WINDOW] for i in range(0, len(ids), A2_WINDOW)] or [[]]

    async def window(k: int, wids: list[str]):
        task = A2_TASK if k == 0 else A2_TASK + (f"\nThis is part {k + 1} of {len(windows)} of the outline: return sections for the "
                                                  "listed section ids only (page_type and scope as best you can).")
        return await ask(rt, agent="A2", role="main", header=hdr, doc_id=doc.doc_id, schema=A2Out, reasoning="off", max_tokens=8000,
                         evidence="# SECTION OUTLINE\n" + outline(doc, max_sections=A2_WINDOW, section_ids=wids, include_meta=k == 0),
                         task=task)

    res = await asyncio.gather(*[window(k, w) for k, w in enumerate(windows)], return_exceptions=True)
    if isinstance(res[0], BaseException):
        raise res[0]
    out = res[0].parsed
    valid = {s.section_id for s in doc.sections}
    assessed = {}
    for r in res:
        if isinstance(r, BaseException):
            st.errors.append(f"A2 window: {type(r).__name__}: {str(r)[:200]}")
            continue
        for s in r.parsed.sections:
            if s.section_id in valid and s.section_id not in assessed:
                assessed[s.section_id] = s
    sections = []
    for s in doc.sections:
        a = assessed.get(s.section_id)
        if a is not None:
            role = a.role
            if s.scope in NON_CONTENT_SCOPES and role not in (SectionRole.navigation, SectionRole.footer):
                role = _SCOPE_ROLE.get(s.scope, role)
            sections.append(SectionAssessment(section_id=s.section_id, role=role, spec_density=a.spec_density,
                                              subjects=a.subjects))
        else:
            role = _SCOPE_ROLE.get(s.scope, SectionRole.other)
            sections.append(SectionAssessment(section_id=s.section_id, role=role, spec_density="none" if role != SectionRole.other else "low"))
    return PageMap(page_type=out.page_type, page_scope=out.page_scope,
                   source_company_candidates=out.source_company_candidates,
                   primary_subject_candidates=out.primary_subject_candidates, sections=sections,
                   risk_flags=out.risk_flags, confidence=out.confidence)


# ---------------------------------------------------------------------------------------------------
# A3
# ---------------------------------------------------------------------------------------------------
async def a3_entities(rt: Runtime, st: DocState) -> KnowledgeGraph:
    doc, prof = st.ingest.doc, st.ingest.profile
    hdr = header(doc, st.page_map, None, prof.language)
    meta = [b.block_id for b in doc.blocks if b.type == BlockType.metadata][:4]
    chunks = build_chunks(doc, st.page_map, max_tokens=6000)
    if not chunks:
        chunks_ids = [meta]
    else:
        chunks_ids = [meta + c.block_ids if i == 0 else c.block_ids for i, c in enumerate(chunks)]
    content_items = {}
    for it in st.ingest.inventory:
        if not it.dismissed_reason:
            content_items[it.block_id] = content_items.get(it.block_id, 0) + 1

    async def run(ids: list[str]):
        ev = "# EVIDENCE\n" + render_blocks(doc, ids, page_map=st.page_map)
        r = await ask(rt, agent="A3", role="main", header=hdr, evidence=ev, task=A3_TASK, schema=A3Out,
                      doc_id=doc.doc_id, reasoning="off", max_tokens=6000)
        if not r.parsed.entities and sum(content_items.get(b, 0) for b in ids) >= 2:  # empty-output guard
            r = await ask(rt, agent="A3:retry_thinking", role="main", header=hdr, evidence=ev, task=A3_TASK,
                          schema=A3Out, doc_id=doc.doc_id, reasoning="low", max_tokens=10000)
        return r

    groups = await asyncio.gather(*[bisect_on_overflow(run, ids) for ids in chunks_ids if ids], return_exceptions=True)
    results = [r for g in groups if not isinstance(g, BaseException) for r in g]
    errs = [g for g in groups if isinstance(g, BaseException)]
    for g in errs:
        st.errors.append(f"A3 chunk: {type(g).__name__}: {str(g)[:200]}")
    if errs and not results:
        raise errs[0]
    valid_blocks = {b.block_id for b in doc.blocks}
    merged: list[dict] = []
    for r in results:
        for e in r.parsed.entities:
            name = (e.name or "").strip()
            if not name or len(name) > 120:
                continue
            keys = {norm_name(name)} | {norm_name(a) for a in e.aliases if a and len(a) < 120}
            keys.discard("")
            target = None
            for m in merged:
                if m["keys"] & keys:
                    target = m
                    break
            if target is None:
                merged.append({"name": name, "aliases": [a for a in e.aliases if a and a != name], "types": [e.entity_type],
                               "specific": e.specific_model, "blocks": [b for b in e.block_ids if b in valid_blocks],
                               "keys": keys})
            else:
                target["keys"] |= keys
                target["types"].append(e.entity_type)
                target["specific"] = target["specific"] or e.specific_model
                for a in [name, *e.aliases]:
                    if a and a != target["name"] and a not in target["aliases"]:
                        target["aliases"].append(a)
                target["blocks"] += [b for b in e.block_ids if b in valid_blocks and b not in target["blocks"]]
    entities: list[DefenceEntity] = []
    for m in merged:
        mentions = mention_scan(doc, [m["name"], *m["aliases"]])
        for b in m["blocks"]:
            if b not in mentions:
                mentions.append(b)
        order = min((doc.block_map()[b].order for b in mentions if b in doc.block_map()), default=10**9)
        etype = max(set(m["types"]), key=m["types"].count)
        entities.append(DefenceEntity(entity_id="", name=m["name"], aliases=m["aliases"][:12], entity_type=etype,
                                      specific_model=m["specific"], mention_block_ids=mentions[:60],
                                      confidence=0.8, note=str(order)))
    entities.sort(key=lambda e: int(e.note or 10**9))
    for i, e in enumerate(entities, 1):
        e.entity_id, e.note = f"E{i}", None
    return KnowledgeGraph(entities=entities, markers=st.ingest.markers)


# ---------------------------------------------------------------------------------------------------
# A4
# ---------------------------------------------------------------------------------------------------
def _a4_evidence(st: DocState, budget_tokens: int = 5000) -> list[str]:
    doc, kg = st.ingest.doc, st.kg
    ids: list[str] = [b.block_id for b in doc.blocks if b.type == BlockType.metadata][:3]
    content = [b for b in doc.blocks if b.scope not in NON_CONTENT_SCOPES and not b.attributes.get("dup_of")
               and b.type != BlockType.metadata]
    mention_count: dict[str, int] = {}
    for e in kg.entities:
        for bid in e.mention_block_ids:
            mention_count[bid] = mention_count.get(bid, 0) + 1
    multi = [b.block_id for b in content if mention_count.get(b.block_id, 0) >= 2]
    first = [b.block_id for b in content[:12]]
    heads = [b.block_id for b in content if b.type == BlockType.heading][:40]
    bmap = doc.block_map()
    tokens = 0
    out: list[str] = []
    for bid in ids + first + multi + heads:
        if bid in out or bid not in bmap:
            continue
        t = est_tokens(bmap[bid].text)
        if tokens + t > budget_tokens:
            continue
        out.append(bid)
        tokens += t
    return sorted(out, key=lambda x: bmap[x].order)


A4_BATCH = 45


_TYPE_ROLE = {EntityType.product: Role.related_product, EntityType.product_family: Role.family,
              EntityType.variant: Role.variant, EntityType.subsystem: Role.subsystem, EntityType.component: Role.component,
              EntityType.weapon: Role.weapon, EntityType.ammunition: Role.ammunition, EntityType.sensor: Role.sensor,
              EntityType.engine: Role.engine, EntityType.platform: Role.related_product,
              EntityType.technology: Role.technology}


async def a4_roles(rt: Runtime, st: DocState) -> KnowledgeGraph:
    doc, prof, kg = st.ingest.doc, st.ingest.profile, st.kg
    if not kg.entities:
        return kg
    hdr = header(doc, st.page_map, kg, prof.language)
    task = A4_TASK.replace("{taxonomy}", rt.onto.taxonomy_prompt())
    ev = "# EVIDENCE\n" + render_blocks(doc, _a4_evidence(st), page_map=st.page_map)
    ids = [e.entity_id for e in kg.entities]
    if len(ids) <= A4_BATCH:
        res = await ask(rt, agent="A4", role="main", header=hdr, evidence=ev, task=task, schema=A4Out, doc_id=doc.doc_id,
                        reasoning="low", max_tokens=8000)
        out = res.parsed
    else:
        # catalogues: hundreds of registry entries do not fit one answer -> roles for one slice of ids per call
        # (every call still sees the whole registry, so parents / relations can point anywhere)
        batches = [ids[i:i + A4_BATCH] for i in range(0, len(ids), A4_BATCH)]
        rs = await asyncio.gather(*[
            ask(rt, agent="A4", role="main", header=hdr, evidence=ev, schema=A4Out, doc_id=doc.doc_id, reasoning="low",
                max_tokens=8000, task=task + "\nTHIS CALL: give roles ONLY for these registry ids: " + ", ".join(b)
                + ". Report relations and same_entity_groups only when they involve at least one of these ids.")
            for b in batches], return_exceptions=True)
        ok = [r.parsed for r in rs if not isinstance(r, BaseException)]
        for r in rs:
            if isinstance(r, BaseException):
                st.errors.append(f"A4 batch: {type(r).__name__}: {str(r)[:200]}")
        if not ok:
            raise next(r for r in rs if isinstance(r, BaseException))
        out = A4Out(focal_entity_ids=list(dict.fromkeys(f for o in ok for f in o.focal_entity_ids)),
                    roles=[x for o in ok for x in o.roles], relations=[x for o in ok for x in o.relations],
                    same_entity_groups=[x for o in ok for x in o.same_entity_groups])
    # the model sometimes leaves registry ids without a role (seen on a 127-entity page: a third of them): ask once more
    # for exactly those ids, so radars, guns and gearboxes are not left as "other" and dropped from the product records
    got = {r.entity_id for r in out.roles}
    missing = [i for i in ids if i not in got]
    if missing:
        rs = await asyncio.gather(*[
            ask(rt, agent="A4:missing", role="main", header=hdr, evidence=ev, schema=A4Out, doc_id=doc.doc_id,
                reasoning="low", max_tokens=8000, task=task + "\nTHIS CALL: give roles ONLY for these registry ids "
                "(every one of them): " + ", ".join(b) + ". Report relations only when they involve at least one of these ids.")
            for b in [missing[i:i + A4_BATCH] for i in range(0, len(missing), A4_BATCH)]], return_exceptions=True)
        for r in rs:
            if isinstance(r, BaseException):
                st.errors.append(f"A4 missing-roles call: {type(r).__name__}: {str(r)[:200]}")
                continue
            out.roles += [x for x in r.parsed.roles if x.entity_id in missing and x.entity_id not in got]
            out.relations += r.parsed.relations
            got |= {x.entity_id for x in r.parsed.roles}
    emap = kg.entity_map()
    # merges (same product under two names / order codes)
    for group in out.same_entity_groups:
        group = [g for g in group if g in emap]
        if len(group) < 2:
            continue
        keep = emap[group[0]]
        for gid in group[1:]:
            other = emap.pop(gid)
            for a in [other.name, *other.aliases]:
                if a not in keep.aliases and a != keep.name:
                    keep.aliases.append(a)
            keep.mention_block_ids = list(dict.fromkeys(keep.mention_block_ids + other.mention_block_ids))
    valid_cats = rt.onto.categories
    for r in out.roles:
        e = emap.get(r.entity_id)
        if e is None:
            continue
        e.role = r.role
        e.parent_entity_id = r.parent_entity_id if r.parent_entity_id in emap and r.parent_entity_id != e.entity_id else None
        classes = []
        for c in r.product_classes:
            cat = c.category.strip().lower().replace(" ", "_")
            dom = valid_cats.get(cat, c.domain.strip().lower())
            classes.append(ProductClass(domain=dom, category=cat))
        e.product_classes = classes[:3]
    for e in emap.values():
        if e.role == Role.other:
            fallback = {EntityType.company: Role.manufacturer, EntityType.organization: Role.customer,
                        EntityType.generic_class: Role.generic_class}
            if e.entity_id not in got:  # still no role from the model: the entity type A3 gave decides
                fallback = {**fallback, **_TYPE_ROLE}
            e.role = fallback.get(e.entity_type, Role.other)
    relations = []
    for i, r in enumerate(out.relations, 1):
        if r.subject_id not in emap:
            continue
        obj = r.object_id if r.object_id in emap else None
        if obj is None and not r.object_text:
            continue
        relations.append(RelationEdge(edge_id=f"R{i}", subject_entity_id=r.subject_id, predicate=r.predicate,
                                      object_entity_id=obj, object_literal=None if obj else r.object_text,
                                      evidence_block_ids=[b for b in r.block_ids if b in doc.block_map()]))
    focal = [f for f in out.focal_entity_ids if f in emap]
    if not focal:  # fall back: product-like entity mentioned in the title / first heading
        title_blocks = {b.block_id for b in doc.blocks if b.source == "title" or b.type == BlockType.heading}
        for e in emap.values():
            if e.role in PRODUCT_LIKE_ROLES and set(e.mention_block_ids) & title_blocks:
                focal.append(e.entity_id)
                break
    for f in focal:
        if emap[f].role not in (Role.focal_product,) and emap[f].role in PRODUCT_LIKE_ROLES | {Role.other}:
            emap[f].role = Role.focal_product
    return KnowledgeGraph(entities=list(emap.values()), relations=relations, focal_entity_ids=focal,
                          markers=kg.markers, references=kg.references)


# ---------------------------------------------------------------------------------------------------
# A7
# ---------------------------------------------------------------------------------------------------
_REF = re.compile(r"(?i)\b(?:the|this|these|its|their|both)\s+(?:system|systems|vehicle|vehicles|missile|missiles|gun|weapon|"
                  r"launcher|platform|turret|howitzer|aircraft|helicopter|drone|uav|vessel|ship|boat|radar|sensor|rifle|"
                  r"round|ammunition|munition|variant|variants|version|versions|model|models|engine|station|unit)\b|\bits\b")


async def a7_references(rt: Runtime, st: DocState) -> KnowledgeGraph:
    doc, kg = st.ingest.doc, st.kg
    product_like = [e for e in kg.entities if e.role in PRODUCT_LIKE_ROLES and e.specific_model]
    if len(product_like) < 2:
        return kg  # single-product page: generic references resolve to the focal product deterministically
    cands = [b for b in doc.blocks if b.scope not in NON_CONTENT_SCOPES and not b.attributes.get("dup_of")
             and _REF.search(b.text) and re.search(r"\d", b.text)]
    if not cands:
        return kg
    ids = [b.block_id for b in cands[:60]]
    bmap = doc.block_map()
    context = []
    for bid in ids:  # include the previous block for antecedents
        b = bmap[bid]
        prev = next((x for x in doc.blocks if x.order == b.order - 1), None)
        if prev is not None:
            context.append(prev.block_id)
        context.append(bid)
    ctx_ids = sorted(dict.fromkeys(context), key=lambda x: bmap[x].order)
    from .segments import local_entities

    hdr = header(doc, st.page_map, kg, st.ingest.profile.language, only=local_entities(st, ctx_ids))
    task = A7_TASK + "\nBlocks to resolve: " + ", ".join(ids)
    res = await ask(rt, agent="A7", role="main", header=hdr, evidence="# EVIDENCE\n" + render_blocks(doc, ctx_ids, page_map=st.page_map),
                    task=task, schema=A7Out, doc_id=doc.doc_id, reasoning="low", max_tokens=5000)
    emap = kg.entity_map()
    links = [ReferenceLink(block_id=l.block_id, phrase=l.phrase, entity_id=l.entity_id if l.entity_id in emap else None,
                           confidence=max(0.0, min(1.0, l.confidence)))
             for l in res.parsed.links if l.block_id in bmap]
    kg.references = links
    return kg


def single_product(kg: KnowledgeGraph) -> str | None:
    """The focal entity when the page has exactly one product-like specific entity, else None."""
    pl = [e for e in kg.entities if e.role in PRODUCT_LIKE_ROLES and e.specific_model]
    if len(kg.focal_entity_ids) == 1 and len(pl) <= 1:
        return kg.focal_entity_ids[0]
    return None


def is_predicate(p: str) -> bool:
    return p in Predicate.__members__
