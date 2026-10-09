"""SemanticDocument: the deterministic, evidence-preserving representation of one input document."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field


class BlockType(StrEnum):
    heading = "heading"
    paragraph = "paragraph"
    list_item = "list_item"
    table_row = "table_row"
    table_header = "table_header"
    key_value = "key_value"
    caption = "caption"
    footnote = "footnote"
    metadata = "metadata"
    other = "other"


class ScopeHint(StrEnum):
    main = "main"
    header = "header"
    navigation = "navigation"
    breadcrumb = "breadcrumb"
    footer = "footer"
    sidebar = "sidebar"
    related_content = "related_content"
    cookie = "cookie"
    form = "form"
    metadata = "metadata"
    unknown = "unknown"


#: scopes whose content is never product evidence unless an agent explicitly links it
NON_CONTENT_SCOPES = {ScopeHint.navigation, ScopeHint.footer, ScopeHint.cookie, ScopeHint.form, ScopeHint.header}


class SemanticBlock(BaseModel):
    block_id: str  # short, prompt-facing id ("b17"); stable for a given input + parser version
    type: BlockType
    text: str
    order: int
    section_id: str
    heading_path: list[str] = Field(default_factory=list)
    level: int | None = None  # heading level for headings
    html_tag: str | None = None
    dom_path: str | None = None
    stable_key: str | None = None  # hash of doc id + dom path (+ fragment index)
    visible: bool = True
    scope: ScopeHint = ScopeHint.main
    source: Literal["body", "title", "meta", "jsonld", "attribute", "breadcrumb", "pdf"] = "body"
    page: int | None = None  # pdf page number (1-based)
    bbox: tuple[float, float, float, float] | None = None
    table_id: str | None = None
    row_index: int | None = None
    attributes: dict[str, str] = Field(default_factory=dict)


class TableCell(BaseModel):
    row: int
    col: int
    text: str
    is_header: bool = False
    rowspan: int = 1
    colspan: int = 1


class SemanticTable(BaseModel):
    table_id: str
    section_id: str
    caption: str | None = None
    n_rows: int
    n_cols: int
    header_rows: list[int] = Field(default_factory=list)
    has_row_labels: bool = False
    cells: list[TableCell] = Field(default_factory=list)
    row_block_ids: list[str] = Field(default_factory=list)
    page: int | None = None


class Section(BaseModel):
    section_id: str
    heading: str | None
    level: int
    heading_path: list[str] = Field(default_factory=list)
    block_ids: list[str] = Field(default_factory=list)
    scope: ScopeHint = ScopeHint.main
    char_count: int = 0
    pages: list[int] = Field(default_factory=list)


class SemanticDocument(BaseModel):
    doc_id: str
    format: Literal["html", "rendered_html", "pdf"]
    source_url: str | None = None
    source_path: str | None = None
    title: str | None = None
    language: str | None = None
    content_sha256: str
    parser_version: str
    blocks: list[SemanticBlock] = Field(default_factory=list)
    tables: list[SemanticTable] = Field(default_factory=list)
    sections: list[Section] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    parse_warnings: list[str] = Field(default_factory=list)

    # ---- helpers (not serialized as fields) -------------------------------------------------
    def block_map(self) -> dict[str, SemanticBlock]:
        return {b.block_id: b for b in self.blocks}

    def section_map(self) -> dict[str, Section]:
        return {s.section_id: s for s in self.sections}

    def main_text_chars(self) -> int:
        return sum(len(b.text) for b in self.blocks if b.scope not in NON_CONTENT_SCOPES and b.source == "body")


class SourceProfile(BaseModel):
    """A0 output: cheap, mostly deterministic profile that sets execution policy."""

    doc_id: str
    format: str
    language: str | None
    language_confidence: float = 0.0
    is_english: bool = True
    main_chars: int = 0
    n_blocks: int = 0
    n_tables: int = 0
    n_sections: int = 0
    n_measurements: int = 0
    measurement_density: float = 0.0  # measurements per 1k main chars
    url_kind: Literal["product", "news", "catalogue", "pdf", "unknown"] = "unknown"
    flags: list[str] = Field(default_factory=list)  # needs_table_priority, multi_product_risk, ...
    size_class: Literal["small", "medium", "large", "huge"] = "small"
