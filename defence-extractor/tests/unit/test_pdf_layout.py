"""PDF layout repairs (broken words, dot leaders, contents, spec cards, text quality), per-page OCR runs, catalogue
entities from model designations, figure targets and page-subset scoring."""

import json
from types import SimpleNamespace

from defence_extractor.contracts.document import BlockType, ScopeHint
from defence_extractor.ingest.pdf_adapter import PdfAdapter, _Builder
from defence_extractor.ingest.pdf_layout import (
    dehyphenate,
    join_spec_cards,
    mark_contents_and_leaders,
    text_quality,
    vocabulary,
)


def test_broken_words_are_rejoined_but_suspended_hyphens_and_compounds_kept():
    vocab = vocabulary(["a cost study", "effective range", "ground based"])
    assert dehyphenate("Ballistic coeffi- cient", vocab) == "Ballistic coefficient"
    assert dehyphenate("Frac- tion of", vocab) == "Fraction of"
    assert dehyphenate("space- and ground-based", vocab) == "space- and ground-based"
    assert dehyphenate("a cost- effective design", vocab) == "a cost-effective design"


def test_text_quality_separates_text_from_glyph_garbage():
    assert text_quality("The vehicle weighs 12,500 kg and carries a crew of three.") > 0.9
    assert text_quality("(cid:12)(cid:45)(cid:77) (cid:3)(cid:90) ��") < 0.3
    assert text_quality("最大射程 120 公里") > 0.6  # non-Latin text is not garbage


def _blocks(lines, page=1):
    b = _Builder("D")
    b.heading("Card", 2, page, None)
    for t in lines:
        b.block(t, BlockType.paragraph, page, None)
    return b.blocks


def test_contents_run_is_navigation_and_dot_leader_specs_become_fields():
    blocks = _blocks(["STAR 30C/BP........................91", "STAR 30E........................93",
                      "STAR 37FM........................95", "STAR 48BV........................99",
                      "Motor diameter, in................................32.5", "Crew ........ 3"])
    mark_contents_and_leaders(blocks)
    assert [b.scope for b in blocks[1:5]] == [ScopeHint.navigation] * 4
    assert blocks[5].text == "Motor diameter, in: 32.5" and blocks[5].type == BlockType.key_value
    assert blocks[6].text == "Crew: 3"


def test_spec_card_boxes_are_joined_into_fields():
    blocks = _blocks(["Muzzle velocity", "m / sec", "910", "fps", "2985", "Ballistic coefficient", "G1", "0.230", "G7", "-",
                      "168 GEE *", "184 PBR *"])
    out = join_spec_cards(blocks)
    texts = [b.text for b in out]
    assert "Muzzle velocity: 910 m / sec; 2985 fps" in texts
    assert "Ballistic coefficient: 0.230 G1; - G7" in texts
    assert "168 GEE *" in texts and "184 PBR *" in texts
    assert [b.order for b in out] == list(range(len(out)))


def test_ordinary_short_lines_are_not_joined():
    blocks = _blocks(["Key features", "Rugged design", "Night vision", "Low weight"])
    assert [b.text for b in join_spec_cards(blocks)] == ["Card", "Key features", "Rugged design", "Night vision", "Low weight"]


def test_ocr_runs_follow_pages_and_page_subsets():
    a = PdfAdapter(window_pages=40, pages={4, 5, 6, 7, 51})
    assert a._windows(60, {6}) == [(4, 5, False), (6, 6, True), (7, 7, False), (51, 51, False)]
    assert PdfAdapter(window_pages=40)._windows(45, set()) == [(1, 40, False), (41, 45, False)]


def test_catalogue_models_missing_from_the_entity_list_become_entities():
    from defence_extractor.agents.understanding import _designation_candidates

    b = _Builder("D")
    for h in ("C2720 Brass", "Features", "C7451 Nickel Silver (NS2)", "UNS No. C74500", "120mm Series"):
        b.heading(h, 2, 1, None)
        b.block("text", BlockType.paragraph, 1, None)
    doc = b.build(fmt="pdf", url=None, path="/x.pdf", sha="0" * 64, title="t", warnings=[], parser="t")
    merged = [{"name": "C2720", "aliases": [], "types": [], "specific": True, "blocks": [], "keys": {"c2720"}}]
    cands = _designation_candidates(doc, merged)
    assert [c["name"] for c in cands] == ["C7451 Nickel Silver (NS2)"]
    assert set(cands[0]["aliases"]) == {"C7451", "NS2"}


