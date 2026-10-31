"""SPIDER — the news reader: what it marked, the full text of an article and its read of it, and questions you ask it
(see app/intel/spider.py and app/intel/article.py)."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from app.intel import spider

router = APIRouter(prefix="/api/spider")


@router.get("/scan")
def get_scan():
    return spider.scan()


@router.get("/article")
def get_article(url: str):
    """The article's full text (fast: no model)."""
    if not url.startswith(("http://", "https://")):
        raise HTTPException(400, "not a web link")
    return spider.article(url)


@router.get("/read")
def get_read(url: str):
    """SPIDER's read of the whole article: summary, key points, impact, and the sentences that matter."""
    if not url.startswith(("http://", "https://")):
        raise HTTPException(400, "not a web link")
    return spider.read_article(url)


@router.post("/ask")
def post_ask(body: dict[str, Any]):
    try:
        return spider.ask(str(body.get("q", "")), body.get("url") or None)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.get("/report")
def get_report(symbol: str, fresh: bool = False, peek: bool = False):
    """The full SPIDER stock report. Starts building it in the background when there is no recent one; poll until
    status == "done". peek=true only returns a stored report (never starts one) — for the SECURITY page strip."""
    from app.intel import spider_report
    try:
        if peek:
            r = spider_report.cached_only(symbol)
            return {"status": "done", "report": r} if r else {"status": "none"}
        return spider_report.report(symbol, fresh)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@router.get("/memory")
def get_memory(q: str = "", n: int = 30):
    """What the spider has stored: counts, its latest full reads, and a search over everything it saw."""
    from app.intel import spider_memory as mem
    return {"stats": mem.stats(), "reads": mem.recent_reads(min(n, 100)), "search": mem.search_news(q, days=60, n=60) if q.strip() else []}
