"""Product map: which product each part of the document describes (deterministic, no LLM calls).

Long multi-product documents fail on ownership, not on reading: a catalogue table that continues on the next page, or a
product name styled as plain text, leaves a chunk without its product in view. The product map gives every section its
product(s) from, in order of strength:
  1. product names in the section's heading path (deepest heading first, most specific name first),
  2. the page map's per-section subjects,
  3. a running page header / footer naming one product (PDF catalogues),
  4. the product named in the first blocks of the section,
  5. continuation of the previous section (same or next page),
  6. the single focal product of a single-product document.
It also finds product-keyed tables (row labels or column headers that are product names), whose cells belong to their
row / column product. Agents then (a) read each chunk with "describes: <product>" next to every section heading,
(b) see only the entities relevant to the blocks in front of them (local registry), and (c) attribute facts
deterministically where the document's structure decides the owner.
"""

from __future__ import annotations

import re

from ..contracts.common import PRODUCT_LIKE_ROLES, Role
from ..contracts.document import BlockType, ScopeHint
from ..contracts.entities import DefenceEntity
from ..contracts.facts import CandidateFact
from ..pipeline.state import DocState
from .base import Runtime, norm_text

_PRIORITY = {Role.focal_product: 0, Role.related_product: 1, Role.variant: 1, Role.family: 2, Role.accessory: 2,
             Role.weapon: 3, Role.ammunition: 3, Role.compatible_system: 4, Role.launch_platform: 4, Role.carrier_platform: 4,
             Role.subsystem: 5, Role.component: 5, Role.sensor: 5, Role.engine: 5}
MAX_LOCAL = 40
_DESIGNATION = re.compile(r"(?<![\w/.])(?=[A-Za-z0-9-]*\d)(?=[A-Za-z0-9-]*[A-Za-z])[A-Za-z0-9][A-Za-z0-9-]{2,}(?![\w.])")


def _owner_candidates(st: DocState) -> list[DefenceEntity]:
    return [e for e in st.kg.entities if e.role in PRODUCT_LIKE_ROLES and e.specific_model]


class NameMatcher:
    """Finds registry products in text: one alternation, longest names first, word boundaries, case-insensitive."""

    def __init__(self, entities: list[DefenceEntity]):
        self.names: dict[str, str] = {}
        for e in sorted(entities, key=lambda x: _PRIORITY.get(x.role, 9)):
            for n in [e.name, *e.aliases]:
                n = (n or "").strip()
                if not n or len(n) > 80 or (len(n) < 3 and not re.search(r"\d", n)):
                    continue
                self.names.setdefault(n.lower(), e.entity_id)
        alts = sorted(self.names, key=len, reverse=True)
        self.rx = re.compile(r"(?<!\w)(" + "|".join(re.escape(a) for a in alts) + r")(?!\w)", re.IGNORECASE) if alts else None

    def find(self, text: str) -> list[str]:
        if self.rx is None or not text:
            return []
        out: list[str] = []
        for m in self.rx.finditer(text):
            eid = self.names.get(m.group(1).lower())
            if eid and eid not in out:
                out.append(eid)
        return out


def _most_specific(ids: list[str], emap: dict[str, DefenceEntity]) -> list[str]:
    """Drop a family when one of its variants / members is also named (heading "NEGEV NG-7" names both)."""
    keep = []
    for i in ids:
        children = [j for j in ids if j != i and emap[j].parent_entity_id == i]
        if not children:
            keep.append(i)
    return keep or ids


