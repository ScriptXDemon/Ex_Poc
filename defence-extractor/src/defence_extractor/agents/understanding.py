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
from .designations import LABEL_PREFIX, clean_heading, designation_tokens
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
async def a2_page_map(rt: Runtime, st: DocState) -> PageMap:
    """Page map over ALL sections: long documents are classified in windows of ``a2_window_sections`` sections (the
    first window also decides page type and scope), so no part of a 300-page catalogue is left without a role or
    subjects."""
    doc, prof = st.ingest.doc, st.ingest.profile
    hdr = header(doc, None, None, prof.language)
    ids = outline_sections(doc)
    w = max(10, rt.settings.a2_window_sections)
    windows = [ids[i:i + w] for i in range(0, len(ids), w)] or [[]]

    async def window(k: int, wids: list[str]):
        task = A2_TASK if k == 0 else A2_TASK + (f"\nThis is part {k + 1} of {len(windows)} of the outline: return sections for the "
                                                  "listed section ids only (page_type and scope as best you can).")
        return await ask(rt, agent="A2", role="main", header=hdr, doc_id=doc.doc_id, schema=A2Out, reasoning="off", max_tokens=8000,
                         evidence="# SECTION OUTLINE\n" + outline(doc, max_sections=w, section_ids=wids, include_meta=k == 0),
                         task=task)

    res = await asyncio.gather(*[window(k, w) for k, w in enumerate(windows)], return_exceptions=True)
    # a window that fails does not fail the document: its sections get roles from their scope and a spec density
    # from the values they print (deterministic), and the failure is recorded
    out = None if isinstance(res[0], BaseException) else res[0].parsed
    failed: set[str] = set()
    values: dict[str, int] = {}
    if any(isinstance(r, BaseException) for r in res):
        bmap = doc.block_map()
        for it in st.ingest.inventory:
            b = bmap.get(it.block_id)
            if b is not None and not it.dismissed_reason:
                values[b.section_id] = values.get(b.section_id, 0) + 1
    valid = {s.section_id for s in doc.sections}
    assessed = {}
    for k, r in enumerate(res):
        if isinstance(r, BaseException):
            st.errors.append(f"A2 window {k + 1}/{len(windows)}: {type(r).__name__}: {str(r)[:200]} (default roles used)")
            failed |= set(windows[k])
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
            density = "none" if role != SectionRole.other else "low"
            if s.section_id in failed and role == SectionRole.other:
                n = values.get(s.section_id, 0)
                density = "high" if n >= 5 else "medium" if n >= 2 else "low"
            sections.append(SectionAssessment(section_id=s.section_id, role=role, spec_density=density))
    if out is None:
        multi = sum(1 for b in doc.blocks if b.type == BlockType.heading and designation_tokens(b.text)) >= 2
        return PageMap(page_type="unknown", page_scope="multi_product" if multi else "single_product", sections=sections,
                       risk_flags=["page_map_failed"], confidence=0.2)
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
    chunks = build_chunks(doc, st.page_map, max_tokens=rt.settings.a3_chunk_tokens)
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
    merged = _split_combined(merged)
    merged += _designation_candidates(doc, merged)
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


MAX_DESIGNATION_ENTITIES = 300


def default_page_map(st: DocState) -> PageMap:
    """The page map when its stage failed: section roles from their scope, spec density from the values they print."""
    doc = st.ingest.doc
    bmap = doc.block_map()
    values: dict[str, int] = {}
    for it in st.ingest.inventory:
        b = bmap.get(it.block_id)
        if b is not None and not it.dismissed_reason:
            values[b.section_id] = values.get(b.section_id, 0) + 1
    sections = []
    for s in doc.sections:
        role = _SCOPE_ROLE.get(s.scope, SectionRole.other)
        n = values.get(s.section_id, 0)
        density = ("high" if n >= 5 else "medium" if n >= 2 else "low") if role == SectionRole.other else "none"
        sections.append(SectionAssessment(section_id=s.section_id, role=role, spec_density=density))
    multi = sum(1 for b in doc.blocks if b.type == BlockType.heading and designation_tokens(b.text)) >= 2
    return PageMap(page_type="unknown", page_scope="multi_product" if multi else "single_product", sections=sections,
                   risk_flags=["page_map_failed"], confidence=0.2)


