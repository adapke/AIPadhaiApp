"""prod-181 — Unified search router.

Two surfaces over `search_aggregate.unified_search`:

  GET /api/search?q=...        Public JSON — grouped results (videos,
                               questions, examples). Powers the SPA
                               search box + any future autocomplete.

  GET /search?q=...            Public HTML page in the AI Pathshala
                               SPA shell — a search box + grouped
                               result cards that link into /concept,
                               /practice, /concept/{slug}.

Both are public (no auth) — search is a discovery surface and works
before sign-in. Pure reads, no Claude cost.
"""
from __future__ import annotations

import html
from urllib.parse import quote

from fastapi import APIRouter, Query
from fastapi.responses import HTMLResponse, JSONResponse

from .. import search_aggregate as _sa

router = APIRouter()


@router.get("/api/search")
def api_search(
    q: str = Query("", description="free-text query"),
    language: str = Query("en"),
    per_group: int = Query(8, ge=1, le=50),
) -> JSONResponse:
    """Public JSON search across concept videos + PYQs + examples."""
    results = _sa.unified_search(q, language=language, per_group=per_group)
    return JSONResponse(results.to_dict())


# --- /search HTML page (SPA chrome, matches /concept) -----------------

_CSS = (
    "body{font-family:Inter,system-ui,sans-serif;margin:0;padding:0;"
    "color:#101828;background:#f5f7fb;line-height:1.55}"
    ".topnav{background:#fff;border-bottom:1px solid #e3e6ec;padding:12px 20px;"
    "display:flex;align-items:center;justify-content:space-between;"
    "flex-wrap:wrap;gap:8px}"
    ".brand{font-weight:700;font-size:17px;color:#0b3a8a;text-decoration:none}"
    ".brand span{color:#1565d8}"
    ".nav-links{display:flex;gap:16px;flex-wrap:wrap;align-items:center}"
    ".nav-links a{color:#445;text-decoration:none;font-size:14px;font-weight:500}"
    ".nav-cta{background:#1565d8;color:#fff !important;padding:7px 14px;"
    "border-radius:6px;font-weight:600 !important}"
    ".crumb{max-width:1000px;margin:14px auto 0;padding:0 20px;font-size:13px;"
    "color:#5a6470}.crumb a{color:#1565d8;text-decoration:none}"
    ".page{max-width:1000px;margin:0 auto;padding:18px 20px 40px}"
    "h1{font-size:26px;margin:6px 0 12px;color:#0b3a8a}"
    ".searchbar{display:flex;gap:8px;margin:0 0 22px;max-width:620px}"
    ".searchbar input{flex:1;padding:11px 15px;border:1px solid #d0d6de;"
    "border-radius:8px;font-size:15px;outline:none;background:#fff}"
    ".searchbar input:focus{border-color:#1565d8;"
    "box-shadow:0 0 0 3px rgba(21,101,216,0.10)}"
    ".searchbar button{background:#1565d8;color:#fff;border:0;border-radius:8px;"
    "padding:0 20px;font-size:15px;font-weight:600;cursor:pointer}"
    ".grp{margin:22px 0}"
    ".grp h2{font-size:16px;color:#0b3a8a;margin:0 0 12px;display:flex;"
    "align-items:baseline;gap:8px}"
    ".grp .n{color:#9aa3b0;font-size:13px;font-weight:500}"
    ".card{background:#fff;border:1px solid #e3e6ec;border-radius:8px;"
    "padding:14px 16px;margin:8px 0;text-decoration:none;color:#101828;"
    "display:block;transition:border-color .15s,box-shadow .15s}"
    ".card:hover{border-color:#1565d8;box-shadow:0 2px 8px rgba(21,101,216,.08)}"
    ".card .t{font-weight:600;font-size:15px;margin-bottom:3px}"
    ".card .m{font-size:12px;color:#5a6470}"
    ".card .s{font-size:13px;color:#445;margin-top:6px}"
    ".empty{color:#5a6470;padding:32px;text-align:center;background:#fff;"
    "border-radius:8px}.empty a{color:#1565d8}"
    ".pageftr{max-width:1000px;margin:32px auto 0;padding:24px 20px;"
    "border-top:1px solid #e3e6ec;color:#5a6470;font-size:13px;display:flex;"
    "flex-wrap:wrap;gap:18px}.pageftr a{color:#1565d8;text-decoration:none}"
)


