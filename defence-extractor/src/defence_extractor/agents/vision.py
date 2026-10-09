"""Page-level vision for PDFs: pages without a text layer, or carried mostly by images, are rendered and transcribed
by the (multimodal) main model into ordinary evidence blocks, marked ``vision``.

Routing is per page, not per file: text-layer pages keep the exact characters of the PDF (the source of truth for
verbatim evidence); only pages the text layer cannot represent go through vision. Each transcribed page becomes its
own section(s) right after the page's text, so every later agent reads it in place with its own heading context.
"""

from __future__ import annotations

import asyncio
import base64
import io
import logging
from typing import Literal

from pydantic import BaseModel

from ..contracts.document import BlockType, ScopeHint, Section, SemanticBlock, SemanticDocument
from ..pipeline.state import DocState
from .base import Runtime, ask, norm_text
from .prompts import VISION_TASK

log = logging.getLogger(__name__)


class VisionBlockOut(BaseModel):
    kind: Literal["heading", "paragraph", "label_value", "table_row", "list_item", "caption"]
    text: str


class VisionPageOut(BaseModel):
    has_technical_content: bool
    blocks: list[VisionBlockOut]


_KIND = {"heading": BlockType.heading, "paragraph": BlockType.paragraph, "label_value": BlockType.key_value,
         "table_row": BlockType.table_row, "list_item": BlockType.list_item, "caption": BlockType.caption}


def vision_pages(doc: SemanticDocument, min_image_share: float, max_text_chars: int, limit: int) -> list[int]:
    """Pages to transcribe: no text layer, or mostly image with little text (in page order, at most ``limit``)."""
    stats = doc.metadata.get("page_stats") or {}
    out = []
    for k in sorted(stats, key=lambda x: int(x)):
        s = stats[k]
        if s.get("chars", 0) < 40 or (s.get("img", 0) >= min_image_share and s.get("chars", 0) < max_text_chars):
            out.append(int(k))
    return out[:limit]


def render_pages(path: str, pages: list[int], long_side_px: int = 1600) -> dict[int, str]:
    """JPEG data URLs of the given 1-based pages (long side about ``long_side_px``)."""
    import pypdfium2 as pdfium

    from ..ingest.pdf_adapter import PDFIUM_LOCK

    with PDFIUM_LOCK:  # pdfium is not thread-safe; documents are parsed and rendered in parallel threads
        return _render(pdfium, path, pages, long_side_px)


def _render(pdfium, path: str, pages: list[int], long_side_px: int) -> dict[int, str]:
    out: dict[int, str] = {}
    pdf = pdfium.PdfDocument(path)
    try:
        for p in pages:
            if not 1 <= p <= len(pdf):
                continue
            page = pdf[p - 1]
            w, h = page.get_size()
            scale = max(1.0, min(4.0, long_side_px / max(w, h, 1.0)))
            img = page.render(scale=scale).to_pil().convert("RGB")
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=85)
            out[p] = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    finally:
        pdf.close()
    return out


def insert_page_blocks(doc: SemanticDocument, page: int, blocks: list[VisionBlockOut]) -> int:
    """Add transcribed blocks for ``page`` as new section(s) placed after the page's existing text. Returns count."""
    existing = {norm_text(b.text) for b in doc.blocks if b.page == page}
    pos = max((i for i, b in enumerate(doc.blocks) if b.page is not None and b.page <= page), default=-1) + 1
    anchor = doc.blocks[pos - 1] if pos > 0 else None
    parent_path = list(anchor.heading_path[:-1]) if anchor is not None and anchor.heading_path else []
    smap = doc.section_map()
    anchor_sec = smap.get(anchor.section_id) if anchor is not None else None
    level = (anchor_sec.level if anchor_sec and anchor_sec.level else 1)
    new_blocks: list[SemanticBlock] = []
    new_sections: list[Section] = []
    sec: Section | None = None
    for i, vb in enumerate(blocks, 1):
        text = " ".join((vb.text or "").split())
        if not text:
            continue
        if sec is None or vb.kind == "heading":
            heading = text if vb.kind == "heading" else f"Page {page} (image content)"
            sec = Section(section_id=f"v{page}s{len(new_sections) + 1}", heading=heading, level=level,
                          heading_path=[*parent_path, heading], pages=[page])
            new_sections.append(sec)
        attrs = {"vision": "1"}
        if norm_text(text) in existing:
            attrs["dup_of"] = "text_layer"
        new_blocks.append(SemanticBlock(
            block_id=f"v{page}b{i}", type=_KIND[vb.kind], text=text, order=0, section_id=sec.section_id,
            heading_path=list(sec.heading_path), level=level if vb.kind == "heading" else None, dom_path=f"page{page}/vision",
            scope=ScopeHint.main, source="pdf", page=page, attributes=attrs))
    if not new_blocks:
        return 0
    doc.blocks[pos:pos] = new_blocks
    for i, b in enumerate(doc.blocks):
        b.order = i
    for s in new_sections:
        s.block_ids = [b.block_id for b in new_blocks if b.section_id == s.section_id]
        s.char_count = sum(len(b.text) for b in new_blocks if b.section_id == s.section_id)
    # sections in reading order: right after the section that holds the anchor block
    si = next((i for i, s in enumerate(doc.sections) if anchor is not None and s.section_id == anchor.section_id), len(doc.sections) - 1)
    doc.sections[si + 1:si + 1] = new_sections
    return len(new_blocks)


async def a0_vision(rt: Runtime, st: DocState) -> None:
    from ..ingest import analyse

    doc = st.ingest.doc
    if doc.format != "pdf" or not rt.settings.vision_enabled or not doc.source_path:
        return
    pages = vision_pages(doc, rt.settings.vision_min_image_share, rt.settings.vision_max_text_chars, rt.settings.vision_max_pages)
    if not pages:
        return
    images = await asyncio.to_thread(render_pages, doc.source_path, pages)
    hdr = f"# DOCUMENT\nid: {doc.doc_id} | format: pdf | title: {doc.title or '(none)'}"

    async def one(p: int) -> tuple[int, VisionPageOut | None]:
        if p not in images:
            return p, None
        res = await ask(rt, agent="VISION", role="vision", header=hdr, evidence="", doc_id=doc.doc_id, schema=VisionPageOut,
                        task=VISION_TASK.replace("{page}", str(p)), reasoning="off", max_tokens=6000, images=[images[p]])
        return p, res.parsed

    results = await asyncio.gather(*[one(p) for p in pages], return_exceptions=True)
    added: dict[str, int] = {}
    for r in sorted((r for r in results if not isinstance(r, BaseException)), key=lambda x: x[0], reverse=True):
        p, out = r
        if out is not None and out.has_technical_content and out.blocks:
            added[str(p)] = insert_page_blocks(doc, p, out.blocks)  # last page first: earlier positions stay valid
    for r in results:
        if isinstance(r, BaseException):
            st.errors.append(f"VISION: {type(r).__name__}: {str(r)[:200]}")
    doc.metadata["vision_pages"] = {"checked": pages, "blocks_added": added}
    if added:
        st.ingest = analyse(doc)  # inventory / profile / markers now include the transcribed pages
