"""How a document was read, and what kind of document it is (deterministic, recorded in every result).

- document kind: product sheet, catalogue, news, tender, manual, paper, slides... from the page map's page type and the
  challenge scan's wording hints, kept with the source so later cleaning can filter (nothing is dropped for its kind)
- page routes: for each PDF page, whether it was read from the text layer, OCR, the vision model or figure transcription
- challenges: what the PDF challenge scan found in the file and on its pages
- review reasons: why a document should be looked at by a person (failed or fallen-back stages, risky files, pages read
  only by OCR, non-product documents)
"""

from __future__ import annotations

from typing import Any

_KIND_FROM_PAGE_TYPE = {
    "manufacturer_product_page": "product_sheet", "datasheet": "product_sheet", "brochure": "product_sheet",
    "manufacturer_catalogue": "catalogue", "product_family_page": "catalogue", "comparison": "comparison",
    "defence_news": "news", "press_release": "news", "technical_article": "article", "tender": "tender",
    "company_page": "company_page",
}
_HINT_KIND = (("tender_language", "tender"), ("manual_language", "manual"), ("academic_language", "academic_paper"),
              ("news_language", "news"), ("slides", "slides"))
HIGH_RISK_FILE_FLAGS = {"encrypted", "corrupt", "not_pdf", "xfa_form", "attachments", "acro_form"}
REVIEW_PAGE_FLAGS = {"bad_text_layer", "rtl_script", "cjk_script", "redaction_suspect", "rotated_text", "tiny_text",
                     "vector_text_suspect", "oversized_page"}
NON_PRODUCT_KINDS = {"tender", "manual", "academic_paper"}  # extracted and kept; flagged for the cleaning step


def document_kind(page_type: str | None, file_flags: list[str] | set[str]) -> tuple[str, dict[str, Any]]:
    """The kind of document and the signals behind it. The page map decides; the scan's wording hints decide only when
    the page map has no clear type (a brochure that mentions maintenance stays a product sheet)."""
    flags = set(file_flags or [])
    kind = _KIND_FROM_PAGE_TYPE.get(page_type or "", "other")
    signals: dict[str, Any] = {"page_type": page_type}
    for flag, k in _HINT_KIND:
        if flag in flags:
            signals[flag] = True
            if kind == "other":
                kind = k
    return kind, signals


def pages_summary(doc) -> list[dict[str, Any]]:
    """One entry per PDF page read: route, text-layer size and quality, image share, challenge flags."""
    md = doc.metadata or {}
    stats = md.get("page_stats") or {}
    if doc.format != "pdf" or not stats:
        return []
    subset = set(md.get("page_subset") or [])
    ocr = set(md.get("ocr_pages") or [])
    vis = md.get("vision_pages") or {}
    vis_added = {int(k) for k in (vis.get("blocks_added") or {})}
    vis_checked = set(vis.get("checked") or [])
    fig_pages = {int(k.split("#")[0][1:]) for k in ((md.get("vision_figures") or {}).get("blocks_added") or {})}
    page_flags = (md.get("challenges") or {}).get("page_flags") or {}
    out = []
    for k in sorted(stats, key=int):
        p = int(k)
        if subset and p not in subset:
            continue
        s = stats[k]
        route = "ocr" if p in ocr else "text"
        if p in vis_added:
            route = "vision" if route == "text" else "ocr+vision"
        elif p in vis_checked:
            route += "+vision(no content)"
        if p in fig_pages:
            route += "+figures"
        if route == "text" and (s.get("chars") or 0) < 40:
            route = "no_text"
        out.append({"page": p, "route": route, "chars": s.get("chars"), "text_quality": s.get("q"),
                    "image_share": s.get("img"), "flags": page_flags.get(k, [])})
    return out


def review_reasons(result: dict[str, Any]) -> list[str]:
    """Why a person should look at this document's result (empty: nothing to review)."""
    doc = result.get("document") or {}
    ex = result.get("execution") or {}
    reasons = []
    if doc.get("failed_stages"):
        reasons.append("stages fell back after failing: " + ", ".join(doc["failed_stages"]))
    ch = doc.get("challenges") or {}
    risky = sorted(set(ch.get("file_flags") or []) & HIGH_RISK_FILE_FLAGS)
    if risky:
        reasons.append("file: " + ", ".join(risky))
    pages = doc.get("pages") or []
    ocr_only = [p["page"] for p in pages if p["route"].startswith("ocr") and "vision" not in p["route"]
                and (p.get("image_share") or 0) >= 0.35]
    if ocr_only:
        reasons.append(f"{len(ocr_only)} image page(s) read by OCR only (no vision check): {ocr_only[:10]}")
    empty = [p["page"] for p in pages if p["route"] == "no_text"]
    if empty:
        reasons.append(f"{len(empty)} page(s) with no readable text: {empty[:10]}")
    flagged: dict[str, list[int]] = {}
    for p in pages:
        for f in set(p.get("flags") or []) & REVIEW_PAGE_FLAGS:
            flagged.setdefault(f, []).append(p["page"])
    for f, ps in sorted(flagged.items()):
        reasons.append(f"{f} on {len(ps)} page(s): {ps[:10]}")
    kind = (doc.get("source") or {}).get("document_kind")
    if kind in NON_PRODUCT_KINDS:
        reasons.append(f"document kind: {kind} (kept; filter when cleaning)")
    if len(ex.get("errors") or []) >= 5:
        reasons.append(f"{len(ex['errors'])} agent errors (see execution.errors)")
    return reasons
