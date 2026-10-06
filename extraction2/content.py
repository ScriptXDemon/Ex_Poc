# -*- coding: utf-8 -*-
"""Production CONTENT step: raw HTML -> the page's main content, any language -> extractor corpus.

Method = the held-out winner of webbench.py (gold/web2, 77 unseen intel pages): every visible line,
minus <nav>/<footer>/role=navigation|contentinfo subtrees, minus the site's TEMPLATE (lines recurring on
>=30% of ~25 same-site, same-locale reference pages): R 0.988 / P 0.852. --judge adds the local-LLM pass
over lines no precise extractor vouched for: P 0.900 / R 0.982 (~3 s/page GPU). Crawler spec_tables
rows not already in the text are appended as "label: value" lines (docprep.spec_blocks).

Reads the DC corpus READ-ONLY, per id, in small batches (never main_text/html in an aggregate).

    python extraction2/content.py --per-company 20 [--judge]   # -> corpus/content/docs.jsonl + stats
"""
import argparse, collections, glob, io, json, os, random, sys, time
from urllib.parse import urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(HERE, "corpus"))
import webbench as W, docprep, dates
OUT = os.path.join(HERE, "corpus", "content")


def host_of(url):
    return urlparse(url).netloc.lower().removeprefix("www.")


def ensure_refs(c, hosts, exclude, n=25):
    """a template needs ~25 pages of the same site; pull them once into the shared REF store"""
    idx_p = os.path.join(W.REF, "index.jsonl")
    have = collections.Counter(host_of(json.loads(l)["url"]) for l in io.open(idx_p, encoding="utf-8"))
    added = 0
    for h in sorted(hosts):
        if have[h] >= n:
            continue
        ids = [r[0] for r in c.execute("select document_id from documents where source_id in (%s, %s) "
                                       "and html_len between 1 and 3000000 limit 400", (h, "www." + h))]
        random.Random(h).shuffle(ids)
        ids = [i for i in ids if i not in exclude][: n - have[h]]
        for b in range(0, len(ids), 5):
            for did, url, html in c.execute("select document_id, url, html from documents where document_id = any(%s)",
                                            (ids[b:b + 5],)):
                io.open(os.path.join(W.REF, did + ".html"), "w", encoding="utf-8").write(html)
                io.open(idx_p, "a", encoding="utf-8").write(json.dumps({"document_id": did, "url": url}) + "\n")
                added += 1
    return added


METHOD = "template_prec"     # webbench.ex_template_prec: web2 held-out P 0.914 R 0.964 (template_tags P 0.848 R 0.966)
STRUCTURED = False           # main() --structured (Task 22, OFF): + structured.py rows not already in the text


MD = False                   # --md (Task 21, OFF): the same kept lines as markdown (webbench.ex_template_prec_md)


def content_text(d, html, spec_tables, judge=False):
    t = W.ex_judge(d, html) if judge else (W.ex_template_prec_md if MD else W.ex_template_prec)(d, html)
    t = "\n\n".join([t] + docprep._novel(docprep.spec_blocks(spec_tables), t))
    return __import__("structured").append_novel(t, html) if STRUCTURED else t


def make_row(m, did, url, title, text, html, st, judge=False, err=None):
    """one content row (the schema main() and the full run share); a failure is recorded, never raised"""
    new = ""
    if html and not err:
        try:
            new = content_text({"document_id": did, "url": url, "title": title, "main_text": text or ""}, html, st, judge)
        except Exception as e:
            err = type(e).__name__
    try:
        hd = dates.html_date(html) if html else None
    except Exception:
        hd = None
    return {"html_date": hd,
            "document_id": did, "url": url, "title": title, "company": m["company"],
            "language": m["language"], "published_at": m["published_at"], "doc_type": m["doc_type"],
            "fills_gap": m["fills_gap"], "source": m["source"], "text": new,
            "crawler_len": len(text or ""), "content_len": len(new), "error": err,
            "method": "template_tags+judge" if judge else METHOD}


