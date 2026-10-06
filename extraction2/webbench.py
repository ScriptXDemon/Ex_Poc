# -*- coding: utf-8 -*-
"""Web-page CONTENT extraction bench: raw HTML -> the page's main content, any language.

Gold (gold/web/gold_*.jsonl) = which lines of the page's full visible text are main content, chosen
by annotators from a numbered line view (views/<id>.txt) -- independent of every extractor. Scored
the way the public benches do (ScrapingHub article-extraction-benchmark, WCEB): bag-of-tokens
precision / recall / F1 of an extractor's text against the gold content; CJK/Thai by character
bigrams (no spaces to split on).

    python extraction2/webbench.py --views            # write numbered views for annotators
    python extraction2/webbench.py --score [names]    # run extractors, score vs gold, per language
    python extraction2/webbench.py --md-demo          # Task 21: template_prec_md self-check (offline, invented site)
"""
import collections, glob, io, json, os, re, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
SET = os.environ.get("WEBBENCH_SET", "web")          # "web" = dev gold, "web2" = fresh held-out gold
WEB = os.path.join(HERE, "corpus", SET)
GOLD = os.path.join(HERE, "gold", SET)
REF = os.path.join(HERE, "corpus", "web", "ref")     # reference pages (site templates) are shared
BLOCK = "p div li tr h1 h2 h3 h4 h5 h6 dt dd br section article table header footer nav blockquote figcaption pre".split()     # td/th stay inline: a table row is one line


def docs():
    return [json.loads(l) for l in io.open(os.path.join(WEB, "docs.jsonl"), encoding="utf-8") if l.strip()]


def html_of(did):
    return io.open(os.path.join(WEB, "html", did + ".html"), encoding="utf-8").read()


def visible_lines(html):
    """every visible text line of the page, block by block (the annotators' universe)"""
    from lxml import html as LH
    t = LH.fromstring(html)
    for e in t.xpath("//script|//style|//noscript|//svg|//template|//head"):
        e.drop_tree()
    for c in t.xpath("//td|//th"):
        c.tail = " | " + (c.tail or "")
    for e in t.iter(*BLOCK):
        e.tail = "\n" + (e.tail or "")
        if e.text is not None and e.tag not in ("td", "th"):
            e.text = "\n" + e.text
    lines = [" ".join(l.split()).strip(" |") for l in t.text_content().split("\n")]
    return [l for l in lines if l]


# ------------------------------------------------------------------ tokens + metric
_CJK = re.compile(r"[぀-ヿ㐀-鿿가-힯฀-๿]")


def tokens(text):
    text = (text or "").lower()
    out = re.findall(r"[^\W_]+", text)
    toks = []
    for w in out:
        if _CJK.search(w):
            toks += [w[i:i + 2] for i in range(max(1, len(w) - 1))]
        else:
            toks.append(w)
    return toks


def prf(pred, gold):
    p, g = collections.Counter(tokens(pred)), collections.Counter(tokens(gold))
    tp = sum((p & g).values())
    P = tp / max(1, sum(p.values())); R = tp / max(1, sum(g.values()))
    return P, R, (2 * P * R / (P + R) if P + R else 0.0)


# ------------------------------------------------------------------ extractors
def ex_crawler(d, html):
    return d["main_text"]


def ex_traf(d, html, **kw):
    import trafilatura
    return trafilatura.extract(html, url=d["url"], include_tables=True, include_comments=False, **kw) or ""


def ex_traf_recall(d, html):
    return ex_traf(d, html, favor_recall=True)


def ex_traf_precision(d, html):
    return ex_traf(d, html, favor_precision=True)


def ex_resiliparse(d, html):
    from resiliparse.extract.html2text import extract_plain_text
    return extract_plain_text(html, main_content=True, alt_texts=False, links=False, list_bullets=False)


def ex_readability(d, html):
    from readability import Document
    from lxml import html as LH
    s = Document(html).summary(html_partial=True)
    return LH.fromstring(s).text_content() if s.strip() else ""


def ex_justext(d, html):
    import justext
    lang = {"de": "German", "fr": "French", "it": "Italian", "es": "Spanish", "pl": "Polish", "sv": "Swedish",
            "fi": "Finnish", "pt": "Portuguese", "tr": "Turkish", "ru": "Russian", "nl": "Dutch", "no": "Norwegian_Bokmal"}
    sl = justext.get_stoplist(lang.get((d.get("gold_lang") or "en")[:2], "English"))
    return "\n".join(p.text for p in justext.justext(html.encode("utf-8", "ignore"), sl) if not p.is_boilerplate)


def _lines(t):
    return [" ".join(l.split()) for l in (t or "").split("\n") if l.strip()]


def ex_union(d, html):
    """crawler text + resiliparse lines not already in it (the two miss DIFFERENT pages)"""
    base = _lines(d["main_text"]); have = {l.lower() for l in base}
    return "\n".join(base + [l for l in _lines(ex_resiliparse(d, html)) if l.lower() not in have])


_TPL = {}


def site_template(host, min_pages=5, share=0.3):
    """lines that recur on >= share of a site's reference pages = the site's template (menus, footer,
    cookie banner) -- language-agnostic: repetition, not words, marks boilerplate"""
    if host not in _TPL:
        import collections
        from urllib.parse import urlparse
        ref = REF
        idx = [json.loads(l) for l in io.open(os.path.join(ref, "index.jsonl"), encoding="utf-8")] if os.path.exists(os.path.join(ref, "index.jsonl")) else []
        pages = [x["document_id"] for x in idx if urlparse(x["url"]).netloc == host]
        cnt = collections.Counter()
        for p in pages:
            try:
                cnt.update(set(visible_lines(io.open(os.path.join(ref, p + ".html"), encoding="utf-8").read())))
            except Exception:
                pass
        _TPL[host] = {l for l, n in cnt.items() if len(pages) >= min_pages and n >= share * len(pages)}
    return _TPL[host]


def ex_template(d, html):
    from urllib.parse import urlparse
    tpl = site_template(urlparse(d["url"]).netloc)
    return "\n".join(l for l in visible_lines(html) if l not in tpl)


# markup that DECLARES itself chrome -- tag/role/class names, not page words, so it holds in any language
_CHROME_TAGS = ("nav", "header", "footer", "aside", "form")
_CHROME_ROLE = re.compile(r"navigation|banner|contentinfo|search|menu", re.I)
_CHROME_CLS = re.compile(r"(^|[\s_-])(nav|navbar|navigation|menu|megamenu|breadcrumbs?|cookie|consent|gdpr|footer|"
                         r"header|masthead|sidebar|social|share|sharing|newsletter|subscribe|skip|language|lang-switch|"
                         r"login|search|topbar|utility|stock|ticker)([\s_-]|$)", re.I)


