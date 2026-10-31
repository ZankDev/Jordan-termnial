"""Full-text reader for news links: fetches a publisher's page and keeps only the article (title, byline, date, image,
paragraphs), so the terminal can show it in-app and the SPIDER can read all of it.

Extraction, in order: the page's JSON-LD `articleBody` (most news sites publish the whole text there); otherwise a
readability-style pass — every paragraph scores its parent (and half to its grandparent) by text length, links and
boilerplate count against it, and the best-scoring container's paragraphs / sub-headings / list items are kept.
Paywalled or script-only pages give little text: the result says `partial` and carries the feed's own summary.

Only public internet addresses are fetched (every redirect hop is checked), so a link can never make the server call
something on this machine or the local network. Results are cached for an hour.
"""
from __future__ import annotations

import ipaddress
import json
import re
import socket
import time
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
import warnings

from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)  # SEC documents are XHTML; lxml's HTML parser reads them fine

# a complete browser header set: several publishers (MarketWatch, …) refuse requests that only send a User-Agent
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9", "Accept-Encoding": "gzip, deflate",
    "Sec-Ch-Ua": '"Chromium";v="130", "Google Chrome";v="130", "Not?A_Brand";v="99"', "Sec-Ch-Ua-Mobile": "?0", "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "document", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Site": "cross-site", "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1", "Referer": "https://www.google.com/",
}
MAX_BYTES = 4_000_000


class _Done(Exception):
    """Internal: the text is already complete (an SEC filing), skip the web-page extraction."""
_cache: dict[str, tuple[float, dict[str, Any]]] = {}
BOILER = re.compile(r"cookie|subscribe|sign up|newsletter|all rights reserved|advertisement|terms of (use|service)|privacy policy|"
                    r"follow us|share this|read more:|click here|download the app|getty images|reuters/|copyright", re.I)


def _public(url: str) -> bool:
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        return False
    try:
        infos = socket.getaddrinfo(p.hostname, p.port or (443 if p.scheme == "https" else 80), proto=socket.IPPROTO_TCP)
    except OSError:
        return False
    return bool(infos) and all(ipaddress.ip_address(i[4][0]).is_global for i in infos)


def _get(url: str) -> requests.Response:
    for _ in range(6):  # follow redirects by hand so every hop is checked
        if not _public(url):
            raise ValueError("that link points somewhere the reader won't open")
        r = requests.get(url, headers=HEADERS, timeout=12, allow_redirects=False, stream=True)
        if r.is_redirect or r.status_code in (301, 302, 303, 307, 308):
            url = urljoin(url, r.headers.get("location", ""))
            r.close()
            continue
        if "pdf" in r.headers.get("content-type", "").lower():  # don't download a whole PDF just to say it's a PDF
            r.close()
            r._content = b""  # noqa: SLF001
            r.url = url
            return r
        body = r.raw.read(MAX_BYTES, decode_content=True)
        r._content = body  # noqa: SLF001
        r.url = url
        return r
    raise ValueError("too many redirects")


def _google_news(url: str) -> str:
    """news.google.com/rss/articles/<id> is a script redirect: ask Google's own decoder endpoint for the publisher's URL
    (the page carries a signature + timestamp for it). Returns the input unchanged if anything fails."""
    m = re.search(r"news\.google\.com/(?:rss/)?articles/([^?/#]+)", url)
    if not m:
        return url
    gid = m.group(1)
    try:
        page = requests.get(f"https://news.google.com/rss/articles/{gid}", headers=HEADERS, timeout=10).text
        sg, ts = re.search(r'data-n-a-sg="([^"]+)"', page), re.search(r'data-n-a-ts="([^"]+)"', page)
        if not (sg and ts):
            return url
        req = ["Fbv4je", f'["garturlreq",[["X","X",["X","X"],null,null,1,1,"US:en",null,1,null,null,null,null,null,0,1],"X","X",1,[1,1,1],1,1,null,0,0,null,0],"{gid}",{ts.group(1)},"{sg.group(1)}"]']
        r = requests.post("https://news.google.com/_/DotsSplashUi/data/batchexecute", data={"f.req": json.dumps([[req]])},
                          headers={**HEADERS, "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"}, timeout=10)
        real = json.loads(json.loads(r.text.split("\n\n")[1])[0][2])[1]
        return real if isinstance(real, str) and real.startswith("http") else url
    except Exception:  # noqa: BLE001
        return url


