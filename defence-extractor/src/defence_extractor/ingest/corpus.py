"""Document references: the spec_pages_1000 corpus (index.csv / pdf_index.csv) and benchmark snapshots."""

from __future__ import annotations

import csv
import hashlib
import json
import re
from pathlib import Path

from pydantic import BaseModel, Field


class DocRef(BaseModel):
    doc_id: str
    path: str
    format: str  # html | rendered_html | pdf
    url: str | None = None
    level: int | None = None
    host: str | None = None
    english: bool | None = None
    hint_page_type: str | None = None
    hint_products: list[str] = Field(default_factory=list)
    source: str = "corpus"
    chars: int | None = None  # main-text characters measured by the corpus gate (size / cost estimate)


def _products(cell: str | None) -> list[str]:
    return [p.strip() for p in re.split(r"[|;]", cell or "") if p.strip()]


def _int(v: str | None) -> int | None:
    return int(v) if v and v.strip().isdigit() else None


def _pdf_id(file: str) -> str:
    stem = Path(file).stem
    return "pdf_" + hashlib.sha1(stem.encode()).hexdigest()[:16]


def load_corpus(root: str | Path) -> list[DocRef]:
    """Every HTML/PDF file under ``root`` (index rows enrich metadata; files missing from the index still load)."""
    root = Path(root)
    refs: dict[str, DocRef] = {}
    idx = root / "index.csv"
    if idx.exists():
        with idx.open(encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                path = root / row["file"].replace("\\", "/")
                if not path.exists():
                    continue
                refs[str(path)] = DocRef(
                    doc_id=row.get("doc") or path.stem, path=str(path), format="html", url=row.get("url") or None,
                    level=int(row["level"]) if row.get("level", "").isdigit() else None, host=row.get("host"),
                    english=row.get("english") == "True", hint_page_type=row.get("page_type") or None,
                    hint_products=_products(row.get("products")), chars=_int(row.get("chars")),
                )
    pidx = root / "pdf_index.csv"
    if pidx.exists():
        with pidx.open(encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                path = root / row["file"].replace("\\", "/")
                if not path.exists():
                    continue
                refs[str(path)] = DocRef(
                    doc_id=_pdf_id(row["file"]), path=str(path), format="pdf", url=row.get("pdf_url") or None,
                    level=int(row["level"]) if row.get("level", "").isdigit() else None, host=row.get("host"),
                    english=row.get("english") == "True", hint_page_type=row.get("page_type") or None,
                    hint_products=_products(row.get("products")), chars=_int(row.get("chars")),
                )
    for p in sorted(root.rglob("*")):
        if p.suffix.lower() not in (".html", ".htm", ".pdf") or str(p) in refs:
            continue
        level = None
        for part in p.parts:
            if part.startswith("level") and part[5:].isdigit():
                level = int(part[5:])
        fmt = "pdf" if p.suffix.lower() == ".pdf" else "html"
        doc_id = _pdf_id(p.name) if fmt == "pdf" else (p.stem.split("__")[-1] if "__" in p.stem else p.stem)
        refs[str(p)] = DocRef(doc_id=doc_id, path=str(p), format=fmt, level=level,
                              host=p.stem.split("__")[0] if "__" in p.stem else None)
    out = sorted(refs.values(), key=lambda r: (r.level or 9, r.format, r.doc_id))
    seen: set[str] = set()
    uniq = []
    for r in out:
        if r.doc_id in seen:
            r.doc_id = f"{r.doc_id}_{hashlib.sha1(r.path.encode()).hexdigest()[:6]}"
        seen.add(r.doc_id)
        uniq.append(r)
    return uniq


def load_benchmark(root: str | Path) -> list[DocRef]:
    """Benchmark snapshots fetched by scripts/fetch_benchmark.py (manifest.json + <id>.html)."""
    root = Path(root)
    man = root / "manifest.json"
    if not man.exists():
        return []
    rows = json.loads(man.read_text())
    return [
        DocRef(doc_id=r["id"], path=str(root / f"{r['id']}.html"), format="html", url=r["url"], source="benchmark",
               hint_products=[r.get("name", "")])
        for r in rows
        if r.get("usable") and (root / f"{r['id']}.html").exists()
    ]
