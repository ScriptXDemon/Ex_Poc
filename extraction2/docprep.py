# -*- coding: utf-8 -*-
"""Document -> extractor input. The extractor reads TEXT; this decides what text a page or PDF becomes.

HTML (raw html + crawler main_text + crawler spec_tables):
  h0  main_text only (trafilatura; what the line fed until now)
  h1  + crawler spec_tables as "label: value" lines
  h2  + bs4/lxml over the raw html: every <table> row keyed by its header, every <dl> dt:dd pair
      (lines already in main_text are not repeated)
PDF (first PAGES pages; the crawler lost the text for 94% of attachments, so every variant beats it):
  pypdf        plain text (the crawler's parser; columns collapse)
  plumber      pdfplumber text + extract_tables rows keyed by header
  mupdf        pymupdf4llm markdown (tables as markdown; AGPL)
  inspector    pdf-inspector markdown (Rust, Firecrawl; flags scanned / table / column pages)
  oxide        pdf_oxide markdown (Rust)
  rust         pdf_oxide text + tablers tables as keyed rows (the "cheap text + table parser" route)
Every value the extractor finds must still be located verbatim in whatever text is produced here.

    python extraction2/docprep.py --build     # writes corpus/best30/prep_<variant>.jsonl + timing
"""
import io, json, os, re, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
B30 = os.path.join(HERE, "corpus", "best30")
PAGES = 6
_WS = re.compile(r"\s+")


def _t(x):
    return _WS.sub(" ", x or "").strip()


def keyed_rows(rows, title=""):
    """table grid -> lines a model reads and a verifier can quote: 'Row | Header: value'.
    2-column grids are label/value pairs; a digit-free first row of a wider grid keys the cells."""
    rows = [[_t(c) for c in r] for r in rows if r and any(_t(c) for c in r)]
    if not rows:
        return ""
    hdr, body = None, rows
    if len(rows) > 1 and len(rows[0]) > 2 and not any(re.search(r"\d", c) for c in rows[0][1:]):
        hdr, body = rows[0], rows[1:]
    out = []
    for r in body:
        if hdr and len(hdr) == len(r):
            cells = ["%s: %s" % (h or "col%d" % i, v) for i, (h, v) in enumerate(zip(hdr[1:], r[1:]), 1) if v]
            out.append(" | ".join([r[0]] + cells) if r[0] else " | ".join(cells))
        elif len(r) == 2:
            out.append("%s: %s" % (r[0], r[1]) if r[0] else r[1])
        else:
            out.append(" | ".join(c for c in r if c))
    return (("Table: %s\n" % _t(title)) if _t(title) else "Table:\n") + "\n".join(out)


def html_structured(html):
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "lxml")
    for t in soup(["script", "style", "noscript", "nav", "footer"]):
        t.decompose()
    blocks = []
    for tb in soup.find_all("table"):
        if tb.find_parent("table"):
            continue                                  # nested layout tables: the outer one carries it
        rows = [[c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])] for tr in tb.find_all("tr")]
        cap = tb.find("caption")
        blocks.append(keyed_rows(rows, cap.get_text(" ", strip=True) if cap else ""))
    for dl in soup.find_all("dl"):
        pairs = [[dt.get_text(" ", strip=True), dt.find_next_sibling("dd").get_text(" ", strip=True)]
                 for dt in dl.find_all("dt") if dt.find_next_sibling("dd")]
        if pairs:
            blocks.append(keyed_rows(pairs))
    return [b for b in blocks if b]


def spec_blocks(spec_tables):
    return [keyed_rows([[r.get("label", ""), r.get("value", "")] for r in (t.get("rows") or [])], t.get("title", ""))
            for t in (spec_tables or []) if isinstance(t, dict)]


def _norm(s):
    return _WS.sub(" ", re.sub(r"[|:]", " ", s.lower())).strip()


def _novel(blocks, text):
    """drop table lines whose every part is already in main_text (trafilatura keeps simple tables)"""
    have = _norm(text)
    out = []
    for b in blocks:
        if not b:
            continue
        lines = b.split("\n")
        keep = [l for l in lines[1:] if not all(_norm(p) in have for p in re.split(r"[:|]", l) if _norm(p))]
        if keep:
            out.append("\n".join([lines[0]] + keep))
    return out


def html_variants(d):
    text = d.get("text") or ""
    html_p = os.path.join(B30, "html", d["document_id"] + ".html")
    html = io.open(html_p, encoding="utf-8").read() if os.path.exists(html_p) else ""
    sb = spec_blocks(d.get("spec_tables"))
    hb = html_structured(html) if html else []
    return {"h0": text,
            "h1": "\n\n".join([text] + _novel(sb, text)),
            "h2": "\n\n".join([text] + _novel(sb + hb, text))}