def _sec_text(html: bytes) -> list[dict[str, str]]:
    """Paragraphs from an SEC document (8-K, EX-99.1 press release, 10-Q): filings are walls of <p>/<div>/<font> and tables;
    keep the prose, drop number-only table cells and page furniture."""
    soup = BeautifulSoup(html, "lxml")
    for t in soup(["script", "style", "ix:header", "head"]):
        t.decompose()
    out, seen = [], set()
    for line in soup.get_text("\n").split("\n"):
        t = _clean(line)
        if len(t) < 30 or t in seen:
            continue
        digits = sum(c.isdigit() for c in t)
        if digits > len(t) * 0.35 or re.fullmatch(r"(Table of Contents|Page \d+.*|\d+)", t, re.I):
            continue
        seen.add(t)
        out.append({"k": "h" if t.isupper() and len(t) < 90 else "p", "t": t[:3000]})
    return out[:140]


def _sec_get(u: str, hdr: dict[str, str]) -> bytes:
    for attempt in range(3):  # EDGAR drops connections now and then
        try:
            r = requests.get(u, headers=hdr, timeout=25)
            r.raise_for_status()
            return r.content
        except requests.RequestException:
            if attempt == 2:
                raise
            time.sleep(1.5 * (attempt + 1))
    return b""


def _sec_filing(url: str) -> dict[str, Any] | None:
    """An EDGAR filing: from its index page read the press release (EX-99.x) first, then the main document; a direct
    document link is read as is. Uses SEC's required identifying header."""
    from app.api.invest_routes import SEC_UA
    hdr = {**SEC_UA, "Accept": "text/html,*/*"}
    docs: list[tuple[str, str]] = []
    if re.search(r"-index\.html?$", url):
        idx = BeautifulSoup(_sec_get(url, hdr), "lxml")
        company = _clean(idx.select_one(".companyName").get_text(" ")) if idx.select_one(".companyName") else ""
        form = _clean(idx.select_one("#formName").get_text(" ")) if idx.select_one("#formName") else ""
        for tr in idx.select("table.tableFile tr"):
            tds, a = tr.find_all("td"), tr.find("a")
            if len(tds) < 4 or not a:
                continue
            kind, href = tds[3].get_text(strip=True).upper(), a["href"].replace("/ix?doc=", "")
            if href.endswith((".htm", ".html", ".txt")) and (kind.startswith("EX-99") or kind in ("8-K", "10-Q", "10-K", "6-K", "8-K/A")):
                docs.append((kind, urljoin("https://www.sec.gov", href)))
        docs.sort(key=lambda d: 0 if d[0].startswith("EX-99") else 1)
        company = re.split(r"\s*\((?:Filer|Subject|\d{10})\)|\s+CIK", company)[0].strip()
        form = re.sub(r"^Form\s+", "", form).rstrip(": ").replace(" - ", " · ")
        title = f"{form or 'SEC filing'} · {company}".strip(" ·")
    else:
        docs, title = [("DOC", url)], "SEC filing"
    paras: list[dict[str, str]] = []
    for kind, u in docs[:2]:
        txt = _sec_text(_sec_get(u, hdr))
        if txt:
            paras += ([{"k": "h", "t": "PRESS RELEASE (EX-99)" if kind.startswith("EX-99") else f"{kind} — MAIN DOCUMENT"}] if len(docs) > 1 else []) + txt
        if sum(len(p["t"]) for p in paras) > 25000:
            break
    return {"title": title, "paragraphs": paras[:160], "site": "SEC EDGAR"} if paras else None