def pruned_lines(html):
    """visible lines after dropping declared chrome; an element holding the page's <h1> is never dropped"""
    from lxml import html as LH
    t = LH.fromstring(html)
    for e in list(t.iter()):
        if not isinstance(e.tag, str) or e.getparent() is None:
            continue
        cls = " ".join([e.get("class") or "", e.get("id") or ""])
        chrome = (e.tag in _CHROME_TAGS and not e.xpath("ancestor::article|ancestor::main")) \
            or _CHROME_ROLE.search(e.get("role") or "") or (cls.strip() and _CHROME_CLS.search(cls))
        if chrome and not e.xpath(".//h1") and e.tag not in ("html", "body", "main", "article"):
            e.drop_tree()
    return visible_lines(LH.tostring(t, encoding="unicode"))


def _locale(url):
    from urllib.parse import urlparse
    m = re.match(r"/([a-z]{2}(?:[-_][a-z]{2})?)(?:/|$)", urlparse(url).path.lower())
    return m.group(1) if m else ""


_TPL2 = {}


def locale_template(url, share=0.3, min_pages=4):
    """site template learnt from reference pages of the SAME locale (a /he/ page's menu is Hebrew; the
    site's /en/ pages never repeat it); falls back to the whole host when a locale has too few pages"""
    from urllib.parse import urlparse
    host, loc = urlparse(url).netloc.lower().removeprefix("www."), _locale(url)   # www. and bare host = one site
    key = (host, loc)
    if key not in _TPL2:
        ref = REF
        idx = [json.loads(l) for l in io.open(os.path.join(ref, "index.jsonl"), encoding="utf-8")]
        same = [x for x in idx if urlparse(x["url"]).netloc.lower().removeprefix("www.") == host]
        loc_pages = [x for x in same if _locale(x["url"]) == loc]
        pages = loc_pages if len(loc_pages) >= min_pages else same
        cnt = collections.Counter()
        for x in pages:
            try:
                cnt.update(set(visible_lines(io.open(os.path.join(ref, x["document_id"] + ".html"), encoding="utf-8").read())))
            except Exception:
                pass
        _TPL2[key] = {l for l, n in cnt.items() if len(pages) >= min_pages and n >= max(2, share * len(pages))}
    return _TPL2[key]


def ex_template2(d, html):
    """pruned chrome + locale-aware template subtraction"""
    tpl = locale_template(d["url"])
    return "\n".join(l for l in pruned_lines(html) if l not in tpl)


def ex_pruned(d, html):
    return "\n".join(pruned_lines(html))


def ex_template_loc(d, html):
    """locale-aware template subtraction over ALL visible lines (no markup pruning)"""
    tpl = locale_template(d["url"])
    return "\n".join(l for l in visible_lines(html) if l not in tpl)


def link_lines(html):
    """visible lines + whether each line is ENTIRELY link text (anchor text wrapped in sentinels)"""
    from lxml import html as LH
    t = LH.fromstring(html)
    for a in t.iter("a"):
        a.text = "" + (a.text or "")          # private-use sentinels: lxml refuses control chars
        a.tail = "" + (a.tail or "")
    out = []
    for l in visible_lines(LH.tostring(t, encoding="unicode")):
        outside = re.sub("[^]*?", "", l).replace("", "").strip(" |")
        clean = l.replace("", "").replace("", "").strip()
        if clean:
            out.append((" ".join(clean.split()), not outside and "" in l))
    return out


def ex_linkrun(d, html, run=5, max_words=6):
    """template_tags + drop menu blocks: >= `run` consecutive short lines that are entirely links
    (language-agnostic: link density, not words). Listing titles survive -- teasers/dates break the run."""
    from lxml import html as LH
    t = LH.fromstring(html)
    for e in t.xpath("//nav|//footer|//*[@role='navigation']|//*[@role='contentinfo']"):
        if e.getparent() is not None and not e.xpath(".//h1"):
            e.drop_tree()
    tpl = locale_template(d["url"])
    ll = [(l, k) for l, k in link_lines(LH.tostring(t, encoding="unicode")) if l not in tpl]
    menu = lambda x: x[1] and len(x[0].split()) <= max_words
    keep, i = [], 0
    while i < len(ll):
        j = i
        while j < len(ll) and menu(ll[j]):
            j += 1
        if j - i >= run:
            i = j
            continue
        keep.append(ll[i][0]); i += 1
    return "\n".join(keep)


JUDGE_MODEL = os.environ.get("WEB_JUDGE_MODEL", "qwen3.8:27b")
_JUDGE_PROMPT = """You see every text line of one web page, numbered, in page order. Lines marked "?" are UNCERTAIN.
Decide which UNCERTAIN lines are page CHROME rather than the page's own content. Chrome = site navigation or menus,
breadcrumbs, language/location pickers, cookie/consent/privacy text, login/search/cart, share buttons, newsletter or
contact forms, "related"/"latest news"/"you may also like" widgets not about this page's subject, footers, legal links,
stock tickers. Content = the page's headline, body text, sub-headings, product descriptions, specification rows, captions,
dates/bylines, and on a listing page the listed item titles and teasers. Any language. When unsure, it is content.
Reply with JSON only: {"chrome": [line numbers]}

PAGE URL: %s
PAGE TITLE: %s
LINES:
%s"""


def _judge(d, lines, unsure):
    """one local-LLM call per page over the uncertain lines only; cached; silence = keep (recall first)"""
    import hashlib, requests
    cache = os.path.join(WEB, "judge"); os.makedirs(cache, exist_ok=True)
    body = "\n".join("%d%s %s" % (i + 1, "?" if i in unsure else " ", l[:160]) for i, l in enumerate(lines[:400]))
    key = hashlib.sha1((JUDGE_MODEL + body).encode("utf-8")).hexdigest()[:16]
    cp = os.path.join(cache, key + ".json")
    if os.path.exists(cp):
        return set(json.load(io.open(cp)))
    r = requests.post("http://127.0.0.1:11434/api/chat", timeout=900, json={
        "model": JUDGE_MODEL, "stream": False, "think": False, "format": "json", "keep_alive": "30m",
        "options": {"temperature": 0, "num_ctx": 16384, "num_predict": 1500},
        "messages": [{"role": "user", "content": _JUDGE_PROMPT % (d["url"], d.get("title") or "", body)}]})
    try:
        out = {int(x) - 1 for x in json.loads(r.json()["message"]["content"]).get("chrome", [])} & unsure
    except Exception:
        out = set()
    json.dump(sorted(out), io.open(cp, "w"))
    return out


