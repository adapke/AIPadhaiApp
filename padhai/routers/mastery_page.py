"""prod-141 — Server-rendered /mastery page.

Consumes the prod-135 mastery aggregator and renders a color-coded
grid of the student's topic mastery: green / yellow / red / untouched.

URLs:
    GET /mastery                            — auto-detect board/grade
    GET /mastery?board=CBSE&grade=10        — explicit
    GET /mastery?board=CBSE&grade=10&subject=Math — filter to one subject

Auth: required (Bearer token via cookie or header). Falls back to a
"sign in to see your mastery map" page when anonymous.

Design: pure server-rendered HTML, no SPA framework. Mobile-friendly
2-column grid that collapses to 1 column on narrow screens. Each cell
is a colored tile with topic title + mastery percentage. Click a
tile → navigate to /tutor?topic=... (existing flow).
"""
from __future__ import annotations

import html
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse

from .. import mastery_aggregate
from ..auth import AuthUser
from ..web import current_user

router = APIRouter()


_COLOR_PALETTE = {
    "green":     ("#16855f", "#e7f6ef", "Strong"),
    "yellow":    ("#a86600", "#fff4db", "Needs review"),
    "red":       ("#b42318", "#fef3f2", "Weak"),
    "untouched": ("#5a6470", "#f5f7fb", "Not started"),
}


def _anon_page() -> HTMLResponse:
    body = (
        "<!doctype html><html lang='en'><head>"
        "<meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<title>Mastery Map — AI Pathshala</title>"
        "<style>body{font-family:Inter,system-ui,sans-serif;max-width:640px;"
        "margin:60px auto;padding:0 16px;color:#101828;text-align:center}"
        "h1{font-size:24px}a{color:#1565d8}</style>"
        "</head><body>"
        "<h1>Mastery Map</h1>"
        "<p>Sign in to see your color-coded mastery across every chapter "
        "in your enrolled board.</p>"
        "<p><a href='/home'>← AI Pathshala home</a></p>"
        "</body></html>"
    )
    return HTMLResponse(body)


def _render_cell(row: mastery_aggregate.ConceptMastery) -> str:
    fg, bg, label = _COLOR_PALETTE.get(row.color_state, _COLOR_PALETTE["untouched"])
    pct = round(row.mastery * 100)
    last_practised = row.last_practised
    if last_practised is None:
        sub = "Not started yet"
    else:
        # Days ago
        import time
        days = max(0, int((time.time() - last_practised) / 86400))
        if days == 0:
            sub = "Practised today"
        elif days == 1:
            sub = "Practised yesterday"
        else:
            sub = f"Practised {days}d ago"

    slug = quote(row.topic_key.replace(" ", "-"))
    title = html.escape(row.title)
    subject = html.escape(row.subject)
    return (
        f'<a class="tile" href="/tutor?topic={slug}" '
        f'style="background:{bg};border-left:4px solid {fg}">'
        f'<div class="title">{title}</div>'
        f'<div class="sub">{subject} · {sub}</div>'
        f'<div class="pill" style="color:{fg}">{label} · {pct}%</div>'
        f'</a>'
    )


