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
from .. import ui_nav as _nav
from ..auth import AuthUser
from ..web import current_user_optional as current_user

router = APIRouter()


_COLOR_PALETTE = {
    "green":     ("#16855f", "#e7f6ef", "Strong"),
    "yellow":    ("#a86600", "#fff4db", "Needs review"),
    "red":       ("#b42318", "#fef3f2", "Weak"),
    "untouched": ("#5a6470", "#f5f7fb", "Not started"),
}


def _anon_page() -> HTMLResponse:
    # prod-160 — Anonymous landing now shares the AI Pathshala SPA chrome
    # (top nav, breadcrumb, footer) instead of looking like a different
    # site. Matches /memory-boost / /tutor-modes / /concept design.
    body = (
        "<!doctype html><html lang='en'><head>"
        "<meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<title>Mastery Map — AI Pathshala</title>"
        "<style>"
        "body{font-family:Inter,system-ui,sans-serif;margin:0;padding:0;"
        "color:#101828;background:#f5f7fb;line-height:1.55}"
        ".topnav{background:#fff;border-bottom:1px solid #e3e6ec;"
        "padding:12px 20px;display:flex;align-items:center;"
        "justify-content:space-between;flex-wrap:wrap;gap:8px}"
        ".brand{font-weight:700;font-size:17px;color:#0b3a8a;"
        "text-decoration:none;letter-spacing:-0.01em}"
        ".brand span{color:#1565d8}"
        ".nav-links{display:flex;gap:16px;flex-wrap:wrap;align-items:center}"
        ".nav-links a{color:#445;text-decoration:none;font-size:14px;font-weight:500}"
        ".nav-cta{background:#1565d8;color:#fff !important;padding:7px 14px;"
        "border-radius:6px;font-weight:600 !important}"
        ".crumb{max-width:1100px;margin:14px auto 0;padding:0 20px;"
        "font-size:13px;color:#5a6470}"
        ".crumb a{color:#1565d8;text-decoration:none}"
        ".page{max-width:1100px;margin:0 auto;padding:18px 20px 40px}"
        ".card{background:white;padding:24px;border-radius:8px;border:1px solid #d9e0ea}"
        ".btn{display:inline-block;background:#1565d8;color:white;padding:9px 18px;"
        "border-radius:6px;text-decoration:none;font-weight:600;font-size:14px}"
        "h1{font-size:26px;margin:6px 0 12px;color:#0b3a8a}"
        ".sub{color:#5a6470;font-size:14px;margin:0 0 16px}"
        + _nav.NAV_STYLE +
        "</style></head><body>"
        + _nav.NAV_HTML + "<script>" + _nav.NAV_SCRIPT + "</script>"
        '<div class="crumb">'
        '<a href="/home">Home</a> &nbsp;›&nbsp; <span>Mastery Map</span>'
        '</div>'
        '<main class="page">'
        '<div class="card">'
        "<h1>Mastery Map</h1>"
        "<p class='sub'>Sign in to see your color-coded mastery across every chapter "
        "in your enrolled board. Each tile shows your % mastery, when you last "
        "practised, and quick links to start a practice / drill / tutor session.</p>"
        "<p><a class='btn' href='/home'>Sign in to AI Pathshala</a></p>"
        "</div></main>"
        "</body></html>"
    )
    return HTMLResponse(body)