def ex_judge(d, html):
    """template_tags, then the LLM removes chrome among lines no precise extractor vouched for"""
    lines = ex_template_tags(d, html).split("\n")
    anchors = {l.lower() for l in _lines(d["main_text"]) + _lines(ex_resiliparse(d, html))}
    unsure = {i for i, l in enumerate(lines) if l.lower() not in anchors}
    drop = _judge(d, lines, unsure) if unsure else set()
    return "\n".join(l for i, l in enumerate(lines) if i not in drop)


def ex_region(d, html, margin=3):
    """template_tags lines, kept only inside the CONTENT REGION: the span between the first and last line
    that a precise extractor (crawler/trafilatura or resiliparse) also emitted, widened by `margin` lines.
    Main content is contiguous; page-specific widgets (galleries, contact forms, related lists) sit outside."""
    lines = ex_template_tags(d, html).split("\n")
    anchors = {l.lower() for l in _lines(d["main_text"]) + _lines(ex_resiliparse(d, html)) if len(l) > 15}
    hit = [i for i, l in enumerate(lines) if l.lower() in anchors]
    if not hit:
        return "\n".join(lines)
    a, b = max(0, hit[0] - margin), min(len(lines), hit[-1] + margin + 1)
    return "\n".join(lines[a:b])


def ex_template_tags(d, html):
    """locale template + drop only <nav>/<footer> and role=navigation|contentinfo (the unambiguous chrome)"""
    from lxml import html as LH
    t = LH.fromstring(html)
    for e in t.xpath("//nav|//footer|//*[@role='navigation']|//*[@role='contentinfo']"):
        if e.getparent() is not None and not e.xpath(".//h1"):
            e.drop_tree()
    tpl = locale_template(d["url"])
    return "\n".join(l for l in visible_lines(LH.tostring(t, encoding="unicode")) if l not in tpl)


# ------------------------------------------------------------------ template_prec: deterministic precision layer
# template_tags + (1) template learnt per page LANGUAGE (url locale, else <html lang>; HTML refs only), (2) drop
# blocks whose markup declares chrome (cookie/consent, dialogs, share, newsletter, breadcrumb, related/latest,
# contact boxes, <aside>, forms, <select>, #footer/#nav ids), (3) start at the <h1>, (4) link lines before the first
# paragraph, (5) on a prose page, link lines after the last paragraph (related / CTA rails). Markup + link density +
# repetition only -- no words of any language. Tuned on gold/web; held-out on gold/web2.
_PREC_CUE = [re.compile(r"cookie|consent|gdpr|onetrust|cookiebot|usercentrics"),
             re.compile(r"(^|\s)((modal|popup|pop-up|dialog|offcanvas)[\w-]*|[\w-]*[^-](_|-)(modal|popup|dialog))(\s|$)"),
             re.compile(r"(^|[\s_-])(share|sharing|social|socials|addthis|sharethis|share-?bar)([\s_-]|$)"),
             re.compile(r"newsletter|subscribe"), re.compile(r"breadcrumb"),
             re.compile(r"related|recommend|latest|more-?news|similar"),
             re.compile(r"(^|[\s_-])contact(s|-?box|-?card|-?person|-?teaser)?([\s_-]|$)")]
_PREC_ID = re.compile(r"^((site-?|page-?|global-?|main-?)?(footer|header)(-?wrap(per)?|-?container|-?area)?|"
                      r"(site-?|main-?|side-?|sub-?|top-?|primary-?|global-?)?(nav|navigation|menu|navbar)(-?main|-?wrap(per)?|-?container|-?bar)?)$")
_PREC_REFS, _TPL5 = None, {}


def _html_lang(html):
    m = re.search(r"<html[^>]*\blang\s*=\s*[\"']?([A-Za-z]{2,3})", html[:5000], re.I)
    return m.group(1).lower() if m else ""


def _prec_refs():
    global _PREC_REFS
    if _PREC_REFS is None:
        from urllib.parse import urlparse
        _PREC_REFS, seen = [], set()
        for l in io.open(os.path.join(REF, "index.jsonl"), encoding="utf-8"):
            x = json.loads(l)
            if x["document_id"] in seen:
                continue
            seen.add(x["document_id"])
            try:
                h = io.open(os.path.join(REF, x["document_id"] + ".html"), encoding="utf-8").read()
            except Exception:
                continue
            if re.search(r"<(html|body)\b", h[:20000], re.I):       # not a .js / .svg / pdf blob
                _PREC_REFS.append({"url": x["url"], "host": urlparse(x["url"]).netloc.lower().removeprefix("www.").split(":")[0],
                                   "loc": _locale(x["url"]), "lang": _html_lang(h), "html": h, "lines": None})
    return _PREC_REFS


def lang_template(url, html, share=0.3, min_pages=4):
    """locale_template, but a site without /xx/ paths is split by the page's <html lang> (otokar: English and
    Turkish pages share one path space), and the share is taken over pages actually parsed"""
    from urllib.parse import urlparse
    host, loc, lang = urlparse(url).netloc.lower().removeprefix("www.").split(":")[0], _locale(url), _html_lang(html)
    key = (host, loc, lang)
    if key not in _TPL5:
        same = [x for x in _prec_refs() if x["host"] == host]
        cand = [x for x in same if loc and x["loc"] == loc]
        if len(cand) < min_pages and lang:
            cand = [x for x in same if x["lang"] == lang and (not loc or x["loc"] in ("", loc))]
            if len(cand) < min_pages:
                cand = [x for x in same if x["lang"] == lang]
        pages = cand if len(cand) >= min_pages else same
        cnt = collections.Counter()
        for x in pages:
            if x["lines"] is None:
                x["lines"] = set(visible_lines(x["html"]))
            cnt.update(x["lines"])
        _TPL5[key] = {l for l, n in cnt.items() if len(pages) >= min_pages and n >= max(2, share * len(pages))}
    return _TPL5[key]


def link_share_lines(t):
    """visible_lines of an lxml tree (tree is modified) + each line's share of characters inside <a>"""
    for e in t.xpath("//script|//style|//noscript|//svg|//template|//head"):
        e.drop_tree()
    for c in t.xpath("//td|//th"):
        c.tail = " | " + (c.tail or "")
    for e in t.iter(*BLOCK):
        e.tail = "\n" + (e.tail or "")
        if e.text is not None and e.tag not in ("td", "th"):
            e.text = "\n" + e.text
    pieces = []

    def walk(e, link):
        lk = link or e.tag == "a"
        if isinstance(e.tag, str) and e.text:
            pieces.append((e.text, lk))
        for c in e:
            walk(c, lk)
            if c.tail:
                pieces.append((c.tail, lk))
    walk(t, False)
    out, cur = [], []

    def flush():
        line = " ".join("".join(x for x, _ in cur).split()).strip(" |")
        if line:
            n = sum(len(x.strip()) for x, _ in cur) or 1
            out.append((line, sum(len(x.strip()) for x, k in cur if k) / n))
        cur.clear()
    for txt, k in pieces:
        for i, p in enumerate(txt.split("\n")):
            if i:
                flush()
            if p:
                cur.append((p, k))
    flush()
    return out


