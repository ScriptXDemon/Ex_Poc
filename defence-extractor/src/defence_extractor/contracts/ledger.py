"""Evidence ledger: every technical-looking statement ends extracted, rejected (with reason) or unresolved."""

from __future__ import annotations

from pydantic import BaseModel, Field

from .common import LedgerStatus


class LedgerItem(BaseModel):
    item_id: str  # inventory item id ("I0001") or discovery-only id ("D0001")
    origin: str  # inventory:<kind> | discovery
    block_id: str
    section_id: str | None = None
    text: str
    char_start: int | None = None
    char_end: int | None = None
    technical_candidate: bool = True
    status: LedgerStatus = LedgerStatus.open
    reason: str | None = None
    fact_ids: list[str] = Field(default_factory=list)
    reviewed_by: list[str] = Field(default_factory=list)


class LedgerSummary(BaseModel):
    total: int = 0
    open: int = 0
    extracted: int = 0
    rejected: int = 0
    unresolved: int = 0
    duplicate: int = 0
    rejected_by_reason: dict[str, int] = Field(default_factory=dict)


class Ledger(BaseModel):
    items: list[LedgerItem] = Field(default_factory=list)

    def by_id(self) -> dict[str, LedgerItem]:
        return {i.item_id: i for i in self.items}

    def summary(self) -> LedgerSummary:
        s = LedgerSummary(total=len(self.items))
        for it in self.items:
            setattr(s, it.status.value, getattr(s, it.status.value) + 1)
            if it.status == LedgerStatus.rejected:
                key = it.reason or "unspecified"
                s.rejected_by_reason[key] = s.rejected_by_reason.get(key, 0) + 1
        return s
