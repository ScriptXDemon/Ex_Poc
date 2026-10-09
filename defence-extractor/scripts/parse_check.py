"""Parse-only check (no LLM calls): ingest documents with the production settings and report pages, blocks, tables,
table continuations, page coverage and vision candidates. Warms the PDF window cache used by the pipeline.

    python scripts/parse_check.py @eval/large_ids.txt [--out var/parse_check.json]
"""

from __future__ import annotations

import json
import sys
import time
from collections import Counter
from pathlib import Path

from defence_extractor.agents.vision import vision_pages
from defence_extractor.config import get_settings
from defence_extractor.ingest import ingest
from defence_extractor.ingest.corpus import load_corpus


def main(argv: list[str]) -> None:
    ids = argv[0]
    ids = [x.strip() for x in Path(ids[1:]).read_text().split() if x.strip()] if ids.startswith("@") else ids.split(",")
    out = Path(argv[argv.index("--out") + 1]) if "--out" in argv else Path("var/parse_check.json")
    s = get_settings()
    refs = {r.doc_id: r for r in load_corpus("data/spec_pages_1000")}
    rows = []
    for did in ids:
        ref = refs.get(did)
        if ref is None:
            print(f"{did}: not in corpus", flush=True)
            continue
        t0 = time.time()
        kw = dict(pdf_ocr=s.pdf_ocr, pdf_threads=s.pdf_threads, cache_dir=s.var_dir / "pdf_windows",
                  window_pages=s.pdf_window_pages) if ref.format == "pdf" else {}
        res = ingest(ref, **kw)
        doc = res.doc
        pages = sorted({b.page for b in doc.blocks if b.page})
        n_pages = doc.metadata.get("pages") or 0
        cont = [t for t in doc.tables if (t.caption or "").startswith("continues ")]
        vis = vision_pages(doc, s.vision_min_image_share, s.vision_max_text_chars, 10_000) if ref.format == "pdf" else []
        stats = doc.metadata.get("page_stats") or {}
        no_text = sum(1 for v in stats.values() if v.get("chars", 0) < 40)
        row = {"doc_id": did, "format": ref.format, "pages": n_pages, "pages_with_blocks": len(pages),
               "first_page": pages[0] if pages else None, "last_page": pages[-1] if pages else None,
               "blocks": len(doc.blocks), "sections": len(doc.sections), "chars": sum(len(b.text) for b in doc.blocks),
               "tables": len(doc.tables), "continued_tables": len(cont),
               "continued_rows": sum(1 for b in doc.blocks if b.attributes.get("continues_table")),
               "types": dict(Counter(b.type.value for b in doc.blocks).most_common(6)),
               "no_text_pages": no_text, "vision_candidates": len(vis),
               "vision_capped_at": s.vision_max_pages, "vision_pages": vis[:60],
               "warnings": doc.parse_warnings, "seconds": round(time.time() - t0, 1)}
        rows.append(row)
        print(json.dumps({k: v for k, v in row.items() if k not in ("vision_pages", "types")}), flush=True)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rows, indent=1))
    print(f"wrote {out}")


if __name__ == "__main__":
    main(sys.argv[1:])