def _drop_declared_chrome(t, tpl, form_keep=0.6):
    """drop blocks whose tag / class / id / role declares chrome; never one holding the <h1>, <main> or <article>,
    nor a <form> that holds >= form_keep of the page's non-template words (a feedback-form page IS its form)"""
    from lxml import html as LH
    words = lambda e: sum(len(l.split()) for l in visible_lines(LH.tostring(e, encoding="unicode")) if l not in tpl)
    body = t.find("body") if t.find("body") is not None else t
    kill = []
    for e in t.iter():
        if not isinstance(e.tag, str) or e.tag in ("html", "body", "main", "article") or e.getparent() is None:
            continue
        cls = ((e.get("class") or "") + " " + (e.get("id") or "")).lower().strip()
        if e.tag in ("aside", "form") or (e.tag == "select" and not e.xpath("ancestor::article|ancestor::main")) \
                or (e.get("role") or "").lower() in ("dialog", "alertdialog") or (e.get("aria-modal") or "") == "true" \
                or _PREC_ID.search((e.get("id") or "").lower()) or (cls and any(rx.search(cls) for rx in _PREC_CUE)):
            kill.append(e)
    ks = set(kill)
    for e in kill:
        if e.getparent() is None or any(a in ks for a in e.iterancestors()) or e.xpath(".//h1|.//main|.//article"):
            continue
        if e.tag == "form" and words(e) >= form_keep * max(1, words(body)):
            continue
        e.drop_tree()


def ex_template_prec(d, html, prose_min=200, link_max=0.3, md=False):
    from lxml import html as LH
    t = LH.fromstring(html)
    for e in t.xpath("//nav|//footer|//*[@role='navigation']|//*[@role='contentinfo']"):
        if e.getparent() is not None and not e.xpath(".//h1"):
            e.drop_tree()
    tpl = lang_template(d["url"], html)
    _drop_declared_chrome(t, tpl)
    s = LH.tostring(t, encoding="unicode")
    h1 = [l for l in (" ".join(x.text_content().split()) for x in LH.fromstring(s).iter("h1")) if l]
    ll = (_md_share_lines if md else link_share_lines)(LH.fromstring(s))
    start = [i for i, (l, _) in enumerate(ll) if h1 and l == h1[0]]
    ll = [x for x in ll[start[0] if start else 0:] if x[0] not in tpl]          # the page starts at its title
    ntok = lambda xs: sum(len(tokens(l)) for l, _ in xs)
    prose = [i for i, (l, k) in enumerate(ll) if k < 0.5 and len(tokens(l)) >= 12]
    if prose:
        ll = [x for i, x in enumerate(ll) if i >= prose[0] or x[1] < 0.5]            # menus ahead of the first paragraph
        prose = [i for i, (l, k) in enumerate(ll) if k < 0.5 and len(tokens(l)) >= 12]
        body = ll[:prose[-1] + 1]
        if ntok([ll[i] for i in prose]) >= prose_min and ntok([x for x in body if x[1] >= 0.5]) < link_max * max(1, ntok(body)):
            ll = [x for i, x in enumerate(ll) if i <= prose[-1] or x[1] < 0.5]       # rails after the last paragraph
    return _md_render(ll, h1) if md else "\n".join(l for l, _ in ll)


# ------------------------------------------------------------------ template_prec_md (Task 21, OFF): the same lines as markdown
# ex_template_prec(md=True) keeps exactly ex_template_prec's lines (same code path, same selection) and only decorates
# them: '#'*n from h1-h6, '- ' for <li>, ': ' for <dd> (after its <dt> line), GFM table rows '| a | b |' with a
# '| --- |' separator under the header row (the <thead>/all-<th> row, else the table's first kept row; rows before a
# later header row stay plain lines), blank lines around tables and before headings, and one '![alt]()' line per
# PRODUCT IMAGE. A product image (deterministic, markup + the page's own title): an <img> the kept content holds (the
# line it sits in or directly precedes is kept), not inside a link to another page (carousels, teasers, logos), whose
# alt names the product: it contains a digit-bearing word of the <h1> ("Zorya-7", "8x8", "L50") or the h1's first word
# if that has >= 4 characters, and is not already a kept line. md_lines() turns the markdown back into the lines.
_MD_ROLE = ("h1", "h2", "h3", "h4", "h5", "h6", "li", "tr", "dt", "dd")
_MD_SEP = re.compile(r"\|(?: --- \|)+$")
_MD_ALT = re.compile(r"!\[(.*)\]\(\)$")
_MD_IMG = re.compile(r"\.(?:jpe?g|png|gif|webp|avif|svg)(?:[?#]|$)", re.I)


class _MdLine(str):
    """a visible line that knows its block: el = nearest h1-h6/li/tr/dt/dd element of its first visible text (None =
    plain), imgs = the <img> elements met since the previous line. Compares/hashes as the plain line."""


def _md_share_lines(t):
    """link_share_lines(t), each line an _MdLine. The roles come from a second walk of the tree link_share_lines has
    already marked, split the same way; if the two walks ever disagree, no line gets a role (plain = no loss)."""
    ll = link_share_lines(t)
    frags, out, cur, imgs = [], [], [], []

    def walk(e, el):
        el = e if e.tag in _MD_ROLE else el
        if isinstance(e.tag, str) and e.text:
            frags.append((e.text, el))
        for c in e:
            if c.tag == "img":
                frags.append((c, el))
            walk(c, el)
            if c.tail:
                frags.append((c.tail, el))
    walk(t, None)

    def flush():
        line = " ".join("".join(x for x, _ in cur).split()).strip(" |")
        if line:
            out.append((line, next((el for x, el in cur if x.replace("|", " ").strip()), None), imgs[:]))
            del imgs[:]
        cur.clear()
    for x, el in frags:
        if not isinstance(x, str):
            imgs.append(x)
            continue
        for i, p in enumerate(x.split("\n")):
            if i:
                flush()
            if p:
                cur.append((p, el))
    flush()
    ok = [x[0] for x in out] == [l for l, _ in ll]
    res = []
    for i, (l, k) in enumerate(ll):
        m = _MdLine(l)
        m.el, m.imgs = out[i][1:] if ok else (None, [])
        res.append((m, k))
    return res


def _md_plain(s):
    """one line of ex_template_prec_md -> its text line; None = blank or '| --- |'; ("alt", text) = a product image"""
    if not s.strip() or _MD_SEP.match(s):
        return None
    m = _MD_ALT.match(s)
    if m:
        return ("alt", m.group(1))
    if s.startswith("| ") and s.endswith(" |") and len(s) > 4:
        return s[2:-2]
    s = re.sub(r"^(?:#{1,6} |- |: )", "", s, count=1)
    return s[1:] if s[:1] == "\\" else s