def _meta(soup: BeautifulSoup, *names: str) -> str | None:
    for n in names:
        m = soup.find("meta", attrs={"property": n}) or soup.find("meta", attrs={"name": n}) or soup.find("meta", attrs={"itemprop": n})
        if m and m.get("content"):
            return m["content"].strip()
    return None


def _ld_body(soup: BeautifulSoup) -> tuple[str | None, dict[str, Any]]:
    """articleBody (+ author/date) from JSON-LD blocks, searched recursively (sites nest it in @graph / lists)."""
    found: dict[str, Any] = {}

    def walk(o: Any) -> None:
        if isinstance(o, dict):
            if isinstance(o.get("articleBody"), str) and len(o["articleBody"]) > len(found.get("body", "")):
                found["body"] = o["articleBody"]
                found["date"] = o.get("datePublished") or found.get("date")
                a = o.get("author")
                if isinstance(a, list) and a:
                    a = a[0]
                found["author"] = (a.get("name") if isinstance(a, dict) else a) or found.get("author")
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    for s in soup.find_all("script", type="application/ld+json"):
        try:
            walk(json.loads(s.string or s.get_text() or "{}"))
        except (ValueError, TypeError):
            continue
    return found.get("body"), found


def _clean(t: str) -> str:
    return re.sub(r"\s+", " ", t).strip()


def _split_long(paras: list[dict[str, str]], limit: int = 900) -> list[dict[str, str]]:
    """Break run-on blocks into ~600-character paragraphs at sentence ends (also where a sentence was glued to the next,
    'Loonie.USD/CAD'), so the reader stays readable and the spider reads it paragraph by paragraph."""
    out = []
    for p in paras:
        if p["k"] != "p" or len(p["t"]) <= limit:
            out.append(p)
            continue
        sents = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9“\"])|(?<=[.!?][\"”’)])\s+(?=[A-Z0-9“\"])|(?<=[a-z][.!?])(?=[A-Z][a-z])", p["t"])
        cur = ""
        for s in sents:
            if cur and len(cur) + len(s) > 600:
                out.append({"k": "p", "t": cur.strip()})
                cur = ""
            cur += s + " "
        if cur.strip():
            out.append({"k": "p", "t": cur.strip()})
    return out


def _paragraphs_from_dom(soup: BeautifulSoup) -> list[dict[str, str]]:
    for t in soup(["script", "style", "noscript", "nav", "footer", "header", "aside", "form", "svg", "iframe", "button", "figure", "figcaption", "template"]):
        t.decompose()
    for t in soup.select('[class*="related"], [class*="newsletter"], [class*="promo"], [class*="share"], [class*="social"], [class*="comment"], [id*="comment"], [class*="advert"], [class*="paywall"]'):
        t.decompose()
    scores: dict[int, float] = {}
    nodes: dict[int, Any] = {}
    for p in soup.find_all("p"):
        txt = _clean(p.get_text(" "))
        if len(txt) < 40 or BOILER.search(txt):
            continue
        link_chars = sum(len(a.get_text()) for a in p.find_all("a"))
        s = len(txt) * (1 - min(0.9, link_chars / max(1, len(txt)))) + 10 * txt.count(",")
        par = p.parent
        if par is None:
            continue
        scores[id(par)] = scores.get(id(par), 0) + s
        nodes[id(par)] = par
        gp = par.parent
        if gp is not None:
            scores[id(gp)] = scores.get(id(gp), 0) + s / 2
            nodes[id(gp)] = gp
    if not scores:
        return []
    best = nodes[max(scores, key=scores.get)]
    out, seen = [], set()
    for el in best.find_all(["p", "h2", "h3", "li", "blockquote"]):
        if el.find_parent(["li", "blockquote"]) and el.name == "p":
            continue
        txt = _clean(el.get_text(" "))
        if not txt or txt in seen or BOILER.search(txt):
            continue
        kind = "h" if el.name in ("h2", "h3") else "li" if el.name == "li" else "q" if el.name == "blockquote" else "p"
        if kind == "p" and len(txt) < 30:
            continue
        if kind == "li" and len(txt) < 25:
            continue
        seen.add(txt)
        out.append({"k": kind, "t": txt[:3000]})
    return out