def fallback_kg(st: DocState) -> KnowledgeGraph:
    """Entities when entity finding failed: the model designations in headings and product-keyed tables, else the
    document title as its one product, so extraction still has owners to attach values to."""
    doc = st.ingest.doc
    ents = []
    for i, c in enumerate(_designation_candidates(doc, []), 1):
        ents.append(DefenceEntity(entity_id=f"E{i}", name=c["name"], aliases=c["aliases"], entity_type=EntityType.product,
                                  specific_model=True, role=Role.related_product, confidence=0.4,
                                  mention_block_ids=mention_scan(doc, [c["name"], *c["aliases"]]),
                                  note="fallback: entity finding failed; model designation from the document's structure"))
    if not ents and doc.title:
        name = doc.title.strip()[:120]
        ents.append(DefenceEntity(entity_id="E1", name=name, entity_type=EntityType.product, specific_model=False,
                                  role=Role.focal_product, confidence=0.3, mention_block_ids=mention_scan(doc, [name]),
                                  note="fallback: entity finding failed; the document title stands for the product"))
    return KnowledgeGraph(entities=ents, focal_entity_ids=[ents[0].entity_id] if len(ents) == 1 else [],
                          markers=st.ingest.markers)


def fallback_roles(kg: KnowledgeGraph) -> None:
    """Roles when the role stage failed: each entity takes the role its type implies."""
    for e in kg.entities:
        if e.role == Role.other:
            e.role = {**_ROLE_FALLBACK_BASE, **_TYPE_ROLE}.get(e.entity_type, Role.other)


_ALT_NAMES = re.compile(r"\s+/\s+")
# "SHARKY 774" next to "SHARKY 775": a name and a model number as separate words (the designation pattern needs letters
# and digits in one token); two or more such headers that share the name and differ in the number are models
_NAME_NUMBER = re.compile(r"^(?P<base>[A-Za-z][\w&.-]*(?:\s[A-Za-z][\w&.-]*){0,2})\s+(?P<num>\d[\w./-]{0,8})$")
_NOT_A_MODEL_BASE = {"section", "chapter", "part", "figure", "fig", "table", "page", "appendix", "annex", "step", "phase",
                     "stage", "item", "note", "level", "option", "version", "issue", "revision", "rev", "edition",
                     "volume", "vol", "no", "number", "day", "week", "year", "group", "lot", "batch", "zone", "area"}


def _sibling_models(names: list[tuple[str, str]]) -> list[tuple[str, str]]:
    groups: dict[str, list[tuple[str, str]]] = {}
    for text, bid in names:
        name = clean_heading(text)
        m = _NAME_NUMBER.match(name)
        if m and m.group("base").split()[-1].lower() not in _NOT_A_MODEL_BASE:
            groups.setdefault(m.group("base").lower(), []).append((name, bid))
    return [x for g in groups.values() if len({n.lower() for n, _ in g}) >= 2 for x in g]


def _split_combined(merged: list[dict]) -> list[dict]:
    """A name that joins models ("SHARKY 774 / SHARKY 775", "AN/PRC-117G / AN/PRC-152A") is one entity per model: the
    combined entity matched both products, so a comparison table's values could not be told apart. Each part joins
    an entity of the same name, or becomes one."""
    combined = [m for m in merged if len(parts := [p for p in _ALT_NAMES.split(m["name"]) if p.strip()]) >= 2
                and all(re.search(r"\d", p) for p in parts) and len({norm_name(p) for p in parts}) == len(parts)]
    if not combined:
        return merged
    out = [m for m in merged if not any(m is c for c in combined)]
    for c in combined:
        for p in (p.strip() for p in _ALT_NAMES.split(c["name"]) if p.strip()):
            key = norm_name(p)
            tgt = next((x for x in out if key in x["keys"]), None)
            if tgt is None:
                out.append({"name": p, "aliases": [], "types": list(c["types"]), "specific": True,
                            "blocks": list(c["blocks"]), "keys": {key}})
            else:
                tgt["blocks"] += [b for b in c["blocks"] if b not in tgt["blocks"]]
    return out