def md_lines(md, alt=True):
    """ex_template_prec_md's markdown -> ex_template_prec's lines (+ the product-image alt texts when alt)"""
    out = []
    for s in (md or "").split("\n"):
        x = _md_plain(s)
        if isinstance(x, tuple):
            if alt:
                out.append(x[1])
        elif x is not None:
            out.append(x)
    return out


def _md_head(tr):
    cells = [c for c in tr if c.tag in ("td", "th")]
    return (tr.getparent() is not None and tr.getparent().tag == "thead") or (bool(cells) and all(c.tag == "th" for c in cells))


def _md_render(ll, h1):
    lines = [l for l, _ in ll]
    have, done, out = set(lines), set(), []
    hw = [w.strip(".,;:!?()[]\"'«»“”").lower() for w in (h1[0] if h1 else "").split()]
    name = {w for w in hw if re.search(r"\d", w)} | ({hw[0]} if hw and len(hw[0]) >= 4 else set())

    def put(pre, l, post=""):                     # decorate; a line that would not read back is escaped / left plain
        s = pre + l + post
        if _md_plain(s) != l:
            s = (pre + "\\" + l) if not post else ("\\" + l if _md_plain(l) != l else l)
        out.append(s)

    def alts(l):
        for img in getattr(l, "imgs", ()):
            a = " ".join((img.get("alt") or "").split())
            if a and a not in have and a not in done and name and not any(
                    x.get("href") and not _MD_IMG.search(x.get("href")) for x in img.iterancestors("a")) \
                    and any(re.search(r"(?<![\w-])%s(?![\w-])" % re.escape(w), a.lower()) for w in name):
                done.add(a)
                out.append("![%s]()" % a)
    role = lambda l: getattr(getattr(l, "el", None), "tag", None)
    table = lambda l: next(l.el.iterancestors("table"), None)
    i = 0
    while i < len(lines):
        l, r = lines[i], role(lines[i])
        if r == "tr":
            j, tb = i, table(l)
            while j < len(lines) and role(lines[j]) == "tr" and table(lines[j]) is tb:
                j += 1
            run = lines[i:j]
            hd = next((k for k, x in enumerate(run) if _md_head(x.el)), 0)
            if out and out[-1]:
                out.append("")
            for k, x in enumerate(run):
                if k == hd and hd and out[-1]:
                    out.append("")
                put("| " if k >= hd else "", x, " |" if k >= hd else "")
                if k == hd:
                    out.append("| " + " | ".join(["---"] * (x.count("|") + 1)) + " |")
            out.append("")
            for x in run:
                alts(x)
            i = j
            continue
        if r in _MD_ROLE[:6] and out and out[-1]:
            out.append("")
        put("#" * int(r[1]) + " " if r in _MD_ROLE[:6] else "- " if r == "li" else ": " if r == "dd" else "", l)
        alts(l)
        i += 1
    while out and not out[-1]:
        out.pop()
    return "\n".join(out)


def ex_template_prec_md(d, html):
    """Task 21 option (OFF): ex_template_prec's lines as structure-preserving markdown (content.py --md)"""
    return ex_template_prec(d, html, md=True)


EXTRACTORS = {"template_prec": ex_template_prec, "judge": ex_judge, "linkrun": ex_linkrun, "region": ex_region, "template_loc": ex_template_loc, "template_tags": ex_template_tags, "crawler": ex_crawler, "union": ex_union, "template": ex_template, "template2": ex_template2, "pruned": ex_pruned, "traf": ex_traf, "traf_recall": ex_traf_recall, "traf_precision": ex_traf_precision,
              "resiliparse": ex_resiliparse, "readability": ex_readability, "justext": ex_justext}
# Task 21: markdown scored on its read-back lines (the scorer's tokens ignore '#', '|', '-' anyway). template_prec_md =
# the kept lines only, so it must score exactly as template_prec (no content lost); _alt adds the product-image alt
# texts the extractor also sees (never in the gold's visible-line universe: they can only cost P)
EXTRACTORS.update({"template_prec_md": lambda d, html: "\n".join(md_lines(ex_template_prec_md(d, html), alt=False)),
                   "template_prec_md_alt": lambda d, html: "\n".join(md_lines(ex_template_prec_md(d, html)))})


# ------------------------------------------------------------------ gold
def load_gold():
    g = {}
    for f in glob.glob(os.path.join(GOLD, "gold_*.jsonl")):
        for l in io.open(f, encoding="utf-8"):
            if l.strip():
                x = json.loads(l); g[x["document_id"]] = x
    return g


def gold_text(did, g):
    lines = visible_lines(html_of(did))
    keep = set()
    for a, b in g.get("main_lines", []):
        keep.update(range(int(a), int(b) + 1))
    return "\n".join(lines[i - 1] for i in sorted(keep) if 1 <= i <= len(lines))


def write_views():
    os.makedirs(os.path.join(GOLD, "views"), exist_ok=True)
    for d in docs():
        lines = visible_lines(html_of(d["document_id"]))
        io.open(os.path.join(GOLD, "views", d["document_id"] + ".txt"), "w", encoding="utf-8").write(
            "URL: %s\nTITLE: %s\nCOMPANY: %s\nLINES: %d\n\n" % (d["url"], d["title"], d["company"], len(lines)) +
            "\n".join("%4d  %s" % (i, l[:400]) for i, l in enumerate(lines, 1)))
    print("views", len(docs()))


def score(names=None):
    gold = load_gold()
    D = [d for d in docs() if d["document_id"] in gold]
    names = names or list(EXTRACTORS)
    res = {}
    for n in names:
        per, byl, t0, empty = [], collections.defaultdict(list), time.time(), []
        for d in D:
            g = gold[d["document_id"]]
            d["gold_lang"] = g.get("language", "en")
            try:
                txt = EXTRACTORS[n](d, html_of(d["document_id"]))
            except Exception as e:
                txt = ""
            gt = gold_text(d["document_id"], g)
            if not tokens(gt):                 # 404 / nav-only page: nothing to recall, scored apart
                empty.append(len(tokens(txt)))
                continue
            r = prf(txt, gt)
            per.append((d["document_id"], r)); byl[g.get("language", "?")].append(r)
        m = lambda xs, i: sum(x[i] for x in xs) / max(1, len(xs))
        rs = [r for _, r in per]
        res[n] = {"docs": len(rs), "P": round(m(rs, 0), 3), "R": round(m(rs, 1), 3), "F1": round(m(rs, 2), 3),
                  "R>=0.95": sum(1 for r in rs if r[1] >= 0.95), "R<0.5": sum(1 for r in rs if r[1] < 0.5),
                  "sec": round(time.time() - t0, 1), "empty_gold_pages_tokens_emitted": empty,
                  "by_lang": {k: [len(v), round(m(v, 1), 3), round(m(v, 2), 3)] for k, v in sorted(byl.items())},
                  "worst": sorted(((round(r[1], 2), did) for did, r in per))[:5]}
        print("%-15s P %.3f R %.3f F1 %.3f | R>=.95 %d/%d  R<.5 %d | %.1fs" % (n, res[n]["P"], res[n]["R"], res[n]["F1"],
              res[n]["R>=0.95"], len(rs), res[n]["R<0.5"], res[n]["sec"]))
    io.open(os.path.join(HERE, "out", "webbench_%s.json" % SET), "w", encoding="utf-8").write(json.dumps(res, indent=1, ensure_ascii=False))
    return res