def fetch(url: str, fallback: dict[str, Any] | None = None) -> dict[str, Any]:
    hit = _cache.get(url)
    if hit and time.time() - hit[0] < 3600:
        return hit[1]
    fb = fallback or {}
    out: dict[str, Any] = {"url": url, "title": fb.get("title") or "", "source": fb.get("source") or urlparse(url).hostname, "ts": fb.get("ts"),
                           "author": None, "published": None, "image": None, "paragraphs": [], "partial": False, "error": None}
    try:
        if re.match(r"https://www\.sec\.gov/Archives/", url):  # company filings: read the documents, not the index page
            f = _sec_filing(url)
            if not f:
                raise ValueError("no readable document in this filing")
            out.update(title=fb.get("title") or f["title"], site=f["site"], paragraphs=f["paragraphs"])
            raise _Done
        real = _google_news(url)
        if real != url:
            out["source"] = f"{out['source']} · {urlparse(real).hostname.replace('www.', '')}"
        r = _get(real)
        out["final_url"] = r.url
        if r.status_code >= 400:
            raise ValueError("this publisher blocks outside readers (paywall / bot wall)" if r.status_code in (401, 403, 451)
                             else f"the publisher answered {r.status_code}")
        ctype = r.headers.get("content-type", "")
        if "pdf" in ctype:
            raise ValueError("this one is a PDF document — open the original to read it")
        if "html" not in ctype and "xml" not in ctype:
            raise ValueError("that link is not a web page")
        soup = BeautifulSoup(r.content, "lxml")
        out["title"] = _meta(soup, "og:title", "twitter:title") or (soup.title.get_text(strip=True) if soup.title else "") or out["title"]
        out["image"] = _meta(soup, "og:image", "twitter:image")
        out["site"] = _meta(soup, "og:site_name")
        out["author"] = _meta(soup, "author", "article:author", "parsely-author")
        out["published"] = _meta(soup, "article:published_time", "og:published_time", "datePublished", "pubdate", "date")
        out["lead"] = _meta(soup, "og:description", "description")
        body, ld = _ld_body(soup)
        paras: list[dict[str, str]] = []
        if body and len(body) > 500:
            paras = [{"k": "p", "t": _clean(x)} for x in re.split(r"\n\s*\n|\n|(?<=[.!?])\s{2,}", body) if len(_clean(x)) > 25]
            out["author"] = out["author"] or (ld.get("author") if isinstance(ld.get("author"), str) else None)
            out["published"] = out["published"] or ld.get("date")
        dom = _paragraphs_from_dom(soup)
        # the page's own paragraphs read better than a JSON-LD body (often one run-on block): prefer them unless clearly shorter
        if len(dom) >= 3 and sum(len(p["t"]) for p in dom) > sum(len(p["t"]) for p in paras) * 0.6:
            paras = dom
        out["paragraphs"] = _split_long(paras)[:140]
    except _Done:
        pass
    except Exception as e:  # noqa: BLE001
        out["error"] = str(e)[:200] if isinstance(e, ValueError) else f"couldn't load the page ({type(e).__name__})"
    chars = sum(len(p["t"]) for p in out["paragraphs"])
    if chars < 600:
        out["partial"] = True
        lead = fb.get("desc") or out.get("lead")
        if lead and not any(lead[:60] in p["t"] for p in out["paragraphs"]):
            out["paragraphs"] = [{"k": "p", "t": lead}] + out["paragraphs"]
    out["chars"] = sum(len(p["t"]) for p in out["paragraphs"])
    out["words"] = sum(len(p["t"].split()) for p in out["paragraphs"])
    if len(_cache) > 300:
        for k in sorted(_cache, key=lambda k: _cache[k][0])[:100]:
            _cache.pop(k, None)
    _cache[url] = (time.time(), out)
    return out
