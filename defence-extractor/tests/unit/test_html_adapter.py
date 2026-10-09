from defence_extractor.contracts.document import BlockType, ScopeHint
from defence_extractor.ingest.html_adapter import HtmlAdapter
from defence_extractor.ingest.inventory import build_inventory
from defence_extractor.ingest.markers import find_marker_bindings

PAGE = """<html lang="en"><head><title>Tarn 8x8</title>
<script type="application/ld+json">{"@type":"Product","name":"Tarn 8x8","weight":"14 t"}</script></head>
<body class="et_header_style_left et_primary_nav_dropdown">
<nav><ul><li><a href="/a">Products</a></li><li><a href="/b">News</a></li><li><a href="/c">Careers</a></li></ul></nav>
<div class="wrapper_menu">
  <h1>Tarn 8x8</h1>
  <p>The Tarn 8x8 is an armoured vehicle with a crew of 3 and room for 8 troops.</p>
  <h2>Specifications</h2>
  <table><tr><th>Parameter</th><th>Value</th></tr>
    <tr><td>Length</td><td>7.2 m</td></tr><tr><td>Max speed</td><td>up to 100 km/h</td></tr></table>
  <table><tr><th></th><th>Variant A</th><th>Variant B</th></tr><tr><td>Range</td><td>600 km</td><td>800 km</td></tr></table>
  <div class="spec"><div class="label">Weight</div><div class="value">14 t</div></div>
  <dl><dt>Engine</dt><dd>Cummins 6BT (160 hp)</dd></dl>
  <p>HOGE: 2,893 m (9,490 ft)* / 2,712 m (8,900 ft)**</p>
  <p>*General Electric CT7-2E1 / **Safran Aneto-1K</p>
  <div class="stat"><span class="counter" data-count="7">0</span> <span>km</span></div>
</div>
<footer><p>© 2026 Example Ltd</p></footer></body></html>"""


def parse():
    return HtmlAdapter().parse(PAGE.encode(), "T1", "https://example.com/tarn")


def texts(doc, **kw):
    return [b.text for b in doc.blocks if all(getattr(b, k) == v for k, v in kw.items())]


def test_scope_and_structure():
    doc = parse()
    assert doc.title == "Tarn 8x8"
    main = texts(doc, scope=ScopeHint.main)
    assert any("crew of 3" in t for t in main), "content inside a menu-classed wrapper must stay main"
    assert any(b.scope == ScopeHint.navigation and b.text == "Products" for b in doc.blocks)
    assert any(b.scope == ScopeHint.footer for b in doc.blocks)
    assert any(b.source == "jsonld" and "weight: 14 t" in b.text for b in doc.blocks)
    assert [s.heading for s in doc.sections if s.heading] == ["Tarn 8x8", "Specifications"]


def test_tables_kv_counter():
    doc = parse()
    rows = texts(doc, type=BlockType.table_row)
    assert "Length: 7.2 m" in rows and "Max speed: up to 100 km/h" in rows
    assert any(t.startswith("Range — Variant A: 600 km; Variant B: 800 km") for t in rows)
    kv = texts(doc, type=BlockType.key_value)
    assert "Weight: 14 t" in kv and "Engine: Cummins 6BT (160 hp)" in kv
    counter = [b for b in doc.blocks if b.attributes.get("counter_attr")]
    assert counter and counter[0].text.startswith("7")


def test_inventory_and_markers():
    doc = parse()
    inv = build_inventory(doc, "en")
    vals = {i.text for i in inv if not i.dismissed_reason}
    assert "7.2 m" in vals and "600 km" in vals and "800 km" in vals and "14 t" in vals
    marks = {m.marker: m.legend_text for m in find_marker_bindings(doc)}
    assert marks.get("*", "").startswith("General Electric CT7-2E1") and marks.get("**", "").startswith("Safran Aneto-1K")


def test_utf8_page_with_late_meta_charset_is_not_mojibake():
    style = "<style>" + ".x{color:red}" * 400 + "</style>"
    page = f"<html><head>{style}<meta charset='utf-8'><title>T</title></head><body><main>" \
           "<p>Operating range: −20 °C to 55 °C, it’s 7.62 × 51 mm – rated ±0.5 m</p></main></body></html>"
    doc = HtmlAdapter().parse(page.encode("utf-8"), "T2")
    body = " ".join(b.text for b in doc.blocks)
    assert "−20 °C to 55 °C" in body and "it’s" in body and "7.62 × 51 mm" in body and "±0.5" in body
    assert "Â" not in body and "â" not in body


def test_declared_legacy_charset_and_xml_declaration():
    page = "<?xml version='1.0' encoding='windows-1252'?><html><head><meta charset='windows-1252'></head>" \
           "<body><p>Température 50 °C</p></body></html>"
    doc = HtmlAdapter().parse(page.encode("cp1252"), "T3")
    assert any("Température 50 °C" in b.text for b in doc.blocks)


def test_wordpress_json_viewer_page_is_unwrapped():
    import html as _h
    import json as _j
    post = {"id": 1, "title": {"rendered": "RAFAEL at Farnborough"}, "slug": "x",
            "content": {"rendered": "<p>ICE BREAKER delivers precision strike at ranges up to 300 kilometers.</p>"},
            "yoast_head": "<meta property='og:title' content='junk'>"}
    page = "<html><head><meta charset='utf-8'></head><body><pre>" + _h.escape(_j.dumps(post)) + "</pre></body></html>"
    doc = HtmlAdapter().parse(page.encode(), "T4")
    body = [b.text for b in doc.blocks if b.scope == ScopeHint.main]
    assert any("up to 300 kilometers" in t and "<p>" not in t for t in body)
    assert not any("junk" in b.text for b in doc.blocks)


def test_pdf_swapped_ligatures_are_repaired():
    from defence_extractor.ingest.pdf_adapter import _norm
    assert _norm("Ri昀氀ing, rate of 昀椀re, o昀昀ering") == "Rifling, rate of fire, offering"
    assert _norm("昀椀 一") == "昀椀 一"  # not inside a Latin word: untouched
