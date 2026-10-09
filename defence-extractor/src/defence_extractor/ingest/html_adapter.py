"""A1 Semantic HTML parser: raw / rendered HTML -> SemanticDocument.

Deterministic. Keeps every visible-text fragment as an evidence block (nothing is deleted; noise is *scoped*),
preserves heading hierarchy, DOM paths, tables with header context, key-value pairs, list items and metadata.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import unicodedata
from dataclasses import dataclass, field

from lxml import etree
from lxml import html as lxml_html

from ..contracts.document import BlockType, ScopeHint, Section, SemanticBlock, SemanticDocument, SemanticTable, TableCell

PARSER_VERSION = "html-1.1"
sys.setrecursionlimit(max(sys.getrecursionlimit(), 20000))

SKIP_TAGS = {
    "script", "style", "noscript", "template", "svg", "iframe", "canvas", "object", "embed", "link", "meta",
    "head", "button", "input", "select", "option", "textarea", "map", "area", "audio", "video", "source",
    "track", "img", "picture", "dialog",
}
BLOCK_TAGS = {
    "address", "article", "aside", "blockquote", "body", "dd", "details", "div", "dl", "dt", "fieldset",
    "figcaption", "figure", "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6", "header", "hgroup", "hr",
    "li", "main", "nav", "ol", "p", "pre", "section", "summary", "table", "tbody", "td", "tfoot", "th",
    "thead", "tr", "ul", "caption", "center", "menu", "html",
}
HEADING_LEVEL = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}
SUPERSCRIPT = str.maketrans("0123456789+-", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻")

_TOKEN_SPLIT = re.compile(r"[\s]+")
_SUB_SPLIT = re.compile(r"[-_]+")
NAV_TOKENS = {"nav", "navbar", "navigation", "menu", "megamenu", "submenu", "subnav", "sitenav", "mainmenu",
              "offcanvas", "hamburger", "dropdown", "skiplink", "skip", "toolbar", "topnav", "mobilenav",
              "menubar", "pagination", "pager", "language", "languages", "langswitcher"}
FOOTER_TOKENS = {"footer", "sitefooter", "copyright", "colophon", "bottombar", "legal"}
HEADER_TOKENS = {"siteheader", "masthead", "globalheader", "topbar", "headerbar"}
BREADCRUMB_TOKENS = {"breadcrumb", "breadcrumbs", "crumbs", "crumb"}
COOKIE_TOKENS = {"cookie", "cookies", "consent", "gdpr", "cmp", "onetrust", "cookiebot", "cookielaw", "privacy"}
SIDEBAR_TOKENS = {"sidebar", "widget", "widgets", "sidecol", "rightcol", "leftcol"}
RELATED_TOKENS = {"related", "recommend", "recommended", "recommendations", "similar", "upsell", "crosssell",
                  "moreproducts", "otherproducts", "youmayalsolike", "relatedproducts", "relatedposts", "morenews",
                  "latestnews", "recentposts"}
SOCIAL_TOKENS = {"share", "sharing", "social", "follow", "newsletter", "subscribe", "signup", "socialmedia"}
FORM_TOKENS = {"search", "searchform", "login", "signin", "modal", "popup", "overlay", "contactform"}
MAIN_TOKENS = {"main", "maincontent", "content", "primary", "product", "productdetail", "productdetails",
               "article", "entrycontent", "postcontent", "pagecontent", "body-content"}

RELATED_HEADING = re.compile(
    r"(?i)\b(related|similar products|you may also|also (?:like|interested)|other products|more products|"
    r"recommended|latest news|recent (?:news|posts|articles)|more news|see also|explore (?:more|our)|"
    r"discover more|popular products|other solutions|related (?:products|solutions|news|content))\b"
)
COUNTER_ATTRS = ("data-count", "data-to", "data-target", "data-number", "data-value", "data-counter",
                 "data-end", "data-countup", "data-purecounter-end", "data-num", "data-final", "data-stop")
_NUMERIC = re.compile(r"^[-+]?\d+(?:[.,]\d+)?$")
_WS = re.compile(r"[ \t\r\f\v]+")
_JSONLD_KEYS = {"name", "alternateName", "description", "brand", "manufacturer", "model", "sku", "mpn", "category",
                "weight", "height", "width", "depth", "additionalProperty", "propertyID", "value", "unitText",
                "unitCode", "headline", "about", "material", "color", "productID", "isVariantOf", "hasVariant"}


def _tokens(el) -> set[str]:
    toks: set[str] = set()
    for attr in ("class", "id"):
        v = el.get(attr)
        if not v:
            continue
        for t in _TOKEN_SPLIT.split(v.lower()):
            if not t:
                continue
            toks.add(t.replace("-", "").replace("_", ""))
            for st in _SUB_SPLIT.split(t):
                if st:
                    toks.add(st)
    return toks


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFC", s)
    s = s.replace("​", "").replace("﻿", "").replace("­", "")
    s = _WS.sub(" ", s)
    s = re.sub(r" *\n+ *", "\n", s)
    return s.strip()


def _is_hidden(el) -> bool:
    if el.get("hidden") is not None or el.get("aria-hidden") == "true":
        return True
    style = (el.get("style") or "").replace(" ", "").lower()
    return "display:none" in style or "visibility:hidden" in style


@dataclass
class _Ctx:
    scope: ScopeHint = ScopeHint.unknown
    in_main: bool = False
    hidden: bool = False
    in_table: bool = False


@dataclass
class _State:
    doc_id: str
    tree: etree._ElementTree
    blocks: list[SemanticBlock] = field(default_factory=list)
    tables: list[SemanticTable] = field(default_factory=list)
    sections: list[Section] = field(default_factory=list)
    heading_stack: list[tuple[int, str, str, bool]] = field(default_factory=list)  # level, text, sid, related
    section_counter: int = 0
    table_counter: int = 0
    current_section: str = "s0"
    seen_texts: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    n_body: int = 0
    n_meta: int = 0


_META_CHARSET = re.compile(rb"""<meta[^>]{0,200}?charset\s*=\s*["']?\s*([A-Za-z0-9_.:\-]+)""", re.I)
_XML_DECL = re.compile(r"^\s*<\?xml[^>]*\?>")


