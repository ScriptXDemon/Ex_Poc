"""Large-document machinery: table carry-over across page breaks, product map, product-keyed tables, structural
ownership, local registry, vision block insertion, dynamic parameter names, position-aware scoring."""

from types import SimpleNamespace

from defence_extractor.contracts.common import EvidenceRef, Role
from defence_extractor.contracts.document import BlockType, ScopeHint, SemanticDocument
from defence_extractor.contracts.entities import DefenceEntity, KnowledgeGraph
from defence_extractor.contracts.facts import CandidateFact
from defence_extractor.ingest.corpus import DocRef
from defence_extractor.ingest.pdf_adapter import _Builder
from defence_extractor.pipeline.state import DocState


def _doc() -> SemanticDocument:
    b = _Builder("D")
    b.heading("Small arms catalogue", 1, 1, None)
    b.heading("NEGEV NG-7", 2, 2, None)
    b.block("The NEGEV NG-7 is a 7.62 mm light machine gun.", BlockType.paragraph, 2, None)
    b.heading("Technical data", 3, 2, None)
    b.table([[("Model", True), ("Barrel length", True), ("Weight", True)],
             [("NEGEV NG-7", False), ("508 mm", False), ("8.2 kg", False)],
             [("NEGEV 7 ULMG", False), ("420 mm", False), ("6.6 kg", False)]], 2, None)
    b.block("Page 2", BlockType.other, 2, None, scope=ScopeHint.footer)
    # the table continues on the next page without repeating its header
    b.table([[("NEGEV SF", False), ("330 mm", False), ("7.6 kg", False)]], 3, None)
    b.heading("Optics", 2, 4, None)
    b.block("MEPRO 21 reflex sight: day / night", BlockType.paragraph, 4, None)
    return b.build(fmt="pdf", url=None, path="/x.pdf", sha="0" * 64, title="Catalogue", warnings=[], parser="t")


def _state(doc: SemanticDocument, entities: list[DefenceEntity], focal: list[str]) -> DocState:
    st = DocState(run_id="R", ref=DocRef(doc_id="D", path="/x.pdf", format="pdf"))
    st.ingest = SimpleNamespace(doc=doc, inventory=[], profile=SimpleNamespace(language="en"), markers=[])
    st.kg = KnowledgeGraph(entities=entities, focal_entity_ids=focal)
    return st


def _entities() -> list[DefenceEntity]:
    return [DefenceEntity(entity_id="E1", name="NEGEV", role=Role.family),
            DefenceEntity(entity_id="E2", name="NEGEV NG-7", role=Role.variant, parent_entity_id="E1"),
            DefenceEntity(entity_id="E3", name="NEGEV 7 ULMG", role=Role.variant, parent_entity_id="E1"),
            DefenceEntity(entity_id="E4", name="NEGEV SF", role=Role.variant, parent_entity_id="E1"),
            DefenceEntity(entity_id="E5", name="MEPRO 21", role=Role.accessory)]


def test_table_header_carries_over_a_page_break():
    doc = _doc()
    rows = [b for b in doc.blocks if b.type == BlockType.table_row]
    assert rows[-1].text == "NEGEV SF — Barrel length: 330 mm; Weight: 7.6 kg"
    assert rows[-1].attributes.get("continues_table") == "t1"


def test_product_map_sections_and_product_keyed_rows():
    from defence_extractor.agents.segments import build_product_map, local_entities, structural_owner

    doc = _doc()
    st = _state(doc, _entities(), ["E1"])
    build_product_map(SimpleNamespace(), st)
    sec = {s.heading: s.section_id for s in doc.sections}
    assert st.segments[sec["NEGEV NG-7"]] == ["E2"]
    assert st.segments[sec["Technical data"]] == ["E2"]      # deeper heading names no product: the parent heading does
    assert st.segments[sec["Optics"]] == ["E5"]               # product named at the top of the section
    rows = {b.attributes.get("row_label"): b.block_id for b in doc.blocks if b.type == BlockType.table_row}
    assert st.row_owners[rows["NEGEV 7 ULMG"]] == "E3" and st.row_owners[rows["NEGEV SF"]] == "E4"
    f = CandidateFact(fact_id="F1", value_text="6.6 kg", evidence=[EvidenceRef(block_id=rows["NEGEV 7 ULMG"], quote="6.6 kg")])
    assert structural_owner(st, f) == ("E3", "table_row")
    assert local_entities(st, [rows["NEGEV SF"]]) is None     # small registry: shown whole


