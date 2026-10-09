"""Deterministic technical-candidate inventory (seeds the evidence ledger)."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class Comparator(StrEnum):
    eq = "eq"
    lt = "lt"
    le = "le"
    gt = "gt"
    ge = "ge"
    approx = "approx"
    up_to = "up_to"
    at_least = "at_least"


class Quantity(BaseModel):
    value: float
    unit: str | None = None  # unit as written (normalised spelling), e.g. "km", "mm", "kn"
    unit_key: str | None = None  # canonical unit key from the lexicon, e.g. "km", "knot"
    dimension: str | None = None  # length, mass, speed, ...
    raw: str = ""


class Measurement(BaseModel):
    """One measurement-like span found in a block of text."""

    text: str  # verbatim span
    start: int
    end: int
    kind: str  # scalar | range | bound | composite | dual | ratio | calibre | count | percent
    comparator: Comparator = Comparator.eq
    quantities: list[Quantity] = Field(default_factory=list)
    dimension: str | None = None
    note: str | None = None


class InventoryKind(StrEnum):
    measurement = "measurement"
    key_value = "key_value"
    table_row = "table_row"
    spec_bullet = "spec_bullet"
    attribute = "attribute"
    designation = "designation"


class InventoryItem(BaseModel):
    item_id: str
    block_id: str
    kind: InventoryKind
    text: str  # verbatim span text
    char_start: int
    char_end: int
    section_id: str
    in_content_scope: bool = True
    measurement: Measurement | None = None
    label: str | None = None  # for key_value / table_row items
    dismissed_reason: str | None = None  # set by deterministic dismissal rules (dates, phone numbers ...)