# ---------------------------------------------------------------- PDF
def pdf_pypdf(p):
    import pypdf
    return [pg.extract_text() or "" for pg in pypdf.PdfReader(p).pages[:PAGES]]


def pdf_plumber(p):
    import pdfplumber
    out = []
    with pdfplumber.open(p) as pdf:
        for pg in pdf.pages[:PAGES]:
            t = pg.extract_text(x_tolerance=1.5, y_tolerance=3) or ""
            tabs = [keyed_rows(tb) for tb in (pg.extract_tables() or []) if tb and len(tb) > 1]
            out.append("\n\n".join([t] + [x for x in tabs if x]))
    return out


def pdf_mupdf(p):
    import pymupdf, pymupdf4llm
    n = min(PAGES, pymupdf.open(p).page_count)
    ch = pymupdf4llm.to_markdown(p, pages=list(range(n)), page_chunks=True, show_progress=False)
    return [c.get("text", "") for c in ch]


def pdf_inspector(p):
    import pdf_inspector as pi, pypdf
    n = min(PAGES, len(pypdf.PdfReader(p).pages))
    r = pi.extract_pages_markdown(p, list(range(n)))
    return [getattr(x, "markdown", "") or "" for x in (getattr(r, "pages", None) or [])]


def pdf_oxide(p):
    import pdf_oxide as po
    doc = po.PdfDocument(p)
    return [doc.to_markdown(i) or "" for i in range(min(PAGES, doc.page_count()))]


def pdf_rust(p):
    import pdf_oxide as po, tablers as tb
    doc = po.PdfDocument(p)
    tdoc = tb.Document(p)
    out = []
    for i in range(min(PAGES, doc.page_count())):
        t = doc.extract_text(i) or ""
        tabs = []
        for tab in tb.find_tables(tdoc.get_page(i), extract_text=True):
            rows = [[(getattr(c, "text", "") or "") if c is not None else "" for c in row.cells] for row in tab.rows]
            tabs.append(keyed_rows(rows))
        out.append("\n\n".join([t] + [x for x in tabs if x]))
    return out


def pdf_pdfium(p):
    import pypdfium2 as pdfium
    pdf = pdfium.PdfDocument(p)
    return [pdf[i].get_textpage().get_text_range() for i in range(min(PAGES, len(pdf)))]


def pdf_plumber_layout(p):
    """layout=True keeps columns apart with whitespace (VAREX: +3-8 EM over plain text); runs of spaces
    are squeezed to 3 so the chunk budget is not spent on padding"""
    import pdfplumber
    out = []
    with pdfplumber.open(p) as pdf:
        for pg in pdf.pages[:PAGES]:
            t = pg.dedupe_chars().extract_text(layout=True) or ""
            t = "\n".join(re.sub(r" {3,}", "   ", l).rstrip() for l in t.splitlines() if l.strip())
            tabs = [keyed_rows(tb) for tb in (pg.extract_tables() or []) if tb and len(tb) > 1]
            out.append("\n\n".join([t] + [x for x in tabs if x]))
    return out


def dedupe_runs(t, keep=1):
    """GLM-OCR can loop ("SAN MARCO" x596): collapse runs of an identical line to `keep` copies"""
    out = []
    for l in t.splitlines():
        if len(out) >= keep and all(x == l for x in out[-keep:]) and l.strip():
            continue
        out.append(l)
    return "\n".join(out)


def ocr_page(p, i, model="glm-ocr"):
    """OCR lane for pages with no usable text layer (pdf-inspector says so): render at 200 DPI with
    pypdfium2, read with GLM-OCR (0.9B, OmniDocBench 94.6) via local Ollama. Cached per page. The
    transcript becomes the page's evidence text -- lower trust than a text layer (source_tier stays)."""
    import base64, requests, pypdfium2 as pdfium
    cp = os.path.join(B30, "ocr", "%s_p%d.txt" % (os.path.basename(p)[:-4], i + 1))
    if os.path.exists(cp):
        return io.open(cp, encoding="utf-8").read()
    img = pdfium.PdfDocument(p)[i].render(scale=200 / 72).to_pil()
    buf = io.BytesIO(); img.save(buf, format="PNG")
    r = requests.post("http://127.0.0.1:11434/api/generate", timeout=600, json={
        "model": model, "prompt": "Text Recognition:", "stream": False, "keep_alive": "10m",
        "images": [base64.b64encode(buf.getvalue()).decode()], "options": {"temperature": 0, "num_predict": 4096}})
    r.raise_for_status()
    t = dedupe_runs(r.json().get("response", ""))
    os.makedirs(os.path.dirname(cp), exist_ok=True)
    io.open(cp, "w", encoding="utf-8").write(t)
    return t


