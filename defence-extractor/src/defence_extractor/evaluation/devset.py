"""Fast test sets: a short list of documents (PDF page subsets allowed) to measure each change quickly.

    docs:
      - {id: pdf_d802f1477a652544, pages: "4-7", why: "spec cards"}
      - {id: E661}

Run with `dx run --devset eval/devset_weak.yaml --run-id dev1`, score with
`dx eval dev1 --gold eval/gold_large,eval/gold_corpus,eval/gold --devset eval/devset_weak.yaml`.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from ..ingest.corpus import DocRef, load_benchmark, load_corpus, parse_pages


def load_devset(path: str | Path) -> list[dict]:
    d = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return [dict(e) for e in d.get("docs", [])]


def devset_refs(entries: list[dict], corpus_root: Path, benchmark_root: Path) -> list[DocRef]:
    """DocRefs for the listed ids (corpus or benchmark), each with its page subset."""
    want = {e["id"]: e for e in entries}
    pool: dict[str, DocRef] = {}
    for loader, root in ((load_corpus, corpus_root), (load_benchmark, benchmark_root)):
        if Path(root).exists():
            for r in loader(root):
                if r.doc_id in want and r.doc_id not in pool:
                    pool[r.doc_id] = r
    missing = [i for i in want if i not in pool]
    if missing:
        raise SystemExit(f"devset ids not found in corpus or benchmark: {missing}")
    out = []
    for e in entries:
        r = pool[e["id"]].model_copy()
        r.pages = str(e["pages"]) if e.get("pages") else None
        out.append(r)
    return out


def page_subsets(entries: list[dict]) -> dict[str, set[int]]:
    return {e["id"]: parse_pages(str(e["pages"])) for e in entries if e.get("pages")}
