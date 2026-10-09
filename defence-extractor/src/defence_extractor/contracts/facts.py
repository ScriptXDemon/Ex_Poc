"""Facts from discovery through to final specification records."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from .common import Confidence, EvidenceRef, FactClass, OntologyStatus, VerificationStatus
from .inventory import Comparator


class Condition(BaseModel):
    """Configuration / variant scope of a value, e.g. {dimension: "powerplant", value_text: "CT7-2E1"}."""

    dimension: str
    value_text: str


class Applicability(BaseModel):
    variant_entity_id: str | None = None
    conditions: list[Condition] = Field(default_factory=list)

    def key(self) -> str:
        conds = "|".join(sorted(f"{c.dimension.lower()}={c.value_text.lower()}" for c in self.conditions))
        return f"{self.variant_entity_id or ''}#{conds}"


class ValuePart(BaseModel):
    value: float
    unit: str | None = None
    normalized_value: float | None = None
    normalized_unit: str | None = None


class StructuredValue(BaseModel):
    """Derived, optional machine-readable reading of ``value_text``. Never replaces the source text."""

    kind: Literal["scalar", "range", "bound", "set", "composite", "count", "ratio", "text", "boolean"] = "text"
    comparator: Comparator = Comparator.eq
    nominal: ValuePart | None = None
    min: ValuePart | None = None
    max: ValuePart | None = None
    components: list[ValuePart] = Field(default_factory=list)  # composite L x W x H, sets of numbers
    alternates: list[ValuePart] = Field(default_factory=list)  # dual units: "2,893 m (9,490 ft)"
    dimension: str | None = None
    multiplier: float | None = None  # "2 x CT7-2E1" -> 2
    text: str | None = None  # textual remainder ("turbo diesel")


class ParameterRef(BaseModel):
    id: str  # canonical / candidate id, or proposed snake_case name for dynamic
    status: OntologyStatus
    source_label: str | None = None  # label as written on the page
    display_name: str | None = None
    category_path: list[str] = Field(default_factory=list)


DiscoveryMethod = Literal[
    "open_discovery", "known_spec_hunter", "coverage_recovery", "deterministic_table", "split", "composite", "designation"
]


class CandidateFact(BaseModel):
    """A technical statement found in the document, before / during attribution, mapping and verification."""

    fact_id: str  # "F0001"
    method: DiscoveryMethod = "open_discovery"
    section_id: str | None = None
    subject_entity_id: str | None = None  # proposed by the discovering agent
    subject_text: str | None = None
    owner_entity_id: str | None = None  # decided by attribution (A6)
    owner_confidence: float = 0.0
    alternative_owner_ids: list[str] = Field(default_factory=list)
    attribution_note: str | None = None
    source_parameter: str | None = None
    parameter_hint: str | None = None
    value_text: str
    fact_class: FactClass = FactClass.technical_specification
    applicability: Applicability = Field(default_factory=Applicability)
    evidence: list[EvidenceRef] = Field(default_factory=list)
    inventory_item_ids: list[str] = Field(default_factory=list)
    parameter: ParameterRef | None = None  # set by A8
    mapping_confidence: float = 0.0
    value: StructuredValue | None = None  # set by A9
    derived_from: str | None = None  # fact id this one was split from
    anomalies: list[str] = Field(default_factory=list)  # set by A11
    verification: VerificationStatus = VerificationStatus.pending
    verification_note: str | None = None
    confidence: Confidence = Field(default_factory=Confidence)
    rejection_reason: str | None = None
    agents: list[str] = Field(default_factory=list)


class SpecRecord(BaseModel):
    """Final, verified (or low-confidence verified) record inside a product."""

    spec_id: str
    parameter: ParameterRef
    fact_class: FactClass
    value_text: str
    value: StructuredValue | None = None
    applies_when: Applicability = Field(default_factory=Applicability)
    evidence: list[EvidenceRef] = Field(default_factory=list)
    confidence: Confidence = Field(default_factory=Confidence)
    verification: VerificationStatus = VerificationStatus.verified
    notes: str | None = None


class TextFactRecord(BaseModel):
    """Capabilities, features, missions, targets, components ... (non-numeric product facts)."""

    fact_id: str
    text: str
    fact_class: FactClass
    source_parameter: str | None = None
    related_entity_id: str | None = None  # e.g. the component / technology / compatible system entity
    applies_when: Applicability = Field(default_factory=Applicability)
    evidence: list[EvidenceRef] = Field(default_factory=list)
    confidence: float = 0.0
    verification: VerificationStatus = VerificationStatus.verified