def build_product_map(rt: Runtime, st: DocState) -> None:
    doc, kg = st.ingest.doc, st.kg
    st.segments, st.segment_source, st.row_owners, st.col_owners = {}, {}, {}, {}
    if kg is None:
        return
    cands = _owner_candidates(st)
    emap = kg.entity_map()
    if not cands:
        return
    match = NameMatcher(cands)
    bmap = doc.block_map()
    focal = [f for f in kg.focal_entity_ids if f in emap]
    single = len(focal) == 1 and len([e for e in cands if _PRIORITY.get(e.role, 9) <= 2]) <= 1
    subjects = {s.section_id: s.subjects for s in st.page_map.sections} if st.page_map else {}

    # running page headers / footers that name exactly one product (catalogue pages)
    page_owner: dict[int, str] = {}
    for b in doc.blocks:
        if b.page and b.scope == ScopeHint.footer:
            found = _most_specific(match.find(b.text), emap)
            if len(found) == 1:
                page_owner.setdefault(b.page, found[0])

    prev: list[str] = []
    prev_pages: list[int] = []
    for s in doc.sections:
        owners: list[str] = []
        src = ""
        for h in reversed(s.heading_path or ([s.heading] if s.heading else [])):
            found = _most_specific(match.find(h), emap)
            if found:
                owners, src = found[:3], "heading"
                break
        if not owners and subjects.get(s.section_id):
            found = _most_specific([x for n in subjects[s.section_id] for x in match.find(n)], emap)
            if found:
                owners, src = list(dict.fromkeys(found))[:3], "page_map"
        if not owners and s.pages:
            po = {page_owner[p] for p in s.pages if p in page_owner}
            if len(po) == 1:
                owners, src = [po.pop()], "page_header"
        if not owners:
            lead = [bmap[i] for i in s.block_ids[:4] if i in bmap and bmap[i].scope == ScopeHint.main]
            found = _most_specific([x for b in lead for x in match.find(b.text)], emap)
            if len(found) == 1:
                owners, src = found, "lead_mention"
        # a heading carrying a model designation we do not know ("C7451 Nickel Silver (NS2)" when the registry missed
        # it) starts another product: never continue the previous product across it, leave the part unowned instead
        new_title = bool(not owners and s.heading and _DESIGNATION.search(s.heading))
        if not owners and prev and not new_title and (not s.pages or not prev_pages or min(s.pages) - max(prev_pages) <= 1):
            owners, src = list(prev), "continuation"
        if not owners and single:
            owners, src = [focal[0]], "single_product"
        if owners:
            st.segments[s.section_id] = owners
            st.segment_source[s.section_id] = src
            prev, prev_pages = owners, s.pages or prev_pages
        elif s.heading:  # a new heading that names no product ends the previous product's run
            prev, prev_pages = [], []

    # product-keyed tables: rows labelled with products, or columns headed by products. A table split by a page break
    # is judged as one table (its continuation parts point to the first part).
    groups: dict[str, list[str]] = {}
    for t in doc.tables:
        groups.setdefault(_continued(t) or t.table_id, []).extend(t.row_block_ids)
    for row_ids in groups.values():
        rows = [bmap[b] for b in row_ids if b in bmap]
        labelled = [(b, b.attributes.get("row_label")) for b in rows if b.attributes.get("row_label")]
        hits = [(b, _most_specific(match.find(lbl), emap)) for b, lbl in labelled]
        distinct = {h[0] for _, h in hits if len(h) == 1}
        if len(distinct) >= 2 and sum(1 for _, h in hits if len(h) == 1) >= max(2, len(labelled) // 2):
            for b, h in hits:
                if len(h) == 1:
                    st.row_owners[b.block_id] = h[0]
    for t in doc.tables:
        heads: dict[str, str] = {}
        for c in t.cells:
            if c.row in t.header_rows and c.col > 0 and c.text:
                h = _most_specific(match.find(c.text), emap)
                if len(h) == 1:
                    heads[c.text] = h[0]
        if len(set(heads.values())) >= 2:
            st.col_owners[t.table_id] = heads
    # tables that continue another table (page break) inherit its column owners
    for t in doc.tables:
        cont = _continued(t)
        if cont and cont in st.col_owners and t.table_id not in st.col_owners:
            st.col_owners[t.table_id] = st.col_owners[cont]


def _continued(t) -> str | None:
    """Id of the table this one continues after a page break (set by the PDF parser), else None."""
    cap = t.caption or ""
    return cap.removeprefix("continues ").strip() or None if cap.startswith("continues ") else None


# ---------------------------------------------------------------------------------------------------
# use by the agents
# ---------------------------------------------------------------------------------------------------
_SEG = re.compile(r"(?:^|;\s*|—\s*)([^:;—]{1,80}):\s*([^;]+)")


def column_owner(st: DocState, block, value: str) -> str | None:
    """Owner of a value inside a row of a column-keyed table ("Range — Product A: 10 km; Product B: 12 km")."""
    heads = st.col_owners.get(block.table_id or "")
    if not heads:
        return None
    nv = norm_text(value)
    for m in _SEG.finditer(block.text):
        head, val = m.group(1).strip(), m.group(2)
        if head in heads and nv and nv in norm_text(val):
            return heads[head]
    return None


def structural_owner(st: DocState, f: CandidateFact) -> tuple[str | None, str]:
    """The owner the document's structure assigns to a fact: product-keyed table row / column, else the single product
    of its section. Returns (entity id or None, source)."""
    bmap = st.ingest.doc.block_map()
    for e in f.evidence:
        if e.block_id in st.row_owners:
            return st.row_owners[e.block_id], "table_row"
        b = bmap.get(e.block_id)
        if b is not None and b.table_id:
            o = column_owner(st, b, f.value_text)
            if o:
                return o, "table_column"
    for e in f.evidence:
        b = bmap.get(e.block_id)
        if b is None:
            continue
        owners = st.segments.get(b.section_id) or []
        if len(owners) == 1:
            return owners[0], "section:" + st.segment_source.get(b.section_id, "")
        break
    return None, ""


def related(st: DocState, a: str | None, b: str | None) -> bool:
    """a and b are the same entity, or one is the parent of the other (a product and its variant / subsystem)."""
    if not a or not b:
        return False
    if a == b:
        return True
    emap = st.kg.entity_map()
    ea, eb = emap.get(a), emap.get(b)
    return bool((ea and ea.parent_entity_id == b) or (eb and eb.parent_entity_id == a))


def local_entities(st: DocState, block_ids: list[str]) -> set[str] | None:
    """Registry entries relevant to these blocks (None = show the whole registry because it is small)."""
    kg = st.kg
    if kg is None or len(kg.entities) <= MAX_LOCAL:
        return None
    doc = st.ingest.doc
    bmap = doc.block_map()
    emap = kg.entity_map()
    blocks = set(block_ids)
    picked: list[str] = []

    def add(eid: str | None) -> None:
        if eid and eid in emap and eid not in picked:
            picked.append(eid)

    secs = list(dict.fromkeys(bmap[b].section_id for b in block_ids if b in bmap))
    for sid in secs:
        for o in st.segments.get(sid, []):
            add(o)
    for b in block_ids:
        add(st.row_owners.get(b))
    for heads in (st.col_owners.get(bmap[b].table_id or "", {}) for b in block_ids if b in bmap):
        for o in heads.values():
            add(o)
    for e in kg.entities:
        if blocks & set(e.mention_block_ids):
            add(e.entity_id)
    for eid in list(picked):  # parents and direct children (variants, subsystems) of what is in view
        add(emap[eid].parent_entity_id)
    for e in kg.entities:
        if e.parent_entity_id in picked[:12]:
            add(e.entity_id)
    for f in kg.focal_entity_ids[:3]:
        add(f)
    return set(picked[:MAX_LOCAL])


def context_notes(st: DocState) -> tuple[dict[str, str], dict[str, str]]:
    """Text shown to agents: per section "describes: E7 NEGEV NG-7", per product-keyed row "row of: E7 ..."."""
    emap = st.kg.entity_map() if st.kg else {}

    def name(eid: str) -> str:
        e = emap.get(eid)
        return f"{eid} {e.name}" if e else eid

    sec = {sid: "describes: " + "; ".join(name(o) for o in owners) for sid, owners in st.segments.items() if owners}
    rows = {bid: "row of: " + name(o) for bid, o in st.row_owners.items()}
    return sec, rows