def test_figure_with_a_caption_is_a_target_anchored_on_its_caption():
    from defence_extractor.agents.vision import figure_targets

    b = _Builder("D")
    b.heading("M322", 2, 3, (50, 800, 300, 780))
    b.block("Muzzle velocity: 1,705 m/s", BlockType.key_value, 3, (50, 770, 300, 760))
    b.block("Figure 2: M322 dimensions", BlockType.caption, 3, (60, 395, 400, 385))
    doc = b.build(fmt="pdf", url=None, path="/x.pdf", sha="0" * 64, title="t", warnings=[], parser="t")
    doc.metadata["page_stats"] = {"3": {"chars": 900, "img": 0.3, "w": 595, "h": 842, "imgs": [[60, 400, 500, 700]]}}
    tg = figure_targets(doc, [], set(), 0.06, 10)
    assert len(tg) == 1 and tg[0]["caption"] == "Figure 2: M322 dimensions"
    assert tg[0]["anchor"] == next(x.block_id for x in doc.blocks if x.type == BlockType.caption)


def test_page_subset_limits_the_answer_key(tmp_path):
    from defence_extractor.evaluation.scorer import score_run

    gold_dir, run_dir = tmp_path / "gold", tmp_path / "run"
    gold_dir.mkdir()
    (gold_dir / "G.yaml").write_text("doc_id: G\npartial: true\nproducts:\n  P1: {name: M322, page: 4}\n  P2: {name: M338, page: 9}\n"
                                     "facts:\n  - {p: P1, param: weight, v: '20 kg', nums: [20]}\n  - {p: P2, param: weight, v: '21 kg', nums: [21]}\n")
    res = {"products": [{"entity_id": "E1", "name": "M322", "aliases": [], "specifications": [
        {"value_text": "20 kg", "parameter": {"id": "weight", "status": "canonical"}, "verification": "verified", "evidence": []}]}],
        "entities": [], "unattributed": [], "execution": {"usage": {"cost_usd": 0}}, "ledger_summary": {"open": 0}}
    (run_dir / "docs" / "G").mkdir(parents=True)
    (run_dir / "docs" / "G" / "result.json").write_text(json.dumps(res))
    sc = score_run(run_dir, [gold_dir], None, page_subsets={"G": {4, 5}})
    assert sc["core"]["n"] == 1 and sc["core"]["matched"] == 1
    _ = SimpleNamespace  # keep import for readability of fixtures above


def test_figures_need_a_technical_caption_or_printed_numbers():
    from defence_extractor.agents.vision import figure_worth_reading

    assert figure_worth_reading({"caption": "Figure 3: M322 dimensions"}, None)
    assert figure_worth_reading({"caption": "Source: Krauss-Maffei Wegmann"}, 7)  # a drawing that prints its numbers
    assert not figure_worth_reading({"caption": "Source: Krauss-Maffei Wegmann"}, 1)  # a photo
    assert not figure_worth_reading({"caption": "", "technical": True}, 0)  # a logo on a spec page
    assert figure_worth_reading({"caption": "", "technical": True}, None)  # no OCR: the page rule decides


def test_the_same_image_drawn_twice_is_one_figure():
    from defence_extractor.agents.vision import figure_targets

    b = _Builder("D")
    b.heading("M322", 2, 3, (50, 800, 300, 780))
    b.block("Figure 2: M322 dimensions", BlockType.caption, 3, (60, 395, 400, 385))
    doc = b.build(fmt="pdf", url=None, path="/x.pdf", sha="0" * 64, title="t", warnings=[], parser="t")
    doc.metadata["page_stats"] = {"3": {"chars": 900, "img": 0.3, "w": 595, "h": 842,
                                        "imgs": [[60, 400, 500, 700], [61, 401, 500, 700]]}}
    assert len(figure_targets(doc, [], set(), 0.06, 10)) == 1