def _designation_candidates(doc, merged: list[dict]) -> list[dict]:
    """Products the entity model missed: headings that carry a model designation ("C7451 Nickel Silver (NS2)",
    "› M322") and product-keyed table row labels / column headers. A large catalogue lists hundreds of models; the
    model lists the ones it notices, so a heading-level model with no entity got no section and no values."""
    known: set[str] = set()
    known_names: set[str] = set()
    for m in merged:
        for n in [m["name"], *m["aliases"]]:
            known |= designation_tokens(n)
            known_names.add(norm_name(n))
    out: list[dict] = []

    def add(name: str, blocks: list[str], sibling: bool = False) -> None:
        toks = designation_tokens(name)
        if (not toks and not sibling) or toks & known or norm_name(name) in known_names or len(name) > 90 \
                or LABEL_PREFIX.match(name):
            return
        known.update(toks)
        known_names.add(norm_name(name))
        out.append({"name": name, "aliases": sorted(t for t in toks if t != name.upper()), "types": [EntityType.product],
                    "specific": True, "blocks": blocks, "keys": {norm_name(name)} | {norm_name(t) for t in toks}})

    heads = [(b.text, b.block_id) for b in doc.blocks
             if b.type == BlockType.heading and b.scope == ScopeHint.main and not b.attributes.get("dup_of")]
    for text, bid in heads:
        add(clean_heading(text), [bid])
    for name, bid in _sibling_models(heads):
        add(name, [bid], sibling=True)
    bmap = doc.block_map()
    for t in doc.tables:  # rows hidden behind a vision transcription (OCR text) do not name products
        rows = [(bmap[r].attributes.get("row_label") or "", r) for r in t.row_block_ids
                if r in bmap and not bmap[r].attributes.get("dup_of")]
        named = [(lbl, r) for lbl, r in rows if lbl and designation_tokens(lbl)]
        if len(named) >= 2:  # a product-keyed table: one model per row
            for lbl, r in named:
                add(clean_heading(lbl), [r])
        heads = [c.text for c in t.cells if c.row in t.header_rows and c.col > 0 and c.text and designation_tokens(c.text)]
        if len(heads) >= 2 and rows:  # one model per column
            for h in heads:
                add(clean_heading(h), [rows[0][1]])
        if rows:  # "SHARKY 774" / "SHARKY 775" column headers, "Piranha 3" / "Piranha 5" row labels
            cols = [(c.text, rows[0][1]) for c in t.cells if c.row in t.header_rows and c.col > 0 and c.text]
            for name, bid in _sibling_models(cols) + _sibling_models([(lbl, r) for lbl, r in rows if lbl]):
                add(name, [bid], sibling=True)
    return out[:MAX_DESIGNATION_ENTITIES]


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