def demo():
    assert tokens("Die Reichweite: 40 km") == ["die", "reichweite", "40", "km"]
    assert tokens("사거리 40") == ["사거", "거리", "40"]
    P, R, F = prf("a b c", "a b d")
    assert round(P, 3) == 0.667 and round(R, 3) == 0.667
    assert visible_lines("<html><body><nav>Home</nav><p>Hello <b>world</b></p><table><tr><td>Range</td><td>40 km</td></tr></table></body></html>") \
        == ["Home", "Hello world", "Range | 40 km"], visible_lines("<html><body><nav>Home</nav><p>Hello <b>world</b></p><table><tr><td>Range</td><td>40 km</td></tr></table></body></html>")
    print("webbench demo ok")


# ------------------------------------------------------------------ Task 21 fixture: an INVENTED site (no real page)
_MD_HOST = "https://norvik-defence.example"
_MD_CHROME = {"en": ('<header class="site-header"><a href="/en/"><img src="/img/logo.svg" alt="Norvik Defence"></a>'
                     '<nav class="main-nav"><ul><li><a href="/en/products/">Products</a></li><li><a href="/en/services/">'
                     'Services</a></li><li><a href="/en/news/">News</a></li><li><a href="/en/careers/">Careers</a></li>'
                     '<li><a href="/en/contact/">Contact</a></li></ul></nav><div class="lang-switch"><a href="/de/">Deutsch'
                     '</a></div></header>',
                     '<footer><p>Norvik Defence Systems AS, Industrivegen 12, Kalvik</p><ul><li><a href="/en/privacy/">'
                     'Privacy</a></li><li><a href="/en/imprint/">Imprint</a></li></ul><p>© 2026 Norvik Defence Systems</p>'
                     '</footer><div id="cookie-banner" class="cookie-consent"><p>We use cookies to improve your experience. '
                     'By continuing you accept our cookie policy.</p><button>Accept all</button><button>Settings</button></div>',
                     '<div class="cta-box"><p>Need more information?</p><a href="/en/contact/">Request a quote</a></div>'),
              "de": ('<header class="site-header"><a href="/de/"><img src="/img/logo.svg" alt="Norvik Defence"></a>'
                     '<nav class="main-nav"><ul><li><a href="/de/produkte/">Produkte</a></li><li><a href="/de/leistungen/">'
                     'Leistungen</a></li><li><a href="/de/presse/">Presse</a></li><li><a href="/de/karriere/">Karriere</a>'
                     '</li><li><a href="/de/kontakt/">Kontakt</a></li></ul></nav><div class="lang-switch"><a href="/en/">'
                     'English</a></div></header>',
                     '<footer><p>Norvik Defence Systems AS, Industrivegen 12, Kalvik</p><ul><li><a href="/de/datenschutz/">'
                     'Datenschutz</a></li><li><a href="/de/impressum/">Impressum</a></li></ul><p>© 2026 Norvik Defence '
                     'Systems</p></footer><div id="cookie-banner" class="cookie-consent"><p>Wir verwenden Cookies, um Ihr '
                     'Erlebnis zu verbessern. Mit der weiteren Nutzung akzeptieren Sie unsere Cookie-Richtlinie.</p><button>'
                     'Alle akzeptieren</button><button>Einstellungen</button></div>',
                     '<div class="cta-box"><p>Weitere Informationen gewünscht?</p><a href="/de/kontakt/">Angebot anfordern'
                     '</a></div>')}
