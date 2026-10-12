"""PDF challenge scan (deterministic, no model calls): which known PDF problems each file and each page shows.

Run it over a corpus before processing it: the summary gives the prevalence of every challenge (share of files and
pages, with example files), so handling is built in order of how often a problem occurs and how much it costs, and
nothing rare goes unnoticed. The same per-page flags are what page routing (native text / OCR / vision) is built on.

    dx pdf-scan --root <folder> --out runs/scan_<name> [--workers 8] [--limit N]

Signals come from pdfium only (text layer, character geometry, page objects, annotations, document catalog); a page
is never rendered. Pages beyond MAX_PAGES are sampled evenly, so a 2,000-page file costs the same as a 300-page one.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import re
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from .pdf_layout import _CONTENTS, _DOTS, text_quality

MAX_PAGES = 300  # pages examined per file (evenly sampled beyond this)
CHAR_SAMPLE = 300  # characters examined per page for angle and size

# --- flags: one line each, shown in the summary in this order ----------------------------------------------------
FLAGS: dict[str, str] = {
    # file level
    "not_pdf": "file is not a PDF (HTML error page, image, empty file renamed .pdf)",
    "encrypted": "needs a password to open",
    "corrupt": "cannot be opened (damaged file)",
    "restricted": "opens, but copying/printing is restricted by the owner",
    "xfa_form": "XFA form: content may live in form XML, not on the page",
    "acro_form": "fillable form: values may sit in form fields",
    "attachments": "embedded files (portfolio / attached datasheets)",
    "duplicate_file": "byte-identical to another file in the scan",
    "slides": "slide deck exported to PDF",
    "multilingual": "pages in two or more languages",
    "tender_language": "requirement wording (shall, bidder, statement of work): a tender, not a product sheet",
    "manual_language": "procedure wording (warning, caution, torque, install): an operator/maintenance manual",
    "news_language": "news wording (announced, awarded, according to)",
    "academic_language": "paper wording (abstract, et al., doi, references)",
    # page level: text layer
    "no_text": "page without a text layer",
    "scan_page": "page is one image without text (scanned or flattened)",
    "scan_with_hidden_text": "page is one image under an invisible text layer (earlier OCR, quality unknown)",
    "background_image": "text set over a full-page image (designed page; the image is not a figure)",
    "bad_text_layer": "text layer of garbage glyphs ((cid:..), private-use characters)",
    "vector_text_suspect": "no text and no image but many vector paths (text drawn as outlines, or a drawing)",
    "image_heavy": "images cover at least 35% of the page",
    "hyphen_breaks": "words broken at line ends",
    "letter_spaced": "letter-spaced words (S P E C I F I C A T I O N S)",
    "fullwidth_chars": "full-width digits or letters (CJK typesetting)",
    "mixed_script_tokens": "words mixing Latin and Cyrillic letters (look-alikes: Т-90 with a Cyrillic Т)",
    "rtl_script": "Arabic or Hebrew text (right-to-left order)",
    "cjk_script": "Chinese, Japanese or Korean text",
    "flattened_powers": "units with flattened powers (m2, cm3) or exponents (10 6)",
    # page level: layout
    "rotated_page": "page rotated in the file (/Rotate)",
    "landscape": "landscape page",
    "oversized_page": "page larger than A3 (fold-out, poster, drawing)",
    "rotated_text": "a share of the text runs vertically or at an angle",
    "tiny_text": "a share of the text is set in very small type (under ~6 pt)",
    "multi_column": "text set in two or more columns",
    "dot_leaders": "dot-leader lines (label ........ value)",
    "contents_page": "table of contents or index",
    "spec_cards": "many short label / number fragments (spec cards, infographics)",
    "borderless_numbers": "lines with three or more numbers in a row (tables without rules)",
    "ditto_marks": "ditto marks (\" or do.) meaning 'same as above'",
    "check_marks": "check marks or dots in a matrix (feature/option tables)",
    "footnote_markers": "values with footnote markers (*, †, ¹)",
    "watermark_or_banner": "a marking or DRAFT / SAMPLE / COPY line repeated on most pages (watermark, banner)",
    "classification_marking": "security or export-control marking / statement (CONFIDENTIAL, ITAR, EAR, NATO ...)",
    "annotations_content": "annotations that carry text (comments, free text, stamps)",
    "ink_annotations": "ink annotations (handwriting, sketches)",
    "redaction_suspect": "redaction annotations or black bars (bars are often design elements: low precision)",
    # page level: content
    "spec_dense": "ten or more number+unit values on the page",
    "designation_dense": "five or more model designations on the page (catalogue)",
}

_UNITS = (r"mm|cm|m|km|nm|nmi|in|ft|yd|mi|kg|g|t|lb|lbs|oz|gr|kn|kN|N|Nm|J|kJ|W|kW|MW|hp|PS|V|kV|A|Ah|mAh|Wh|kWh|Hz|kHz|"
          r"MHz|GHz|dB|dBm|rpm|rds/min|rd/min|km/h|kph|mph|kts?|knots|m/s|ft/s|fps|°C|°F|°|%|bar|psi|psia|kPa|MPa|l|L|ml|gal|"
          r"mrad|MOA|mil|s|sec|min|h|hrs?|lbf|lbm|kgf")
_VALUNIT = re.compile(r"(?<![\w.])\d[\d.,]*\s?(?:" + _UNITS + r")(?![\w])")
_DESIG = re.compile(r"(?<![\w/.])(?=[A-Za-z0-9/-]*\d)(?=[A-Za-z0-9/-]*[A-Za-z])[A-Za-z0-9][A-Za-z0-9/-]{2,}(?<![/-])(?![\w.])")
_NOT_DESIG = re.compile(r"(?i)^\d+(?:" + _UNITS + r"|st|nd|rd|th|x\d+)$|^\d+x\d+$")
_BROKEN = re.compile(r"\b[A-Za-z]{2,}-\s*\n\s*[a-z]{2,}\b|\b[A-Za-z]{2,}- [a-z]{2,}\b")
_LETTER_SPACED = re.compile(r"(?:\b[A-Z] ){5,}[A-Z]\b")
_FULLWIDTH = re.compile(r"[０-９Ａ-Ｚａ-ｚ]")
_MIXED = re.compile(r"\b(?=\w*[A-Za-z])(?=\w*[\u0400-\u04ff])\w+\b")
_POWERS = re.compile(r"\b(?:m|cm|mm|km|ft|in)[23]\b|\b10 [1-9]\b")
_NUMROW = re.compile(r"^(?:\S*\d\S*\s+){2,}\S*\d\S*\s*$")
_DITTO = re.compile(r"(?:^|\s)(?:\"|〃|do\.|-do-|ditto)(?:\s|$)", re.I)
_CHECKS = re.compile(r"[●○◉◯✓✔✗✘☑☐■□]")
_FOOTNOTE = re.compile(r"\d[\d.,]*\s?(?:\w{1,5}\s?)?[*†‡§¹²³⁴⁵⁶⁷⁸⁹]+(?!\w)")
_MARKING = re.compile(r"\b(?:TOP SECRET|SECRET|CONFIDENTIAL|RESTRICTED|UNCLASSIFIED|OFFICIAL[- ]SENSITIVE|NATO (?:UN)?CLASSIFIED|"
                      r"FOUO|FOR OFFICIAL USE ONLY|CUI|ITAR|EAR99|EXPORT CONTROLLED|PROPRIETARY|DRAFT)\b")
_BANNER = re.compile(_MARKING.pattern + r"|\b(?:SAMPLE|COPY|PREVIEW|NOT FOR (?:DISTRIBUTION|RELEASE)|WATERMARK)\b", re.I)
_HINTS = {
    "tender_language": re.compile(r"(?i)\b(?:shall be|shall have|the contractor shall|bidders?|tenderers?|request for (?:proposal|quotation)|"
                                  r"\bRFP\b|\bRFQ\b|statement of work|technical requirements?|compliance matrix)\b"),
    "manual_language": re.compile(r"(?i)\b(?:warning|caution|procedure|torque|remove the|install the|step \d|maintenance|lubricat\w*|"
                                  r"disassembl\w*|troubleshoot\w*)\b"),
    "news_language": re.compile(r"(?i)\b(?:announced|according to|was awarded|contract worth|said in a statement|press release|"
                                r"told reporters|ministry of defen[cs]e)\b"),
    "academic_language": re.compile(r"(?i)\b(?:abstract|et al\.|doi:|proceedings|journal of|in this paper)\b"),
}
_SCRIPTS = {"latin": (0x41, 0x24F), "greek": (0x370, 0x3FF), "cyrillic": (0x400, 0x52F), "hebrew": (0x590, 0x5FF),
            "arabic": (0x600, 0x6FF), "devanagari": (0x900, 0x97F), "thai": (0xE00, 0xE7F), "hangul": (0xAC00, 0xD7AF),
            "cjk": (0x3040, 0x9FFF)}


def _script_counts(text: str) -> Counter:
    c: Counter = Counter()
    for ch in text:
        if not ch.isalpha():
            continue
        o = ord(ch)
        for name, (lo, hi) in _SCRIPTS.items():
            if lo <= o <= hi:
                c[name] += 1
                break
        else:
            c["other"] += 1
    return c


def _sample_pages(n: int, k: int = MAX_PAGES) -> list[int]:
    if n <= k:
        return list(range(n))
    step = n / k
    return sorted({int(i * step) for i in range(k)})


def _multi_column(rects: list[tuple[float, float, float, float]], w: float) -> bool:
    """Two or more columns: text lines start in two clusters, at the left margin and near or past the middle, and most
    lines are narrower than half the page."""
    lines = [r for r in rects if (r[2] - r[0]) > 20]
    if len(lines) < 12:
        return False
    narrow = sum(1 for r in lines if (r[2] - r[0]) < 0.5 * w) / len(lines)
    left = sum(1 for r in lines if r[0] < 0.3 * w)
    right = sum(1 for r in lines if r[0] >= 0.45 * w)
    return narrow > 0.7 and left >= 0.25 * len(lines) and right >= 0.25 * len(lines)


def _page_signals(pdfium_c, page, tp, text: str) -> tuple[dict, set[str]]:
    w, h = page.get_size()
    area = max(1.0, w * h)
    flags: set[str] = set()
    n = tp.count_chars()
    chars = len(text.strip())
    q = text_quality(text[:20000]) if chars else 0.0
    # character geometry (sampled): angle and size
    angled = tiny = sampled = 0
    if n:
        step = max(1, n // CHAR_SAMPLE)
        for i in range(0, n, step):
            ch = text[i] if i < len(text) else ""
            if not ch.strip():
                continue
            sampled += 1
            a = pdfium_c.FPDFText_GetCharAngle(tp.raw, i)
            if a > 0.05 and abs(a - 6.2832) > 0.05:
                angled += 1
            try:
                l, b, r, t = tp.get_charbox(i, loose=True)
                if 0 < t - b < 6.5:
                    tiny += 1
            except Exception:
                pass
    rects = [tp.get_rect(i) for i in range(min(tp.count_rects(), 600))]
    # page objects: images, vector paths, black bars
    img_area = 0.0
    n_img = n_path = black_bars = n_text = n_invisible = 0
    try:
        for k, obj in enumerate(page.get_objects(max_depth=2)):
            if k > 4000:
                break
            if obj.type == pdfium_c.FPDF_PAGEOBJ_IMAGE:
                n_img += 1
                l, b, r, t = obj.get_bounds()
                img_area += max(0.0, r - l) * max(0.0, t - b)
            elif obj.type == pdfium_c.FPDF_PAGEOBJ_TEXT:
                n_text += 1
                if n_text <= 2000 and pdfium_c.FPDFTextObj_GetTextRenderMode(obj.raw) == 3:  # invisible: an OCR layer
                    n_invisible += 1
            elif obj.type == pdfium_c.FPDF_PAGEOBJ_PATH:
                n_path += 1
                if n_path > 1500:
                    continue
                fill, stroke = ctypes.c_int(0), ctypes.c_int(0)
                if not pdfium_c.FPDFPath_GetDrawMode(obj.raw, ctypes.byref(fill), ctypes.byref(stroke)) or not fill.value:
                    continue
                cr, cg, cb, ca = (ctypes.c_uint(0) for _ in range(4))
                if pdfium_c.FPDFPageObj_GetFillColor(obj.raw, ctypes.byref(cr), ctypes.byref(cg), ctypes.byref(cb), ctypes.byref(ca)):
                    if cr.value < 30 and cg.value < 30 and cb.value < 30 and ca.value > 200:
                        l, b, r, t = obj.get_bounds()
                        if 5 <= t - b <= 30 and r - l >= 30 and (r - l) * (t - b) < 0.2 * area:
                            black_bars += 1
    except Exception:
        pass
    img = min(1.0, img_area / area)
    # annotations
    annot_text = annot_ink = annot_redact = 0
    try:
        for i in range(pdfium_c.FPDFPage_GetAnnotCount(page.raw)):
            an = pdfium_c.FPDFPage_GetAnnot(page.raw, i)
            st = pdfium_c.FPDFAnnot_GetSubtype(an)
            pdfium_c.FPDFPage_CloseAnnot(an)
            if st in (pdfium_c.FPDF_ANNOT_TEXT, pdfium_c.FPDF_ANNOT_FREETEXT, pdfium_c.FPDF_ANNOT_STAMP):
                annot_text += 1
            elif st == pdfium_c.FPDF_ANNOT_INK:
                annot_ink += 1
            elif st == pdfium_c.FPDF_ANNOT_REDACT:
                annot_redact += 1
    except Exception:
        pass

    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if chars < 40:
        flags.add("no_text")
        if img >= 0.85:
            flags.add("scan_page")
        elif img < 0.1 and n_path >= 300:
            flags.add("vector_text_suspect")
    elif q < 0.45:
        flags.add("bad_text_layer")
    if img >= 0.85 and chars >= 200:
        flags.add("scan_with_hidden_text" if n_invisible >= 0.5 * max(1, min(n_text, 2000)) else "background_image")
    if img >= 0.35:
        flags.add("image_heavy")
    if page.get_rotation():
        flags.add("rotated_page")
    if w > h * 1.05:
        flags.add("landscape")
    if max(w, h) > 1191 * 1.02:
        flags.add("oversized_page")
    if sampled >= 30 and angled / sampled > 0.1:
        flags.add("rotated_text")
    if sampled >= 30 and tiny / sampled > 0.3:
        flags.add("tiny_text")
    if _multi_column(rects, w):
        flags.add("multi_column")
    dots = [ln for ln in lines if _DOTS.match(ln)]
    if len(dots) >= 3:
        flags.add("dot_leaders")
        if sum(1 for ln in dots if re.search(r"\d{1,4}\s*$", ln)) >= 4:
            flags.add("contents_page")
    if any(_CONTENTS.match(ln) for ln in lines[:5]):
        flags.add("contents_page")
    short = [ln for ln in lines if len(ln) <= 24]
    if len(lines) >= 20 and len(short) / len(lines) > 0.6 and sum(1 for ln in short if re.search(r"\d", ln)) >= 8:
        flags.add("spec_cards")
    if sum(1 for ln in lines if _NUMROW.match(ln)) >= 3:
        flags.add("borderless_numbers")
    if sum(1 for ln in lines if _DITTO.search(ln)) >= 2:
        flags.add("ditto_marks")
    if len(_CHECKS.findall(text)) >= 6:
        flags.add("check_marks")
    if len(_FOOTNOTE.findall(text)) >= 2:
        flags.add("footnote_markers")
    if len(_BROKEN.findall(text)) >= 3:
        flags.add("hyphen_breaks")
    if _LETTER_SPACED.search(text):
        flags.add("letter_spaced")
    if _FULLWIDTH.search(text):
        flags.add("fullwidth_chars")
    if _MIXED.search(text):
        flags.add("mixed_script_tokens")
    if _POWERS.search(text):
        flags.add("flattened_powers")
    if any(_MARKING.search(ln) for ln in lines if len(ln) <= 80):  # a marking stands on its own line
        flags.add("classification_marking")
    if annot_text:
        flags.add("annotations_content")
    if annot_ink:
        flags.add("ink_annotations")
    if annot_redact or black_bars >= 3:
        flags.add("redaction_suspect")
    if len(_VALUNIT.findall(text)) >= 10:
        flags.add("spec_dense")
    if sum(1 for t in _DESIG.findall(text) if not _NOT_DESIG.match(t)) >= 5:
        flags.add("designation_dense")
    sc = _script_counts(text[:20000])
    letters = max(1, sum(sc.values()))
    if (sc["arabic"] + sc["hebrew"]) / letters > 0.2:
        flags.add("rtl_script")
    if (sc["cjk"] + sc["hangul"]) / letters > 0.2:
        flags.add("cjk_script")
    sig = {"chars": chars, "q": q, "img": round(img, 3), "n_img": n_img, "n_path": n_path, "w": round(w), "h": round(h)}
    return sig, flags


def scan_pdf(path: str | Path) -> dict:
    """Challenge flags of one file: file-level flags, per-page flags and the pages each flag fired on."""
    import pypdfium2 as pdfium
    import pypdfium2.raw as pdfium_c

    path = Path(path)
    t0 = time.time()
    out: dict = {"path": str(path), "name": path.name, "flags": {}, "page_flags": {}}
    try:
        raw = path.read_bytes()
    except OSError as e:
        return {**out, "status": "unreadable", "error": str(e)[:200]}
    out["bytes"] = len(raw)
    out["sha256"] = hashlib.sha256(raw).hexdigest()
    if b"%PDF-" not in raw[:1024]:
        out["status"] = "not_pdf"
        out["flags"]["not_pdf"] = 1
        return out
    try:
        pdf = pdfium.PdfDocument(raw)
    except pdfium.PdfiumError as e:
        msg = str(e).lower()
        out["status"] = "encrypted" if "password" in msg else "corrupt"
        out["flags"][out["status"]] = 1
        out["error"] = str(e)[:200]
        return out
    try:
        n = len(pdf)
        out.update(status="ok", pages=n, version=pdf.get_version(), tagged=bool(pdf.is_tagged()))
        meta = pdf.get_metadata_dict()
        out["producer"], out["creator"] = (meta.get("Producer") or "")[:80], (meta.get("Creator") or "")[:80]
        form = pdf.get_formtype()
        if form in (pdfium_c.FORMTYPE_XFA_FULL, pdfium_c.FORMTYPE_XFA_FOREGROUND):
            out["flags"]["xfa_form"] = 1
        elif form == pdfium_c.FORMTYPE_ACRO_FORM:
            out["flags"]["acro_form"] = 1
        if pdf.count_attachments():
            out["flags"]["attachments"] = 1
        if pdfium_c.FPDF_GetSecurityHandlerRevision(pdf.raw) != -1 and pdfium_c.FPDF_GetDocPermissions(pdf.raw) != 0xFFFFFFFF:
            out["flags"]["restricted"] = 1
        page_counts: Counter = Counter()
        repeated: Counter = Counter()
        langs: Counter = Counter()
        hints: Counter = Counter()
        landscape = 0
        idx = _sample_pages(n)
        lang_pages = set(idx[:: max(1, len(idx) // 10)])
        from .language import detect_language

        for i in idx:
            try:
                page = pdf[i]
                tp = page.get_textpage()
                text = tp.get_text_range() or ""
                sig, flags = _page_signals(pdfium_c, page, tp, text)
            except Exception as e:  # one bad page does not stop the scan of the file
                out.setdefault("page_errors", []).append(f"p{i + 1}: {type(e).__name__}")
                continue
            if "landscape" in flags:
                landscape += 1
            for f in flags:
                page_counts[f] += 1
            if flags:
                out["page_flags"][str(i + 1)] = sorted(flags)
            for ln in {ln.strip() for ln in text.splitlines() if 3 <= len(ln.strip()) <= 40}:
                repeated[ln] += 1
            for k, rx in _HINTS.items():
                hints[k] += len(rx.findall(text))
            if i in lang_pages and len(text.strip()) >= 200:
                code, prob = detect_language(text)
                if code and prob >= 0.8:
                    langs[code] += 1
        sampled = max(1, len(idx))
        # a marking or draft/sample line on most pages: a watermark or banner (running headers alone are normal)
        banner = [ln for ln, c in repeated.items() if c >= max(3, 0.6 * sampled) and _BANNER.search(ln)]
        if banner:
            out["flags"]["watermark_or_banner"] = 1
            out["banner_lines"] = banner[:5]
        prod = f"{out['producer']} {out['creator']}".lower()
        if any(k in prod for k in ("powerpoint", "keynote", "impress", "google slides")) or (n >= 4 and landscape / sampled > 0.8):
            out["flags"]["slides"] = 1
        if len([c for c, k in langs.items() if k >= 1]) >= 2:
            out["flags"]["multilingual"] = 1
        out["languages"] = dict(langs)
        for k, c in hints.items():
            if c >= max(5, 0.5 * sampled):
                out["flags"][k] = 1
        out["hint_counts"] = {k: c for k, c in hints.items() if c}
        for f, c in page_counts.items():
            out["flags"][f] = c
        out["pages_scanned"] = len(idx)
    finally:
        pdf.close()
    out["secs"] = round(time.time() - t0, 2)
    return out


def _job(path: str) -> dict:
    try:
        return scan_pdf(path)
    except Exception as e:
        return {"path": path, "name": Path(path).name, "status": "scan_error", "error": f"{type(e).__name__}: {str(e)[:200]}",
                "flags": {}, "page_flags": {}}


def scan_corpus(paths: list[Path], out_dir: Path, workers: int = 8, progress=None) -> dict:
    """Scan files in parallel processes; writes scan.jsonl (one line per file) and summary.json; returns the summary."""
    out_dir.mkdir(parents=True, exist_ok=True)
    results: list[dict] = []
    with (out_dir / "scan.jsonl").open("w", encoding="utf-8") as fh, ProcessPoolExecutor(max_workers=max(1, workers)) as ex:
        futs = {ex.submit(_job, str(p)): p for p in paths}
        for k, fut in enumerate(as_completed(futs), 1):
            r = fut.result()
            results.append(r)
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
            if progress and k % 25 == 0:
                progress(k, len(paths))
    summary = summarize(results)
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=1, ensure_ascii=False), encoding="utf-8")
    return summary


def summarize(results: list[dict]) -> dict:
    files = len(results)
    ok = [r for r in results if r.get("status") == "ok"]
    pages = sum(r.get("pages_scanned", 0) for r in ok)
    by_sha: dict[str, list[str]] = {}
    for r in results:
        if r.get("sha256"):
            by_sha.setdefault(r["sha256"], []).append(r["name"])
    dup_names = {n for names in by_sha.values() if len(names) > 1 for n in names[1:]}
    for r in results:
        if r["name"] in dup_names:
            r["flags"]["duplicate_file"] = 1
    rows = []
    for f, desc in FLAGS.items():
        hit = [r for r in results if r["flags"].get(f)]
        page_hits = sum(r["flags"][f] for r in hit) if f not in _FILE_FLAGS else 0
        rows.append({"flag": f, "description": desc, "files": len(hit), "files_pct": round(100 * len(hit) / max(1, files), 2),
                     "pages": page_hits, "pages_pct": round(100 * page_hits / max(1, pages), 2) if page_hits else 0.0,
                     "examples": [r["name"] for r in sorted(hit, key=lambda x: -x["flags"][f])[:5]]})
    status = Counter(r.get("status") for r in results)
    return {"files": files, "pages_scanned": pages, "status": dict(status),
            "pages_per_file": round(pages / max(1, len(ok)), 1),
            "languages": dict(sum((Counter(r.get("languages") or {}) for r in ok), Counter()).most_common(15)),
            "flags": rows}


_FILE_FLAGS = {"not_pdf", "encrypted", "corrupt", "restricted", "xfa_form", "acro_form", "attachments", "duplicate_file",
               "slides", "multilingual", "tender_language", "manual_language", "news_language", "academic_language",
               "watermark_or_banner"}


def file_challenges(path: str | Path, pages: set[int] | None = None) -> dict:
    """The challenge scan of one file, compact, for its result: file-level flags, page flags (only the pages read when
    a page subset is given) and their counts."""
    from .pdf_adapter import PDFIUM_LOCK

    with PDFIUM_LOCK:
        r = scan_pdf(path)
    page_flags = {p: f for p, f in (r.get("page_flags") or {}).items() if not pages or int(p) in pages}
    counts: Counter = Counter(f for fl in page_flags.values() for f in fl)
    return {"status": r.get("status"), "file_flags": sorted(f for f in (r.get("flags") or {}) if f in _FILE_FLAGS),
            "page_flag_counts": dict(counts), "page_flags": page_flags, "languages": r.get("languages") or {},
            "hint_counts": r.get("hint_counts") or {}}
