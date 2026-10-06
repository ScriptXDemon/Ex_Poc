# -*- coding: utf-8 -*-
"""extraction2 / PROVEN publish date per document (P0 output 3: fetch != publish).

No single source is trusted. Four independent witnesses, each measured on its own:
  U  the date in the URL path (/2025/03/12/, 2025-03-12)          -- rarely wrong when present
  H  htmldate on the raw html (meta tags, JSON-LD, <time>)          -- original_date=True
  C  the crawler's documents.published_at                           -- can be the FETCH date
  T  a dateline in the first lines of the text (multilingual)       -- dateparser
Decision: listing/index pages get NO date (their "date" is any year on the page); future dates
are dropped; the date with the most independent witnesses wins (day level, else month); C is
refused when it equals the fetch day and nothing else agrees. Every row records its witnesses,
so "proven" (>= 2 agree, or a day-level URL date) is distinguishable from "single-source".

    python extraction2/dates.py            # resolve all docs -> out/doc_dates.json + source audit
"""
import collections, datetime, io, json, os, re, sys, warnings

HERE = os.path.dirname(os.path.abspath(__file__))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
warnings.filterwarnings("ignore")

TODAY = datetime.date.today()
CORPUS = os.path.join(HERE, "corpus")


def is_article_url(url):
    """an article's own slug outranks every listing cue: a last segment of >=6 words, or >=3 words under a
    /YYYY/MM/ path (gold: '/archives/.../seven-eu-member-states-order-155mm-shells-...' and an opex360
    /2021/03/16/<slug> page with 6 '...' were skipped as listings -- 4 facts + 5 events lost)"""
    from urllib.parse import urlsplit
    path = (urlsplit(url or "").path or "").rstrip("/").lower()
    words = len([w for w in re.split(r"[-_]+", path.rsplit("/", 1)[-1]) if w])
    return words >= 6 or (words >= 3 and bool(re.search(r"/(19|20)\d\d/\d\d?/", path + "/")))


def is_listing(url):
    from urllib.parse import urlsplit
    if is_article_url(url):
        return False
    u = urlsplit(url or "")
    path = (u.path or "").rstrip("/").lower()
    if not path:
        return True
    for seg in ("/tag/", "/tags/", "/category/", "/categories/", "/label/", "/author/", "/authors/",
                "/topic/", "/topics/", "/section/", "/archive/", "/archives/", "/search/", "/page/"):
        if seg in path + "/":
            return True
    q = (u.query or "").lower()
    if re.search(r"(?:^|&)(?:page|start|offset|l|p|pg)=\d", q):
        return True                                      # paginated index (?start=90, ?l=50)
    last = path.rsplit("/", 1)[-1]
    return last in ("news", "media", "press", "press-releases", "news-releases", "releases", "blog",
                    "newsroom", "latest", "archive") or last.endswith("-in-media")


def is_listing_doc(url, text):
    """URL shape OR a body made of teasers: >= 5 truncation marks ("…" / "...") is a list of other
    stories, whatever the language (review: 16 teaser pages were extracted as articles)."""
    # a lone ellipsis or EXACTLY three dots -- not a dot-leader run ("......") in a magazine's
    # table of contents (two AMR issues were misread as listings)
    return is_listing(url) or (not is_article_url(url) and
                               len(re.findall(r"(?<!\.)(?:…|\.\.\.)(?![.…])", text or "")) >= 5)


_URL_DAY = re.compile(r"(?<!\d)(20[0-3]\d|199\d)[/_-](0?[1-9]|1[0-2])[/_-](0?[1-9]|[12]\d|3[01])(?!\d)")
_URL_MON = re.compile(r"/(20[0-3]\d|199\d)/(0[1-9]|1[0-2])/")
_URL_COMPACT = re.compile(r"(?<!\d)(20[0-3]\d)(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])(?!\d)")


def _d(y, m, d=None):
    try:
        x = datetime.date(int(y), int(m), int(d or 1))
    except ValueError:
        return None
    return None if x > TODAY else (x.isoformat() if d else x.isoformat()[:7])


def url_date(url):
    from urllib.parse import urlsplit
    path = urlsplit(url or "").path
    m = _URL_DAY.search(path) or _URL_COMPACT.search(path)
    if m:
        return _d(*m.groups())
    m = _URL_MON.search(path)
    return _d(*m.groups()) if m else None


