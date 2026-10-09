"""Typed contracts shared by every stage. Pydantic models are the single source of truth for JSON Schemas."""

from .common import (
    NON_PRODUCT_CLASSES,
    PRODUCT_LIKE_ROLES,
    SPEC_CLASSES,
    Confidence,
    EntityType,
    EvidenceRef,
    FactClass,
    LedgerStatus,
    OntologyStatus,
    Predicate,
    RejectionReason,
    Role,
    StageTiming,
    Usage,
    VerificationStatus,
)
from .document import (
    NON_CONTENT_SCOPES,
    BlockType,
    ScopeHint,
    Section,
    SemanticBlock,
    SemanticDocument,
    SemanticTable,
    SourceProfile,
    TableCell,
)
from .entities import DefenceEntity, KnowledgeGraph, MarkerBinding, ProductClass, ReferenceLink, RelationEdge
from .facts import (
    Applicability,
    CandidateFact,
    Condition,
    ParameterRef,
    SpecRecord,
    StructuredValue,
    TextFactRecord,
    ValuePart,
)
from .inventory import Comparator, InventoryItem, InventoryKind, Measurement, Quantity
from .ledger import Ledger, LedgerItem, LedgerSummary
from .page import EXTRACTABLE_ROLES, PageMap, SectionAssessment, SectionRole
from .result import Coverage, ExecutionManifest, FinalDocumentResult, ProductRecord

__all__ = [name for name in dir() if not name.startswith("_")]