def decode_html(raw: bytes | str) -> str:
    """Bytes -> text. libxml2 only honours a <meta charset> near the top of <head> and otherwise falls back to
    Latin-1, which turns UTF-8 pages with a large inline <style> first into mojibake ("itâ\x80\x99s", "Â°").
    Order: BOM, strict UTF-8, declared charset, cp1252."""
    if isinstance(raw, str):
        return _XML_DECL.sub("", raw, count=1)
    if raw.startswith(b"\xef\xbb\xbf"):
        text = raw[3:].decode("utf-8", "replace")
    elif raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        text = raw.decode("utf-16", "replace")
    else:
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = None
            m = _META_CHARSET.search(raw[:300000])
            if m:
                try:
                    text = raw.decode(m.group(1).decode("ascii", "ignore"), "replace")
                except LookupError:
                    text = None
            if text is None:
                text = raw.decode("cp1252", "replace")
    return _XML_DECL.sub("", text, count=1)


_JSON_TEXT_KEYS = ("content", "description", "body", "text", "summary")
_JSON_SKIP = {"_links", "_embedded", "yoast_head", "yoast_head_json", "guid", "link", "slug", "template", "meta",
              "class_list", "featured_media", "author", "comment_status", "ping_status", "format", "sticky", "type",
              "status", "modified_gmt", "date_gmt"}


def _json_text(v) -> str | None:
    if isinstance(v, dict) and isinstance(v.get("rendered"), str):
        v = v["rendered"]
    return v if isinstance(v, str) and v.strip() else None


def json_to_html(data) -> str | None:
    """API responses saved as pages (WordPress /wp-json/ posts etc.): rebuild an HTML page from the rendered title and
    content; other objects become a definition list of their scalar fields."""
    items = [x for x in (data if isinstance(data, list) else [data]) if isinstance(x, dict)][:50]
    if not items:
        return None
    title, parts = None, []
    for it in items:
        t = _json_text(it.get("title")) or _json_text(it.get("name")) or _json_text(it.get("headline"))
        title = title or t
        if t:
            parts.append(f"<h1>{t}</h1>")
        texts = [x for x in (_json_text(it.get(k)) for k in _JSON_TEXT_KEYS) if x]
        if texts:
            parts.append(f"<article>{texts[0]}</article>")
            continue
        rows = [(k, v) for k, v in it.items() if k not in _JSON_SKIP and isinstance(v, (str, int, float)) and str(v).strip()]
        parts.append("<dl>" + "".join(f"<dt>{_esc(k)}</dt><dd>{_esc(str(v))}</dd>" for k, v in rows) + "</dl>")
    head = f"<title>{title}</title>" if title else ""
    return f"<html><head>{head}</head><body><main>{''.join(parts)}</main></body></html>"


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _unwrap_json(root) -> str | None:
    """Raw JSON, or the browser's JSON viewer (<body><pre>{...}</pre>), parsed as HTML -> rebuilt HTML or None."""
    body = root.find("body")
    if body is None:
        return None
    kids = [k for k in body if isinstance(k.tag, str)]
    if not kids or kids[0].tag not in ("pre", "p") or len(kids) > 3:
        return None
    txt = kids[0].text_content().strip()
    if txt[:1] not in "{[":
        return None
    try:
        data = json.loads(txt)
    except ValueError:
        return None
    return json_to_html(data)