def sample(per_company, seed=7):
    """worklist docs first (typed + they fill UI gaps), then best30 -- half of those from the 200-1000
    char band the crawler left suspiciously thin, so that band gets explained"""
    R = [json.loads(l) for l in io.open(os.path.join(HERE, "corpus", "manifest.jsonl"), encoding="utf-8")]
    rnd = random.Random(seed)
    by = collections.defaultdict(list)
    for r in R:
        if r["has_html"] and not (r["url"] or "").lower().split("?")[0].endswith((".pdf", ".jpg", ".png")):
            by[r["company"]].append(r)
    pick = []
    for comp, xs in sorted(by.items()):
        rnd.shuffle(xs)
        wl = [x for x in xs if x["fills_gap"]] + [x for x in xs if x["source"] == "worklist" and not x["fills_gap"]]
        thin = [x for x in xs if x["source"] == "best30" and 200 <= (x["text_len"] or 0) < 1000]
        rest = [x for x in xs if x["source"] == "best30" and not 200 <= (x["text_len"] or 0) < 1000]
        k = per_company
        chosen = wl[: k // 2]
        chosen += thin[: (k - len(chosen)) // 2]
        chosen += rest[: k - len(chosen)]
        chosen += [x for x in thin + wl if x not in chosen][: k - len(chosen)]
        pick += chosen[:k]
    return pick


def main():
    global STRUCTURED, METHOD
    import dc
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-company", type=int, default=20)
    ap.add_argument("--judge", action="store_true")
    ap.add_argument("--md", action="store_true", help="Task 21 (OFF): markdown content (rows tagged template_prec_md)")
    ap.add_argument("--structured", action="store_true", help="append structured.py rows not in the text (rows tagged)")
    a = ap.parse_args()
    global MD, METHOD, STRUCTURED     # without `global`, --structured only set a local and did nothing
    MD, METHOD = a.md, "template_prec_md" if a.md else METHOD
    if a.structured:
        STRUCTURED, METHOD = True, METHOD + "+structured"
    os.makedirs(OUT, exist_ok=True)
    pick = sample(a.per_company)
    print("sample %d docs over %d companies" % (len(pick), len({p["company"] for p in pick})), flush=True)
    rows, t0 = [], time.time()
    with dc.connect() as c:
        added = ensure_refs(c, {host_of(p["url"]) for p in pick}, {p["document_id"] for p in pick})
        print("reference pages added %d" % added, flush=True)
        meta = {p["document_id"]: p for p in pick}
        ids = list(meta)
        for b in range(0, len(ids), 5):
            for did, url, title, text, html, st in c.execute(
                    "select document_id, url, title, main_text, html, spec_tables from documents where document_id = any(%s)",
                    (ids[b:b + 5],)):
                rows.append(make_row(meta[did], did, url, title, text, html, st, a.judge))
            if b % 100 == 0:
                print("  %d / %d  (%.0fs)" % (len(rows), len(ids), time.time() - t0), flush=True)
    with io.open(os.path.join(OUT, "docs.jsonl"), "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    stats(rows)


def stats(rows, out_dir=OUT):
    """what the crawler's text lost, and whether its thin band is genuinely short or lost content"""
    n = len(rows)
    gain = lambda r: r["content_len"] / max(1, r["crawler_len"])
    band = lambda r: "0" if r["crawler_len"] == 0 else "<200" if r["crawler_len"] < 200 else "200-1k" if r["crawler_len"] < 1000 \
        else "1k-5k" if r["crawler_len"] < 5000 else "5k+"
    out = {"docs": n, "errors": sum(1 for r in rows if r["error"]), "by_band": {}}
    for bnd in ("0", "<200", "200-1k", "1k-5k", "5k+"):
        xs = [r for r in rows if band(r) == bnd]
        if not xs:
            continue
        g = sorted(gain(r) for r in xs)
        out["by_band"][bnd] = {"docs": len(xs), "median_gain": round(g[len(g) // 2], 2),
                               "lost_content(>=2x)": sum(1 for v in g if v >= 2),
                               "genuinely_short(<1.3x)": sum(1 for v in g if v < 1.3),
                               "median_new_len": sorted(r["content_len"] for r in xs)[len(xs) // 2]}
    out["by_doc_type"] = dict(collections.Counter(r["doc_type"] or "best30" for r in rows))
    io.open(os.path.join(out_dir, "stats.json"), "w", encoding="utf-8").write(json.dumps(out, indent=1))
    print(json.dumps(out, indent=1))


def backfill_dates():
    """add the HTML date witness to an existing content corpus (one read-only html pass)"""
    import dc
    p = os.path.join(OUT, "docs.jsonl")
    rows = [json.loads(l) for l in io.open(p, encoding="utf-8")]
    need = [r["document_id"] for r in rows if "html_date" not in r]
    got = {}
    with dc.connect() as c:
        for b in range(0, len(need), 5):
            for did, html in c.execute("select document_id, html from documents where document_id = any(%s)", (need[b:b + 5],)):
                got[did] = dates.html_date(html) if html else None
    for r in rows:
        if r["document_id"] in got:
            r["html_date"] = got[r["document_id"]]
    with io.open(p, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print("html dates: %d of %d docs" % (sum(1 for r in rows if r.get("html_date")), len(rows)))


# ---- full run: every manifest HTML doc, resumable -------------------------------------------------------
# corpus/content_full/part-<ts>.jsonl = append-only shards (main()'s row schema); a doc with a row is done,
# except "fetch:*" rows (transient, retried on every resume). Own reference store content_full/ref/ = the
# shared corpus/web/ref hardlinked (never written) + the same-locale pages the full set needs.
FULL = os.path.join(HERE, "corpus", "content_full")
FREF = os.path.join(FULL, "ref")
NOT_HTML = (".pdf", ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".js", ".css", ".xml", ".ics", ".rtf", ".json",
            ".zip", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".mp4")
BIG = 30_000_000        # html_len above this: recorded as too_big, never fetched
LOC_RE = "^[a-z]+://[^/?#]+/[a-z]{2}([-_][a-z]{2})?([/?#]|$)"     # webbench._locale, in SQL (url only, no TOAST)


def key_of(url):
    return host_of(url or ""), W._locale(url or "")


def full_docs():
    """all manifest HTML docs, most useful first (gap fillers, worklist, product/spec), so a partial run still pays"""
    R = [json.loads(l) for l in io.open(os.path.join(HERE, "corpus", "manifest.jsonl"), encoding="utf-8")]
    xs = [r for r in R if r["has_html"] and not (r["url"] or "").lower().split("?")[0].endswith(NOT_HTML)]
    return sorted(xs, key=lambda r: (tier(r), key_of(r["url"]), r["document_id"]))


def tier(r):
    return 0 if r["fills_gap"] else 1 if r["source"] == "worklist" else 2 if r["doc_type"] == "product/spec" else 3


def shard_rows(d=FULL, text=True):
    """every row of every shard, oldest shard first; a torn last line (process killed mid-write) is skipped"""
    for fn in sorted(glob.glob(os.path.join(d, "part-*.jsonl"))):
        for l in io.open(fn, encoding="utf-8"):
            try:
                r = json.loads(l)
            except ValueError:
                continue
            if not text:
                r.pop("text", None)
            yield r


def done_ids(d=FULL, retry_errors=False, method=METHOD):
    """the last row per doc decides: another method -> redo; ok -> done; fetch:* error -> redo;
    other error -> done unless retry_errors"""
    last = {}
    for r in shard_rows(d, text=False):
        last[r["document_id"]] = (r["error"], r.get("method"))
    return {k for k, (e, m) in last.items() if m == method and (not e or not (retry_errors or e.startswith("fetch:")))}


def spread(xs, n, seed=7):
    """n docs round-robin over companies (the pilot)"""
    by = collections.defaultdict(list)
    for x in xs:
        by[x["company"]].append(x)
    rnd, out = random.Random(seed), []
    for v in by.values():
        rnd.shuffle(v)
    while len(out) < n and any(by.values()):
        for k in sorted(by):
            if by[k] and len(out) < n:
                out.append(by[k].pop())
    return out


def html_lens(c, ids):
    """html_len per doc (metadata column only, batches of 1000), cached; None = not on the DC"""
    p = os.path.join(FULL, "html_len.json")
    got = json.load(io.open(p, encoding="utf-8")) if os.path.exists(p) else {}
    need = [i for i in ids if i not in got]
    for b in range(0, len(need), 1000):
        got.update(dict(c.execute("select document_id, html_len from documents where document_id = any(%s)",
                                  (need[b:b + 1000],)).fetchall()))
    for i in need:
        got.setdefault(i, None)
    if need:
        json.dump(got, io.open(p + ".tmp", "w", encoding="utf-8"))
        os.replace(p + ".tmp", p)
    return got


def ref_index():
    p = os.path.join(FREF, "index.jsonl")
    return [json.loads(l) for l in io.open(p, encoding="utf-8")] if os.path.exists(p) else []


def seed_refs(c, todo, docs, lens, n=12, cap=3_000_000):
    """n reference pages per host+LOCALE this run touches (webbench.locale_template uses the locale's own pages
    once it has 4), taken from the MANIFEST docs themselves: the full run fetches them anyway, and a doc whose
    html is in the ref store is read locally (_work), so a ref costs no extra transfer. This run's docs first,
    pages under `cap` before bigger ones (a site of only big pages got no template under ensure_refs' cap).
    A key with < 4 manifest docs falls back to the host template. Returns (pages added, html chars pulled)."""
    import shutil
    os.makedirs(FREF, exist_ok=True)
    idx_p = os.path.join(FREF, "index.jsonl")
    if not os.path.exists(idx_p):
        lines = list(io.open(os.path.join(W.REF, "index.jsonl"), encoding="utf-8"))
        for l in lines:
            did = json.loads(l)["document_id"]
            src, dst = os.path.join(W.REF, did + ".html"), os.path.join(FREF, did + ".html")
            if os.path.exists(src) and not os.path.exists(dst):
                try:
                    os.link(src, dst)
                except OSError:
                    shutil.copyfile(src, dst)
        io.open(idx_p + ".tmp", "w", encoding="utf-8").writelines(lines)
        os.replace(idx_p + ".tmp", idx_p)
    idx = ref_index()
    have = collections.Counter(key_of(x["url"]) for x in idx)
    seen = {x["document_id"] for x in idx}
    by = collections.defaultdict(list)
    for r in docs:
        by[key_of(r["url"])].append(r)
    mine = {r["document_id"] for r in todo}
    ids = []
    for k in sorted({key_of(r["url"]) for r in todo}):
        if have[k] >= n or len(by[k]) < 4 or not k[0]:
            continue
        xs = [r["document_id"] for r in by[k] if r["document_id"] not in seen and 0 < (lens.get(r["document_id"]) or 0) <= BIG]
        random.Random(k[0] + k[1]).shuffle(xs)
        xs.sort(key=lambda i: (i not in mine, lens[i] > cap))
        ids += xs[: n - have[k]]
    print("reference pages to pull %d (%.1f MB, from %d keys)" % (len(ids), sum(lens[i] for i in ids) / 1e6,
                                                                   len({key_of(r["url"]) for r in todo})), flush=True)
    added = chars = 0
    for b in range(0, len(ids), 5):
        try:
            for did, url, html in c.execute("select document_id, url, html from documents where document_id = any(%s)",
                                            (ids[b:b + 5],)):
                if not html:
                    continue
                p = os.path.join(FREF, did + ".html")
                io.open(p + ".tmp", "w", encoding="utf-8", errors="surrogatepass").write(html)
                os.replace(p + ".tmp", p)
                io.open(idx_p, "a", encoding="utf-8").write(json.dumps({"document_id": did, "url": url}) + "\n")
                added, chars = added + 1, chars + len(html)
        except Exception as e:
            print("  refs batch %d FAILED %s" % (b, type(e).__name__), flush=True)
            c = _reconnect(c)
        if b % 100 == 0:
            print("  refs %d / %d  %.1f MB" % (added, len(ids), chars / 1e6), flush=True)
    return added, chars, c


def _reconnect(c):
    import dc
    try:
        c.close()
    except Exception:
        pass
    for k in range(5):
        try:
            c = dc.connect()
            c.autocommit = True        # a live shared DB: never hold one read transaction open for hours
            return c
        except Exception:
            time.sleep(15 * (k + 1))
    raise SystemExit("DC unreachable")


_C, _LOCAL = None, set()


def _winit(ref):
    """worker: own DC connection, the full run's ref store, and BELOW-normal priority (the GPU run comes first)"""
    global _LOCAL
    W.REF = ref
    _LOCAL = {x["document_id"] for x in ref_index() if os.path.exists(os.path.join(ref, x["document_id"] + ".html"))}
    try:
        import ctypes
        ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x4000)
    except Exception:
        try:
            os.nice(10)
        except Exception:
            pass


def _work(ms):
    """one batch of <=5 manifest docs -> (rows, html chars pulled). Fetch per id (html read from the ref store
    when it is a ref: never pulled twice); retried with a fresh connection; a batch that still fails is
    recorded as fetch:<Error> (retried on resume)"""
    global _C
    import dc
    err = None
    ids = [m["document_id"] for m in ms]
    loc = [i for i in ids if i in _LOCAL]
    rem = [i for i in ids if i not in _LOCAL]
    for k in range(4):
        try:
            if _C is None:
                _C = dc.connect()
                _C.autocommit = True
            got = {r[0]: r for r in _C.execute("select document_id, url, title, main_text, html, spec_tables "
                                               "from documents where document_id = any(%s)", (rem,))} if rem else {}
            for r in _C.execute("select document_id, url, title, main_text, spec_tables "
                                "from documents where document_id = any(%s)", (loc,)) if loc else []:
                got[r[0]] = r[:4] + (io.open(os.path.join(W.REF, r[0] + ".html"), encoding="utf-8",
                                             errors="surrogatepass").read(), r[4])
            err = None
            break
        except Exception as e:
            err = "fetch:" + type(e).__name__
            try:
                _C.close()
            except Exception:
                pass
            _C = None
            time.sleep(10 * (k + 1))
    rows, nb = [], 0
    for m in ms:
        did = m["document_id"]
        if err or did not in got:
            rows.append(make_row(m, did, m["url"], m["title"], "", None, None, err=err or "missing"))
            continue
        _, url, title, text, html, st = got[did]
        nb += len(html or "") if did in rem else 0
        e = None if html else "no_html"
        if html and html.lstrip()[:5] == "%PDF-":
            e, html = "not_html", None
        rows.append(make_row(m, did, url, title, text, html, st, err=e))
    return rows, nb


# careers / investor / metering (Diehl Metering is not defence) / feeds / assets: never product or event pages
JUNK = __import__("re").compile(r"(?i)/(career|careers|karriere|jobs?|metering|investor|investoren|investors|"
                                r"investor-relations|yatirim|rss|_layouts|_catalogs|assets|cookie|privacy|legal|impressum|"
                                r"datenschutz|iletisim|contact|search|tag)(/|$)|(stock-exchange|board-?meeting|voting|"
                                r"dividend|credit-rating|grivance|openoffer|investor-forms)")


def full(workers=3, limit=None, pilot=None, retry_errors=False, max_tier=3, companies=None):
    import dc, multiprocessing as mp
    os.makedirs(FULL, exist_ok=True)
    docs = full_docs()
    done = done_ids(FULL, retry_errors)
    todo = [r for r in docs if r["document_id"] not in done and tier(r) <= max_tier]
    if companies:                                       # Step 1 (advisor D1): top up the thin competitors only
        todo = [r for r in todo if r["company"] in companies and not JUNK.search(r["url"] or "")]
    todo = spread(todo, pilot) if pilot else todo
    todo = todo[:limit] if limit else todo
    print("full: %d manifest html docs, %d done, %d to do, %d workers" % (len(docs), len(done), len(todo), workers), flush=True)
    t0 = time.time()
    c = _reconnect(None)
    lens = html_lens(c, [r["document_id"] for r in docs])
    print("html_len known %d / %d  (%.0fs)" % (sum(1 for v in lens.values() if v is not None), len(docs), time.time() - t0), flush=True)
    added, rchars, c = seed_refs(c, todo, docs, lens)
    c.close()
    t_ref = time.time() - t0
    print("reference pages added %d (%.1f MB) in %.0fs" % (added, rchars / 1e6, t_ref), flush=True)
    shard = os.path.join(FULL, "part-%s.jsonl" % time.strftime("%Y%m%d-%H%M%S"))
    why = lambda n: "missing" if n is None else "no_html" if n == 0 else "too_big" if n > BIG else None
    pre = [r for r in todo if why(lens.get(r["document_id"]))]
    skip = {r["document_id"] for r in pre}
    go = [r for r in todo if r["document_id"] not in skip]
    batches = [go[b:b + 5] for b in range(0, len(go), 5)]
    n = nerr = nb = 0
    t1 = time.time()
    with io.open(shard, "a", encoding="utf-8") as f:
        for m in pre:      # known from metadata alone: never fetched
            f.write(json.dumps(make_row(m, m["document_id"], m["url"], m["title"], "", None, None,
                                        err=why(lens.get(m["document_id"]))), ensure_ascii=False) + "\n")
        f.flush()
        n = nerr = len(pre)
        with mp.Pool(workers, initializer=_winit, initargs=(FREF,)) as pool:
            for rows, b in pool.imap_unordered(_work, batches):
                for r in rows:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
                f.flush()
                n, nb, nerr = n + len(rows), nb + b, nerr + sum(1 for r in rows if r["error"])
                if n % 500 < 5 or n == len(todo):
                    el = time.time() - t1
                    prog = {"done": n, "todo": len(todo), "errors": nerr, "docs_s": round(n / max(el, 1e-9), 2),
                            "html_mb": round(nb / 1e6, 1), "elapsed_s": round(el), "eta_h": round((len(todo) - n) / max(n / max(el, 1e-9), 1e-9) / 3600, 2),
                            "shard": os.path.basename(shard), "at": time.strftime("%H:%M:%S")}
                    io.open(os.path.join(FULL, "progress.json"), "w").write(json.dumps(prog))
                    print("  " + json.dumps(prog), flush=True)
    el = time.time() - t1
    ids = {r["document_id"] for r in todo}
    report(ids, lens, el, t_ref)
    stats([r for r in {r["document_id"]: r for r in shard_rows(FULL, text=False)}.values() if r.get("method") == METHOD], FULL)


def report(ids, lens, secs, t_ref=0):
    """this run's numbers: rate, transfer, errors, template coverage, crawler-vs-content bands"""
    rows = {}
    for r in shard_rows(FULL, text=False):
        if r["document_id"] in ids:
            rows[r["document_id"]] = r
    rows = list(rows.values())
    idx = ref_index()
    loc_n = collections.Counter(key_of(x["url"]) for x in idx)
    host_n = collections.Counter(key_of(x["url"])[0] for x in idx)
    cov = collections.Counter("locale" if loc_n[key_of(r["url"])] >= 4 else "host" if host_n[key_of(r["url"])[0]] >= 4
                              else "none" for r in rows)
    mb = [lens[r["document_id"]] / 1e6 for r in rows if lens.get(r["document_id"])]
    out = {"docs": len(rows), "secs": round(secs), "docs_per_s": round(len(rows) / max(secs, 1e-9), 2),
           "ref_secs": round(t_ref), "html_mb_per_doc_mean": round(sum(mb) / max(1, len(mb)), 3),
           "html_mb_per_doc_median": round(sorted(mb)[len(mb) // 2], 3) if mb else None,
           "errors": dict(collections.Counter(r["error"] for r in rows if r["error"])),
           "error_rate": round(sum(1 for r in rows if r["error"]) / max(1, len(rows)), 4),
           "template": {k: round(v / max(1, len(rows)), 3) for k, v in cov.items()}}
    print(json.dumps(out, indent=1))
    io.open(os.path.join(FULL, "report-%s.json" % time.strftime("%Y%m%d-%H%M%S")), "w").write(json.dumps(out, indent=1))
    return out


def demo():
    """resume/done-set logic on a scratch dir: last row wins, torn line skipped, fetch errors redone"""
    import tempfile
    d = tempfile.mkdtemp()
    row = lambda i, e=None, m=METHOD: json.dumps({"document_id": i, "error": e, "text": "x", "method": m}) + "\n"
    io.open(os.path.join(d, "part-1.jsonl"), "w").write(row("a") + row("b", "fetch:OperationalError") + row("c", "ParserError")
                                                        + row("d", "fetch:OperationalError") + row("f", m="template_tags")
                                                        + row("g", m="template_tags"))
    io.open(os.path.join(d, "part-2.jsonl"), "w").write(row("d") + row("g") + '{"document_id": "e", "err')   # d, g redone; e torn
    assert done_ids(d) == {"a", "c", "d", "g"}, done_ids(d)                  # f: an older method is not done
    assert done_ids(d, retry_errors=True) == {"a", "d", "g"}, done_ids(d, True)
    assert done_ids(d, method="template_tags") == {"f"}
    assert [r["document_id"] for r in shard_rows(d)] == ["a", "b", "c", "d", "f", "g", "d", "g"]
    assert "text" not in next(shard_rows(d, text=False))
    xs = [{"company": c, "document_id": c + str(k)} for c in "ab" for k in range(5)] + [{"company": "c", "document_id": "c0"}]
    s = spread(xs, 5)
    assert len(s) == 5 and {x["company"] for x in s} == {"a", "b", "c"} and len({x["document_id"] for x in s}) == 5
    r = make_row({"company": "X", "language": "en", "published_at": None, "doc_type": None, "fills_gap": None, "source": "best30"},
                 "z", "https://x.com/en/a", "t", "abc", None, None, err="missing")
    assert r["error"] == "missing" and r["content_len"] == 0 and r["crawler_len"] == 3 and r["html_date"] is None
    assert key_of("https://www.rheinmetall.com/de/produkte") == ("rheinmetall.com", "de") and key_of(None) == ("", "")
    print("content demo ok")


if __name__ == "__main__":
    if "--backfill-dates" in sys.argv:
        backfill_dates()
    elif "--demo" in sys.argv:
        demo()
    elif "--all" in sys.argv:
        demo()
        ap = argparse.ArgumentParser()
        ap.add_argument("--all", action="store_true")
        ap.add_argument("--workers", type=int, default=3)
        ap.add_argument("--limit", type=int)
        ap.add_argument("--pilot", type=int, help="n docs round-robin over companies")
        ap.add_argument("--retry-errors", action="store_true")
        ap.add_argument("--max-tier", type=int, default=3, help="0 fills_gap, 1 +worklist, 2 +product/spec, 3 all")
        ap.add_argument("--companies", help="comma list of manifest company names (with --pilot N: N spread over them)")
        a = ap.parse_args()
        full(min(a.workers, 4), a.limit, a.pilot, a.retry_errors, a.max_tier,
             set(a.companies.split(",")) if a.companies else None)
    else:
        main()