def test_an_image_page_read_by_ocr_still_goes_to_vision_and_its_ocr_text_steps_aside():
    from defence_extractor.agents.vision import hide_ocr_text, vision_pages

    b = _Builder("D")
    b.heading("IWI PRODUCT LIST", 2, 24, None)
    b.block('M90RP17: 9mm, 4.1" Barrel ' * 30, BlockType.paragraph, 24, None)  # long OCR text
    b.block("Plain text page " * 40, BlockType.paragraph, 25, None)
    doc = b.build(fmt="pdf", url=None, path="/x.pdf", sha="0" * 64, title="t", warnings=[], parser="t")
    doc.metadata["page_stats"] = {"24": {"chars": 0, "img": 1.0}, "25": {"chars": 700, "img": 0.0}}
    doc.metadata["ocr_pages"] = [24]
    assert vision_pages(doc, 0.35, 400, 40) == [24]
    assert hide_ocr_text(doc, 24) == 2
    assert all(x.attributes.get("dup_of") == "vision" for x in doc.blocks if x.page == 24)


def test_a_page_sized_image_is_not_a_figure():
    from defence_extractor.agents.vision import figure_targets

    b = _Builder("D")
    b.heading("IWI PRODUCT LIST", 2, 24, (50, 800, 300, 780))
    b.block("M9ORP17: 9mm", BlockType.paragraph, 24, (50, 700, 300, 690))
    doc = b.build(fmt="pdf", url=None, path="/x.pdf", sha="0" * 64, title="t", warnings=[], parser="t")
    doc.metadata["page_stats"] = {"24": {"chars": 900, "img": 1.0, "w": 595, "h": 842, "imgs": [[0, 0, 595, 842]]}}
    assert figure_targets(doc, [], set(), 0.06, 10) == []


def test_a_name_joining_two_models_becomes_one_entity_per_model():
    from defence_extractor.agents.understanding import _split_combined

    merged = [{"name": "SHARKY 774", "aliases": [], "types": [], "specific": True, "blocks": ["b1"], "keys": {"sharky 774"}},
              {"name": "SHARKY 774 / SHARKY 775", "aliases": ["SHARKY 775"], "types": [], "specific": True, "blocks": ["b9"],
               "keys": {"sharky 774 / sharky 775", "sharky 775"}},
              {"name": "AN/PRC-152A", "aliases": [], "types": [], "specific": True, "blocks": [], "keys": {"an/prc-152a"}}]
    out = _split_combined(merged)
    assert sorted(m["name"] for m in out) == ["AN/PRC-152A", "SHARKY 774", "SHARKY 775"]
    assert next(m for m in out if m["name"] == "SHARKY 774")["blocks"] == ["b1", "b9"]


def test_model_names_with_a_separate_number_side_by_side_are_products():
    from defence_extractor.agents.understanding import _designation_candidates

    b = _Builder("D")
    b.heading("SHARKY series", 2, 1, None)
    b.table([[("Feature", True), ("SHARKY 774", True), ("SHARKY 775", True)],
             [("Approval", False), ("MID (EN 1434)", False), ("MID (EN 1434)", False)],
             [("Sizes", False), ("DN 15 - DN 20", False), ("DN 15 - DN 100", False)]], 1, None)
    b.heading("Section 2", 2, 2, None)
    b.heading("Section 3", 2, 3, None)
    doc = b.build(fmt="pdf", url=None, path="/x.pdf", sha="0" * 64, title="t", warnings=[], parser="t")
    known = [{"name": "SHARKY 774", "aliases": [], "types": [], "specific": True, "blocks": [], "keys": {"sharky 774"}}]
    assert [c["name"] for c in _designation_candidates(doc, known)] == ["SHARKY 775"]  # "Section 2/3" are not models


def test_document_kind_comes_from_the_page_map_and_the_scan_only_fills_gaps():
    from defence_extractor.pipeline.provenance import document_kind

    assert document_kind("brochure", ["manual_language"])[0] == "product_sheet"  # a brochure that mentions maintenance
    assert document_kind("other", ["tender_language"])[0] == "tender"
    assert document_kind(None, [])[0] == "other"
