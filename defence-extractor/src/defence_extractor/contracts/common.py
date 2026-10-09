"""Shared enums and small value objects used across all contracts."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class FactClass(StrEnum):
    technical_specification = "technical_specification"
    performance_specification = "performance_specification"
    physical_specification = "physical_specification"
    configuration = "configuration"
    capability = "capability"
    feature = "feature"
    compatibility = "compatibility"
    component = "component"
    payload = "payload"
    armament = "armament"
    ammunition = "ammunition"
    mission = "mission"
    target = "target"
    protection = "protection"
    operational_characteristic = "operational_characteristic"
    technology = "technology"
    marketing_claim = "marketing_claim"
    procurement_information = "procurement_information"
    operational_event = "operational_event"  # deployments, exercises, port visits, readiness, incidents: not specs
    other = "other"


#: fact classes that end up in the product's ``specifications`` list
SPEC_CLASSES = {
    FactClass.technical_specification,
    FactClass.performance_specification,
    FactClass.physical_specification,
    FactClass.configuration,
    FactClass.payload,
    FactClass.armament,
    FactClass.ammunition,
    FactClass.protection,
    FactClass.operational_characteristic,
}
#: fact classes that are never product specifications (kept only in the ledger)
NON_PRODUCT_CLASSES = {FactClass.marketing_claim, FactClass.procurement_information, FactClass.operational_event}


class EntityType(StrEnum):
    company = "company"
    product = "product"
    product_family = "product_family"
    variant = "variant"
    subsystem = "subsystem"
    component = "component"
    weapon = "weapon"
    ammunition = "ammunition"
    sensor = "sensor"
    engine = "engine"
    platform = "platform"
    technology = "technology"
    organization = "organization"
    target = "target"
    generic_class = "generic_class"
    other = "other"


class Role(StrEnum):
    focal_product = "focal_product"
    related_product = "related_product"
    variant = "variant"
    family = "family"
    subsystem = "subsystem"
    component = "component"
    weapon = "weapon"
    ammunition = "ammunition"
    launch_platform = "launch_platform"
    carrier_platform = "carrier_platform"
    compatible_system = "compatible_system"
    sensor = "sensor"
    engine = "engine"
    technology = "technology"
    reference_product = "reference_product"
    competitor = "competitor"
    legacy_product = "legacy_product"
    accessory = "accessory"
    manufacturer = "manufacturer"
    customer = "customer"
    target = "target"
    generic_class = "generic_class"
    other = "other"


#: roles whose facts we extract into product records
PRODUCT_LIKE_ROLES = {
    Role.focal_product,
    Role.related_product,
    Role.variant,
    Role.family,
    Role.subsystem,
    Role.component,
    Role.weapon,
    Role.ammunition,
    Role.launch_platform,
    Role.carrier_platform,
    Role.compatible_system,
    Role.sensor,
    Role.engine,
    Role.technology,
    Role.reference_product,
    Role.competitor,
    Role.legacy_product,
    Role.accessory,
}


class Predicate(StrEnum):
    manufactures = "manufactures"
    variant_of = "variant_of"
    part_of = "part_of"
    mounted_on = "mounted_on"
    carries = "carries"
    fires = "fires"
    uses = "uses"
    integrated_with = "integrated_with"
    launched_from = "launched_from"
    compatible_with = "compatible_with"
    targets = "targets"
    replaces = "replaces"
    compares_with = "compares_with"
    powered_by = "powered_by"
    equipped_with = "equipped_with"
    transportable_by = "transportable_by"
    incorporates_technology = "incorporates_technology"
    supplied_to = "supplied_to"
    other = "other"


class VerificationStatus(StrEnum):
    verified = "verified"
    verified_low_confidence = "verified_low_confidence"
    unresolved = "unresolved"
    rejected = "rejected"
    pending = "pending"


class LedgerStatus(StrEnum):
    open = "open"
    extracted = "extracted"
    rejected = "rejected"
    unresolved = "unresolved"
    duplicate = "duplicate"


class RejectionReason(StrEnum):
    procurement_information = "procurement_information"
    price = "price"
    date = "date"
    marketing_only = "marketing_only"
    belongs_to_other_entity = "belongs_to_other_entity"
    generic_statement = "generic_statement"
    navigation_contamination = "navigation_contamination"
    related_content = "related_content"
    out_of_scope_section = "out_of_scope_section"
    placeholder = "placeholder"
    unsupported = "unsupported"
    unsupported_mapping = "unsupported_mapping"
    duplicate = "duplicate"
    not_technical = "not_technical"
    no_owner = "no_owner"
    other = "other"


class OntologyStatus(StrEnum):
    canonical = "canonical"
    candidate = "candidate"
    dynamic = "dynamic"


class EvidenceRef(BaseModel):
    """Pointer into the SemanticDocument. ``quote`` is a verbatim substring of the block text."""

    block_id: str
    quote: str | None = None
    char_start: int | None = None
    char_end: int | None = None
    page: int | None = None  # PDF page of the block (filled in the final result)


class Confidence(BaseModel):
    evidence: float = 0.0
    attribution: float = 0.0
    mapping: float = 0.0
    value: float = 0.0
    overall: float = 0.0

    @classmethod
    def combine(cls, evidence: float, attribution: float, mapping: float, value: float) -> Confidence:
        vals = [max(0.0, min(1.0, v)) for v in (evidence, attribution, mapping, value)]
        return cls(evidence=vals[0], attribution=vals[1], mapping=vals[2], value=vals[3], overall=min(vals))


class Usage(BaseModel):
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    cached_tokens: int = 0
    cost_usd: float = 0.0
    cache_hits: int = 0

    def add(self, other: Usage) -> None:
        for f in ("calls", "prompt_tokens", "completion_tokens", "reasoning_tokens", "cached_tokens", "cache_hits"):
            setattr(self, f, getattr(self, f) + getattr(other, f))
        self.cost_usd = round(self.cost_usd + other.cost_usd, 6)


class StageTiming(BaseModel):
    stage: str
    seconds: float
    usage: Usage = Field(default_factory=Usage)
    note: str | None = None
