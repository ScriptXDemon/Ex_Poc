"""LLM-facing output schemas (one per agent). Sent as strict JSON Schema; validated with Pydantic."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from ..contracts.common import EntityType, FactClass, Predicate, Role
from ..contracts.page import SectionRole


# ---- A2 -------------------------------------------------------------------------------------------
class A2SectionOut(BaseModel):
    section_id: str
    role: SectionRole
    spec_density: Literal["high", "medium", "low", "none"]
    subjects: list[str]


class A2Out(BaseModel):
    page_type: str
    page_scope: str
    source_company_candidates: list[str]
    primary_subject_candidates: list[str]
    sections: list[A2SectionOut]
    risk_flags: list[str]
    confidence: float


# ---- A3 -------------------------------------------------------------------------------------------
class A3EntityOut(BaseModel):
    name: str
    aliases: list[str]
    entity_type: EntityType
    specific_model: bool
    block_ids: list[str]


class A3Out(BaseModel):
    entities: list[A3EntityOut]


# ---- A4 -------------------------------------------------------------------------------------------
class ClassOut(BaseModel):
    domain: str
    category: str


class A4RoleOut(BaseModel):
    entity_id: str
    role: Role
    parent_entity_id: str | None
    product_classes: list[ClassOut]


class A4RelOut(BaseModel):
    subject_id: str
    predicate: Predicate
    object_id: str | None
    object_text: str | None
    block_ids: list[str]


class A4Out(BaseModel):
    focal_entity_ids: list[str]
    roles: list[A4RoleOut]
    relations: list[A4RelOut]
    same_entity_groups: list[list[str]]


# ---- A7 -------------------------------------------------------------------------------------------
class A7LinkOut(BaseModel):
    block_id: str
    phrase: str
    entity_id: str | None
    confidence: float


class A7Out(BaseModel):
    links: list[A7LinkOut]


# ---- A5A / A5B / A10 facts ------------------------------------------------------------------------
class CondOut(BaseModel):
    dimension: str
    value_text: str


class FactOut(BaseModel):
    subject_id: str | None
    subject_text: str | None
    label: str | None
    parameter: str
    value_text: str
    fact_class: FactClass
    conditions: list[CondOut]
    block_ids: list[str]


class FactsOut(BaseModel):
    facts: list[FactOut]


# ---- A6 -------------------------------------------------------------------------------------------
class A6DecisionOut(BaseModel):
    fact_id: str
    owner_id: str | None
    alternative_ids: list[str]
    confidence: float
    reason: str


class A6Out(BaseModel):
    decisions: list[A6DecisionOut]


# ---- A8 -------------------------------------------------------------------------------------------
class A8MapOut(BaseModel):
    key: str
    parameter_id: str | None
    dynamic_name: str | None
    confidence: float


class A8Out(BaseModel):
    mappings: list[A8MapOut]


# ---- A10 ------------------------------------------------------------------------------------------
class A10IssueOut(BaseModel):
    kind: Literal["missing", "wrong_owner", "merged", "incomplete_value", "not_a_fact"]
    fact_id: str | None
    item_id: str | None
    fact: FactOut | None
    note: str


class A10Out(BaseModel):
    issues: list[A10IssueOut]


# ---- A12 ------------------------------------------------------------------------------------------
class A12VerdictOut(BaseModel):
    fact_id: str
    verdict: Literal["supported", "unsupported", "wrong_owner", "wrong_parameter", "not_a_spec", "ambiguous"]
    correct_owner_id: str | None
    attribution_confidence: float
    mapping_confidence: float
    value_confidence: float
    note: str | None


class A12Out(BaseModel):
    verdicts: list[A12VerdictOut]