def test_local_registry_is_capped_for_large_documents():
    from defence_extractor.agents.segments import build_product_map, local_entities

    doc = _doc()
    ents = _entities() + [DefenceEntity(entity_id=f"E{i}", name=f"Widget {i}", role=Role.related_product) for i in range(6, 80)]
    st = _state(doc, ents, ["E1"])
    build_product_map(SimpleNamespace(), st)
    rows = [b.block_id for b in doc.blocks if b.attributes.get("row_label") == "NEGEV SF"]
    keep = local_entities(st, rows)
    assert keep is not None and "E4" in keep and "E1" in keep and len(keep) <= 40
    assert "E70" not in keep


def test_vision_blocks_are_inserted_in_reading_order():
    from defence_extractor.agents.vision import VisionBlockOut, insert_page_blocks

    doc = _doc()
    n_before = len(doc.blocks)
    added = insert_page_blocks(doc, 2, [VisionBlockOut(kind="heading", text="NEGEV NG-7 cut-away"),
                                        VisionBlockOut(kind="label_value", text="Rate of fire: 600 ~ 750 rds/min")])
    assert added == 2 and len(doc.blocks) == n_before + 2
    pages = [b.page for b in doc.blocks if b.page]
    assert pages == sorted(pages)  # page 2 vision blocks sit before page 3 content
    v = [b for b in doc.blocks if b.attributes.get("vision")]
    assert v[0].type == BlockType.heading and v[1].section_id == v[0].section_id
    assert [b.order for b in doc.blocks] == list(range(len(doc.blocks)))
    assert any(s.section_id == v[0].section_id for s in doc.sections)


def test_dynamic_parameter_names_stay_short():
    from defence_extractor.agents.mapping import _dynamic

    f = CandidateFact(fact_id="F1", value_text="3/4", source_parameter="Of MLI's dogs have been sponsored by caring American citizens")
    ref = _dynamic(f)
    assert len(ref.id.split("_")) <= 6 and "long_parameter_name" in f.anomalies
    g = CandidateFact(fact_id="F2", value_text="10 km", source_parameter="Link range", parameter_hint="data_link_range_los")
    assert _dynamic(g).id == "data_link_range_los"


def test_position_breakdown_in_scoring(tmp_path):
    from defence_extractor.evaluation.scorer import load_gold, score_doc

    gp = tmp_path / "G.yaml"
    gp.write_text("doc_id: G\npartial: true\nproducts:\n  P1: {name: NEGEV SF, pos: end, page: 3}\n"
                  "facts:\n  - {p: P1, param: weight, v: \"7.6 kg\", nums: [7.6]}\n  - {p: P1, param: barrel_length, v: \"330 mm\", nums: [330]}\n")
    gold = load_gold(gp)
    res = {"products": [{"entity_id": "E4", "name": "NEGEV SF", "aliases": [], "specifications": [
        {"value_text": "7.6 kg", "parameter": {"id": "weight", "status": "canonical"}, "verification": "verified"}]},
        {"entity_id": "E9", "name": "Other", "aliases": [], "specifications": [
            {"value_text": "1 m", "parameter": {"id": "length", "status": "canonical"}, "verification": "verified"}]}],
        "entities": [], "unattributed": []}
    sd = score_doc(gold, res)
    assert sd["by_pos"]["end"] == {"n": 2, "matched": 1, "misattributed": 0, "unresolved": 0}
    assert sd["other_product_facts"] == 0  # partial key: products outside the sample are not judged