PDF_VARIANTS = {"pypdf": pdf_pypdf, "pdfium": pdf_pdfium, "plumber": pdf_plumber, "plumber_layout": pdf_plumber_layout,
                "mupdf": pdf_mupdf, "inspector": pdf_inspector, "oxide": pdf_oxide, "rust": pdf_rust}


def pdf_units():
    """random-page PDFs (pdfs.json: mostly policies/reports) + datasheet/brochure PDFs (datasheets.json)"""
    man = json.load(io.open(os.path.join(B30, "pdfs.json"), encoding="utf-8"))
    docs = {}
    for l in io.open(os.path.join(B30, "docs.jsonl"), encoding="utf-8"):
        d = json.loads(l); docs[d["document_id"]] = d
    man = [dict(m, company=docs[m["document_id"]]["company"]) for m in man]
    dsp = os.path.join(B30, "datasheets.json")
    man += [m for m in (json.load(io.open(dsp, encoding="utf-8")) if os.path.exists(dsp) else []) if m.get("file")]
    seen = set()
    for m in man:
        if m.get("file") and m["file"] not in seen:
            seen.add(m["file"])
            yield {"document_id": "pdf_" + m["file"][:-4], "parent": m["document_id"], "url": m["url"],
                   "company": m["company"], "path": os.path.join(B30, "pdf", m["file"])}


def html_docs():
    rows = []
    for name in ("docs.jsonl", "docs_prod.jsonl"):
        p = os.path.join(B30, name)
        if os.path.exists(p):
            rows += [json.loads(l) for l in io.open(p, encoding="utf-8") if l.strip()]
    return rows


def build(only=None):
    docs = html_docs()
    timing, outs = {}, {}
    if not only:
        for d in docs:
            for k, text in html_variants(d).items():
                outs.setdefault(k, []).append({"document_id": d["document_id"], "url": d["url"], "title": d["title"],
                                               "company": d["company"], "language": "en", "text": text})
        for k in ("h0", "h1", "h2"):
            timing[k] = {"chars": sum(len(x["text"]) for x in outs[k])}
    units = list(pdf_units())
    for k, fn in PDF_VARIANTS.items():
        if only and k not in only:
            continue
        t0, fails = time.time(), 0
        for u in units:
            try:
                pages = fn(u["path"])
            except Exception as e:
                pages, fails = [], fails + 1
                print("  %s fail %s: %s" % (k, u["document_id"], str(e)[:120]))
            text = "\n\n".join("[page %d]\n%s" % (i + 1, p.strip()) for i, p in enumerate(pages) if p.strip())
            outs.setdefault(k, []).append({"document_id": u["document_id"], "url": u["url"], "title": "",
                                           "company": u["company"], "language": "en", "text": text})
        timing[k] = {"sec": round(time.time() - t0, 2), "fails": fails,
                     "chars": sum(len(x["text"]) for x in outs[k]), "empty": sum(1 for x in outs[k] if not x["text"])}
    for k, rows in outs.items():
        with io.open(os.path.join(B30, "prep_%s.jsonl" % k), "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    tp = os.path.join(B30, "prep_timing.json")
    old = json.load(io.open(tp)) if os.path.exists(tp) else {}
    old.update(timing)
    io.open(tp, "w").write(json.dumps(old, indent=1))
    for k, v in timing.items():
        print("%-10s %s" % (k, v))


def demo():
    assert keyed_rows([["Length", "130 mm"], ["Weight", "335g"]]) == "Table:\nLength: 130 mm\nWeight: 335g"
    g = keyed_rows([["Model", "Calibre", "Range"], ["K9", "155 mm", "40 km"], ["K10", "", "n/a"]], "Specs")
    assert g.split("\n")[1] == "K9 | Calibre: 155 mm | Range: 40 km", g
    h = html_structured("<table><tr><th>Speed</th><td>90 km/h</td></tr></table><dl><dt>Crew</dt><dd>3</dd></dl>")
    assert h == ["Table:\nSpeed: 90 km/h", "Table:\nCrew: 3"], h
    assert _novel(["Table:\nLength: 130 mm\nWidth: 65 mm"], "| Length | 130 mm |") == ["Table:\nWidth: 65 mm"]
    assert dedupe_runs("A\nA\nA\nB") == "A\nB"
    print("docprep demo ok")


if __name__ == "__main__":
    demo()
    if "--build" in sys.argv:
        build([a for a in sys.argv[2:] if not a.startswith("-")] or None)
