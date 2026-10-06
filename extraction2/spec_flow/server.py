# -*- coding: utf-8 -*-
"""Spec prompt-builder POC: serves index.html and the two steps that must run our own code.

  POST /resolve {url, html} -> our production resolver (content.content_text = webbench.ex_template_prec, the
                               site template from corpus/web/ref) + every visible line of the raw HTML (the
                               verifier's view of the actual page)
  POST /tokens  {text}      -> exact Qwen2.5 token count of `text` sent as one user message (chat template incl.)
  GET  /base                -> the production spec prompt (the 1,105-call form in out/s2fp_trace.jsonl) + its schema

The LLM calls go from the browser straight to OpenRouter (or any OpenAI-compatible URL) with the user's key;
no key ever reaches this server.

    python extraction2/spec_flow/server.py [--port 8765]     # then open http://127.0.0.1:8765/
"""
import io, json, os, sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
E2 = os.path.dirname(HERE)
sys.path.insert(0, E2); sys.path.insert(0, os.path.join(E2, "corpus"))
import content, webbench as W                                # noqa: E402
import runview                                               # noqa: E402  (the batch run, for /results.html)
from tokenizers import Tokenizer                             # noqa: E402

if not os.path.exists(os.path.join(W.REF, "index.jsonl")):   # no reference pages: production mode has no site template
    W._PREC_REFS = []
TOK = Tokenizer.from_file(os.path.join(HERE, "qwen2.5_tokenizer.json"))   # Qwen/Qwen2.5-72B-Instruct
# Qwen2.5's chat template: with no system message it inserts its default one
CHAT = ("<|im_start|>system\nYou are Qwen, created by Alibaba Cloud. You are a helpful assistant.<|im_end|>\n"
        "<|im_start|>user\n%s<|im_end|>\n<|im_start|>assistant\n")
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
XMLDECL = __import__("re").compile(r"^\s*<\?xml[^>]*\?>")
BASE = json.load(io.open(os.path.join(HERE, "base_prompt.json"), encoding="utf-8"))


def resolve(url, html, mode="text"):
    """mode "text": every visible line (only code, tags, svg, styles dropped: no text is judged boilerplate);
    mode "production": the production resolver, which also drops nav/footer, the site template and link rails"""
    html = XMLDECL.sub("", html, 1)                          # lxml refuses a str that declares its own encoding
    vis = "\n".join(W.visible_lines(html))
    if mode == "text":
        return {"text": vis, "visible": vis, "template_lines": None, "ref_pages": None}
    text = content.content_text({"url": url}, html, [])      # no crawler spec_tables for an uploaded page
    tpl = W.lang_template(url, html)
    host = url.split("//")[-1].split("/")[0].lower().removeprefix("www.")
    refs = sum(1 for x in W._prec_refs() if x["host"] == host)
    return {"text": text, "visible": vis, "template_lines": len(tpl), "ref_pages": refs}


def fetch(url):
    """the page's raw HTML: the latest copy in the DC corpus (read-only, one id), else the live page"""
    u = url.strip().split("#")[0]
    bare = u.split("//", 1)[-1].removeprefix("www.").rstrip("/")
    variants = [s + w + bare + t for s in ("https://", "http://") for w in ("", "www.") for t in ("", "/")]
    try:
        import dc
        with dc.connect() as c:
            row = c.execute("select document_id, fetched_at from documents where url = any(%s) order by fetched_at desc limit 1",
                            (variants,)).fetchone()
            if row:
                h = c.execute("select html from documents where document_id = %s", (row[0],)).fetchone()[0]
                return {"html": h, "source": "DC corpus %s, fetched %s" % row}
    except Exception as e:
        dc_err = "%s: %s" % (type(e).__name__, e)
    else:
        dc_err = "not in the DC corpus"
    import urllib.request
    r = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                                           "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"})
    try:
        with urllib.request.urlopen(r, timeout=30) as f:
            h = f.read().decode(f.headers.get_content_charset() or "utf-8", "replace")
        if len(W.visible_lines(h)) >= 5:
            return {"html": h, "source": "live fetch (%s)" % dc_err}
    except Exception as e:
        h = "fetch %s: %s" % (type(e).__name__, e)
    try:                                                     # a script-rendered page (SPA): render it, as the crawler does
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            b = p.chromium.launch(executable_path=EDGE, headless=True)
            pg = b.new_page(); pg.goto(u, wait_until="networkidle", timeout=60000)
            try:                                             # networkidle can come before the script has drawn the content
                pg.wait_for_function("document.body && document.body.innerText.length > 1000", timeout=20000)
            except Exception:
                pass
            pg.wait_for_timeout(2000)
            h = pg.content(); b.close()
        return {"html": h, "source": "live page rendered in headless Edge (%s; the plain fetch had no visible text)" % dc_err}
    except Exception as e:
        raise RuntimeError("%s; the live page could not be fetched or rendered (%s: %s): upload the HTML file instead" % (dc_err, type(e).__name__, e))


