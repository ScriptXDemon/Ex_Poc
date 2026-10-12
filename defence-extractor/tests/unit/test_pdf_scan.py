"""PDF challenge scan: file-level status, page flags from the text layer, duplicates in the summary."""

from defence_extractor.ingest.pdf_scan import scan_pdf, summarize


def _pdf(lines: list[str]) -> bytes:
    """A one-page PDF with Helvetica text lines (pdfium rebuilds the cross-reference table if it is off)."""
    ops = ["BT /F1 10 Tf 14 TL 60 780 Td"] + [f"({ln}) Tj T*" for ln in lines] + ["ET"]
    stream = "\n".join(ops).encode("latin-1")
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
            b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offs = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1) + b"".join(b"%010d 00000 n \n" % o for o in offs)
    return out + b"trailer << /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)


def test_contents_and_dot_leader_pages_are_flagged(tmp_path):
    lines = ["Contents"] + [f"STAR {i}0 ........................ {10 + i * 2}" for i in range(1, 7)]
    lines += ["Motor diameter, in ........................ 32.5", "Burn time, sec ........................ 53.6"]
    f = tmp_path / "catalogue.pdf"
    f.write_bytes(_pdf(lines))
    r = scan_pdf(f)
    assert r["status"] == "ok" and r["pages"] == 1
    assert {"dot_leaders", "contents_page"} <= set(r["page_flags"]["1"])
    assert "no_text" not in r["flags"]


def test_a_file_that_is_not_a_pdf_and_duplicates_are_reported(tmp_path):
    bad = tmp_path / "error.pdf"
    bad.write_bytes(b"<html><body>404 Not Found</body></html>")
    a, b = tmp_path / "a.pdf", tmp_path / "b.pdf"
    a.write_bytes(_pdf(["Range 120 km"]))
    b.write_bytes(_pdf(["Range 120 km"]))
    res = [scan_pdf(bad), scan_pdf(a), scan_pdf(b)]
    assert res[0]["status"] == "not_pdf"
    sm = summarize(res)
    rows = {r["flag"]: r for r in sm["flags"]}
    assert rows["not_pdf"]["files"] == 1 and rows["duplicate_file"]["files"] == 1
