"""A13 final assembler + evidence-ledger closure."""

from __future__ import annotations

from typing import Any

from .. import PIPELINE_VERSION
from ..contracts.common import (
    PRODUCT_LIKE_ROLES,
    SPEC_CLASSES,
    EntityType,
    FactClass,
    LedgerStatus,
    Predicate,
    Role,
    Usage,
    VerificationStatus,
)
from ..contracts.facts import CandidateFact, SpecRecord, TextFactRecord
from ..contracts.ledger import Ledger, LedgerItem
from ..contracts.page import EXTRACTABLE_ROLES, NONMAIN_EXTRACTABLE_ROLES
from ..contracts.result import Coverage, ExecutionManifest, FinalDocumentResult, ProductRecord
from ..pipeline.state import DocState
from .base import Runtime, norm_text

_PRODUCTISH_TYPES = {EntityType.product, EntityType.product_family, EntityType.variant, EntityType.subsystem,
                     EntityType.component, EntityType.weapon, EntityType.ammunition, EntityType.sensor, EntityType.engine,
                     EntityType.platform, EntityType.technology}

ACCEPTED = (VerificationStatus.verified, VerificationStatus.verified_low_confidence)
_TEXT_BUCKET = {
    FactClass.capability: "capabilities",
    FactClass.feature: "features",
    FactClass.other: "features",
    FactClass.technology: "technologies",
    FactClass.component: "components",
    FactClass.compatibility: "compatibility",
    FactClass.mission: "missions",
    FactClass.target: "targets",
}


def _fact_ledger_status(f: CandidateFact) -> tuple[LedgerStatus, str | None]:
    if f.verification in ACCEPTED:
        return LedgerStatus.extracted, None
    if f.verification == VerificationStatus.unresolved:
        return LedgerStatus.unresolved, f.rejection_reason or "unresolved"
    if f.rejection_reason == "split_into_parts":
        return LedgerStatus.duplicate, "split_into_parts"
    return LedgerStatus.rejected, f.rejection_reason or "unsupported"


def build_ledger(st: DocState) -> Ledger:
    facts = st.facts
    by_item: dict[str, list[CandidateFact]] = {}
    for f in facts:
        for i in f.inventory_item_ids:
            by_item.setdefault(i, []).append(f)
    roles = {s.section_id: s.role for s in st.page_map.sections} if st.page_map else {}
    bmap = st.ingest.doc.block_map()
    skipped = set(st.skipped_block_ids)
    items: list[LedgerItem] = []
    for it in st.ingest.inventory:
        linked = [f for f in by_item.get(it.item_id, []) if f.rejection_reason != "split_into_parts"] or by_item.get(it.item_id, [])
        li = LedgerItem(item_id=it.item_id, origin=f"inventory:{it.kind.value}", block_id=it.block_id, section_id=it.section_id,
                        text=it.text, char_start=it.char_start, char_end=it.char_end, fact_ids=[f.fact_id for f in linked])
        if linked:
            sts = [_fact_ledger_status(f) for f in linked]
            if any(s == LedgerStatus.extracted for s, _ in sts):
                li.status = LedgerStatus.extracted
            elif any(s == LedgerStatus.unresolved for s, _ in sts):
                li.status, li.reason = LedgerStatus.unresolved, next(r for s, r in sts if s == LedgerStatus.unresolved)
            else:
                li.status, li.reason = sts[0]
            li.reviewed_by = sorted({a for f in linked for a in f.agents})
        elif it.dismissed_reason:
            li.status, li.reason, li.technical_candidate = LedgerStatus.rejected, it.dismissed_reason, False
            li.reviewed_by = ["A1-rules"]
        elif it.item_id in st.item_dismissals:
            li.status, li.reason, li.technical_candidate = LedgerStatus.rejected, st.item_dismissals[it.item_id], False
            li.reviewed_by = ["A10"]
        elif (getattr(bmap.get(it.block_id), "scope", None) is not None and bmap[it.block_id].scope.value != "main"
              and (roles.get(it.section_id) is None or roles[it.section_id] not in NONMAIN_EXTRACTABLE_ROLES)):
            # same rule as extractable_blocks: non-main blocks are read only on a positive page-map judgement
            role = roles.get(it.section_id)
            li.status = LedgerStatus.rejected
            li.reason = ("related_content" if (role is not None and role.value == "related_products")
                         or bmap[it.block_id].scope.value == "related_content" else "out_of_scope_section")
            li.reviewed_by = ["A2"]
        elif it.block_id in skipped:
            li.status, li.reason = LedgerStatus.unresolved, "not_processed_document_budget"
            li.reviewed_by = ["ledger-closure"]
        else:
            li.status, li.reason = LedgerStatus.unresolved, "unexplained_inventory_item"
            li.reviewed_by = ["ledger-closure"]
        items.append(li)
    covered = {i for f in facts for i in f.inventory_item_ids}
    n = 0
    for f in facts:
        if f.inventory_item_ids and set(f.inventory_item_ids) & covered:
            continue
        n += 1
        status, reason = _fact_ledger_status(f)
        ev = f.evidence[0] if f.evidence else None
        items.append(LedgerItem(item_id=f"D{n:04d}", origin=f"discovery:{f.method}", block_id=ev.block_id if ev else "",
                                section_id=f.section_id, text=f.value_text, char_start=ev.char_start if ev else None,
                                char_end=ev.char_end if ev else None, status=status, reason=reason, fact_ids=[f.fact_id],
                                technical_candidate=f.fact_class not in (FactClass.marketing_claim, FactClass.procurement_information),
                                reviewed_by=sorted(set(f.agents))))
    return Ledger(items=items)