def _nav() -> str:
    return (
        '<nav class="topnav"><a class="brand" href="/home">AI '
        '<span>Pathshala</span></a><div class="nav-links">'
        '<a href="/concept">Concepts</a><a href="/syllabus">Syllabus</a>'
        '<a href="/practice">Practice</a><a href="/mastery">Mastery</a>'
        '<a class="nav-cta" href="/home">Sign in</a></div></nav>'
    )


def _slugify(name: str) -> str:
    return quote(name.strip().replace(" ", "-").lower())


def _render_results(r: _sa.SearchResults) -> str:
    if not r.query:
        return (
            "<div class='empty'>Type a concept, chapter, or question above "
            "— e.g. <b>Newton's laws</b>, <b>photosynthesis</b>, "
            "<b>quadratic equations</b>.</div>"
        )
    if r.total == 0:
        return (
            f"<div class='empty'>No results for "
            f"<b>{html.escape(r.query)}</b>. Try a broader term, or "
            "<a href='/concept'>browse all concepts</a>.</div>"
        )

    blocks = []

    if r.videos:
        cards = "".join(
            f'<a class="card" href="/concept/{_slugify(v["concept"])}">'
            f'<div class="t">▶ {html.escape(v.get("title") or v["concept"])}</div>'
            f'<div class="m">{html.escape(v.get("channel") or "Curated video")}'
            f' · {html.escape(v.get("subject") or "")}</div></a>'
            for v in r.videos
        )
        blocks.append(
            f'<section class="grp"><h2>Concept videos '
            f'<span class="n">({len(r.videos)})</span></h2>{cards}</section>'
        )

    if r.questions:
        cards = "".join(
            f'<a class="card" href="/practice?topic={quote(qn.get("chapter") or qn["question_text"][:40])}">'
            f'<div class="t">{html.escape(qn["question_text"][:140])}'
            f'{"…" if len(qn["question_text"]) > 140 else ""}</div>'
            f'<div class="m">{html.escape((qn.get("board") or "").upper())} '
            f'Class {qn.get("grade") or "?"} · {html.escape(qn.get("subject") or "")}'
            f'{" · " + html.escape(qn["chapter"]) if qn.get("chapter") else ""}'
            f'{" · " + str(qn["year"]) if qn.get("year") else ""}</div></a>'
            for qn in r.questions
        )
        blocks.append(
            f'<section class="grp"><h2>Practice questions '
            f'<span class="n">({len(r.questions)})</span></h2>{cards}</section>'
        )

    if r.examples:
        cards = "".join(
            f'<a class="card" href="/concept/{_slugify(e["concept_slug"])}">'
            f'<div class="t">💡 {html.escape(e["concept_slug"].title())}</div>'
            f'<div class="s">{html.escape(e.get("snippet") or "")}</div></a>'
            for e in r.examples
        )
        blocks.append(
            f'<section class="grp"><h2>Real-world examples '
            f'<span class="n">({len(r.examples)})</span></h2>{cards}</section>'
        )

    return "".join(blocks)


@router.get("/search", response_class=HTMLResponse)
def search_page(
    q: str = Query("", description="free-text query"),
) -> HTMLResponse:
    """Public search page in the AI Pathshala SPA shell."""
    results = _sa.unified_search(q)
    qesc = html.escape(q)
    body = (
        "<!doctype html><html lang='en'><head>"
        "<meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>{('Search: ' + qesc) if q else 'Search'} — AI Pathshala</title>"
        "<meta name='description' content='Search AI Pathshala — concept "
        "videos, past-year questions, and real-world examples across "
        "CBSE / ICSE / state boards / NEET / JEE / UPSC.'>"
        f"<style>{_CSS}</style></head><body>"
        + _nav()
        + '<div class="crumb"><a href="/home">Home</a> &nbsp;›&nbsp; '
        '<span>Search</span></div>'
        '<main class="page">'
        "<h1>Search</h1>"
        '<form class="searchbar" method="get" action="/search">'
        f'<input type="search" name="q" value="{qesc}" autofocus '
        'placeholder="Search concepts, chapters, questions…">'
        '<button type="submit">Search</button>'
        '</form>'
        + _render_results(results)
        + "</main>"
        '<footer class="pageftr">'
        '<a href="/concept">All concepts</a><a href="/syllabus">Syllabus</a>'
        '<a href="/practice">Practice tests</a><a href="/home">Home</a>'
        '<span style="margin-left:auto">Made for Indian students</span>'
        "</footer></body></html>"
    )
    return HTMLResponse(body)