def test_value_glued_to_its_condition_is_trimmed_to_the_verbatim_measurement():
    from defence_extractor.agents.discovery import _verbatim_head

    block = "1,009 km (545 nm)* / 913 km (493 nm)** with Standard self-sealing fuel tank - no reserve"
    head, rest = _verbatim_head("1,009 km (545 nm) with Standard self-sealing fuel tank - no reserve", [block])
    assert head == "1,009 km (545 nm)" and rest.startswith("with Standard")
    assert _verbatim_head("5 h 05 min with Standard tanks", ["5 h 05 min*/4 h 28 min** with Standard tanks"])[0] == "5 h 05 min"
    assert _verbatim_head("12 kg", ["Weight: 12"]) == (None, "")          # a bare number is not a measurement
    assert _verbatim_head("operates day and night", ["operates day or night"]) == (None, "")  # text is never cut


def test_unseen_part_subject_becomes_a_component_of_its_product():
    from defence_extractor.agents.discovery import _ensure_entity

    doc = _doc()
    st = _state(doc, [DefenceEntity(entity_id="E1", name="CHARGER 2.2KW 120VDC", role=Role.related_product)], [])
    st.segments = {}
    blk = [b.block_id for b in doc.blocks if b.type == BlockType.table_row][:1]
    part = st.kg.get(_ensure_entity(st, "Integrated DC/DC Converter", blk))
    assert part.role == Role.component and part.parent_entity_id == "E1"
    other = st.kg.get(_ensure_entity(st, "XK-200 Charger", blk))  # a model number: a product of its own
    assert other.role == Role.related_product and other.parent_entity_id is None


def test_table_unit_from_label_and_odd_hyphens():
    from defence_extractor.agents.base import norm_text
    from defence_extractor.agents.discovery import _unit_from_context

    assert _unit_from_context("259 mm", ["Depth — EU: 250; IMU: 259; Units: mm"]) == "259"
    assert _unit_from_context("≥23 %", ["Elongation — Temper: %; 1/4H: ≥23; 1/2H: ≥18"]) == "≥23"
    assert _unit_from_context("32 in", ["Trajectory [in] — 100 m: 32"]) == "32"
    assert _unit_from_context("32 in", ["fits in 32 slots"]) is None            # "in" as a word is not a unit label
    assert _unit_from_context("12 km", ["Range: 12 nm at 30 km/h"]) is None     # "km" only inside "km/h"
    assert _unit_from_context("Class 7", ["Class 7"]) is None
    assert norm_text("DeuGen‑N (FE36) lock‐up") == norm_text("DeuGen-N (FE36) lock-up")


def test_a4_type_fallback_table_covers_product_types():
    from defence_extractor.agents.understanding import _TYPE_ROLE
    from defence_extractor.contracts.common import PRODUCT_LIKE_ROLES, EntityType

    for t in (EntityType.subsystem, EntityType.sensor, EntityType.weapon, EntityType.platform, EntityType.product):
        assert _TYPE_ROLE[t] in PRODUCT_LIKE_ROLES


def test_continuation_stops_at_an_unknown_model_heading():
    from defence_extractor.agents.segments import build_product_map

    b = _Builder("D")
    b.heading("C2720 Brass", 1, 50, None)
    b.block("C2720 is a brass for deep drawing.", BlockType.paragraph, 50, None)
    b.heading("Features", 2, 50, None)
    b.block("Good formability.", BlockType.paragraph, 50, None)
    b.heading("C7451 Nickel Silver (NS2)", 1, 51, None)  # the registry missed this alloy
    b.block("Nickel silver for shielding.", BlockType.paragraph, 51, None)
    b.heading("Typical Applications", 2, 51, None)
    b.block("Coins", BlockType.list_item, 51, None)
    doc = b.build(fmt="pdf", url=None, path="/x.pdf", sha="0" * 64, title="Alloys", warnings=[], parser="t")
    st = _state(doc, [DefenceEntity(entity_id="E1", name="C2720", role=Role.related_product, specific_model=True),
                      DefenceEntity(entity_id="E2", name="C2100", role=Role.related_product, specific_model=True)], [])
    build_product_map(SimpleNamespace(), st)
    sec = {s.heading: s.section_id for s in doc.sections}
    assert st.segments[sec["C2720 Brass"]] == ["E1"] and st.segments[sec["Features"]] == ["E1"]
    assert sec["C7451 Nickel Silver (NS2)"] not in st.segments and sec["Typical Applications"] not in st.segments