def _spec(f: CandidateFact, n: int) -> SpecRecord:
    return SpecRecord(spec_id=f"S{n:04d}", parameter=f.parameter, fact_class=f.fact_class, value_text=f.value_text, value=f.value,
                      applies_when=f.applicability, evidence=f.evidence, confidence=f.confidence, verification=f.verification,
                      notes="; ".join([a for a in f.anomalies if not a.startswith("dimension_mismatch")] +
                                      ([f.verification_note] if f.verification_note else [])) or None)


def _text(f: CandidateFact) -> TextFactRecord:
    return TextFactRecord(fact_id=f.fact_id, text=f.value_text, fact_class=f.fact_class, source_parameter=f.source_parameter,
                          applies_when=f.applicability, evidence=f.evidence, confidence=f.confidence.overall,
                          verification=f.verification)


def _merge_duplicates(facts: list[CandidateFact]) -> list[CandidateFact]:
    """One record per (parameter, value, applicability) within a product: the same value stated on several pages
    keeps all its evidence."""
    out: list[CandidateFact] = []
    seen: dict[tuple, CandidateFact] = {}
    for f in sorted(facts, key=lambda x: (-x.confidence.overall, x.fact_id)):
        pid = f.parameter.id if f.parameter else (f.parameter_hint or f.source_parameter or "")
        key = (f.fact_class in SPEC_CLASSES, pid, norm_text(f.value_text), f.applicability.key())
        if key in seen:
            keep = seen[key]
            for e in f.evidence:
                if e.block_id not in {x.block_id for x in keep.evidence}:
                    keep.evidence.append(e)
            continue
        seen[key] = f
        out.append(f)
    return sorted(out, key=lambda x: x.fact_id)


def _conflicts(rt: Runtime, pr: ProductRecord) -> list[dict[str, Any]]:
    """Same parameter, same conditions, different values (single-valued numeric parameters): listed, never silently
    resolved — e.g. a fuel capacity of 212,000 lb in the text and 212,299 lb in the specification table."""
    groups: dict[tuple, list[SpecRecord]] = {}
    for sp in pr.specifications:
        p = rt.onto.get(sp.parameter.id)
        if p is None or p.multi or p.type != "number":
            continue
        groups.setdefault((sp.parameter.id, sp.applies_when.key()), []).append(sp)
    out = []
    for (pid, _), sps in groups.items():
        vals = {norm_text(x.value_text) for x in sps}
        if len(vals) > 1:
            out.append({"parameter": pid, "values": [{"spec_id": x.spec_id, "value_text": x.value_text,
                                                      "pages": sorted({e.page for e in x.evidence if e.page})} for x in sps]})
            for x in sps:
                x.notes = "; ".join(filter(None, [x.notes, "conflicting values stated for this parameter"]))
    return out