_MD_PAGES = {   # id: (lang, path, title, body) -- 3 spec sheets: nav, footer, cookie banner, spec <table> with a header
    # row, <dl>, bullet list, product <img alt>, related-products carousel
    "md_zorya7": ("en", "/en/products/zorya-7", "Zorya-7",
                  '<div class="breadcrumb"><a href="/en/">Home</a> › <a href="/en/products/">Products</a> › Zorya-7</div>'
                  '<article class="product"><h1>Zorya-7 Self-Propelled Howitzer</h1><figure class="hero"><img '
                  'src="/img/products/zorya-7-hero.jpg" alt="Zorya-7 155 mm self-propelled howitzer firing at the Kalvik '
                  'test range"><figcaption>Zorya-7 during live-fire trials.</figcaption></figure><p>The Zorya-7 is a tracked '
                  '155 mm self-propelled howitzer developed by Norvik Defence Systems for fast, protected fire support. Its '
                  'automated loader and digital fire control let a small crew fire within seconds of stopping.</p><h2>Key '
                  'features</h2><ul><li>Automated loader with 40 ready rounds</li><li>3-man crew in an armoured cab</li><li>'
                  'Shoot-and-scoot in under 60 seconds</li></ul><h2>Specifications</h2><table class="spec-table"><thead><tr>'
                  '<th>Parameter</th><th>Value</th></tr></thead><tbody><tr><td>Calibre</td><td>155 mm / 52 cal</td></tr><tr>'
                  '<td>Maximum range</td><td>45 km</td></tr><tr><td>Rate of fire</td><td>9 rpm</td></tr><tr><td>Combat '
                  'weight</td><td>27 t</td></tr></tbody></table><h3>Mobility</h3><dl class="specs"><dt>Engine</dt><dd>MTU 8V '
                  '199, 600 hp</dd><dt>Road speed</dt><dd>67 km/h</dd><dt>Road range</dt><dd>480 km</dd></dl></article>'
                  '{cta}<section class="product-carousel"><h2>You may also like</h2><div class="slide"><a href="/en/products/'
                  'harran-8x8"><img src="/img/harran.jpg" alt="Harran 8x8 wheeled vehicle"><span>Harran 8x8</span></a></div>'
                  '<div class="slide"><a href="/en/products/kestrel-l50"><img src="/img/kestrel.jpg" alt="Kestrel L50 gun">'
                  '<span>Kestrel L50</span></a></div><div class="slide"><a href="/en/products/varda"><img src="/img/varda.jpg" '
                  'alt="Varda mortar carrier"><span>Varda mortar carrier</span></a></div></section>'),
    "md_harran": ("en", "/en/products/harran-8x8", "Harran 8x8",
                  '<div class="breadcrumb"><a href="/en/">Home</a> › Harran 8x8</div><h1>Harran 8x8 Armoured Vehicle Family'
                  '</h1><p><img src="/img/products/harran-8x8.jpg" alt="Harran 8x8 IFV with the Kestrel L50 turret"></p><p>'
                  'Harran 8x8 is a family of wheeled armoured vehicles for troop transport and direct fire support, built on '
                  'one common hull.</p><h2>Variants</h2><table><tr><td colspan="4">Harran 8x8 variants</td></tr><tr><th>'
                  'Variant</th><th>Combat weight</th><th>Crew + troops</th><th>Main armament</th></tr><tr><td>Harran 8x8 APC'
                  '</td><td>28 t</td><td>3 + 8</td><td>12.7 mm RWS</td></tr><tr><td>Harran 8x8 IFV</td><td>30 t</td><td>3 + '
                  '6</td><td>Kestrel L50 105 mm gun</td></tr></table><h2>Automotive data</h2><dl><dt>Engine power</dt><dd>550 '
                  'kW</dd><dt>Maximum speed</dt><dd>105 km/h</dd><dt>Operational range</dt><dd>800 km</dd></dl><h2>Options'
                  '</h2><ul><li>Amphibious kit</li><li>Mine-protected seats</li><li>Remote weapon station</li></ul>{cta}'
                  '<section class="related-products"><h2>Related products</h2><div class="slide"><a href="/en/products/'
                  'zorya-7"><img src="/img/zorya.jpg" alt="Zorya-7 howitzer"><span>Zorya-7</span></a></div><div class="slide">'
                  '<a href="/en/products/varda"><img src="/img/varda.jpg" alt="Varda"><span>Varda mortar carrier</span></a>'
                  '</div></section>'),
    "md_kestrel": ("de", "/de/produkte/kestrel-l50", "Kestrel L50",
                   '<div class="breadcrumb"><a href="/de/">Start</a> › Kestrel L50</div><h1>Kestrel L50 Waffenanlage</h1>'
                   '<div class="gallery"><img src="/img/products/kestrel-l50.jpg" alt="Kestrel L50 105-mm-Kanone im Turm des '
                   'Harran 8x8"></div><p>Die Kestrel L50 ist eine stabilisierte 105-mm-Kanone für mittelschwere Radfahrzeuge. '
                   'Sie wird im Harran 8x8 IFV eingesetzt.</p><h2>Leistungsmerkmale</h2><ul><li>Stabilisiert für Feuer aus '
                   'der Fahrt</li><li>Ladeautomat mit 12 Schuss</li></ul><p>- Optional: Wärmebildkamera der dritten Generation'
                   '</p><h2>Technische Daten</h2><table><tr><td><b>Merkmal</b></td><td><b>Wert</b></td></tr><tr><td>Kaliber'
                   '</td><td>105 mm</td></tr><tr><td>Rohrlänge</td><td>L/50</td></tr><tr><td>Kadenz</td><td>8 Schuss/min</td>'
                   '</tr><tr><td>Gewicht</td><td>2,8 t</td></tr></table><dl><dt>Rohrrücklauf</dt><dd>520 mm</dd><dt>'
                   'Höhenrichtbereich</dt><dd>-10° bis +42°</dd></dl>{cta}<aside class="more-products"><h2>Weitere Produkte'
                   '</h2><a href="/de/produkte/zorya-7"><img src="/img/zorya.jpg" alt="Zorya-7 Panzerhaubitze"><span>Zorya-7'
                   '</span></a><a href="/de/produkte/varda"><img src="/img/varda.jpg" alt="Varda"><span>Varda</span></a>'
                   '</aside>')}
_MD_REFS = [(lg, p, t, b) for lg, rows in (
    ("en", [("/en/about/", "About us", "<h1>About Norvik</h1><p>Norvik Defence Systems builds artillery and armoured "
             "vehicles in Kalvik.</p>{cta}"),
            ("/en/news/winter-trials/", "Winter trials", "<h1>Winter trials completed</h1><p>The Varda mortar carrier "
             "finished winter trials in the north.</p>"),
            ("/en/news/new-plant/", "New plant", "<h1>New plant opens</h1><p>A second assembly hall opened in Kalvik.</p>"),
            ("/en/products/varda/", "Varda", "<h1>Varda Mortar Carrier</h1><p>A 120 mm mortar on a tracked chassis.</p>{cta}")]),
    ("de", [("/de/ueber-uns/", "Über uns", "<h1>Über Norvik</h1><p>Norvik Defence Systems baut Artillerie und gepanzerte "
             "Fahrzeuge in Kalvik.</p>{cta}"),
            ("/de/presse/wintererprobung/", "Wintererprobung", "<h1>Wintererprobung abgeschlossen</h1><p>Der Varda "
             "Mörserträger hat die Wintererprobung beendet.</p>"),
            ("/de/presse/neues-werk/", "Neues Werk", "<h1>Neues Werk eröffnet</h1><p>In Kalvik wurde eine zweite "
             "Montagehalle eröffnet.</p>"),
            ("/de/produkte/varda/", "Varda", "<h1>Varda Mörserträger</h1><p>Ein 120-mm-Mörser auf Kettenfahrwerk.</p>{cta}")]))
    for p, t, b in rows]


def _md_html(lang, title, body):
    head, foot, cta = _MD_CHROME[lang]
    return ('<!DOCTYPE html><html lang="%s"><head><meta charset="utf-8"><title>%s | Norvik Defence</title></head><body>%s'
            '<main>%s</main>%s</body></html>' % (lang, title, head, body.replace("{cta}", cta), foot))


def md_fixture(ref_dir):
    """write the invented site's 8 reference pages into ref_dir (index.jsonl + <id>.html); returns the 3 test pages as
    [(doc, html)] with doc = {document_id, url, title, main_text}"""
    os.makedirs(ref_dir, exist_ok=True)
    with io.open(os.path.join(ref_dir, "index.jsonl"), "w", encoding="utf-8") as f:
        for i, (lg, p, t, b) in enumerate(_MD_REFS):
            did = "md_ref%d" % i
            io.open(os.path.join(ref_dir, did + ".html"), "w", encoding="utf-8").write(_md_html(lg, t, b))
            f.write(json.dumps({"document_id": did, "url": _MD_HOST + p}) + "\n")
    return [({"document_id": k, "url": _MD_HOST + p, "title": t, "main_text": ""}, _md_html(lg, t, b))
            for k, (lg, p, t, b) in _MD_PAGES.items()]