def html_date(html):
    if not html:
        return None
    # htmldate reads the canonical / og:url link's date FIRST -- that is the URL witness again, not an
    # independent one (review: 64/69 "agreements" were this). Strip them so H = meta/JSON-LD/<time>.
    html = re.sub(r"<link[^>]+rel=[\"']?canonical[^>]*>", " ", html, flags=re.I)
    html = re.sub(r"<meta[^>]+(?:og:url|twitter:url)[^>]*>", " ", html, flags=re.I)
    try:
        from htmldate import find_date
        d = find_date(html, original_date=True, extensive_search=False, outputformat="%Y-%m-%d",
                      max_date=TODAY.isoformat())
    except Exception:
        return None
    return d


def text_date(text, lang):
    """a dateline: the first full date in the opening lines (the dateline leads the page)."""
    head = (text or "")[:500]
    try:
        from dateparser.search import search_dates
        found = search_dates(head, languages=[lang] if lang and lang not in ("und",) else None,
                             settings={"REQUIRE_PARTS": ["day", "month", "year"],
                                       "PREFER_DATES_FROM": "past", "RETURN_AS_TIMEZONE_AWARE": False})
    except Exception:
        found = None
    for s, dt in found or []:
        if not re.search(r"\d{4}", s):                    # a bare "12 March" is not a dateline
            continue
        d = dt.date()
        if datetime.date(1995, 1, 1) <= d <= TODAY:
            return d.isoformat()
    return None


def resolve(url, text, lang, crawler_pub, fetched, html, html_witness=None):
    """html_witness: the page's HTML date read earlier (content.py keeps no html, only this)"""
    if is_listing_doc(url, text):
        return {"date": None, "status": "listing", "witnesses": {}}
    w = {"U": url_date(url), "H": html_date(html) if html else html_witness, "T": text_date(text, lang),
         "C": (crawler_pub or "")[:10] or None}
    if w["C"] and w["C"] > TODAY.isoformat():
        w["C"] = None
    fetch_day = (fetched or "")[:10]
    days = collections.Counter(v for v in w.values() if v and len(v) == 10)
    if w["C"] and w["C"] == fetch_day and days[w["C"]] < 2:
        w["C"] = None                                      # the crawler stamped its own fetch day
        days = collections.Counter(v for v in w.values() if v and len(v) == 10)
    if days:
        best, n = max(days.items(), key=lambda kv: (kv[1], kv[0] == w["U"], kv[0] == w["H"]))
        # a day-level URL date outranks a lone disagreeing witness
        if w["U"] and len(w["U"]) == 10 and days[w["U"]] >= n:
            best, n = w["U"], days[w["U"]]
        proven = n >= 2 or best == w["U"]
        return {"date": best, "status": "proven" if proven else "single", "witnesses": w}
    months = collections.Counter(v[:7] for v in w.values() if v)
    if months:
        best, n = months.most_common(1)[0]
        return {"date": best, "status": "proven-month" if n >= 2 else "single-month", "witnesses": w}
    return {"date": None, "status": "undated", "witnesses": w}


def main():
    docs = {json.loads(l)["document_id"]: json.loads(l)
            for l in io.open(os.path.join(CORPUS, "docs.jsonl"), encoding="utf-8") if l.strip()}
    meta = {json.loads(l)["document_id"]: json.loads(l)
            for l in io.open(os.path.join(CORPUS, "meta.jsonl"), encoding="utf-8") if l.strip()}
    out, status = {}, collections.Counter()
    agree = collections.defaultdict(lambda: [0, 0])        # source -> [agrees with U, compared]
    for did, d in docs.items():
        m = meta.get(did, {})
        hp = os.path.join(CORPUS, "html", did + ".html")
        html = io.open(hp, encoding="utf-8").read() if os.path.exists(hp) else None
        r = resolve(d.get("url"), d.get("text"), d.get("language"), m.get("published_at"),
                    m.get("fetched_at"), html)
        out[did] = r; status[r["status"]] += 1
        u = r["witnesses"].get("U")
        if u and len(u) == 10:
            for s in ("H", "C", "T"):
                v = r["witnesses"].get(s)
                if v:
                    agree[s][1] += 1; agree[s][0] += v == u
    cp = os.path.join(CORPUS, "content", "docs.jsonl")
    if os.path.exists(cp):                           # content-step docs (rival sites): no html kept
        for l in io.open(cp, encoding="utf-8"):
            d = json.loads(l)
            if d["document_id"] in out:
                continue
            r = resolve(d.get("url"), d.get("text"), d.get("language"), d.get("published_at"), None, None,
                        d.get("html_date"))
            out[d["document_id"]] = r; status[r["status"]] += 1
    io.open(os.path.join(HERE, "out", "doc_dates.json"), "w", encoding="utf-8").write(
        json.dumps(out, indent=1, ensure_ascii=False))
    print("docs %d | %s" % (len(out), dict(status)))
    print("witness accuracy vs day-level URL dates:",
          {s: "%d/%d" % tuple(v) for s, v in agree.items()})
    print("wrote out/doc_dates.json")