def a13_assemble(rt: Runtime, st: DocState, usage: Usage, providers: dict[str, int], duration_s: float) -> FinalDocumentResult:
    doc, kg = st.ingest.doc, st.kg
    emap = kg.entity_map()
    bmap = doc.block_map()
    for f in st.facts:  # page provenance on every evidence pointer (PDFs)
        for e in f.evidence:
            b = bmap.get(e.block_id)
            if b is not None and b.page:
                e.page = b.page
    owned: dict[str, list[CandidateFact]] = {}
    unresolved_by_owner: dict[str, list[CandidateFact]] = {}
    unattributed = []
    for f in st.facts:
        if f.rejection_reason == "split_into_parts":
            continue
        if f.verification in ACCEPTED and f.owner_entity_id in emap:
            owned.setdefault(f.owner_entity_id, []).append(f)
        elif f.verification == VerificationStatus.unresolved:
            if f.owner_entity_id in emap:
                unresolved_by_owner.setdefault(f.owner_entity_id, []).append(f)
            else:
                unattributed.append(f)
    manufacturer_of: dict[str, str] = {}
    for r in kg.relations:
        if r.predicate == Predicate.manufactures and r.object_entity_id:
            manufacturer_of[r.object_entity_id] = r.subject_entity_id
    products: list[ProductRecord] = []
    n_spec = 0
    for e in kg.entities:
        if e.role not in PRODUCT_LIKE_ROLES and e.entity_id not in kg.focal_entity_ids and not (
                e.role == Role.other and e.entity_type in _PRODUCTISH_TYPES and owned.get(e.entity_id)):
            continue  # (a product-type entity left without a role keeps its verified values instead of losing them)
        facts = owned.get(e.entity_id, [])
        if not facts and e.entity_id not in kg.focal_entity_ids and not unresolved_by_owner.get(e.entity_id):
            continue
        pr = ProductRecord(entity_id=e.entity_id, name=e.name, aliases=e.aliases, entity_type=e.entity_type, role=e.role,
                           parent_entity_id=e.parent_entity_id, classification=e.product_classes,
                           relations=[r for r in kg.relations if r.subject_entity_id == e.entity_id])
        mid = manufacturer_of.get(e.entity_id)
        if mid and mid in emap:
            pr.manufacturer, pr.manufacturer_entity_id = emap[mid].name, mid
        for f in _merge_duplicates(facts):
            if f.fact_class in SPEC_CLASSES:
                n_spec += 1
                pr.specifications.append(_spec(f, n_spec))
            else:
                bucket = _TEXT_BUCKET.get(f.fact_class, "features")
                getattr(pr, bucket).append(_text(f))
        for f in unresolved_by_owner.get(e.entity_id, []):
            pr.unresolved.append(_unres(f))
        pr.conflicts = _conflicts(rt, pr)
        products.append(pr)
    by_id = {pr.entity_id: pr for pr in products}
    for pr in products:  # roll a part's specifications up into its parent product's sheet
        parent = by_id.get(pr.parent_entity_id or "")
        if parent is not None and parent is not pr and pr.specifications:
            parent.subsystems.append({"entity_id": pr.entity_id, "name": pr.name, "role": pr.role.value,
                                      "specifications": [{"spec_id": x.spec_id, "parameter": x.parameter.id,
                                                          "label": x.parameter.source_label, "value_text": x.value_text}
                                                         for x in pr.specifications]})
    ledger = build_ledger(st)
    summary = ledger.summary()
    inv_ids = {it.item_id for it in st.ingest.inventory}
    cov = Coverage(ledger_items=summary.total, extracted=summary.extracted, rejected=summary.rejected,
                   unresolved=summary.unresolved, duplicate=summary.duplicate, open=summary.open,
                   inventory_items=len(inv_ids),
                   inventory_found_by_discovery=len(set(st.discovered_inventory_ids) & inv_ids),
                   recovered_by_audit=sum(1 for f in st.facts if f.method == "coverage_recovery" and f.verification in ACCEPTED))
    execution = ExecutionManifest(
        pipeline_version=PIPELINE_VERSION, ontology_version=rt.onto.version, parser_version=doc.parser_version,
        run_id=st.run_id, models={"main": rt.settings.main_model, "verifier": rt.settings.verifier_model},
        providers_used=providers, usage=usage, stages=st.timings, duration_s=round(duration_s, 2),
        status="completed" if not st.errors else "completed_with_errors", errors=st.errors[:50])
    return FinalDocumentResult(
        document={"doc_id": doc.doc_id, "url": doc.source_url, "path": st.ref.path, "format": doc.format, "title": doc.title,
                  "language": st.ingest.profile.language, "content_sha256": doc.content_sha256,
                  "page_type": st.page_map.page_type if st.page_map else None,
                  "page_scope": st.page_map.page_scope if st.page_map else None, "level": st.ref.level,
                  "profile_flags": st.ingest.profile.flags, "parse_warnings": doc.parse_warnings},
        companies=[e for e in kg.entities if e.entity_type == EntityType.company],
        entities=kg.entities, products=products, relations=kg.relations,
        unattributed=[_unres(f) for f in unattributed], coverage=cov, ledger_summary=summary, ledger=ledger.items,
        execution=execution)


def _unres(f: CandidateFact) -> dict[str, Any]:
    return {"fact_id": f.fact_id, "label": f.source_parameter, "parameter": f.parameter.id if f.parameter else f.parameter_hint,
            "value_text": f.value_text, "fact_class": f.fact_class.value, "owner_entity_id": f.owner_entity_id,
            "alternative_owner_ids": f.alternative_owner_ids, "reason": f.rejection_reason or f.verification_note,
            "evidence": [e.model_dump() for e in f.evidence]}
