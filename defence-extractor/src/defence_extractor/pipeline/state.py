"""Per-document pipeline state. Persisted after every stage so a run can resume from its last checkpoint."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field

from ..contracts.common import StageTiming
from ..contracts.entities import KnowledgeGraph
from ..contracts.facts import CandidateFact
from ..contracts.page import PageMap
from ..ingest import IngestResult
from ..ingest.corpus import DocRef

STAGES = ["ingest", "vision", "page_map", "entities", "roles", "references", "product_map", "discovery", "attribution",
          "known_hunt", "mapping", "structuring", "audit", "anomaly", "verification", "assembly"]


class DocState(BaseModel):
    run_id: str
    ref: DocRef
    ingest: IngestResult | None = None
    page_map: PageMap | None = None
    kg: KnowledgeGraph | None = None
    facts: list[CandidateFact] = Field(default_factory=list)
    item_dismissals: dict[str, str] = Field(default_factory=dict)  # inventory item -> reason (A10 not_a_fact)
    audit_rounds: int = 0
    recovered_fact_ids: list[str] = Field(default_factory=list)
    discovered_inventory_ids: list[str] = Field(default_factory=list)  # items covered before the audit (recall proxy)
    skipped_block_ids: list[str] = Field(default_factory=list)  # not read by A5B (document budget)
    segments: dict[str, list[str]] = Field(default_factory=dict)  # section id -> product(s) it describes (product map)
    segment_source: dict[str, str] = Field(default_factory=dict)  # section id -> how the owner was decided
    row_owners: dict[str, str] = Field(default_factory=dict)  # table-row block id -> product (product-keyed tables)
    col_owners: dict[str, dict[str, str]] = Field(default_factory=dict)  # table id -> {column header: product}
    done: list[str] = Field(default_factory=list)
    timings: list[StageTiming] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    status: str = "running"
    fact_counter: int = 0

    def next_fact_id(self) -> str:
        self.fact_counter += 1
        return f"F{self.fact_counter:04d}"

    # ------------------------------------------------------------------------------------------
    @staticmethod
    def path_for(runs_dir: Path, run_id: str, doc_id: str) -> Path:
        return runs_dir / run_id / "docs" / doc_id

    def save(self, runs_dir: Path) -> None:
        d = self.path_for(runs_dir, self.run_id, self.ref.doc_id)
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / "state.json.tmp"
        tmp.write_text(self.model_dump_json(), encoding="utf-8")
        tmp.replace(d / "state.json")

    @classmethod
    def load(cls, runs_dir: Path, run_id: str, doc_id: str) -> DocState | None:
        p = cls.path_for(runs_dir, run_id, doc_id) / "state.json"
        if not p.exists():
            return None
        try:
            return cls.model_validate(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            return None