_ROLE_FALLBACK_BASE = {EntityType.company: Role.manufacturer, EntityType.organization: Role.customer,
                       EntityType.generic_class: Role.generic_class}
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
    ev = "# EVIDENCE\n" + render_blocks(doc, _a4_evidence(st, rt.settings.a4_evidence_tokens), page_map=st.page_map)
    ids = [e.entity_id for e in kg.entities]
    n = max(5, rt.settings.a4_batch)
    families = [e for e in kg.entities if e.entity_type == EntityType.product_family]
    emap0 = kg.entity_map()

    def batch_header(b: list[str]) -> str:
        # a small context window cannot carry a catalogue's whole registry: the batch's entries plus the families they
        # may belong to, by a shared name word (a large window keeps the whole registry, so parents can point anywhere)
        if not rt.settings.context_window:
            return hdr
        words = {w for i in b if i in emap0 for w in norm_name(emap0[i].name).split() if len(w) >= 3}
        fam = [f.entity_id for f in families if words & set(norm_name(f.name).split())][:15]
        return header(doc, st.page_map, kg, prof.language, only=set(b) | set(fam))

    if len(ids) <= n:
        batches = [ids]
        calls = [ask(rt, agent="A4", role="main", header=batch_header(ids), evidence=ev, task=task, schema=A4Out,
                     doc_id=doc.doc_id, reasoning="low", max_tokens=8000)]
    else:
        # catalogues: hundreds of registry entries do not fit one answer -> roles for one slice of ids per call
        batches = [ids[i:i + n] for i in range(0, len(ids), n)]
        calls = [ask(rt, agent="A4", role="main", header=batch_header(b), evidence=ev, schema=A4Out, doc_id=doc.doc_id,
                     reasoning="low", max_tokens=8000, task=task + "\nTHIS CALL: give roles ONLY for these registry ids: "
                     + ", ".join(b) + ". Report relations and same_entity_groups only when they involve at least one of these ids.")
                 for b in batches]
    rs = await asyncio.gather(*calls, return_exceptions=True)
    ok = [r.parsed for r in rs if not isinstance(r, BaseException)]
    for r in rs:
        if isinstance(r, BaseException):  # no roles from this call: the entity types decide below, the document goes on
            st.errors.append(f"A4 batch: {type(r).__name__}: {str(r)[:200]}")
    out = A4Out(focal_entity_ids=list(dict.fromkeys(f for o in ok for f in o.focal_entity_ids)),
                roles=[x for o in ok for x in o.roles], relations=[x for o in ok for x in o.relations],
                same_entity_groups=[x for o in ok for x in o.same_entity_groups])
    # the model sometimes leaves registry ids without a role (seen on a 127-entity page: a third of them): ask once more
    # for exactly those ids, so radars, guns and gearboxes are not left as "other" and dropped from the product records
    got = {r.entity_id for r in out.roles}
    missing = [i for i in ids if i not in got]
    if missing:
        rs = await asyncio.gather(*[
            ask(rt, agent="A4:missing", role="main", header=batch_header(b), evidence=ev, schema=A4Out, doc_id=doc.doc_id,
                reasoning="low", max_tokens=8000, task=task + "\nTHIS CALL: give roles ONLY for these registry ids "
                "(every one of them): " + ", ".join(b) + ". Report relations only when they involve at least one of these ids.")
            for b in [missing[i:i + n] for i in range(0, len(missing), n)]], return_exceptions=True)
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
            # "other" on a product, weapon, sensor, ship... (or no answer at all) loses its values from the product
            # records and hides it from the product map: the entity type A3 gave decides the role instead
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
    from .segments import local_entities

    ids_all = [b.block_id for b in cands[:60]]
    bmap = doc.block_map()
    order = {b.order: b for b in doc.blocks}
    emap = kg.entity_map()

    async def resolve(ids: list[str]) -> list[ReferenceLink]:
        context = []
        for bid in ids:  # include the previous block for antecedents
            prev = order.get(bmap[bid].order - 1)
            if prev is not None:
                context.append(prev.block_id)
            context.append(bid)
        ctx_ids = sorted(dict.fromkeys(context), key=lambda x: bmap[x].order)
        hdr = header(doc, st.page_map, kg, st.ingest.profile.language, only=local_entities(st, ctx_ids))
        res = await ask(rt, agent="A7", role="main", header=hdr, evidence="# EVIDENCE\n" + render_blocks(doc, ctx_ids, page_map=st.page_map),
                        task=A7_TASK + "\nBlocks to resolve: " + ", ".join(ids), schema=A7Out, doc_id=doc.doc_id,
                        reasoning="low", max_tokens=5000)
        return [ReferenceLink(block_id=l.block_id, phrase=l.phrase, entity_id=l.entity_id if l.entity_id in emap else None,
                              confidence=max(0.0, min(1.0, l.confidence)))
                for l in res.parsed.links if l.block_id in bmap]

    n = max(5, rt.settings.a7_batch_blocks)
    groups = await asyncio.gather(*[bisect_on_overflow(resolve, ids_all[i:i + n]) for i in range(0, len(ids_all), n)],
                                  return_exceptions=True)
    links: list[ReferenceLink] = []
    for g in groups:
        if isinstance(g, BaseException):  # those references stay unresolved; section ownership still applies
            st.errors.append(f"A7: {type(g).__name__}: {str(g)[:200]}")
            continue
        links += [x for part in g for x in part]
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
