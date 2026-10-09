"""Deterministic ingestion: A0 profile + A1 parsing + inventory + marker bindings."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from ..contracts.document import SemanticDocument, SourceProfile
from ..contracts.entities import MarkerBinding
from ..contracts.inventory import InventoryItem
from .corpus import DocRef
from .html_adapter import HtmlAdapter
from .inventory import build_inventory
from .markers import find_marker_bindings
from .profile import profile_document


class IngestResult(BaseModel):
    doc: SemanticDocument
    profile: SourceProfile
    inventory: list[InventoryItem] = Field(default_factory=list)
    markers: list[MarkerBinding] = Field(default_factory=list)


def ingest(ref: DocRef, pdf_ocr: bool = True, pdf_threads: int = 8, cache_dir: str | Path | None = None,
           window_pages: int = 40) -> IngestResult:
    path = Path(ref.path)
    if ref.format == "pdf":
        from .pdf_adapter import PdfAdapter

        doc = PdfAdapter(ocr=pdf_ocr, threads=pdf_threads, window_pages=window_pages, cache_dir=cache_dir).parse(
            path, ref.doc_id, ref.url)
    else:
        doc = HtmlAdapter().parse(path.read_bytes(), ref.doc_id, ref.url, str(path), fmt=ref.format)
    return analyse(doc)


def analyse(doc: SemanticDocument) -> IngestResult:
    """Inventory, profile and marker legends for a parsed document (re-run after vision adds blocks)."""
    # first pass inventory (language unknown), then profile (detects language), then re-run with language
    inv = build_inventory(doc, doc.language)
    prof = profile_document(doc, inv)
    if prof.language and prof.language != (doc.language or "en"):
        doc.language = prof.language
        inv = build_inventory(doc, doc.language)
    elif doc.language is None:
        doc.language = prof.language
    markers = find_marker_bindings(doc)
    return IngestResult(doc=doc, profile=prof, inventory=inv, markers=markers)
