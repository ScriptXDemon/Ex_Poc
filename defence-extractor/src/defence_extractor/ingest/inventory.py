"""Deterministic technical-candidate inventory. Seeds the evidence ledger so silent drops are measurable.

What counts as an inventory item (each with a verbatim char span inside one block):
  * every measurement span (number + unit, ranges, bounds, composites, calibres, counts, percentages ...)
  * the value of every key/value block and every value cell of a spec-table row (numeric or not)
  * JS counter values recovered from data-* attributes
Deterministic dismissal: dates, prices, phone-like numbers, and everything in navigation/footer/cookie scopes.
"""

from __future__ import annotations

import re

from ..contracts.document import NON_CONTENT_SCOPES, BlockType, ScopeHint, SemanticBlock, SemanticDocument
from ..contracts.inventory import InventoryItem, InventoryKind, Measurement
from .measurements import PHONE_RX, find_measurements, looks_like_date, looks_like_price

_YEAR_ONLY = re.compile(r"^(?:19|20)\d{2}$")
_CONTACT_LABEL = re.compile(r"(?i)\b(phone|tel|telephone|fax|mobile|contact|e-?mail|address|hours|available|opening|"
                            r"postcode|zip|vat|cin|gst|registration|reg\.? no|p\.?o\.? box)\b")
_CLOCK = re.compile(r"(?i)\b\d{1,2}[:.]\d{2}\s?(?:am|pm|hrs)\b")


def _value_spans(b: SemanticBlock) -> list[tuple[int, int, str | None]]:
    """(start, end, label) spans of the *values* inside key/value and table-row blocks."""
    t = b.text
    spans: list[tuple[int, int, str | None]] = []
    if b.type == BlockType.table_row and " — " in t:
        label, rest = t.split(" — ", 1)
        off = len(label) + 3
        pos = 0
        for part in rest.split("; "):
            seg_start = off + pos
            val_rel = part.find(": ")
            if val_rel >= 0:
                vs = seg_start + val_rel + 2
                ve = seg_start + len(part)
            else:
                vs, ve = seg_start, seg_start + len(part)
            if t[vs:ve].strip():
                spans.append((vs, ve, label.strip()))
            pos += len(part) + 2
        return spans
    if b.type in (BlockType.table_row, BlockType.key_value) and ": " in t:
        label, _ = t.split(": ", 1)
        if len(label) <= 80:
            vs = len(label) + 2
            spans.append((vs, len(t), label.strip()))
            return spans
    if b.type == BlockType.table_row and " | " in t:
        pos = 0
        cells = t.split(" | ")
        for i, c in enumerate(cells):
            if i > 0 and c.strip():
                spans.append((pos, pos + len(c), cells[0].strip()))
            pos += len(c) + 3
        return spans
    return spans


def build_inventory(doc: SemanticDocument, lang: str | None = None) -> list[InventoryItem]:
    items: list[InventoryItem] = []
    seen: set[tuple[str, int, int]] = set()

    def add(b: SemanticBlock, kind: InventoryKind, s: int, e: int, label: str | None = None,
            m: Measurement | None = None) -> None:
        key = (b.block_id, s, e)
        if key in seen or e <= s:
            return
        seen.add(key)
        text = b.text[s:e]
        in_scope = b.scope not in NON_CONTENT_SCOPES
        dismissed = None
        if not in_scope:
            dismissed = "navigation_contamination"
        elif looks_like_price(text):
            dismissed = "price"
        elif looks_like_date(text) or _YEAR_ONLY.match(text.strip()):
            dismissed = "date"
        elif m is None and PHONE_RX.fullmatch(text.strip() or "x"):
            dismissed = "not_technical"
        elif label and _CONTACT_LABEL.search(label):
            dismissed = "not_technical"
        elif _CLOCK.search(text):
            dismissed = "not_technical"
        items.append(
            InventoryItem(
                item_id=f"I{len(items) + 1:04d}", block_id=b.block_id, kind=kind, text=text, char_start=s,
                char_end=e, section_id=b.section_id, in_content_scope=in_scope, measurement=m, label=label,
                dismissed_reason=dismissed,
            )
        )

    for b in doc.blocks:
        if b.attributes.get("dup_of"):
            continue
        if b.type == BlockType.metadata and b.source != "title":
            continue
        if b.scope in (ScopeHint.cookie, ScopeHint.form):
            continue
        if b.type == BlockType.heading and b.source != "title" and not re.search(r"\d", b.text):
            continue
        vspans = _value_spans(b) if b.type in (BlockType.key_value, BlockType.table_row) else []
        ms = find_measurements(b.text, lang)
        covered: list[tuple[int, int]] = []
        for vs, ve, label in vspans:
            inner = [m for m in ms if m.start >= vs and m.end <= ve]
            if len(inner) >= 2 and all(";" in b.text[a.end:c.start] or "/" in b.text[a.end:c.start] or "," in b.text[a.end:c.start]
                                       for a, c in zip(inner, inner[1:])):
                for m in inner:  # several independent values in one cell -> one item each
                    add(b, InventoryKind.table_row if b.type == BlockType.table_row else InventoryKind.key_value,
                        m.start, m.end, label, m)
            else:
                add(b, InventoryKind.table_row if b.type == BlockType.table_row else InventoryKind.key_value,
                    vs, ve, label, inner[0] if len(inner) == 1 else None)
            covered.append((vs, ve))
        for m in ms:
            if any(vs <= m.start and m.end <= ve for vs, ve in covered):
                continue
            add(b, InventoryKind.measurement, m.start, m.end, None, m)
        if b.attributes.get("counter_attr") and not ms:
            add(b, InventoryKind.attribute, 0, len(b.text), None, None)
    return items
