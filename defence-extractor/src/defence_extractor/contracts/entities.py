"""Entities, relations and the page-local knowledge graph (A3, A4, A7)."""

from __future__ import annotations

from pydantic import BaseModel, Field

from .common import EntityType, Predicate, Role


class ProductClass(BaseModel):
    domain: str
    category: str
    subcategory: str | None = None


class DefenceEntity(BaseModel):
    entity_id: str  # "E1", "E2", ...
    name: str  # exact surface form
    aliases: list[str] = Field(default_factory=list)
    entity_type: EntityType = EntityType.other
    specific_model: bool = True
    role: Role = Role.other
    parent_entity_id: str | None = None  # variant_of / part_of / technology_of
    product_classes: list[ProductClass] = Field(default_factory=list)
    mention_block_ids: list[str] = Field(default_factory=list)
    confidence: float = 0.8
    note: str | None = None


class RelationEdge(BaseModel):
    edge_id: str
    subject_entity_id: str
    predicate: Predicate
    object_entity_id: str | None = None
    object_literal: str | None = None
    evidence_block_ids: list[str] = Field(default_factory=list)
    confidence: float = 0.8


class MarkerBinding(BaseModel):
    """A footnote / variant marker (``*``, ``**``, ``1``...) bound to its legend text."""

    marker: str
    legend_text: str
    legend_block_id: str
    used_in_block_ids: list[str] = Field(default_factory=list)


class ReferenceLink(BaseModel):
    """A7: a generic reference ("the system", "its") in a block resolved to an entity."""

    block_id: str
    phrase: str
    entity_id: str | None
    confidence: float = 0.7


class KnowledgeGraph(BaseModel):
    entities: list[DefenceEntity] = Field(default_factory=list)
    relations: list[RelationEdge] = Field(default_factory=list)
    focal_entity_ids: list[str] = Field(default_factory=list)
    markers: list[MarkerBinding] = Field(default_factory=list)
    references: list[ReferenceLink] = Field(default_factory=list)

    def entity_map(self) -> dict[str, DefenceEntity]:
        return {e.entity_id: e for e in self.entities}

    def get(self, entity_id: str | None) -> DefenceEntity | None:
        if entity_id is None:
            return None
        for e in self.entities:
            if e.entity_id == entity_id:
                return e
        return None