def to_md(html):
    """the WHOLE raw page as Markdown (menus and footers kept, so the reviewer also sees what the resolver dropped):
    headings -> '#', list items -> '- ', each table row -> '| a | b |'"""
    from lxml import html as LH
    t = LH.fromstring(XMLDECL.sub("", html, 1))
    for e in t.xpath("//script|//style|//noscript|//svg|//template|//head|//iframe"):
        e.drop_tree()
    for tr in t.xpath("//tr"):
        cells = [" ".join(c.text_content().split()) for c in tr.xpath("./th|./td")]
        p = LH.Element("p"); p.text = " " + " | ".join(cells) + " "; p.tail = tr.tail   # visible_lines strips '|'
        tr.getparent().replace(tr, p)
    for n in range(1, 7):
        for h in t.iter("h%d" % n):
            h.text = "#" * n + " " + (h.text or "")
    for li in t.iter("li"):
        li.text = "- " + (li.text or "")
    return "\n".join(W.visible_lines(LH.tostring(t, encoding="unicode"))).replace("", "|")


def tokens(text):
    return len(TOK.encode(CHAT % text, add_special_tokens=False).ids)


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        b = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code); self.send_header("Content-Type", ctype); self.send_header("Content-Length", len(b))
        self.end_headers(); self.wfile.write(b)

    def do_GET(self):
        from urllib.parse import urlparse, parse_qs
        u = urlparse(self.path)
        if self.path in ("/", "/index.html", "/results.html"):
            f = "results.html" if self.path == "/results.html" else "index.html"
            return self._send(200, io.open(os.path.join(HERE, f), "rb").read(), "text/html; charset=utf-8")
        try:
            if u.path == "/api/results":                     # the batch run, for the results page
                return self._send(200, runview.results())
            if u.path == "/api/batch":                       # the whole-corpus run: running or not
                return self._send(200, runview.batch_status())
            if u.path == "/api/result":
                return self._send(200, runview.detail(parse_qs(u.query)["case"][0]))
            if u.path == "/download/xlsx":
                b = io.open(runview.XLSX, "rb").read()
                self.send_response(200)
                self.send_header("Content-Type", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
                self.send_header("Content-Disposition", 'attachment; filename="%s"' % os.path.basename(runview.XLSX))
                self.send_header("Content-Length", len(b)); self.end_headers(); self.wfile.write(b)
                return
        except Exception as e:
            return self._send(500, {"error": "%s: %s" % (type(e).__name__, e)})
        if self.path == "/base":
            return self._send(200, BASE)
        self._send(404, {"error": "not found"})

    def do_POST(self):
        try:
            b = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            if self.path == "/resolve":
                return self._send(200, resolve(b.get("url") or "", b["html"], b.get("mode") or "text"))
            if self.path == "/api/batch/start":              # resume the whole-corpus run (done pages are skipped)
                return self._send(200, runview.batch_start())
            if self.path == "/api/batch/stop":
                return self._send(200, runview.batch_stop())
            if self.path == "/markdown":
                return self._send(200, {"md": to_md(b["html"])})
            if self.path == "/fetch":
                return self._send(200, fetch(b["url"]))
            if self.path == "/tokens":
                return self._send(200, {"tokens": tokens(b["text"])})
            self._send(404, {"error": "not found"})
        except Exception as e:
            self._send(500, {"error": "%s: %s" % (type(e).__name__, e)})


def demo():
    assert tokens("") > 20 and tokens("x " * 100) > tokens("x")
    p = BASE["template"]
    assert p.startswith("You extract the technical specifications") and "PARAMETERS:" in p and p.endswith("specification.)\n\n")
    page = "<html><body><nav>Home | Products</nav><h1>Gun X</h1><p>Calibre: 30 mm</p><script>var x=1</script><svg><text>logo</text></svg></body></html>"
    r = resolve("https://example.com/p", page, "production")
    assert "Calibre: 30 mm" in r["text"] and "Home" not in r["text"] and "Home" in r["visible"], r
    r = resolve("https://example.com/p", page)                # text mode keeps the nav, drops the code and the svg
    assert r["text"].split("\n") == ["Home | Products", "Gun X", "Calibre: 30 mm"], r
    assert resolve("", '<?xml version="1.0" encoding="UTF-8"?>' + page)["text"] == r["text"]   # an XHTML page
    md = to_md("<html><body><nav><ul><li>Home</li></ul></nav><h2>Gun X</h2><table><tr><th>Calibre</th><td>30 mm</td></tr></table></body></html>")
    assert md.split("\n") == ["- Home", "## Gun X", "| Calibre | 30 mm |"], md
    print("ok")


if __name__ == "__main__":
    if "--demo" in sys.argv:
        demo(); sys.exit()
    port = int(sys.argv[sys.argv.index("--port") + 1]) if "--port" in sys.argv else 8765
    srv = ThreadingHTTPServer(("127.0.0.1", port), H)        # listen first: a restart costs seconds, not minutes
    __import__("threading").Thread(target=W._prec_refs, daemon=True).start()   # site templates load in the background
    print("http://127.0.0.1:%d/" % port, flush=True)
    srv.serve_forever()
