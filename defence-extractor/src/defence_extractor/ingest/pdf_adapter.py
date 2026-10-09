"""A1 PDF adapter: PDF -> SemanticDocument.

Primary path: Docling (layout analysis, reading order, TableFormer table structure, OCR for bitmap regions).
Fallback: pypdfium2 text layer (one paragraph block per text line group) — recorded as a parse warning.
Provenance: page number + bounding box on every block.
Long PDFs are converted in page windows (no page limit); each window's conversion is cached on disk, so an interrupted
ingest resumes. Per-page statistics (text characters, image coverage) feed the page-level vision routing.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import functools
import threading
from pathlib import Path

from ..contracts.document import BlockType, ScopeHint, Section, SemanticBlock, SemanticDocument, SemanticTable, TableCell

log = logging.getLogger(__name__)
PARSER_VERSION = "pdf-1.2"
PDFIUM_LOCK = threading.RLock()  # pdfium is not thread-safe: every pdfium / docling use in this process goes through it
_LOCK = PDFIUM_LOCK


def _pdfium_locked(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        with PDFIUM_LOCK:
            return fn(*args, **kwargs)
    return wrapper
_CONVERTERS: dict[bool, object] = {}


def _converter(ocr: bool, threads: int):
    """Docling converters are expensive to build (they load models) — one per (ocr on/off) per process."""
    with _LOCK:
        if ocr in _CONVERTERS:
            return _CONVERTERS[ocr]
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions, TableFormerMode
        from docling.document_converter import DocumentConverter, PdfFormatOption

        opts = PdfPipelineOptions()
        opts.do_ocr = ocr
        opts.do_table_structure = True
        opts.table_structure_options.mode = TableFormerMode.ACCURATE
        try:
            opts.table_structure_options.do_cell_matching = True
        except Exception:
            pass
        try:
            from docling.datamodel.accelerator_options import AcceleratorDevice, AcceleratorOptions

            opts.accelerator_options = AcceleratorOptions(num_threads=threads, device=AcceleratorDevice.CPU)
        except Exception:
            pass
        if ocr:
            try:
                from docling.datamodel.pipeline_options import RapidOcrOptions

                opts.ocr_options = RapidOcrOptions()
            except Exception:
                pass
        conv = DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)})
        _CONVERTERS[ocr] = conv
        return conv


# Some fonts carry ToUnicode entries for ligature glyphs as byte-swapped UTF-16, so "fi"/"fl"/"ff" come out as the
# CJK ideographs U+6600/U+6900/U+6C00 ("Ri昀氀ing", "昀椀re"). Inside a Latin word they map back to the ASCII letter.
_SWAPPED = re.compile(r"(?<=[A-Za-z])[\u6600\u6900\u6c00\u7400]+|[\u6600\u6900\u6c00\u7400]+(?=[A-Za-z])")


def _unswap(s: str) -> str:
    return _SWAPPED.sub(lambda m: "".join(chr(ord(c) >> 8) for c in m.group(0)), s)


def _norm(s: str) -> str:
    s = _unswap(s or "")
    s = re.sub(r"[ \t\r\f\v]+", " ", s)
    s = s.replace("­", "").replace("﻿", "")
    return re.sub(r" *\n+ *", " ", s).strip()


@_pdfium_locked
def page_stats(path: str | Path) -> dict[str, dict]:
    """Per page: characters in the text layer and the share of the page covered by images (vision routing)."""
    out: dict[str, dict] = {}
    try:
        import pypdfium2 as pdfium
        import pypdfium2.raw as pdfium_c

        pdf = pdfium.PdfDocument(str(path))
    except Exception:
        return out
    try:
        for i in range(len(pdf)):
            page = pdf[i]
            w, h = page.get_size()
            chars = len((page.get_textpage().get_text_range() or "").strip())
            img = 0.0
            try:
                for obj in page.get_objects(max_depth=3):
                    if obj.type != pdfium_c.FPDF_PAGEOBJ_IMAGE:
                        continue
                    l, bt, r, t = obj.get_bounds() if hasattr(obj, "get_bounds") else obj.get_pos()
                    img += max(0.0, r - l) * max(0.0, t - bt)
            except Exception:
                pass
            out[str(i + 1)] = {"chars": chars, "img": round(min(1.0, img / max(1.0, w * h)), 3), "w": round(w), "h": round(h)}
    finally:
        pdf.close()
    return out


class PdfAdapter:
    def __init__(self, ocr: bool = True, threads: int = 8, window_pages: int = 40, cache_dir: str | Path | None = None):
        self.ocr = ocr
        self.threads = threads
        self.window_pages = window_pages
        self.cache_dir = Path(cache_dir) if cache_dir else None

    def parse(self, path: str | Path, doc_id: str, source_url: str | None = None) -> SemanticDocument:
        path = Path(path)
        raw = path.read_bytes()
        sha = hashlib.sha256(raw).hexdigest()
        warnings: list[str] = []
        n_pages = self._page_count(path)
        stats = page_stats(path)
        needs_ocr = self.ocr and n_pages > 0 and not self._has_text_layer(path)
        if needs_ocr:
            warnings.append("no text layer detected: OCR pipeline used")
        try:
            doc = self._parse_docling(path, doc_id, source_url, sha, warnings, n_pages, ocr=needs_ocr)
            if sum(len(b.text) for b in doc.blocks) < 50 and n_pages:
                warnings.append("docling produced almost no text; falling back to pypdfium2 text layer")
                doc = self._parse_pdfium(path, doc_id, source_url, sha, warnings, n_pages)
        except Exception as e:  # never lose the document: fall back to the plain text layer
            log.exception("docling failed for %s", path)
            warnings.append(f"docling failed ({type(e).__name__}: {e}); used pypdfium2 text layer")
            doc = self._parse_pdfium(path, doc_id, source_url, sha, warnings, n_pages)
        doc.metadata["pages"] = n_pages
        doc.metadata["page_stats"] = stats
        return doc

    # ------------------------------------------------------------------------------------------
    @_pdfium_locked
    def _page_count(self, path: Path) -> int:
        try:
            import pypdfium2 as pdfium

            pdf = pdfium.PdfDocument(str(path))
            n = len(pdf)
            pdf.close()
            return n
        except Exception:
            return 0

    @_pdfium_locked
    def _has_text_layer(self, path: Path) -> bool:
        try:
            import pypdfium2 as pdfium

            pdf = pdfium.PdfDocument(str(path))
            try:
                n = min(len(pdf), 5)
                chars = sum(len((pdf[i].get_textpage().get_text_range() or "").strip()) for i in range(n))
            finally:
                pdf.close()
            return chars >= 40 * max(1, n) // 2
        except Exception:
            return True

    def _windows(self, n_pages: int) -> list[tuple[int, int]]:
        if not n_pages:
            return [(0, 0)]  # unknown page count: one conversion of the whole file
        w = max(1, self.window_pages)
        return [(a, min(n_pages, a + w - 1)) for a in range(1, n_pages + 1, w)]

    def _convert_window(self, path: Path, sha: str, start: int, end: int, ocr: bool) -> list[dict]:
        """Docling items for pages start..end as plain dicts (cached on disk per window)."""
        cache = None
        if self.cache_dir is not None:
            cache = self.cache_dir / f"{sha[:24]}_{start}_{end}_{int(ocr)}_{PARSER_VERSION}.json"
            if cache.exists():
                try:
                    return json.loads(cache.read_text(encoding="utf-8"))
                except ValueError:
                    pass
        conv = _converter(ocr, self.threads)
        with _LOCK:  # docling models: one conversion at a time per process
            if start:
                try:
                    res = conv.convert(str(path), page_range=(start, end))
                except TypeError:
                    res = conv.convert(str(path))
            else:
                res = conv.convert(str(path))
        ddoc = res.document
        items: list[dict] = []
        for item, _level in ddoc.iterate_items():
            label = str(getattr(item, "label", "")).split(".")[-1].lower()
            prov = (getattr(item, "prov", None) or [None])[0]
            page = getattr(prov, "page_no", None) if prov else None
            bbox = None
            if prov is not None and getattr(prov, "bbox", None) is not None:
                bb = prov.bbox
                bbox = [float(bb.l), float(bb.t), float(bb.r), float(bb.b)]
            if label == "table":
                rows = self._table_rows(item, ddoc)
                if rows:
                    items.append({"label": "table", "page": page, "bbox": bbox, "rows": rows})
                continue
            text = _norm(getattr(item, "text", "") or "")
            if text:
                items.append({"label": label, "page": page, "bbox": bbox, "text": text,
                              "level": int(getattr(item, "level", 1) or 1)})
        if cache is not None:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(items), encoding="utf-8")
        return items

    def _parse_docling(self, path: Path, doc_id: str, url: str | None, sha: str, warnings: list[str],
                       n_pages: int, ocr: bool = False) -> SemanticDocument:
        b = _Builder(doc_id)
        title = None
        wins = self._windows(n_pages)
        if len(wins) > 1:
            warnings.append(f"PDF has {n_pages} pages; parsed in {len(wins)} windows of {self.window_pages} pages")
        for start, end in wins:
            for it in self._convert_window(path, sha, start, end, ocr):
                label, page = it["label"], it.get("page")
                bbox = tuple(it["bbox"]) if it.get("bbox") else None
                if label == "table":
                    b.table([[(c[0], bool(c[1])) for c in row] for row in it["rows"]], page, bbox)
                    continue
                text = it["text"]
                if label in ("title", "section_header"):
                    lvl = 1 if label == "title" else min(6, int(it.get("level") or 1) + 1)
                    if title is None and label == "title":
                        title = text
                    b.heading(text, lvl, page, bbox)
                elif label in ("page_header", "page_footer"):
                    b.block(text, BlockType.other, page, bbox, scope=ScopeHint.footer)
                elif label == "list_item":
                    b.block(text, BlockType.list_item, page, bbox)
                elif label == "caption":
                    b.block(text, BlockType.caption, page, bbox)
                elif label == "footnote":
                    b.block(text, BlockType.footnote, page, bbox)
                elif label in ("picture", "chart"):
                    continue
                else:
                    btype = BlockType.key_value if re.match(r"^[^:]{1,60}:\s*\S", text) and len(text) < 220 else BlockType.paragraph
                    b.block(text, btype, page, bbox)
        if title is None:
            title = next((blk.text for blk in b.blocks if blk.type == BlockType.heading), None)
        return b.build(fmt="pdf", url=url, path=str(path), sha=sha, title=title, warnings=warnings,
                       parser=f"{PARSER_VERSION}+docling")

    def _table_rows(self, item, ddoc) -> list[list[list]]:
        data = getattr(item, "data", None)
        grid = getattr(data, "grid", None) if data is not None else None
        if not grid:
            try:
                df = item.export_to_dataframe(doc=ddoc)
                grid_rows = [list(map(str, df.columns))] + [list(map(str, r)) for r in df.values.tolist()]
                return [[[_norm(c), r == 0] for c in row] for r, row in enumerate(grid_rows)]
            except Exception:
                return []
        return [[[_norm(getattr(c, "text", "") or ""), bool(getattr(c, "column_header", False))] for c in row] for row in grid]

    @_pdfium_locked
    def _parse_pdfium(self, path: Path, doc_id: str, url: str | None, sha: str, warnings: list[str],
                      n_pages: int) -> SemanticDocument:
        import pypdfium2 as pdfium

        b = _Builder(doc_id)
        pdf = pdfium.PdfDocument(str(path))
        try:
            for i in range(len(pdf)):
                page = pdf[i]
                text = page.get_textpage().get_text_range() or ""
                for para in re.split(r"\n\s*\n|\r\n\r\n", text):
                    for line in para.splitlines():
                        t = _norm(line)
                        if t:
                            b.block(t, BlockType.paragraph, i + 1, None)
        finally:
            pdf.close()
        if not b.blocks:
            warnings.append("no text layer found (scanned PDF?) and OCR path unavailable")
        return b.build(fmt="pdf", url=url, path=str(path), sha=sha, title=None, warnings=warnings,
                       parser=f"{PARSER_VERSION}+pdfium")


class _Builder:
    def __init__(self, doc_id: str):
        self.doc_id = doc_id
        self.blocks: list[SemanticBlock] = []
        self.tables: list[SemanticTable] = []
        self.sections: list[Section] = [Section(section_id="s0", heading=None, level=0)]
        self.stack: list[tuple[int, str, str]] = []
        self.current = "s0"
        self.n_sections = 0
        self.seen: dict[str, str] = {}
        self.prev_table: dict | None = None  # last table, for header carry-over across page / window breaks
        self.since_table = 0  # content blocks added since the last table ended

    def heading(self, text: str, level: int, page, bbox) -> None:
        while self.stack and self.stack[-1][0] >= level:
            self.stack.pop()
        self.n_sections += 1
        sid = f"s{self.n_sections}"
        self.stack.append((level, text, sid))
        self.current = sid
        self.sections.append(Section(section_id=sid, heading=text, level=level, heading_path=[h[1] for h in self.stack]))
        self.block(text, BlockType.heading, page, bbox, level=level)

    def block(self, text: str, btype: BlockType, page, bbox, level: int | None = None,
              scope: ScopeHint = ScopeHint.main, table_id: str | None = None, row_index: int | None = None,
              attrs: dict[str, str] | None = None) -> SemanticBlock:
        bid = f"b{len(self.blocks) + 1}"
        a = dict(attrs or {})
        key = text.lower()
        if len(key) > 15 and btype != BlockType.heading:
            if key in self.seen:
                a["dup_of"] = self.seen[key]
            else:
                self.seen[key] = bid
        if scope != ScopeHint.footer and table_id is None:
            self.since_table += 1
        blk = SemanticBlock(
            block_id=bid, type=btype, text=text, order=len(self.blocks), section_id=self.current,
            heading_path=[h[1] for h in self.stack], level=level, html_tag=None, dom_path=f"page{page}",
            stable_key=hashlib.sha1(f"{self.doc_id}|{page}|{len(self.blocks)}".encode()).hexdigest()[:12],
            scope=scope, source="pdf", page=page, bbox=bbox, table_id=table_id, row_index=row_index, attributes=a,
        )
        self.blocks.append(blk)
        return blk

    def table(self, rows: list[list[tuple[str, bool]]], page, bbox) -> None:
        if not rows:
            return
        tid = f"t{len(self.tables) + 1}"
        n_cols = max(len(r) for r in rows)
        header_rows = [i for i, r in enumerate(rows[:3]) if r and all(h for _, h in r if _)]
        if not header_rows and len(rows) > 2 and n_cols > 2:
            first = [c for c, _ in rows[0]]
            if all(first) and not any(re.search(r"\d", x) for x in first[1:]):
                header_rows = [0]
        col_headers = {c: " / ".join(dict.fromkeys(rows[r][c][0] for r in header_rows if c < len(rows[r]) and rows[r][c][0]))
                       for c in range(n_cols)}
        body = [i for i in range(len(rows)) if i not in header_rows]
        has_row_labels = n_cols >= 2 and sum(
            1 for i in body if rows[i] and rows[i][0][0] and not re.match(r"^[\d\s.,]+$", rows[i][0][0])
        ) >= max(1, len(body) // 2)
        continues = None
        pt = self.prev_table
        if (not header_rows and pt is not None and self.since_table == 0 and pt["n_cols"] == n_cols
                and (page is None or pt["page"] is None or 0 <= page - pt["page"] <= 1)):
            # the same table continued after a page (or parsing-window) break without repeating its header
            col_headers, has_row_labels, continues = dict(pt["col_headers"]), pt["has_row_labels"], pt["tid"]
        cells: list[TableCell] = []
        row_ids: list[str] = []
        for i, row in enumerate(rows):
            vals: list[tuple[int, str]] = []
            for c, (txt, is_h) in enumerate(row):
                cells.append(TableCell(row=i, col=c, text=txt, is_header=is_h))
                if txt and (not vals or vals[-1][1] != txt):
                    vals.append((c, txt))
            if not vals:
                continue
            if i in header_rows:
                blk = self.block(" | ".join(v for _, v in vals), BlockType.table_header, page, bbox, table_id=tid, row_index=i)
            elif has_row_labels and len(vals) >= 2:
                label, rest = vals[0][1], vals[1:]
                if len(rest) == 1 and (n_cols == 2 or not col_headers.get(rest[0][0])):
                    text = f"{label}: {rest[0][1]}"
                else:
                    text = f"{label} — " + "; ".join(f"{col_headers[c]}: {v}" if col_headers.get(c) else v for c, v in rest)
                attrs = {"row_label": label, **({"continues_table": continues} if continues else {})}
                blk = self.block(text, BlockType.table_row, page, bbox, table_id=tid, row_index=i, attrs=attrs)
            else:
                text = "; ".join(f"{col_headers[c]}: {v}" if col_headers.get(c) else v for c, v in vals) if any(col_headers.values()) else " | ".join(v for _, v in vals)
                blk = self.block(text, BlockType.table_row, page, bbox, table_id=tid, row_index=i,
                                 attrs={"continues_table": continues} if continues else None)
            row_ids.append(blk.block_id)
        self.tables.append(SemanticTable(table_id=tid, section_id=self.current, n_rows=len(rows), n_cols=n_cols,
                                         header_rows=header_rows, has_row_labels=has_row_labels, cells=cells,
                                         row_block_ids=row_ids, page=page,
                                         caption=f"continues {continues}" if continues else None))
        self.prev_table = {"tid": continues or tid, "n_cols": n_cols, "col_headers": col_headers,
                           "has_row_labels": has_row_labels, "page": page}
        self.since_table = 0

    def build(self, fmt, url, path, sha, title, warnings, parser) -> SemanticDocument:
        by_id = {s.section_id: s for s in self.sections}
        for blk in self.blocks:
            sec = by_id[blk.section_id]
            sec.block_ids.append(blk.block_id)
            sec.char_count += len(blk.text)
            if blk.page and blk.page not in sec.pages:
                sec.pages.append(blk.page)
        return SemanticDocument(
            doc_id=self.doc_id, format=fmt, source_url=url, source_path=path, title=title, language=None,
            content_sha256=sha, parser_version=parser, blocks=self.blocks, tables=self.tables,
            sections=[s for s in self.sections if s.block_ids or s.section_id == "s0"], parse_warnings=warnings,
        )
