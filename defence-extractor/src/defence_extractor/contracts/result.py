"""A13 final document result (the JSON the system publishes)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from .common import EntityType, Role, StageTiming, Usage
from .entities import DefenceEntity, ProductClass, RelationEdge
from .facts import SpecRecord, TextFactRecord
from .ledger import LedgerItem, LedgerSummary


class Coverage(BaseModel):
    ledger_items: int = 0
    extracted: int = 0
    rejected: int = 0
    unresolved: int = 0
    duplicate: int = 0
    open: int = 0
    inventory_items: int = 0
    inventory_found_by_discovery: int = 0  # recall proxy: inventory items matched by LLM discovery before audit
    recovered_by_audit: int = 0


class ProductRecord(BaseModel):
    entity_id: str
    name: str
    aliases: list[str] = Field(default_factory=list)
    entity_type: EntityType
    role: Role
    manufacturer: str | None = None
    manufacturer_entity_id: str | None = None
    parent_entity_id: str | None = None
    classification: list[ProductClass] = Field(default_factory=list)
    specifications: list[SpecRecord] = Field(default_factory=list)
    capabilities: list[TextFactRecord] = Field(default_factory=list)
    features: list[TextFactRecord] = Field(default_factory=list)
    technologies: list[TextFactRecord] = Field(default_factory=list)
    components: list[TextFactRecord] = Field(default_factory=list)
    compatibility: list[TextFactRecord] = Field(default_factory=list)
    missions: list[TextFactRecord] = Field(default_factory=list)
    targets: list[TextFactRecord] = Field(default_factory=list)
    unresolved: list[dict[str, Any]] = Field(default_factory=list)
    relations: list[RelationEdge] = Field(default_factory=list)
    # product sheet extras: values stated differently in different places, and the specs of this product's own
    # parts (engine, winch, sensor, variant...) rolled up from their records
    conflicts: list[dict[str, Any]] = Field(default_factory=list)
    subsystems: list[dict[str, Any]] = Field(default_factory=list)


class ExecutionManifest(BaseModel):
    pipeline_version: str
    ontology_version: str
    parser_version: str
    run_id: str
    models: dict[str, str] = Field(default_factory=dict)
    providers_used: dict[str, int] = Field(default_factory=dict)
    usage: Usage = Field(default_factory=Usage)
    stages: list[StageTiming] = Field(default_factory=list)
    duration_s: float = 0.0
    status: str = "completed"
    errors: list[str] = Field(default_factory=list)


class FinalDocumentResult(BaseModel):
    document: dict[str, Any]
    companies: list[DefenceEntity] = Field(default_factory=list)
    entities: list[DefenceEntity] = Field(default_factory=list)
    products: list[ProductRecord] = Field(default_factory=list)
    relations: list[RelationEdge] = Field(default_factory=list)
    unattributed: list[dict[str, Any]] = Field(default_factory=list)
    coverage: Coverage = Field(default_factory=Coverage)
    ledger_summary: LedgerSummary = Field(default_factory=LedgerSummary)
    ledger: list[LedgerItem] = Field(default_factory=list)
    execution: ExecutionManifest
