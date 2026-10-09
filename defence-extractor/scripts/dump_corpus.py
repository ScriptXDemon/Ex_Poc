"""Dump the deterministic main content of corpus documents (for writing gold labels). No LLM involved.

usage: python scripts/dump_corpus.py <run_id> --pick 16-31          # list the picked docs (by position in selection.json) with sizes
       python scripts/dump_corpus.py <run_id> --ids doc_x,pdf_y [--max-chars 40000]
"""

from __future__ import annotations

import argparse
import json

from defence_extractor.config import get_settings
from defence_extractor.contracts.document import NON_CONTENT_SCOPES, BlockType
from defence_extractor.ingest import ingest
from defence_extractor.ingest.corpus import DocRef


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_id")
    ap.add_argument("--pick")
    ap.add_argument("--ids")
    ap.add_argument("--max-chars", type=int, default=40000)
    a = ap.parse_args()
    sel = [DocRef(**r) for r in json.loads((get_settings().runs_dir / a.run_id / "selection.json").read_text())]
    if a.pick:
        lo, hi = (int(x) for x in a.pick.split("-"))
        for i, r in enumerate(sel[lo:hi + 1], lo):
            print(i, r.doc_id, f"L{r.level}", r.format, r.host, r.hint_page_type, r.hint_products[:3], r.url[:110])
        return
    refs = {r.doc_id: r for r in sel}
    for i in a.ids.split(","):
        r = refs[i]
        d = ingest(r).doc
        print(f"\n######## {i} | L{r.level} {r.format} | {r.url}")
        out, n = [], 0
        for b in d.blocks:
            if b.attributes.get("dup_of") or b.scope in NON_CONTENT_SCOPES:
                continue
            if b.type == BlockType.metadata and b.source not in ("title", "meta"):
                continue
            line = f"[{b.block_id}|{b.scope.value[:4]}] {'## ' if b.type == BlockType.heading else ''}{b.text}"
            n += len(line)
            if n > a.max_chars:
                out.append("... (truncated)")
                break
            out.append(line)
        print(f"(main chars ~{n})")
        print("\n".join(out))


if __name__ == "__main__":
    main()
