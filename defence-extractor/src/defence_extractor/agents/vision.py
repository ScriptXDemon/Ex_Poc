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
import re
import threading
from typing import Literal

from pydantic import BaseModel

from ..contracts.document import BlockType, ScopeHint, Section, SemanticBlock, SemanticDocument
from ..pipeline.state import DocState
from .base import Runtime, ask, norm_text
from .prompts import FIGURE_TASK, VISION_TASK

log = logging.getLogger(__name__)


class VisionBlockOut(BaseModel):
    kind: Literal["heading", "paragraph", "label_value", "table_row", "list_item", "caption"]
    text: str


class VisionPageOut(BaseModel):
    has_technical_content: bool
    blocks: list[VisionBlockOut]


_KIND = {"heading": BlockType.heading, "paragraph": BlockType.paragraph, "label_value": BlockType.key_value,
         "table_row": BlockType.table_row, "list_item": BlockType.list_item, "caption": BlockType.caption}


def page_text_chars(doc: SemanticDocument) -> dict[int, int]:
    """Characters of usable text per page after parsing (text layer or OCR), headers / footers / duplicates excluded."""
    out: dict[int, int] = {}
    for b in doc.blocks:
        if b.page and b.scope == ScopeHint.main and not b.attributes.get("dup_of") and not b.attributes.get("vision"):
            out[b.page] = out.get(b.page, 0) + len(b.text)
    return out


def vision_pages(doc: SemanticDocument, min_image_share: float, max_text_chars: int, limit: int) -> list[int]:
    """Pages to transcribe whole (page order): no usable text even after OCR; mostly image with little text; or mostly
    image and readable only through OCR. OCR misreads codes ("M90RP17" for "M9ORP17", "0DG" for "ODG"), which splits a
    product in two, so an image page goes to the vision model and its OCR text stays only as the fallback."""
    stats = doc.metadata.get("page_stats") or {}
    subset = set(doc.metadata.get("page_subset") or [])
    ocr = set(doc.metadata.get("ocr_pages") or [])
    chars = page_text_chars(doc)
    out = []
    for k in sorted(stats, key=lambda x: int(x)):
        p, s = int(k), stats[k]
        if subset and p not in subset:
            continue
        c = chars.get(p, 0)
        if c < 40 or (s.get("img", 0) >= min_image_share and (c < max_text_chars or p in ocr)):
            out.append(p)
    return out[:limit]


def hide_ocr_text(doc: SemanticDocument, page: int) -> int:
    """The page's OCR blocks step aside for its vision transcription (kept in the document, out of extraction)."""
    n = 0
    for b in doc.blocks:
        if b.page == page and not b.attributes.get("vision") and not b.attributes.get("dup_of"):
            b.attributes["dup_of"] = "vision"
            n += 1
    return n


_TECH = re.compile(r"\d|(?i:dimension|specification|technical|range|diagram|schematic|layout|cross[- ]section|cut[- ]?away|"
                   r"drawing|chart|graph|performance|data)")
# a caption that announces technical content by itself ("Figure 3: M322 dimensions", "Range chart"); a bare "Figure 2"
# or a photo credit does not
_TECH_CAPTION = re.compile(r"(?i)dimension|specification|technical|diagram|schematic|layout|cross[- ]section|cut[- ]?away|"
                           r"drawing|chart|graph|performance|\bdata\b|\branges?\b|\d\s?(?:mm|cm|m|km|kg|t|°|%)\b")
MIN_FIGURE_NUMBERS = 3  # numbers printed inside a figure before it is worth a vision call (drawings, charts: many)
PAGE_SIZED = 0.85  # an image this large is the page itself: page-level vision, not a figure


