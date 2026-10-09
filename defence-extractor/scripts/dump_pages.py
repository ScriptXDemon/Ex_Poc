"""Dump a document's parsed text with page numbers (for writing position-aware answer keys). No LLM involved.

    python scripts/dump_pages.py <doc_id> [--pages 1-10,50-55] [--frac 0.45-0.55] [--out file]

PDFs use the same window cache as the pipeline; --frac selects by position in the block sequence (HTML or PDF).
"""

from __future__ import annotations

import argparse
from pathlib import Path

from defence_extractor.config import get_settings
from defence_extractor.contracts.document import NON_CONTENT_SCOPES, BlockType
from defence_extractor.ingest import ingest
from defence_extractor.ingest.corpus import load_corpus


def _ranges(spec: str | None) -> list[tuple[float, float]]:
    out = []
    for part in (spec or "").split(","):
        if part.strip():
            a, _, b = part.partition("-")
            out.append((float(a), float(b or a)))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("doc_id")
    ap.add_argument("--pages")
    ap.add_argument("--frac")
    ap.add_argument("--out")
    a = ap.parse_args()
    s = get_settings()
    ref = next(r for r in load_corpus("data/spec_pages_1000") if r.doc_id == a.doc_id)
    kw = dict(pdf_ocr=s.pdf_ocr, pdf_threads=s.pdf_threads, cache_dir=s.var_dir / "pdf_windows",
              window_pages=s.pdf_window_pages) if ref.format == "pdf" else {}
    doc = ingest(ref, **kw).doc
    pages, fracs = _ranges(a.pages), _ranges(a.frac)
    n = len(doc.blocks)
    smap = doc.section_map()
    lines = [f"# {ref.doc_id} | {ref.format} | {ref.url} | blocks={n} pages={doc.metadata.get('pages')}"]
    last_sec = None
    for i, b in enumerate(doc.blocks):
        if pages and not any(lo <= (b.page or 0) <= hi for lo, hi in pages):
            continue
        if fracs and not any(lo <= i / max(1, n) <= hi for lo, hi in fracs):
            continue
        if b.scope in NON_CONTENT_SCOPES and b.type != BlockType.heading:
            continue
        if b.section_id != last_sec:
            sec = smap.get(b.section_id)
            lines.append(f"\n=== {' > '.join(sec.heading_path) if sec and sec.heading_path else '(no heading)'}")
            last_sec = b.section_id
        tag = f"p{b.page}|" if b.page else ""
        lines.append(f"[{tag}{i / max(1, n):.2f}|{b.block_id}|{b.type.value[:5]}] {'## ' if b.type == BlockType.heading else ''}{b.text}")
    text = "\n".join(lines)
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")
        print(f"wrote {a.out} ({len(text)} chars)")
    else:
        print(text)


if __name__ == "__main__":
    main()