def demo():
    """resolve()/is_listing_doc() on hand-made rows: no corpus, no html (H comes in as html_witness)"""
    r = lambda url, crawler, fetched, hw, text="Body.": resolve(url, text, "en", crawler, fetched, None, hw)
    # witness vote: H + C agree -> proven; a day-level URL date outranks a lone disagreeing H
    assert r("https://x.com/a/story", "2025-03-12T10:00", "2025-06-01T08:00", "2025-03-12")["status"] == "proven"
    x = r("https://x.com/2025/03/12/story", None, None, "2025-03-10")
    assert (x["date"], x["status"]) == ("2025-03-12", "proven"), x
    assert r("https://x.com/a/story", "2025-03-12", None, "2025-03-14")["date"] == "2025-03-14"   # tie: H over C
    # the crawler's fetch-day stamp is refused alone, kept when another witness agrees
    x = r("https://x.com/a/story", "2025-06-01T09:00", "2025-06-01T08:00", None)
    assert (x["date"], x["status"], x["witnesses"]["C"]) == (None, "undated", None), x
    assert r("https://x.com/a/story", "2025-06-01T09:00", "2025-06-01T08:00", "2025-06-01")["date"] == "2025-06-01"
    x = r("https://x.com/2025/03/story", "2025-06-01", "2025-06-01", None)
    assert (x["date"], x["status"]) == ("2025-03", "single-month"), x
    # listing / teaser pages get no date, whatever the witnesses say
    for u in ("https://x.com", "https://x.com/news/", "https://x.com/tag/k9/", "https://x.com/list?page=2",
              "https://x.com/en/press-releases"):
        assert r(u, "2025-03-12", None, "2025-03-12") == {"date": None, "status": "listing", "witnesses": {}}, u
    assert r("https://x.com/a/story", "2025-03-12", None, None, "One... Two... Three... Four… Five...")["status"] == "listing"
    toc = "Contents...... 4\nLynx...... 9\nK9...... 12\nCAESAR...... 20\nPULS...... 31"      # dot leaders
    assert not is_listing_doc("https://x.com/a/story", toc) and not is_listing("https://x.com/2025/03/12/story")
    # an article's own slug outranks /archives/ and a teaser-like "..." count; category slugs stay listings
    assert not is_listing("https://ar.com/archives/land-2023/seven-eu-member-states-order-155mm-shells-through-eda")
    assert not is_listing_doc("https://o.com/2021/03/16/comme-le-scaf-le-projet", "a... b... c... d... e... f...")
    assert is_listing("https://ar.com/focus/army/defence-security-industry-technology?start=90")
    assert is_listing("https://x.com/tag/leonardo-drs") and is_listing("https://x.com/author/jreed")
    # future dates are dropped from every witness that can carry one
    assert url_date("https://x.com/2099/01/01/story") is None and url_date("https://x.com/2099/01/") is None
    x = r("https://x.com/2099/01/01/story", "2099-01-01", None, None)
    assert (x["date"], x["witnesses"]["U"], x["witnesses"]["C"]) == (None, None, None), x
    assert r("https://x.com/2099/01/01/story", "2099-01-01", None, "2025-03-12")["date"] == "2025-03-12"
    assert url_date("https://x.com/news/20250312-k9") == "2025-03-12" and url_date("https://x.com/2025/02/30/x") is None
    print("dates demo ok")


if __name__ == "__main__":
    demo() if "--demo" in sys.argv else main()