def _iou(a, b) -> float:
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    if w <= 0 or h <= 0:
        return 0.0
    inter = w * h
    return inter / ((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter)


def figure_worth_reading(target: dict, printed: int | None) -> bool:
    """A figure goes to the vision model when its caption announces technical content or it prints numbers itself
    (dimension drawings, charts, tables saved as pictures); photos and logos print almost none. Without OCR
    (printed is None) the page-level rule decides."""
    if _TECH_CAPTION.search(target.get("caption") or ""):
        return True
    if printed is None:
        return bool(target.get("technical"))
    return printed >= MIN_FIGURE_NUMBERS


_OCR_ENGINE = None
_OCR_LOCK = threading.Lock()


def printed_numbers(data_url: str | None) -> int | None:
    """Tokens with a digit that CPU OCR reads inside a figure crop; None when OCR is unavailable."""
    global _OCR_ENGINE
    if not data_url:
        return None
    try:
        import numpy as np
        from PIL import Image

        with _OCR_LOCK:
            if _OCR_ENGINE is None:
                from rapidocr import RapidOCR

                _OCR_ENGINE = RapidOCR()
            img = Image.open(io.BytesIO(base64.b64decode(data_url.split(",", 1)[1]))).convert("RGB")
            res = _OCR_ENGINE(np.array(img))
        txts = list(getattr(res, "txts", None) or [])
    except Exception as e:  # no OCR engine: decide without it
        log.warning("figure OCR unavailable: %s", e)
        return None
    return sum(1 for t in txts for tok in str(t).split() if any(ch.isdigit() for ch in tok))


def figure_targets(doc: SemanticDocument, inventory, skip_pages: set[int], min_area: float, limit: int) -> list[dict]:
    """Figures on text pages worth reading: an image of at least ``min_area`` of the page that has a caption, sits on a
    page that prints specification values, or is large. Each target: page, box (PDF points, origin bottom-left),
    caption block id / text, anchor block id (where the transcription goes). Largest captioned figures first."""
    stats = doc.metadata.get("page_stats") or {}
    subset = set(doc.metadata.get("page_subset") or [])
    values_on_page: dict[int, int] = {}
    bmap = doc.block_map()
    for it in inventory or []:
        b = bmap.get(it.block_id)
        if b is not None and b.page and not it.dismissed_reason:
            values_on_page[b.page] = values_on_page.get(b.page, 0) + 1
    by_page: dict[int, list] = {}
    for b in doc.blocks:
        if b.page:
            by_page.setdefault(b.page, []).append(b)
    out: list[dict] = []
    for k, s in stats.items():
        p = int(k)
        if p in skip_pages or (subset and p not in subset) or not by_page.get(p):
            continue
        area_page = max(1.0, float(s.get("w", 1)) * float(s.get("h", 1)))
        seen: list[tuple] = []
        for l, bt, r, t in s.get("imgs") or []:
            share = (r - l) * (t - bt) / area_page
            if share < min_area or share >= PAGE_SIZED or any(_iou((l, bt, r, t), o) > 0.8 for o in seen):
                continue  # too small, the page itself, or the same image drawn twice
            seen.append((l, bt, r, t))
            cap = None
            for b in by_page[p]:  # caption just below or above the figure, overlapping it horizontally
                if b.type != BlockType.caption or not b.bbox:
                    continue
                cl, ct, cr, cb = b.bbox
                overlap = min(r, cr) - max(l, cl)
                gap = min(abs(bt - ct), abs(cb - t))
                if overlap > 0.3 * (r - l) and gap <= 72:
                    cap = b
                    break
            technical = (cap is not None and bool(_TECH.search(cap.text))) or values_on_page.get(p, 0) >= 2 or share >= 0.2
            if not (cap is not None or technical):
                continue
            if cap is not None:
                anchor = cap
            else:  # the text printed just above the figure (block bottom at or above the figure top)
                mains = [b for b in by_page[p] if b.scope == ScopeHint.main]
                above = [b for b in mains if b.bbox and b.bbox[3] >= t - 1]
                anchor = above[-1] if above else (mains[-1] if mains else by_page[p][-1])
            out.append({"page": p, "box": [l, bt, r, t], "share": round(share, 3), "caption": cap.text if cap else "",
                        "anchor": anchor.block_id, "technical": technical, "priority": (cap is not None, share)})
    out.sort(key=lambda x: x["priority"], reverse=True)
    return out[:limit]


def render_crops(path: str, targets: list[dict], long_side_px: int = 1400) -> list[str | None]:
    """JPEG data URLs of figure boxes (same order as ``targets``; None where a crop failed)."""
    import pypdfium2 as pdfium

    from ..ingest.pdf_adapter import PDFIUM_LOCK

    out: list[str | None] = []
    with PDFIUM_LOCK:
        pdf = pdfium.PdfDocument(path)
        try:
            for tg in targets:
                try:
                    page = pdf[tg["page"] - 1]
                    w, h = page.get_size()
                    l, bt, r, t = tg["box"]
                    scale = max(2.0, min(6.0, long_side_px / max(r - l, t - bt, 1.0)))
                    img = page.render(scale=scale).to_pil().convert("RGB")
                    box = (max(0, int(l * scale)), max(0, int((h - t) * scale)), min(img.width, int(r * scale)),
                           min(img.height, int((h - bt) * scale)))
                    crop = img.crop(box)
                    buf = io.BytesIO()
                    crop.save(buf, format="JPEG", quality=88)
                    out.append("data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode())
                except Exception:
                    out.append(None)
        finally:
            pdf.close()
    return out


def insert_figure_blocks(doc: SemanticDocument, target: dict, k: int, blocks: list[VisionBlockOut]) -> int:
    """Transcribed figure lines go right after the figure's caption (or the text printed above it), in that section."""
    bmap = doc.block_map()
    anchor = bmap.get(target["anchor"])
    if anchor is None:
        return 0
    existing = {norm_text(b.text) for b in doc.blocks if b.page == target["page"]}
    new_blocks: list[SemanticBlock] = []
    for i, vb in enumerate(blocks, 1):
        text = " ".join((vb.text or "").split())
        if not text:
            continue
        attrs = {"vision": "1", "figure": "1"}
        if target.get("caption"):
            attrs["figure_caption"] = target["caption"][:200]
        if norm_text(text) in existing:
            attrs["dup_of"] = "text_layer"
        btype = _KIND[vb.kind] if vb.kind != "heading" else BlockType.paragraph  # a figure title is not a section
        new_blocks.append(SemanticBlock(
            block_id=f"f{target['page']}_{k}b{i}", type=btype, text=text, order=0, section_id=anchor.section_id,
            heading_path=list(anchor.heading_path), dom_path=f"page{target['page']}/figure{k}", scope=ScopeHint.main,
            source="pdf", page=target["page"], attributes=attrs))
    if not new_blocks:
        return 0
    pos = next(i for i, b in enumerate(doc.blocks) if b.block_id == anchor.block_id) + 1
    doc.blocks[pos:pos] = new_blocks
    for i, b in enumerate(doc.blocks):
        b.order = i
    sec = doc.section_map().get(anchor.section_id)
    if sec is not None:
        j = sec.block_ids.index(anchor.block_id) + 1 if anchor.block_id in sec.block_ids else len(sec.block_ids)
        sec.block_ids[j:j] = [b.block_id for b in new_blocks]
        sec.char_count += sum(len(b.text) for b in new_blocks)
    return len(new_blocks)


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
    existing = {norm_text(b.text) for b in doc.blocks if b.page == page and not b.attributes.get("dup_of")}
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
    figures = figure_targets(doc, st.ingest.inventory, set(pages), rt.settings.figure_min_area, rt.settings.vision_max_figures)
    if not pages and not figures:
        return
    images = await asyncio.to_thread(render_pages, doc.source_path, pages) if pages else {}
    hdr = f"# DOCUMENT\nid: {doc.doc_id} | format: pdf | title: {doc.title or '(none)'}"

    async def one(p: int) -> tuple[int, VisionPageOut | None]:
        if p not in images:
            return p, None
        res = await ask(rt, agent="VISION", role="vision", header=hdr, evidence="", doc_id=doc.doc_id, schema=VisionPageOut,
                        task=VISION_TASK.replace("{page}", str(p)), reasoning="off", max_tokens=6000, images=[images[p]])
        return p, res.parsed

    results = await asyncio.gather(*[one(p) for p in pages], return_exceptions=True)
    added: dict[str, int] = {}
    ocr_pages = set(doc.metadata.get("ocr_pages") or [])
    hidden: dict[str, int] = {}
    for r in sorted((r for r in results if not isinstance(r, BaseException)), key=lambda x: x[0], reverse=True):
        p, out = r
        if out is not None and out.has_technical_content and out.blocks:
            if p in ocr_pages:
                hidden[str(p)] = hide_ocr_text(doc, p)
            added[str(p)] = insert_page_blocks(doc, p, out.blocks)  # last page first: earlier positions stay valid
    for r in results:
        if isinstance(r, BaseException):
            st.errors.append(f"VISION: {type(r).__name__}: {str(r)[:200]}")
    doc.metadata["vision_pages"] = {"checked": pages, "blocks_added": added, "ocr_blocks_replaced": hidden}

    # figures on text pages: drawings, labelled photos, tables saved as pictures
    fig_added: dict[str, int] = {}
    candidates = len(figures)
    if figures:
        crops = await asyncio.to_thread(render_crops, doc.source_path, figures)
        printed = await asyncio.to_thread(lambda: [None if _TECH_CAPTION.search(tg.get("caption") or "") else printed_numbers(u)
                                                   for tg, u in zip(figures, crops)])
        kept = [(tg, u) for tg, u, n in zip(figures, crops, printed) if u and figure_worth_reading(tg, n)]
        figures, crops = [tg for tg, _ in kept], [u for _, u in kept]
    if figures:

        async def fig(k: int, tg: dict, url: str | None) -> tuple[int, VisionPageOut | None]:
            if url is None:
                return k, None
            cap = f" Its caption reads: \"{tg['caption'][:300]}\"." if tg.get("caption") else ""
            res = await ask(rt, agent="VISION:figure", role="vision", header=hdr, evidence="", doc_id=doc.doc_id,
                            schema=VisionPageOut, task=FIGURE_TASK.replace("{page}", str(tg["page"])).replace("{caption}", cap),
                            reasoning="off", max_tokens=4000, images=[url])
            return k, res.parsed

        fres = await asyncio.gather(*[fig(k, tg, u) for k, (tg, u) in enumerate(zip(figures, crops), 1)], return_exceptions=True)
        for r in fres:
            if isinstance(r, BaseException):
                st.errors.append(f"VISION figure: {type(r).__name__}: {str(r)[:200]}")
                continue
            k, out = r
            if out is not None and out.has_technical_content and out.blocks:
                tg = figures[k - 1]
                n = insert_figure_blocks(doc, tg, k, out.blocks)
                if n:
                    fig_added[f"p{tg['page']}#{k}"] = n
    if candidates:
        doc.metadata["vision_figures"] = {"candidates": candidates, "checked": len(figures), "blocks_added": fig_added}
    if added or fig_added:
        st.ingest = analyse(doc)  # inventory / profile / markers now include the transcribed pages and figures
