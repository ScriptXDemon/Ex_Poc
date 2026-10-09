"""Deterministic, byte-stable rendering of document context for prompts (cache-friendly).

Prompt layout for every agent call:  SHARED_RULES (system)  ->  DOC HEADER  ->  EVIDENCE  ->  TASK.
The header is identical for every call on a document once page understanding is done, so providers / SGLang
can reuse the cached prefix.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..contracts.document import NON_CONTENT_SCOPES, BlockType, ScopeHint, SemanticBlock, SemanticDocument
from ..contracts.entities import KnowledgeGraph
from ..contracts.page import EXTRACTABLE_ROLES, NONMAIN_EXTRACTABLE_ROLES, PageMap, SectionRole

CHARS_PER_TOKEN = 3.6


def est_tokens(text: str) -> int:
    return int(len(text) / CHARS_PER_TOKEN) + 1


def block_line(b: SemanticBlock, note: str | None = None) -> str:
    tag = f"({note}) " if note else ""
    if b.type == BlockType.heading:
        tag = "(heading) " + tag
    elif b.type == BlockType.table_header:
        tag = "(table header) " + tag
    elif b.source in ("title", "meta", "jsonld", "breadcrumb"):
        tag = f"({b.source}) " + tag
    elif b.scope in (ScopeHint.related_content, ScopeHint.sidebar):
        tag = f"({b.scope.value}) " + tag
    if b.attributes.get("vision"):
        tag = "(read from page image) " + tag
    if b.page:
        tag = f"(p{b.page}) " + tag
    return f"[{b.block_id}] {tag}{b.text}"


def render_blocks(doc: SemanticDocument, block_ids: list[str], with_sections: bool = True,
                  page_map: PageMap | None = None, notes: tuple[dict[str, str], dict[str, str]] | None = None) -> str:
    """Evidence lines grouped under their section heading path. ``notes`` (from the product map) adds which product
    each section describes and which product a product-keyed table row belongs to."""
    bmap = doc.block_map()
    smap = doc.section_map()
    roles = {s.section_id: s.role.value for s in page_map.sections} if page_map else {}
    sec_notes, row_notes = notes if notes else ({}, {})
    lines: list[str] = []
    cur = None
    for bid in block_ids:
        b = bmap.get(bid)
        if b is None:
            continue
        if with_sections and b.section_id != cur:
            cur = b.section_id
            sec = smap.get(cur)
            path = " > ".join(sec.heading_path) if sec and sec.heading_path else (sec.heading if sec and sec.heading else "")
            role = roles.get(cur)
            lines.append(f"## {cur}" + (f" | {path}" if path else "") + (f" | section role: {role}" if role else "")
                         + (f" | {sec_notes[cur]}" if cur in sec_notes else ""))
        lines.append(block_line(b, row_notes.get(bid)))
    return "\n".join(lines)


def header(doc: SemanticDocument, page_map: PageMap | None, kg: KnowledgeGraph | None, profile_lang: str | None,
           only: set[str] | None = None) -> str:
    """Document header for every call. ``only`` restricts the entity registry to the entries relevant to the evidence
    (large documents: hundreds of entities would otherwise ride along with every call)."""
    out = ["# DOCUMENT",
           f"id: {doc.doc_id} | format: {doc.format} | language: {profile_lang or doc.language or 'unknown'}"
           + (f" | url: {doc.source_url}" if doc.source_url else ""),
           f"title: {doc.title or '(none)'}"]
    if page_map is not None:
        out.append(f"page type: {page_map.page_type} | scope: {page_map.page_scope}")
    if kg is not None and kg.entities:
        shown = [e for e in kg.entities if only is None or e.entity_id in only]
        out.append("\n# ENTITY REGISTRY (refer to entities by id)" + (
            f" — the {len(shown)} of {len(kg.entities)} entities relevant to this evidence; for another entity give "
            "subject_text" if only is not None else ""))
        for e in shown:
            parts = [e.entity_id, e.name, e.entity_type.value, e.role.value]
            if e.aliases:
                parts.append("aliases: " + "; ".join(e.aliases[:6]))
            if e.parent_entity_id:
                parts.append(f"parent: {e.parent_entity_id}")
            if e.product_classes:
                parts.append("class: " + ", ".join(f"{c.domain}/{c.category}" for c in e.product_classes[:3]))
            out.append(" | ".join(parts))
        if kg.focal_entity_ids:
            out.append("focal: " + ", ".join(kg.focal_entity_ids[:12]))
    if kg is not None and kg.markers:
        out.append("\n# MARKER LEGENDS (footnote / variant markers)")
        for m in kg.markers:
            out.append(f"{m.marker} = {m.legend_text} (legend in {m.legend_block_id}; used in {', '.join(m.used_in_block_ids[:6])})")
    return "\n".join(out)


@dataclass
class Chunk:
    chunk_id: str
    section_ids: list[str]
    block_ids: list[str]
    tokens: int = 0
    roles: list[str] = field(default_factory=list)


def extractable_blocks(doc: SemanticDocument, page_map: PageMap | None) -> list[SemanticBlock]:
    """Main-scope blocks are always extractable (a page-map misjudgement must never hide product content);
    related/sidebar/comment blocks only when the page map positively says their section is extractable (sections
    beyond the page-map outline -- e.g. a thousand reader comments -- have no role and are not read)."""
    roles = {s.section_id: s.role for s in page_map.sections} if page_map else {}
    out = []
    for b in doc.blocks:
        if b.scope in NON_CONTENT_SCOPES or b.attributes.get("dup_of") or b.type == BlockType.metadata and b.source not in ("title",):
            continue
        if b.scope in (ScopeHint.cookie, ScopeHint.form, ScopeHint.breadcrumb):
            continue
        role = roles.get(b.section_id)
        if b.scope != ScopeHint.main and (role is None or role not in NONMAIN_EXTRACTABLE_ROLES):
            continue
        out.append(b)
    return out


def build_chunks(doc: SemanticDocument, page_map: PageMap | None, max_tokens: int = 3500,
                 max_items: int | None = None, items: dict[str, int] | None = None) -> list[Chunk]:
    """Group extractable blocks into section-aligned chunks of <= max_tokens (never split inside a block).
    ``max_items`` additionally caps the number of value items per chunk (``items``: block id -> count): a dense
    catalogue table yields far more facts per input token than prose, and the answer must fit max output tokens."""
    blocks = extractable_blocks(doc, page_map)
    roles = {s.section_id: s.role.value for s in page_map.sections} if page_map else {}
    chunks: list[Chunk] = []
    cur: Chunk | None = None
    n_items = 0
    for b in blocks:
        t = est_tokens(b.text) + 4
        k = (items or {}).get(b.block_id, 0)
        new_section = cur is not None and (not cur.section_ids or cur.section_ids[-1] != b.section_id)
        too_dense = max_items is not None and cur is not None and cur.block_ids and n_items + k > max_items
        if cur is None or cur.tokens + t > max_tokens or (new_section and cur.tokens > max_tokens * 0.6) or too_dense:
            cur = Chunk(chunk_id=f"c{len(chunks) + 1}", section_ids=[], block_ids=[])
            chunks.append(cur)
            n_items = 0
        n_items += k
        if not cur.section_ids or cur.section_ids[-1] != b.section_id:
            cur.section_ids.append(b.section_id)
            r = roles.get(b.section_id)
            if r and r not in cur.roles:
                cur.roles.append(r)
        cur.block_ids.append(b.block_id)
        cur.tokens += t
    return [c for c in chunks if c.block_ids]


def outline_sections(doc: SemanticDocument) -> list[str]:
    """Section ids the page map assesses: sections with visible content (site-chrome-only sections skipped)."""
    bmap = doc.block_map()
    out = []
    for s in doc.sections:
        blocks = [bmap[i] for i in s.block_ids if i in bmap and bmap[i].type != BlockType.metadata and not bmap[i].attributes.get("dup_of")]
        if not blocks:
            continue
        if not any(b.scope == ScopeHint.main for b in blocks) and s.scope in NON_CONTENT_SCOPES and s.section_id != "s0":
            continue
        out.append(s.section_id)
    return out


def outline(doc: SemanticDocument, max_sections: int = 160, preview_chars: int = 140,
            section_ids: list[str] | None = None, include_meta: bool = True) -> str:
    """Section outline for A2: id, heading path, scope hint, size and a short preview (one window of sections)."""
    bmap = doc.block_map()
    smap = doc.section_map()
    lines = []
    if include_meta:
        meta = [b for b in doc.blocks if b.type == BlockType.metadata]
        for m in meta[:4]:
            lines.append(f"[{m.block_id}] ({m.source}) {m.text[:300]}")
    ids = section_ids if section_ids is not None else outline_sections(doc)
    for n, sid in enumerate(ids):
        if n >= max_sections:
            lines.append(f"... {len(ids) - max_sections} more sections omitted")
            break
        s = smap[sid]
        blocks = [bmap[i] for i in s.block_ids if i in bmap and bmap[i].type != BlockType.metadata and not bmap[i].attributes.get("dup_of")]
        main_blocks = [b for b in blocks if b.scope == ScopeHint.main]
        prev_src = [b for b in (main_blocks or blocks) if b.type != BlockType.heading and b.source in ("body", "pdf")]
        text = " / ".join(b.text for b in prev_src[:5])[:preview_chars]
        path = " > ".join(s.heading_path) if s.heading_path else "(preamble)"
        nt = sum(1 for b in main_blocks if b.type in (BlockType.table_row, BlockType.key_value))
        main_chars = sum(len(b.text) for b in main_blocks)
        other = sorted({b.scope.value for b in blocks if b.scope != ScopeHint.main})
        pages = f" | pages {s.pages[0]}-{s.pages[-1]}" if s.pages else ""
        lines.append(f"{s.section_id} | {path} | main content chars={main_chars}" + (f" (+ site chrome: {', '.join(other)})" if other else "")
                     + f"{pages} | kv/table rows={nt} | {text}")
    return "\n".join(lines)


SECTION_ROLE_VALUES = [r.value for r in SectionRole]