@router.get("/mastery", response_class=HTMLResponse)
def mastery_page(
    board: str | None = Query(None),
    grade: int | None = Query(None, ge=1, le=12),
    subject: str | None = Query(None),
    user: AuthUser | None = Depends(current_user),
) -> HTMLResponse:
    """prod-141 — Server-rendered mastery map.

    When `board` / `grade` are missing, picks the user's most recent
    enrolled exam-pack as a sensible default. Falls back to CBSE
    Class 10 when no enrollment exists.
    """
    if user is None:
        return _anon_page()

    # Default board+grade from the user's most recent exam-pack
    # enrollment. Falls back to CBSE Class 10 if nothing on file.
    if not board or not grade:
        try:
            from .. import exam_taxonomy
            scope = exam_taxonomy.taxonomy_scope_for_user(user.id)
            if scope:
                board = board or (scope.board_hint or "CBSE")
                # Default grade — exam-pack typically encodes the grade
                # in board_hint or chapter_titles; fall back to 10.
        except Exception:
            pass
        board = board or "CBSE"
        grade = grade or 10

    rows = mastery_aggregate.build_mastery_map(
        user_id=user.id, board=board, grade=grade, subject=subject,
    )
    summary = mastery_aggregate.summarise(rows)

    # Filter chips (subjects observed in rows)
    subjects = sorted({r.subject for r in rows})
    subject_chips = []
    for s in subjects:
        active = "active" if (subject and s.lower() == subject.lower()) else ""
        href = f"/mastery?board={quote(board)}&grade={grade}"
        if s.lower() != "all":
            href += f"&subject={quote(s)}"
        subject_chips.append(
            f'<a class="chip {active}" href="{href}">{html.escape(s)}</a>'
        )
    if subject:
        # Add a "clear filter" chip
        subject_chips.insert(0, (
            f'<a class="chip" href="/mastery?board={quote(board)}'
            f'&grade={grade}">All subjects</a>'
        ))

    # Render tiles
    if not rows:
        tiles_html = (
            '<div class="empty">No curriculum loaded for your board+grade '
            'yet. Try <a href="/syllabus">browsing the syllabus</a> first.</div>'
        )
    else:
        tiles_html = "".join(_render_cell(r) for r in rows)

    body = (
        "<!doctype html><html lang='en'><head>"
        "<meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>Mastery Map · {html.escape(board)} Class {grade} — AI Pathshala</title>"
        "<style>"
        "body{font-family:Inter,system-ui,sans-serif;max-width:1100px;"
        "margin:0 auto;padding:18px 16px;color:#101828;background:#f5f7fb}"
        "h1{font-size:26px;margin:6px 0 4px}"
        ".sub{color:#5a6470;font-size:14px;margin:0 0 16px}"
        ".summary{display:flex;gap:12px;margin:12px 0 20px;flex-wrap:wrap}"
        ".summary-pill{background:white;border:1px solid #d9e0ea;padding:8px 14px;"
        "border-radius:8px;display:flex;align-items:center;gap:8px;font-size:14px}"
        ".dot{width:10px;height:10px;border-radius:50%;display:inline-block}"
        ".chips{display:flex;gap:8px;flex-wrap:wrap;margin:12px 0 20px}"
        ".chip{background:white;border:1px solid #d9e0ea;color:#101828;"
        "padding:6px 12px;border-radius:999px;text-decoration:none;font-size:13px}"
        ".chip.active{background:#1565d8;color:white;border-color:#1565d8}"
        ".grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));"
        "gap:10px}"
        ".tile{display:block;background:white;padding:12px;border-radius:8px;"
        "text-decoration:none;color:#101828;border-left:4px solid #d9e0ea;"
        "transition:transform 0.1s}"
        ".tile:hover{transform:translateY(-1px);box-shadow:0 2px 6px rgba(0,0,0,0.06)}"
        ".tile .title{font-weight:600;font-size:14px;line-height:1.3}"
        ".tile .sub{color:#5a6470;font-size:12px;margin:4px 0 6px}"
        ".tile .pill{font-size:11px;font-weight:600;text-transform:uppercase;"
        "letter-spacing:0.3px}"
        ".empty{background:white;padding:32px;border-radius:8px;text-align:center;"
        "color:#5a6470}"
        ".empty a{color:#1565d8}"
        ".foot{margin-top:24px;color:#5a6470;font-size:13px;text-align:center}"
        ".foot a{color:#1565d8;margin:0 8px}"
        "</style></head><body>"
        f"<h1>Mastery Map</h1>"
        f"<p class='sub'>{html.escape(board)} · Class {grade}"
        f"{' · ' + html.escape(subject) if subject else ''}</p>"
        # Color-state summary pills
        "<div class='summary'>"
        f"<div class='summary-pill'><span class='dot' style='background:#16855f'></span>"
        f"<b>{summary['green']}</b> strong</div>"
        f"<div class='summary-pill'><span class='dot' style='background:#a86600'></span>"
        f"<b>{summary['yellow']}</b> review needed</div>"
        f"<div class='summary-pill'><span class='dot' style='background:#b42318'></span>"
        f"<b>{summary['red']}</b> weak</div>"
        f"<div class='summary-pill'><span class='dot' style='background:#5a6470'></span>"
        f"<b>{summary['untouched']}</b> not started</div>"
        "</div>"
        # Subject filter chips
        f"<div class='chips'>{''.join(subject_chips)}</div>"
        # Tile grid
        f"<div class='grid'>{tiles_html}</div>"
        "<div class='foot'>"
        "<a href='/memory-boost'>📚 Today's 3-question drill</a> · "
        "<a href='/home'>← Home</a>"
        "</div>"
        "</body></html>"
    )
    return HTMLResponse(body)