def _render_cell(row: mastery_aggregate.ConceptMastery) -> str:
    """prod-156 — Render a single mastery tile with per-topic detail:
    color-coded mastery %, last-practised relative time, source-attempt
    breakdown (which modules contributed to the mastery score), and
    four quick-action links (Practice / Flashcards / Tutor / Memory
    Boost) that pass the topic context as query params.
    """
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

    # Source-attempt breakdown ("flashcards: 3, practice: 2") so users
    # understand *why* this topic has the score it does. Empty when no
    # signal at all.
    src = row.source_attempts or {}
    breakdown_parts = []
    for module, count in sorted(src.items()):
        if count > 0:
            breakdown_parts.append(f"{module}: {count}")
    breakdown = " · ".join(breakdown_parts) if breakdown_parts else "no attempts yet"

    topic_q = quote(row.title)
    title = html.escape(row.title)
    subject = html.escape(row.subject)
    decay = row.decay_state or "fresh"
    decay_chip = ""
    if decay == "decayed":
        decay_chip = '<span class="decay">⏰ Decayed — review now</span>'
    elif decay == "stale":
        decay_chip = '<span class="decay">⏳ Stale</span>'

    # Quick-action links. Each one passes the topic name + (subject)
    # context so the destination page can pre-filter to the topic
    # rather than reset to the generic catalog. /chat additionally
    # gets a `q=` prompt so the tutor opens with the topic already
    # in conversation context.
    chat_q = quote(f"Teach me {row.title} ({row.subject})")
    actions = (
        f'<div class="actions">'
        f'<a class="act-btn" href="/practice?topic={topic_q}" title="Practice this topic">📝 Practice</a>'
        f'<a class="act-btn" href="/flashcards?topic={topic_q}" title="Review flashcards">📚 Flashcards</a>'
        f'<a class="act-btn" href="/chat?topic={topic_q}&q={chat_q}" title="Ask the AI tutor">💬 Tutor</a>'
        f'<a class="act-btn" href="/memory-boost" title="Daily 3-question drill">🔥 Drill</a>'
        f'</div>'
    )

    return (
        f'<div class="tile" style="background:{bg};border-left:4px solid {fg}">'
        f'<div class="title">{title}</div>'
        f'<div class="sub">{subject} · {sub}</div>'
        f'<div class="pill" style="color:{fg}">{label} · {pct}%</div>'
        f'{decay_chip}'
        f'<div class="breakdown">Signals: {html.escape(breakdown)}</div>'
        f'{actions}'
        f'</div>'
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

    # prod-160 — Shared AI Pathshala SPA chrome (top nav + breadcrumb +
    # footer) so /mastery matches /concept's visual style.
    body = (
        "<!doctype html><html lang='en'><head>"
        "<meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>Mastery Map · {html.escape(board)} Class {grade} — AI Pathshala</title>"
        "<style>"
        "body{font-family:Inter,system-ui,sans-serif;margin:0;padding:0;"
        "color:#101828;background:#f5f7fb;line-height:1.55}"
        # Top nav
        ".topnav{background:#fff;border-bottom:1px solid #e3e6ec;"
        "padding:12px 20px;display:flex;align-items:center;"
        "justify-content:space-between;flex-wrap:wrap;gap:8px}"
        ".brand{font-weight:700;font-size:17px;color:#0b3a8a;"
        "text-decoration:none;letter-spacing:-0.01em}"
        ".brand span{color:#1565d8}"
        ".nav-links{display:flex;gap:16px;flex-wrap:wrap;align-items:center}"
        ".nav-links a{color:#445;text-decoration:none;font-size:14px;font-weight:500}"
        ".nav-links a:hover{color:#1565d8}"
        ".nav-cta{background:#1565d8;color:#fff !important;padding:7px 14px;"
        "border-radius:6px;font-weight:600 !important}"
        ".crumb{max-width:1100px;margin:14px auto 0;padding:0 20px;"
        "font-size:13px;color:#5a6470}"
        ".crumb a{color:#1565d8;text-decoration:none}"
        ".page{max-width:1100px;margin:0 auto;padding:18px 20px 40px}"
        ".pageftr{max-width:1100px;margin:32px auto 0;padding:24px 20px;"
        "border-top:1px solid #e3e6ec;color:#5a6470;font-size:13px;"
        "display:flex;flex-wrap:wrap;gap:18px}"
        ".pageftr a{color:#1565d8;text-decoration:none}"
        # Body
        "h1{font-size:26px;margin:6px 0 4px;color:#0b3a8a}"
        ".sub{color:#5a6470;font-size:14px;margin:0 0 16px}"
        ".summary{display:flex;gap:12px;margin:12px 0 20px;flex-wrap:wrap}"
        ".summary-pill{background:white;border:1px solid #d9e0ea;padding:8px 14px;"
        "border-radius:8px;display:flex;align-items:center;gap:8px;font-size:14px}"
        ".dot{width:10px;height:10px;border-radius:50%;display:inline-block}"
        ".chips{display:flex;gap:8px;flex-wrap:wrap;margin:12px 0 20px}"
        ".chip{background:white;border:1px solid #d9e0ea;color:#101828;"
        "padding:6px 12px;border-radius:999px;text-decoration:none;font-size:13px}"
        ".chip.active{background:#1565d8;color:white;border-color:#1565d8}"
        ".grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));"
        "gap:12px}"
        # prod-156 tile redesign — now a full mini-card with actions
        ".tile{background:white;padding:14px;border-radius:8px;"
        "border-left:4px solid #d9e0ea;display:flex;flex-direction:column;gap:4px}"
        ".tile .title{font-weight:600;font-size:15px;line-height:1.3;color:#101828}"
        ".tile .sub{color:#5a6470;font-size:12px;margin:0}"
        ".tile .pill{font-size:11px;font-weight:600;text-transform:uppercase;"
        "letter-spacing:0.3px;margin:4px 0}"
        ".tile .decay{display:inline-block;background:#fef3f2;color:#b42318;"
        "padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600;"
        "margin:2px 0}"
        ".tile .breakdown{color:#5a6470;font-size:11px;margin:6px 0 8px;"
        "font-style:italic}"
        ".tile .actions{display:flex;flex-wrap:wrap;gap:6px;margin-top:auto;"
        "padding-top:8px;border-top:1px solid #f0f2f7}"
        ".tile .act-btn{background:#eef3fc;color:#0b3a8a;padding:6px 10px;"
        "border-radius:6px;text-decoration:none;font-size:12px;font-weight:600;"
        "transition:background 0.15s}"
        ".tile .act-btn:hover{background:#1565d8;color:white}"
        ".empty{background:white;padding:32px;border-radius:8px;text-align:center;"
        "color:#5a6470}"
        ".empty a{color:#1565d8}"
        + _nav.NAV_STYLE +
        "</style></head><body>"
        # Shared persona nav (ui_nav) — matches the rest of the app
        + _nav.NAV_HTML + "<script>" + _nav.NAV_SCRIPT + "</script>"
        # Breadcrumb
        '<div class="crumb">'
        '<a href="/home">Home</a> &nbsp;›&nbsp; <span>Mastery Map</span>'
        '</div>'
        # Main page
        '<main class="page">'
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
        '</main>'
        # Footer
        '<footer class="pageftr">'
        '<a href="/memory-boost">🔥 Today\'s 3-question drill</a>'
        '<a href="/syllabus">Syllabus</a>'
        '<a href="/practice">Practice tests</a>'
        '<a href="/concept">Concept videos</a>'
        '<span style="margin-left:auto">Made for Indian students</span>'
        '</footer>'
        "</body></html>"
    )
    return HTMLResponse(body)
