"""Developer check: ingest a sample of corpus documents and print parse statistics.

usage: python scripts/dev_ingest.py <corpus_root> [--per-level N] [--pdf N] [--show DOC_ID]
"""

from __future__ import annotations

import argparse
import random
import time

from defence_extractor.ingest import ingest
from defence_extractor.ingest.corpus import load_corpus


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("--per-level", type=int, default=3)
    ap.add_argument("--pdf", type=int, default=0)
    ap.add_argument("--show", default=None)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    refs = load_corpus(args.root)
    print(f"corpus: {len(refs)} docs | html {sum(r.format != 'pdf' for r in refs)} | pdf {sum(r.format == 'pdf' for r in refs)}")
    rnd = random.Random(args.seed)
    sample = []
    if args.show:
        sample = [r for r in refs if r.doc_id == args.show]
    else:
        for lvl in (1, 2, 3, 4):
            html = [r for r in refs if r.level == lvl and r.format != "pdf"]
            sample += rnd.sample(html, min(args.per_level, len(html)))
        pdfs = [r for r in refs if r.format == "pdf"]
        sample += rnd.sample(pdfs, min(args.pdf, len(pdfs)))
    for r in sample:
        t0 = time.time()
        res = ingest(r)
        dt = time.time() - t0
        d, p = res.doc, res.profile
        open_items = [i for i in res.inventory if not i.dismissed_reason]
        print(
            f"L{r.level} {r.format:4} {r.doc_id[:22]:22} {dt:5.1f}s blocks={len(d.blocks):4} tables={len(d.tables):2} "
            f"sections={len(d.sections):3} lang={p.language} main_chars={p.main_chars:6} inv={len(res.inventory):4} "
            f"(open {len(open_items)}) markers={len(res.markers)} flags={p.flags} warn={len(d.parse_warnings)}"
        )
        if args.show:
            for b in d.blocks[:400]:
                print(f"  [{b.block_id}] {b.type.value[:5]:5} {b.scope.value[:5]:5} {b.section_id:4} {b.text[:150]!r}"
                      + (f"  dup_of={b.attributes['dup_of']}" if b.attributes.get('dup_of') else ""))
            for i in res.inventory[:120]:
                print(f"  {i.item_id} {i.kind.value:11} {i.block_id:5} {i.text[:60]!r} {i.dismissed_reason or ''}")
            for m in res.markers:
                print("  marker", m)


if __name__ == "__main__":
    main()
