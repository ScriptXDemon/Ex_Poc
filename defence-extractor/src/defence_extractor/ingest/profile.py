"""A0 Source profiler (deterministic). Sets execution policy before any LLM call."""

from __future__ import annotations

import re

from ..contracts.document import NON_CONTENT_SCOPES, BlockType, ScopeHint, SemanticDocument, SourceProfile
from ..contracts.inventory import InventoryItem
from .language import detect_language

_NEWS_URL = re.compile(r"(?i)/(news|press|media|article|articles|blog|stories|story|newsroom|press-releases?|insights?|events?)/")
_CATALOGUE_URL = re.compile(r"(?i)/(products|catalogue|catalog|solutions|portfolio|range)/?$")


def profile_document(doc: SemanticDocument, inventory: list[InventoryItem]) -> SourceProfile:
    main_blocks = [b for b in doc.blocks if b.scope not in NON_CONTENT_SCOPES and b.source in ("body", "pdf")]
    main_text = " ".join(b.text for b in main_blocks if not b.attributes.get("dup_of"))
    lang, conf = detect_language(main_text)
    if doc.language and (conf < 0.8 or lang is None):
        lang = doc.language
    main_chars = len(main_text)
    n_meas = sum(1 for i in inventory if i.measurement is not None and i.in_content_scope and not i.dismissed_reason)
    density = n_meas / max(1.0, main_chars / 1000.0)
    url = doc.source_url or ""
    if doc.format == "pdf":
        url_kind = "pdf"
    elif _NEWS_URL.search(url):
        url_kind = "news"
    elif _CATALOGUE_URL.search(url):
        url_kind = "catalogue"
    elif url:
        url_kind = "product"
    else:
        url_kind = "unknown"
    flags: list[str] = []
    spec_rows = sum(1 for b in main_blocks if b.type in (BlockType.table_row, BlockType.key_value))
    if spec_rows >= 5:
        flags.append("needs_table_priority")
    headings = [b for b in main_blocks if b.type == BlockType.heading]
    if len(headings) >= 6 and n_meas >= 20:
        flags.append("multi_product_risk")
    related_chars = sum(len(b.text) for b in doc.blocks if b.scope in (ScopeHint.related_content, ScopeHint.sidebar))
    if related_chars > 0.3 * max(1, main_chars):
        flags.append("contamination_risk")
    if main_chars < 400:
        flags.append("low_content")
    if main_chars < 400 and len(doc.blocks) < 15 and doc.format != "pdf":
        flags.append("dynamic_content_hint")
    if any(b.attributes.get("counter_attr") for b in doc.blocks):
        flags.append("js_counters")
    if doc.parse_warnings:
        flags.append("parse_warnings")
    size_class = "small" if main_chars < 6000 else "medium" if main_chars < 24000 else "large" if main_chars < 80000 else "huge"
    return SourceProfile(
        doc_id=doc.doc_id, format=doc.format, language=lang, language_confidence=round(conf, 3),
        is_english=(lang or "en") == "en", main_chars=main_chars, n_blocks=len(doc.blocks), n_tables=len(doc.tables),
        n_sections=len(doc.sections), n_measurements=n_meas, measurement_density=round(density, 2),
        url_kind=url_kind, flags=flags, size_class=size_class,
    )