def demo_md():
    """Task 21 self-check, offline, on the invented site: (1) ex_template_prec / content_text output is byte-identical to
    origin/main's (sha1 captured from origin/main before this change); (2) ex_template_prec_md reads back to exactly
    ex_template_prec's lines + the listed alt lines, with headings / list items / tables / <dd> / escapes in place; (3)
    extract_v3 (s_cws flags, fake ollama_json that proposes every wanted value found in its passage) locates + verifies
    the same cells on the markdown as on the plain text, table-row values with their markdown row as evidence"""
    import contextlib, hashlib, importlib, shutil, tempfile
    W = importlib.import_module("webbench")            # the module content.py uses, also when this file runs as a script
    import content, extract_v3 as X, extract_values as E, products
    tmp, old = tempfile.mkdtemp(), (W.REF, W._PREC_REFS, W._TPL5, content.MD, products.INDEX, products.CANON, E.ollama_json)
    sha = lambda s: hashlib.sha1(s.encode("utf-8")).hexdigest()[:12]
    ST = [{"title": "Specifications", "rows": [{"label": "Calibre", "value": "155 mm / 52 cal"},
                                              {"label": "Elevation", "value": "-3° to +70°"}]}]
    main = {"md_zorya7": ("2a90bc0efd27", "39cf8e6e9789"), "md_harran": ("1dd100bd48ec",) * 2,
            "md_kestrel": ("a66f446b987c",) * 2}
    alts = {"md_zorya7": ["Zorya-7 155 mm self-propelled howitzer firing at the Kalvik test range"],
            "md_harran": ["Harran 8x8 IFV with the Kestrel L50 turret"],
            "md_kestrel": ["Kestrel L50 105-mm-Kanone im Turm des Harran 8x8"]}
    try:
        pages = W.md_fixture(os.path.join(tmp, "ref"))
        W.REF, W._PREC_REFS, W._TPL5 = os.path.join(tmp, "ref"), None, {}
        md, plain = {}, {}
        for d, html in pages:
            k, st = d["document_id"], (ST if d["document_id"] == "md_zorya7" else None)
            plain[k] = W.ex_template_prec(d, html)
            content.MD = False
            assert (sha(plain[k]), sha(content.content_text(d, html, st))) == main[k], k        # OFF = origin/main
            md[k] = W.ex_template_prec_md(d, html)
            assert W.md_lines(md[k], alt=False) == plain[k].split("\n"), k                      # the same lines ...
            assert [x for x in W.md_lines(md[k]) if x not in plain[k].split("\n")] == alts[k], k  # ... + alt lines
            content.MD = True
            assert content.content_text(d, html, st) == md[k] + ("\n\nTable: Specifications\nElevation: -3° to +70°"
                                                                 if st else ""), k             # --md plumbing
        z, h, g = md["md_zorya7"], md["md_harran"], md["md_kestrel"]
        assert z.startswith("# Zorya-7 Self-Propelled Howitzer\nZorya-7 during live-fire trials.\n![Zorya-7 155 mm")
        assert "\n## Key features\n- Automated loader with 40 ready rounds\n- 3-man crew in an armoured cab\n" in z
        assert "\n\n| Parameter | Value |\n| --- | --- |\n| Calibre | 155 mm / 52 cal |\n" in z and "\nRoad speed\n: 67 km/h\n" in z
        assert "Harran 8x8 wheeled vehicle" not in z and "Norvik Defence" not in z.split("\n")    # carousel / logo images
        assert "\nHarran 8x8 variants\n\n| Variant | Combat weight | Crew + troops | Main armament |\n| --- | --- | --- | --- |\n" in h
        assert "\n| Merkmal | Wert |\n| --- | --- |\n| Kaliber | 105 mm |\n" in g                  # no <th>: the first row
        assert "\\- Optional: Wärmebildkamera der dritten Generation" in g.split("\n")          # escaped, reads back
        # extract_v3 on the markdown: every value is still located (pipes / '- ' / ': ' / '#' do not break locate_evidence)
        json.dump([{"family": "Zorya-7", "sector": "materiel.weapons.artillery.self_propelled"}],
                  io.open(os.path.join(tmp, "idx.json"), "w"))
        products.INDEX, products.CANON = os.path.join(tmp, "idx.json"), os.path.join(tmp, "none.json")
        want = [("calibre_mm", "155 mm"), ("maximum_range_km", "45 km"), ("rate_of_fire_rpm", "9 rpm"),
                ("vehicle_weight_t", "27 t"), ("engine_power_hp", "600 hp"), ("max_speed_kmh", "67 km/h"),
                ("operational_range_km", "480 km"), ("crew_count", "3-man crew")]
        E.ollama_json = lambda prompt, schema: {"products": [{"product": "Zorya-7", "maker": "", "specs": [
            {"parameter": p, "value_text": v} for p, v in want if v in prompt.split('PASSAGE:\n"""', 1)[1].split('"""', 1)[0]]}]}
        res = {}
        for kind, text in (("plain", plain["md_zorya7"]), ("md", z)):
            with contextlib.redirect_stdout(io.StringIO()):
                res[kind] = X.run([{"document_id": "md_zorya7", "url": _MD_HOST + "/en/products/zorya-7", "language": "en",
                                    "text": text}], 1, glossary=True, single=True, strings="compatible_weapon_system")
        got = {k: sorted((c["param"], c["value_text"]) for c in r["cells"]) for k, r in res.items()}
        assert got["md"] == got["plain"] == sorted(want), got
        assert not [r for r in res["md"]["rejects"] if r["why"] == "value_not_located"], res["md"]["rejects"]
        ev = {c["param"]: c["evidence"] for c in res["md"]["cells"]}
        assert "| Maximum range | 45 km |" in ev["maximum_range_km"] and "| Combat weight | 27 t |" in ev["vehicle_weight_t"]
        assert ev["max_speed_kmh"] == "Road speed\n: 67 km/h" and "- 3-man crew in an armoured cab" in ev["crew_count"]
        print("  md evidence  maximum_range_km: %r" % ev["maximum_range_km"])
        print("  md evidence  max_speed_kmh: %r | crew_count: %r" % (ev["max_speed_kmh"], ev["crew_count"]))
        print("webbench md demo ok: 3 invented pages, OFF == origin/main (sha1), md reads back to the same lines + %d alt "
              "lines, %d/%d values located + verified in md (plain: %d)" % (sum(map(len, alts.values())), len(got["md"]),
                                                                            len(want), len(got["plain"])))
    finally:
        W.REF, W._PREC_REFS, W._TPL5, content.MD, products.INDEX, products.CANON, E.ollama_json = old
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    demo()
    if "--md-demo" in sys.argv:
        demo_md()
    if "--views" in sys.argv:
        write_views()
    if "--score" in sys.argv:
        score([a for a in sys.argv[1:] if not a.startswith("-")] or None)