class HtmlAdapter:
    def parse(self, raw: bytes | str, doc_id: str, source_url: str | None = None, source_path: str | None = None,
              fmt: str = "html") -> SemanticDocument:
        content_sha = hashlib.sha256(raw if isinstance(raw, bytes) else raw.encode("utf-8", "ignore")).hexdigest()
        try:
            root = lxml_html.document_fromstring(decode_html(raw))
            rebuilt = _unwrap_json(root)
            if rebuilt is not None:
                root = lxml_html.document_fromstring(rebuilt)
        except (etree.ParserError, ValueError) as e:
            root = lxml_html.document_fromstring(f"<html><body><pre>{e}</pre></body></html>")
        tree = etree.ElementTree(root)
        st = _State(doc_id=doc_id, tree=tree)
        st.sections.append(Section(section_id="s0", heading=None, level=0, heading_path=[]))

        title = self._metadata_blocks(root, st)
        self._resolve_counters(root)
        body = root.find("body")
        if body is None:
            body = root
        self._walk(body, _Ctx(), st)
        self._finalize_sections(st)
        lang = (root.get("lang") or "").split("-")[0].lower() or None
        return SemanticDocument(
            doc_id=doc_id,
            format="rendered_html" if fmt == "rendered_html" else "html",
            source_url=source_url,
            source_path=source_path,
            title=title,
            language=lang,
            content_sha256=content_sha,
            parser_version=PARSER_VERSION,
            blocks=st.blocks,
            tables=st.tables,
            sections=[s for s in st.sections if s.block_ids or s.section_id == "s0"],
            metadata={"html_lang": lang},
            parse_warnings=st.warnings,
        )

    # ------------------------------------------------------------------------------------------
    # metadata: title, meta description / og, JSON-LD
    # ------------------------------------------------------------------------------------------
    def _metadata_blocks(self, root, st: _State) -> str | None:
        title = None
        t = root.find(".//title")
        if t is not None and (t.text or "").strip():
            title = _norm(t.text_content())
            self._emit_meta(st, title, "title", "page title")
        seen = set()
        for m in root.iter("meta"):
            key = (m.get("property") or m.get("name") or "").lower()
            if key in ("description", "og:title", "og:description", "twitter:title", "twitter:description", "keywords"):
                val = _norm(m.get("content") or "")
                if val and val not in seen and val != title:
                    seen.add(val)
                    self._emit_meta(st, val, "meta", key)
        for s in root.iter("script"):
            if (s.get("type") or "").lower() != "application/ld+json":
                continue
            raw = (s.text or "").strip()
            raw = re.sub(r"^<!--|-->$|^/\*<!\[CDATA\[\*/|/\*\]\]>\*/$", "", raw).strip()
            try:
                data = json.loads(raw)
            except Exception:
                continue
            lines: list[str] = []
            self._flatten_jsonld(data, "", lines, depth=0)
            text = "; ".join(lines)[:2000]
            if text and any(k in text for k in ("name:", "description:", "headline:")):
                self._emit_meta(st, text, "jsonld", "json-ld")
        return title

    def _flatten_jsonld(self, obj, prefix: str, lines: list[str], depth: int) -> None:
        if depth > 5 or len(lines) > 60:
            return
        if isinstance(obj, list):
            for it in obj[:30]:
                self._flatten_jsonld(it, prefix, lines, depth + 1)
        elif isinstance(obj, dict):
            if "@graph" in obj:
                self._flatten_jsonld(obj["@graph"], prefix, lines, depth + 1)
            typ = obj.get("@type")
            if isinstance(typ, str) and typ in ("WebSite", "BreadcrumbList", "SearchAction", "ImageObject",
                                                "Organization", "WebPage", "ReadAction", "EntryPoint"):
                if typ != "WebPage":
                    return
            for k, v in obj.items():
                if k.startswith("@") or k not in _JSONLD_KEYS:
                    continue
                key = f"{prefix}{k}"
                if isinstance(v, (str, int, float)):
                    sv = _norm(str(v))
                    if sv and len(sv) < 600:
                        lines.append(f"{key}: {sv}")
                else:
                    self._flatten_jsonld(v, f"{key}.", lines, depth + 1)

    def _emit_meta(self, st: _State, text: str, source: str, label: str) -> None:
        st.n_meta += 1
        st.blocks.append(
            SemanticBlock(
                block_id=f"m{st.n_meta}", type=BlockType.metadata, text=text, order=len(st.blocks), section_id="s0",
                heading_path=[], scope=ScopeHint.metadata, source=source, attributes={"meta": label},
            )
        )

    # ------------------------------------------------------------------------------------------
    # JS counters: <span data-count="7">0</span>  ->  text "7" (flagged)
    # ------------------------------------------------------------------------------------------
    def _resolve_counters(self, root) -> None:
        for el in root.iter():
            if not isinstance(el.tag, str):
                continue
            for a in COUNTER_ATTRS:
                v = el.get(a)
                if v is None or not _NUMERIC.match(v.strip()):
                    continue
                txt = (el.text or "").strip()
                if len(el) == 0 and (txt == "" or _NUMERIC.match(txt) and float(txt.replace(",", ".")) == 0.0):
                    if float(v.replace(",", ".")) != 0.0:
                        el.text = v.strip()
                        el.set("data-dx-counter", f"{a}={v.strip()} (static text {txt!r})")
                        p = el.getparent()
                        hops = 0
                        while p is not None and hops < 6:  # mark a few ancestors so _emit can find it cheaply
                            if isinstance(p.tag, str):
                                p.set("data-dx-counter-anc", "1")
                            p = p.getparent()
                            hops += 1
                break

    # ------------------------------------------------------------------------------------------
    # scope
    # ------------------------------------------------------------------------------------------
    @staticmethod
    def _link_density(el) -> tuple[float, int]:
        text = el.text_content().strip()
        n = len(text)
        if n == 0:
            return 1.0, 0
        links = sum(len((a.text_content() or "").strip()) for a in el.iter("a"))
        return links / n, n

    def _linky(self, el, thr: float) -> bool:
        d, n = self._link_density(el)
        return d >= thr or n < 120

    def _refine(self, el, ctx: _Ctx) -> _Ctx:
        """Scope is inferred conservatively: class/id tokens only count for link-heavy (menu-like) containers,
        body/html classes are ignored, and real content containers override a wrongly inherited scope."""
        tag = el.tag.lower() if isinstance(el.tag, str) else ""
        role = (el.get("role") or "").lower()
        new = _Ctx(scope=ctx.scope, in_main=ctx.in_main, hidden=ctx.hidden or _is_hidden(el), in_table=ctx.in_table)
        if tag in ("body", "html"):
            return new
        toks = _tokens(el)
        if toks & COOKIE_TOKENS and toks & {"banner", "notice", "consent", "cookie", "cookies", "bar", "popup", "modal"}:
            new.scope = ScopeHint.cookie
        elif tag == "form" or (toks & FORM_TOKENS and self._linky(el, 0.3)):
            new.scope = ScopeHint.form
        elif toks & BREADCRUMB_TOKENS or "breadcrumb" in (el.get("aria-label") or "").lower():
            new.scope = ScopeHint.breadcrumb
        elif tag == "nav" or role == "navigation" or (toks & NAV_TOKENS and self._linky(el, 0.5)):
            new.scope = ScopeHint.navigation
        elif (tag == "footer" and not ctx.in_main) or role == "contentinfo" or (toks & FOOTER_TOKENS and self._linky(el, 0.2)):
            new.scope = ScopeHint.footer
        elif ((tag == "header" and not ctx.in_main and el.find(".//h1") is None) or role == "banner"
              or (toks & HEADER_TOKENS and self._linky(el, 0.4))):
            new.scope = ScopeHint.header
        elif (tag == "aside" and not ctx.in_main) or role == "complementary" or (toks & SIDEBAR_TOKENS and self._linky(el, 0.4)):
            new.scope = ScopeHint.sidebar
        elif (toks & RELATED_TOKENS or toks & SOCIAL_TOKENS) and self._linky(el, 0.25):
            new.scope = ScopeHint.related_content
        elif tag in ("main", "article") or role == "main" or toks & MAIN_TOKENS:
            if ctx.scope in (ScopeHint.unknown, ScopeHint.main):
                new.scope, new.in_main = ScopeHint.main, True
            elif ctx.scope not in (ScopeHint.cookie, ScopeHint.form):
                density, n = self._link_density(el)
                if density < 0.4 and n >= 300:  # real content inside a wrongly-scoped wrapper
                    new.scope, new.in_main = ScopeHint.main, True
        return new

    # ------------------------------------------------------------------------------------------
    # walk
    # ------------------------------------------------------------------------------------------
    def _walk(self, el, ctx: _Ctx, st: _State, depth: int = 0) -> None:
        if not isinstance(el.tag, str):
            return
        tag = el.tag.lower()
        if tag in SKIP_TAGS or depth > 400:
            return
        ctx = self._refine(el, ctx)
        if ctx.scope == ScopeHint.breadcrumb:
            text = " > ".join(t for t in (_norm(self._inline_text(li)) for li in el.iter("li", "a")) if t)
            if not text:
                text = _norm(self._inline_text(el))
            if text:
                self._emit(st, el, text, BlockType.other, ctx, source="breadcrumb")
            return
        if tag in HEADING_LEVEL:
            text = _norm(self._inline_text(el)).replace("\n", " ")
            if text:
                self._open_heading(st, HEADING_LEVEL[tag], text, ctx)
                self._emit(st, el, text, BlockType.heading, ctx, level=HEADING_LEVEL[tag])
            return
        if tag == "table":
            if self._process_table(el, ctx, st):
                return
        if tag == "dl":
            self._process_dl(el, ctx, st)
            return
        has_block = self._has_block_child(el)
        if not has_block:
            self._emit_inline(el, ctx, st)
            return
        kv = self._kv_pair(el)
        if kv:
            self._emit(st, el, f"{kv[0]}: {kv[1]}", BlockType.key_value, ctx, attrs={"kv_label": kv[0]})
            return
        if self._is_layout_link_list(el, ctx):
            ctx = _Ctx(scope=ScopeHint.navigation, in_main=ctx.in_main, hidden=ctx.hidden, in_table=ctx.in_table)
        if el.text and el.text.strip():
            self._emit_text_fragment(st, el, el.text, ctx, frag="t")
        for i, child in enumerate(el):
            self._walk(child, ctx, st, depth + 1)
            if child.tail and child.tail.strip():
                self._emit_text_fragment(st, el, child.tail, ctx, frag=f"tail{i}")

    def _has_block_child(self, el) -> bool:
        for d in el.iterdescendants():
            if isinstance(d.tag, str) and d.tag.lower() in BLOCK_TAGS:
                return True
        return False

    def _is_layout_link_list(self, el, ctx: _Ctx) -> bool:
        if ctx.in_main or el.tag.lower() not in ("ul", "ol", "div", "section", "menu"):
            return False
        links = el.findall(".//a")
        if len(links) < 6:
            return False
        text = _norm(el.text_content())
        if not text:
            return False
        link_text = sum(len(_norm(a.text_content())) for a in links)
        return link_text / max(1, len(text)) > 0.7

    # ------------------------------------------------------------------------------------------
    # inline text
    # ------------------------------------------------------------------------------------------
    def _inline_text(self, el) -> str:
        parts: list[str] = []

        def add(s: str | None) -> None:
            if s:
                parts.append(s)

        def sep() -> None:
            if parts and parts[-1] and not parts[-1][-1].isspace():
                parts.append(" ")

        def rec(e) -> None:
            if not isinstance(e.tag, str):
                return
            t = e.tag.lower()
            if t in SKIP_TAGS:
                return
            if t == "br":
                parts.append("\n")
                return
            if t == "sup":
                txt = _norm(e.text_content())
                parts.append(txt.translate(SUPERSCRIPT) if re.fullmatch(r"[\d+\-]+", txt or "x") else txt)
                return
            if t in ("p", "div", "li", "td", "th", "dd", "dt", "tr") and parts:
                parts.append("\n")
            add(e.text)
            for c in e:
                if isinstance(c.tag, str) and c.tag.lower() not in ("sup", "sub", "br"):
                    sep()
                rec(c)
                if c.tail:
                    if isinstance(c.tag, str) and c.tag.lower() not in ("sup", "sub", "br") and not c.tail[:1].isspace():
                        if c.tail[:1].isalnum():
                            sep()
                    add(c.tail)

        rec(el)
        return "".join(parts)

    def _emit_inline(self, el, ctx: _Ctx, st: _State) -> None:
        kv = self._kv_pair(el)
        if kv:
            self._emit(st, el, f"{kv[0]}: {kv[1]}", BlockType.key_value, ctx, attrs={"kv_label": kv[0]})
            return
        raw = self._inline_text(el)
        text = _norm(raw)
        if not text:
            return
        tag = el.tag.lower()
        lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
        if len(lines) >= 2 and all(len(ln) <= 220 for ln in lines) and (
            sum(1 for ln in lines if ":" in ln or re.search(r"\d", ln)) >= max(2, len(lines) // 2)
        ):
            for i, ln in enumerate(lines):
                btype = BlockType.key_value if re.match(r"^[^:]{1,60}:\s*\S", ln) else self._type_for(tag, ln)
                self._emit(st, el, ln, btype, ctx, frag=f"l{i}")
            return
        text = " ".join(lines)
        if self._pseudo_heading(el, text, ctx):
            self._open_heading(st, 5, text, ctx)
            self._emit(st, el, text, BlockType.heading, ctx, level=5, attrs={"pseudo_heading": "1"})
            return
        self._emit(st, el, text, self._type_for(tag, text), ctx)

    def _type_for(self, tag: str, text: str) -> BlockType:
        if tag == "li":
            return BlockType.list_item
        if tag in ("figcaption", "caption"):
            return BlockType.caption
        if re.match(r"^\s*(?:\*{1,3}|[¹²³⁴⁵⁶⁷⁸⁹†‡]|\(\d\))\s*\S", text) and len(text) < 300:
            return BlockType.footnote
        if re.match(r"^[^:]{1,60}:\s*\S", text) and len(text) < 200:
            return BlockType.key_value
        return BlockType.paragraph

    def _pseudo_heading(self, el, text: str, ctx: _Ctx) -> bool:
        if ctx.in_table or len(text) > 80 or len(text.split()) > 10 or text.endswith((".", ":", ";", ",")):
            return False
        if el.tag.lower() in ("li", "a", "span", "td", "th", "label", "small"):
            return False
        toks = _tokens(el)
        if toks & {"title", "heading", "headline", "subtitle", "sectiontitle", "h1", "h2", "h3", "h4"}:
            return not re.search(r"\d{2,}\s?(?:mm|km|kg|m|t)\b", text)
        kids = [c for c in el if isinstance(c.tag, str)]
        if len(kids) == 1 and kids[0].tag.lower() in ("strong", "b") and not (el.text or "").strip() and not (kids[0].tail or "").strip():
            return not re.search(r"\d", text)
        return False

    def _kv_pair(self, el) -> tuple[str, str] | None:
        kids = [c for c in el if isinstance(c.tag, str) and c.tag.lower() not in SKIP_TAGS]
        if (el.text or "").strip():
            return None
        if len(kids) == 3 and _norm(self._inline_text(kids[1])) in (":", "-", "–"):
            kids = [kids[0], kids[2]]
        if len(kids) != 2:
            return None
        if any((k.tail or "").strip() for k in kids):
            return None
        if any(self._has_block_child(k) for k in kids):
            return None
        a = _norm(self._inline_text(kids[0])).replace("\n", " ")
        b = _norm(self._inline_text(kids[1])).replace("\n", " / ")
        if not a or not b or len(a) > 70 or len(a.split()) > 9 or len(b) > 400:
            return None
        if a.endswith((".", "!", "?")):
            return None
        label_like = (
            a.endswith(":")
            or kids[0].tag.lower() in ("strong", "b", "th", "dt", "label", "h4", "h5", "h6")
            or bool(_tokens(kids[0]) & {"label", "title", "name", "key", "term", "spec", "property", "attribute", "caption"})
        )
        value_like = bool(re.search(r"\d", b)) or bool(_tokens(kids[1]) & {"value", "data", "desc", "detail", "text", "val"})
        if not (label_like or value_like):
            return None
        if not label_like and re.search(r"\d", a) and not re.search(r"[A-Za-z]{3}", a):
            return None
        return a.rstrip(":").strip(), b

    # ------------------------------------------------------------------------------------------
    # emit
    # ------------------------------------------------------------------------------------------
    def _emit_text_fragment(self, st: _State, el, text: str, ctx: _Ctx, frag: str) -> None:
        t = _norm(text)
        if t:
            self._emit(st, el, t.replace("\n", " "), self._type_for("p", t), ctx, frag=frag)

    def _emit(self, st: _State, el, text: str, btype: BlockType, ctx: _Ctx, level: int | None = None,
              frag: str | None = None, attrs: dict[str, str] | None = None, source: str = "body",
              table_id: str | None = None, row_index: int | None = None) -> SemanticBlock:
        order = len(st.blocks)
        st.n_body += 1
        bid = f"b{st.n_body}"
        try:
            path = st.tree.getpath(el)
        except Exception:
            path = el.tag if isinstance(el.tag, str) else "?"
        if frag:
            path = f"{path}#{frag}"
        a = dict(attrs or {})
        counter = el.get("data-dx-counter") if isinstance(el.tag, str) else None
        if counter is None and isinstance(el.tag, str) and el.get("data-dx-counter-anc") and frag is None:
            for d in el.iterdescendants():
                if isinstance(d.tag, str) and d.get("data-dx-counter"):
                    counter = d.get("data-dx-counter")
                    break
        if counter:
            a["counter_attr"] = counter
        scope = ctx.scope if ctx.scope != ScopeHint.unknown else ScopeHint.main
        if st.heading_stack and any(h[3] for h in st.heading_stack) and scope == ScopeHint.main:
            scope = ScopeHint.related_content
        key = text.strip().lower()
        if len(key) > 15 and btype != BlockType.heading:
            if key in st.seen_texts:
                a["dup_of"] = st.seen_texts[key]
            else:
                st.seen_texts[key] = bid
        blk = SemanticBlock(
            block_id=bid, type=btype, text=text, order=order, section_id=st.current_section,
            heading_path=[h[1] for h in st.heading_stack], level=level,
            html_tag=el.tag.lower() if isinstance(el.tag, str) else None, dom_path=path,
            stable_key=hashlib.sha1(f"{st.doc_id}|{path}".encode()).hexdigest()[:12],
            visible=not ctx.hidden, scope=scope, source=source, attributes=a, table_id=table_id, row_index=row_index,
        )
        st.blocks.append(blk)
        return blk

    def _open_heading(self, st: _State, level: int, text: str, ctx: _Ctx) -> None:
        while st.heading_stack and st.heading_stack[-1][0] >= level:
            st.heading_stack.pop()
        st.section_counter += 1
        sid = f"s{st.section_counter}"
        related = bool(RELATED_HEADING.search(text)) and len(text) < 80
        st.heading_stack.append((level, text, sid, related))
        st.current_section = sid
        scope = ctx.scope if ctx.scope != ScopeHint.unknown else ScopeHint.main
        st.sections.append(Section(section_id=sid, heading=text, level=level,
                                   heading_path=[h[1] for h in st.heading_stack],
                                   scope=ScopeHint.related_content if related else scope))

    # ------------------------------------------------------------------------------------------
    # definition lists
    # ------------------------------------------------------------------------------------------
    def _process_dl(self, el, ctx: _Ctx, st: _State) -> None:
        label = None
        for child in el:
            if not isinstance(child.tag, str):
                continue
            t = child.tag.lower()
            if t == "div":  # <dl><div><dt/><dd/></div></dl>
                self._process_dl(child, self._refine(child, ctx), st)
                continue
            text = _norm(self._inline_text(child)).replace("\n", " / ")
            if not text:
                continue
            if t == "dt":
                if label:
                    self._emit(st, child, label, BlockType.paragraph, ctx)
                label = text
            elif t == "dd":
                if label:
                    self._emit(st, child, f"{label.rstrip(':')}: {text}", BlockType.key_value, ctx,
                               attrs={"kv_label": label.rstrip(":")})
                    label = None
                else:
                    self._emit(st, child, text, BlockType.paragraph, ctx)
        if label:
            self._emit(st, el, label, BlockType.paragraph, ctx)

    # ------------------------------------------------------------------------------------------
    # tables
    # ------------------------------------------------------------------------------------------
    def _process_table(self, el, ctx: _Ctx, st: _State) -> bool:
        """Returns False if the table is a layout table (caller then walks it as normal content)."""
        if el.get("role") == "presentation" or el.find(".//table") is not None:
            return False
        rows = [r for r in el.iter("tr") if self._closest_table(r) is el]
        if not rows:
            return False
        long_cells = 0
        ncells = 0
        grid: dict[tuple[int, int], tuple[str, bool]] = {}
        spans: list[TableCell] = []
        for ri, tr in enumerate(rows):
            ci = 0
            for cell in tr:
                if not isinstance(cell.tag, str) or cell.tag.lower() not in ("td", "th"):
                    continue
                while (ri, ci) in grid:
                    ci += 1
                text = _norm(self._inline_text(cell)).replace("\n", " / ")
                ncells += 1
                if len(text) > 400 or len(cell.findall(".//p")) > 3:
                    long_cells += 1
                is_header = cell.tag.lower() == "th" or self._closest(cell, "thead") is not None
                try:
                    rs = max(1, min(int(cell.get("rowspan", "1") or 1), 50))
                    cs = max(1, min(int(cell.get("colspan", "1") or 1), 50))
                except ValueError:
                    rs, cs = 1, 1
                spans.append(TableCell(row=ri, col=ci, text=text, is_header=is_header, rowspan=rs, colspan=cs))
                for dr in range(rs):
                    for dc in range(cs):
                        grid[(ri + dr, ci + dc)] = (text, is_header)
                ci += cs
        if ncells and long_cells / ncells > 0.4:
            return False
        n_rows = max((r for r, _ in grid), default=-1) + 1
        n_cols = max((c for _, c in grid), default=-1) + 1
        if n_rows == 0 or n_cols == 0:
            return False
        st.table_counter += 1
        tid = f"t{st.table_counter}"
        caption_el = el.find("caption")
        caption = _norm(self._inline_text(caption_el)) if caption_el is not None else None
        if caption:
            self._emit(st, caption_el, caption, BlockType.caption, ctx, table_id=tid)
        header_rows = [r for r in range(n_rows) if all(grid.get((r, c), ("", False))[1] for c in range(n_cols) if (r, c) in grid)]
        if not header_rows and n_rows > 2 and n_cols > 2:
            first = [grid.get((0, c), ("", False))[0] for c in range(n_cols)]
            if all(first) and not any(re.search(r"\d", x) for x in first[1:]):
                header_rows = [0]
        header_rows = [r for r in header_rows if r < 3]
        has_row_labels = n_cols >= 2 and sum(
            1 for r in range(n_rows) if r not in header_rows and grid.get((r, 0), ("", False))[0]
            and not re.match(r"^[\d\s.,]+$", grid.get((r, 0), ("", False))[0])
        ) >= max(1, (n_rows - len(header_rows)) // 2)
        col_headers: dict[int, str] = {}
        for c in range(n_cols):
            hs = [grid.get((r, c), ("", False))[0] for r in header_rows]
            hs = [h for i, h in enumerate(hs) if h and (i == 0 or h != hs[i - 1])]
            col_headers[c] = " / ".join(hs)
        row_ids: list[str] = []
        tctx = _Ctx(scope=ctx.scope, in_main=ctx.in_main, hidden=ctx.hidden, in_table=True)
        for r in range(n_rows):
            cells = [grid.get((r, c), ("", False))[0] for c in range(n_cols)]
            # collapse colspans that duplicated the same text
            dedup: list[tuple[int, str]] = []
            for c, v in enumerate(cells):
                if v and (not dedup or dedup[-1][1] != v):
                    dedup.append((c, v))
            if not dedup:
                continue
            tr_el = rows[r] if r < len(rows) else el
            if r in header_rows:
                text = " | ".join(v for _, v in dedup)
                b = self._emit(st, tr_el, text, BlockType.table_header, tctx, table_id=tid, row_index=r)
                row_ids.append(b.block_id)
                continue
            if has_row_labels and len(dedup) >= 2:
                label = dedup[0][1]
                vals = dedup[1:]
                if len(vals) == 1 and (not col_headers.get(vals[0][0]) or n_cols == 2):
                    text = f"{label}: {vals[0][1]}"
                else:
                    text = f"{label} — " + "; ".join(
                        f"{col_headers[c]}: {v}" if col_headers.get(c) else v for c, v in vals
                    )
            elif any(col_headers.values()):
                text = "; ".join(f"{col_headers[c]}: {v}" if col_headers.get(c) else v for c, v in dedup)
            else:
                text = " | ".join(v for _, v in dedup)
            b = self._emit(st, tr_el, text, BlockType.table_row, tctx, table_id=tid, row_index=r,
                           attrs={"row_label": dedup[0][1]} if has_row_labels else None)
            row_ids.append(b.block_id)
        st.tables.append(
            SemanticTable(
                table_id=tid, section_id=st.current_section, caption=caption, n_rows=n_rows, n_cols=n_cols,
                header_rows=header_rows, has_row_labels=has_row_labels, cells=spans, row_block_ids=row_ids,
            )
        )
        return True

    def _closest_table(self, el):
        p = el.getparent()
        while p is not None and (not isinstance(p.tag, str) or p.tag.lower() != "table"):
            p = p.getparent()
        return p

    def _closest(self, el, tag: str):
        p = el.getparent()
        while p is not None:
            if isinstance(p.tag, str) and p.tag.lower() == tag:
                return p
            if isinstance(p.tag, str) and p.tag.lower() == "table":
                return None
            p = p.getparent()
        return None

    # ------------------------------------------------------------------------------------------
    def _finalize_sections(self, st: _State) -> None:
        by_id = {s.section_id: s for s in st.sections}
        scopes: dict[str, dict[ScopeHint, int]] = {}
        for b in st.blocks:
            sec = by_id.get(b.section_id)
            if sec is None:
                continue
            sec.block_ids.append(b.block_id)
            if b.source == "body":
                sec.char_count += len(b.text)
                d = scopes.setdefault(sec.section_id, {})
                d[b.scope] = d.get(b.scope, 0) + len(b.text)
        for sec in st.sections:
            d = scopes.get(sec.section_id)
            if d and sec.scope != ScopeHint.related_content:
                total = sum(d.values())
                main = d.get(ScopeHint.main, 0)
                # a section with real main content is main, even if site chrome follows it under the same heading
                sec.scope = ScopeHint.main if main >= max(150, 0.15 * total) else max(d.items(), key=lambda kv: kv[1])[0]
