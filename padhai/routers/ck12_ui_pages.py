"""prod-142..145 — Server-rendered SPA-wiring pages for the CK-12 borrows.

Bundles the four small UI pages that surface the prod-135..140 APIs:

  GET  /tutor-modes
      Demo page for prod-136 tutor modes — chips that POST to the
      tutor-message endpoint with `mode=<key>`. Lets users try each
      lens without needing the full /tutor SPA.

  GET  /memory-boost
      prod-139 daily 3-question drill UI. Server-rendered.

  GET  /teacher/class/{class_id}/heat-map?org_id=...&board=CBSE&grade=10
      prod-140 teacher dashboard heat-map grid + weak-topics list.

  GET  /admin/examples-queue
      prod-137 curator queue — pending Real-World Examples with
      1-click approve/reject (POSTs to the existing admin endpoints).

All four pages are server-rendered HTML; no SPA framework. Auth via
the standard `current_user` dep. Admin pages additionally gate on
the prod-9 admin role injector (paths under /api/admin/* are already
gated; the /admin/* HTML pages here run our own admin check).
"""
from __future__ import annotations

import html
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse

from .. import concept_examples as _ex
from .. import memory_boost as _mb
from .. import tutor_modes as _modes
from ..auth import AuthUser
from ..web import current_user

router = APIRouter()


# Shared CSS + chrome (kept minimal — pages all follow concept_seo style)
_BASE_CSS = """
body{font-family:Inter,system-ui,sans-serif;max-width:1100px;margin:0 auto;
padding:18px 16px;color:#101828;background:#f5f7fb}
h1{font-size:26px;margin:6px 0 4px}
h2{font-size:18px;margin:18px 0 8px}
.sub{color:#5a6470;font-size:14px;margin:0 0 16px}
.card{background:white;padding:18px;border-radius:8px;margin:12px 0;
border:1px solid #d9e0ea}
.btn{display:inline-block;background:#1565d8;color:white;padding:8px 16px;
border-radius:6px;text-decoration:none;border:0;cursor:pointer;font-size:14px}
.btn.secondary{background:#5a6470}
.btn.danger{background:#b42318}
.btn.success{background:#16855f}
.chip{display:inline-flex;align-items:center;gap:6px;background:white;
border:1px solid #d9e0ea;color:#101828;padding:8px 14px;border-radius:999px;
text-decoration:none;font-size:14px;margin:4px 4px 4px 0;cursor:pointer}
.chip.active{background:#1565d8;color:white;border-color:#1565d8}
.foot{margin-top:24px;color:#5a6470;font-size:13px;text-align:center}
.foot a{color:#1565d8;margin:0 8px}
.empty{color:#5a6470;padding:32px;text-align:center}
.tag{display:inline-block;padding:2px 8px;border-radius:4px;
font-size:11px;text-transform:uppercase;letter-spacing:0.3px}
.tag-critical{background:#fef3f2;color:#b42318}
.tag-warmup{background:#fff4db;color:#a86600}
.tag-fresh{background:#eaf2ff;color:#1565d8}
"""


def _anon_landing(title: str, sub: str) -> HTMLResponse:
    body = (
        "<!doctype html><html lang='en'><head>"
        "<meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>{html.escape(title)} — AI Pathshala</title>"
        f"<style>{_BASE_CSS}</style></head><body>"
        f"<div class='card'><h1>{html.escape(title)}</h1>"
        f"<p class='sub'>{html.escape(sub)}</p>"
        "<p><a class='btn' href='/home'>Sign in to AI Pathshala</a></p>"
        "</div></body></html>"
    )
    return HTMLResponse(body)


# ---------- prod-142 — /tutor-modes ----------


@router.get("/tutor-modes", response_class=HTMLResponse)
def tutor_modes_page(
    user: AuthUser | None = Depends(current_user),
) -> HTMLResponse:
    """prod-142 — Tutor Mode demo page (chips).

    Renders the 6 modes from `tutor_modes.MODES` as colored chips
    with their bilingual labels. Each chip opens a session and
    sends a test message in that mode via the existing API.
    """
    if user is None:
        return _anon_landing(
            "Tutor modes",
            "Pick a conversation lens — quick board recall, JEE drill, NEET MCQ, "
            "CBSE 5-mark, desi analogy, or rural-simple.",
        )

    chips_html = "".join(
        f'<button class="chip" type="button" data-mode="{m["key"]}" '
        f'data-label="{html.escape(m["label_en"])}">'
        f'<span style="font-size:18px">{m["icon"]}</span>'
        f'<span><b>{html.escape(m["label_en"])}</b><br>'
        f'<span class="sub" style="font-size:11px">{html.escape(m["one_line_en"])}</span></span>'
        f'</button>'
        for m in _modes.list_modes()
    )

    body = (
        "<!doctype html><html lang='en'><head>"
        "<meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<title>Tutor Modes — AI Pathshala</title>"
        f"<style>{_BASE_CSS}"
        ".chip{flex-direction:row;text-align:left;align-items:flex-start;"
        "max-width:280px;padding:12px 14px;line-height:1.2}"
        ".chip:hover{transform:translateY(-1px);box-shadow:0 2px 6px rgba(0,0,0,0.06)}"
        ".chip.active{background:#1565d8;color:white;border-color:#1565d8}"
        ".chip.active .sub{color:#cdd9eb}"
        "textarea{width:100%;padding:10px;border:1px solid #d9e0ea;"
        "border-radius:8px;font-family:inherit;font-size:14px;"
        "min-height:80px;resize:vertical}"
        "#reply{white-space:pre-wrap;padding:12px;background:#f5f7fb;"
        "border-radius:6px;margin-top:12px;font-size:14px;line-height:1.5}"
        "</style></head><body>"
        "<h1>Tutor modes</h1>"
        "<p class='sub'>Pick a lens — each mode tunes the tutor for a different "
        "exam context. Hindi labels render in Devanagari for non-English locales.</p>"
        f"<div class='card'><div style='display:flex;flex-wrap:wrap;gap:10px'>{chips_html}</div></div>"
        "<div class='card'>"
        "<h2>Try a question</h2>"
        "<p class='sub' id='mode-indicator'>Pick a mode above first.</p>"
        "<textarea id='q' placeholder='Ask anything — e.g. \"Explain Newton's first law\"'></textarea>"
        "<div style='margin-top:8px'><button class='btn' onclick='askTutor()'>Ask in this mode →</button></div>"
        "<div id='reply'></div>"
        "</div>"
        "<div class='foot'><a href='/home'>← Home</a> · <a href='/mastery'>Mastery map</a></div>"
        "<script>"
        "let selectedMode=null;"
        "document.querySelectorAll('.chip').forEach(c=>c.addEventListener('click',()=>{"
        "  document.querySelectorAll('.chip').forEach(x=>x.classList.remove('active'));"
        "  c.classList.add('active');"
        "  selectedMode=c.dataset.mode;"
        "  document.getElementById('mode-indicator').textContent='Mode: '+c.dataset.label;"
        "}));"
        "async function askTutor(){"
        "  if(!selectedMode){alert('Pick a mode first.');return;}"
        "  const q=document.getElementById('q').value.trim();"
        "  if(!q){return;}"
        "  const tok=localStorage.getItem('pathshala_token');"
        "  if(!tok){alert('Sign in first.');return;}"
        "  document.getElementById('reply').textContent='Thinking…';"
        "  const s=await fetch('/api/tutor/sessions',{method:'POST',headers:{Authorization:'Bearer '+tok}});"
        "  if(!s.ok){document.getElementById('reply').textContent='Session failed';return;}"
        "  const sd=await s.json();"
        "  const f=new FormData();f.append('text',q);f.append('mode',selectedMode);"
        "  const r=await fetch('/api/tutor/sessions/'+sd.session_id+'/message',"
        "    {method:'POST',headers:{Authorization:'Bearer '+tok},body:f});"
        "  const d=await r.json();"
        "  document.getElementById('reply').textContent=d.reply||JSON.stringify(d);"
        "}"
        "</script>"
        "</body></html>"
    )
    return HTMLResponse(body)


# ---------- prod-143 — /memory-boost ----------


@router.get("/memory-boost", response_class=HTMLResponse)
def memory_boost_page(
    board: str = Query("CBSE"),
    grade: int = Query(10, ge=1, le=12),
    user: AuthUser | None = Depends(current_user),
) -> HTMLResponse:
    """prod-143 — Daily 3-question drill UI."""
    if user is None:
        return _anon_landing(
            "Memory Boost",
            "Your daily 3-question drill — one weak topic, one warmup, one fresh. "
            "Builds a daily streak.",
        )

    _mb.migrate()
    picks = _mb.get_or_create_pack(user_id=user.id, board=board, grade=grade)
    hydrated = _mb.hydrate_picks(picks)
    streak = _mb.get_streak(user.id)

    if not hydrated:
        cards_html = (
            "<div class='card empty'>No questions in the pool yet for "
            f"{html.escape(board)} Class {grade}. Try a different board / grade, "
            "or come back after we tag more PYQs.</div>"
        )
    else:
        cards = []
        for entry in hydrated:
            bucket = entry["bucket"]
            item = entry["item"]
            tag_class = f"tag-{bucket}"
            tag_label = {
                "critical": "Critical review",
                "warmup": "Warm-up",
                "fresh": "Fresh material",
            }.get(bucket, bucket)
            if item.get("missing"):
                continue
            q_text = html.escape(item.get("question_text", ""))
            subject = html.escape(item.get("subject", ""))
            chapter = html.escape(item.get("chapter", "") or "")
            options = item.get("options") or []
            opts_html = ""
            if options:
                opt_items = "".join(
                    f'<label style="display:block;padding:6px 0">'
                    f'<input type="radio" name="q_{entry["pick_id"]}" '
                    f'value="{html.escape(o)}"> {html.escape(o)}</label>'
                    for o in options
                )
                opts_html = f'<div style="margin-top:8px">{opt_items}</div>'

            cards.append(
                f'<div class="card" data-pick="{entry["pick_id"]}">'
                f'<span class="tag {tag_class}">{tag_label}</span>'
                f'<span class="sub" style="margin-left:8px">'
                f'{subject}{" · " + chapter if chapter else ""}</span>'
                f'<p style="margin:8px 0 0;font-size:15px">{q_text}</p>'
                f'{opts_html}'
                f'<div style="margin-top:10px">'
                f'  <button class="btn success" onclick="answer(\'{entry["pick_id"]}\',true)">'
                f'    Got it right</button> '
                f'  <button class="btn danger" onclick="answer(\'{entry["pick_id"]}\',false)">'
                f'    Got it wrong</button>'
                f'</div></div>'
            )
        cards_html = "".join(cards)

    streak_html = (
        f'<div class="card"><h2 style="margin-top:0">🔥 Streak</h2>'
        f'<p style="font-size:32px;margin:4px 0;font-weight:700">'
        f'{streak["current_streak"]} day{"s" if streak["current_streak"] != 1 else ""}</p>'
        f'<p class="sub">Longest: {streak["longest_streak"]} days · '
        f'Last active: {html.escape(streak.get("last_active_date") or "never")}</p>'
        f'</div>'
    )

    body = (
        "<!doctype html><html lang='en'><head>"
        "<meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<title>Memory Boost — AI Pathshala</title>"
        f"<style>{_BASE_CSS}</style></head><body>"
        "<h1>Memory Boost</h1>"
        f"<p class='sub'>Your daily 3-question drill — {html.escape(board)} "
        f"Class {grade}. Pick comes from the chapters you most need to review.</p>"
        f"{streak_html}"
        f"{cards_html}"
        "<div class='foot'><a href='/home'>← Home</a> · "
        "<a href='/mastery'>Mastery map</a></div>"
        "<script>"
        "async function answer(pickId, wasCorrect){"
        "  const tok=localStorage.getItem('pathshala_token');"
        "  if(!tok){alert('Sign in first.');return;}"
        "  const r=await fetch('/api/me/memory-boost/answer',"
        "    {method:'POST',headers:{Authorization:'Bearer '+tok,"
        "    'Content-Type':'application/json'},"
        "    body:JSON.stringify({pick_id:pickId,was_correct:wasCorrect})});"
        "  if(r.ok){const d=await r.json();"
        "    document.querySelector(`[data-pick=\"${pickId}\"]`).style.opacity='0.5';"
        "    alert('Recorded! Streak: '+d.streak.current_streak+' day(s)');"
        "  }else{alert('Failed to record');}"
        "}"
        "</script></body></html>"
    )
    return HTMLResponse(body)


# ---------- prod-144 — /teacher/class/{class_id}/heat-map ----------


@router.get("/teacher/class/{class_id}/heat-map", response_class=HTMLResponse)
def teacher_heat_map_page(
    class_id: str,
    org_id: str = Query(..., description="Org context for the class"),
    board: str = Query("CBSE"),
    grade: int = Query(10, ge=1, le=12),
    subject: str | None = Query(None),
    user: AuthUser | None = Depends(current_user),
) -> HTMLResponse:
    """prod-144 — Server-rendered teacher heat-map.

    Auth/role: enforced by the underlying /api/orgs/.../heat-map
    endpoint via require_org_role. We fetch the JSON server-side
    using the same auth flow as the user — the underlying
    `class_heat_map` router function does the role check.
    """
    if user is None:
        return _anon_landing(
            "Class heat map",
            "Teacher dashboard — see which topics the whole class is weakest on.",
        )

    # Reuse the JSON endpoint's helper directly so we get one auth path.
    from .class_heat_map import class_heat_map as _heat_endpoint_fn
    try:
        heat = _heat_endpoint_fn(
            org_id=org_id, class_id=class_id, board=board, grade=grade,
            subject=subject, user=user,
        )
    except HTTPException as e:
        return HTMLResponse(
            f"<!doctype html><html><head><style>{_BASE_CSS}</style></head><body>"
            f"<div class='card'><h1>Class heat map</h1>"
            f"<p class='sub'>{e.status_code}: {html.escape(str(e.detail))}</p>"
            f"<p><a class='btn' href='/home'>← Home</a></p></div></body></html>",
            status_code=e.status_code,
        )
    except Exception as e:
        # Degrade gracefully — covers org tables missing in test envs,
        # transient DB errors, etc. Show a friendly page instead of 500.
        return HTMLResponse(
            f"<!doctype html><html><head><style>{_BASE_CSS}</style></head><body>"
            f"<div class='card'><h1>Class heat map</h1>"
            f"<p class='sub'>Unable to load heat map: "
            f"{html.escape(type(e).__name__)}</p>"
            f"<p><a class='btn' href='/home'>← Home</a></p></div></body></html>",
            status_code=503,
        )

    students = heat["students"]
    topics = heat["topics"]
    cells = heat["cells"]
    summary = heat["class_summary"]

    if not students or not topics:
        grid_html = (
            "<div class='card empty'>This class has no students yet or "
            "no curriculum loaded for the board+grade.</div>"
        )
    else:
        # Build column headers (topics) — truncated for fit
        col_headers = "".join(
            f'<th title="{html.escape(t["title"])}">'
            f'{html.escape(t["title"][:18] + ("…" if len(t["title"]) > 18 else ""))}'
            f'</th>'
            for t in topics
        )
        # Build rows
        rows = []
        for i, st in enumerate(students):
            row_cells_html = ""
            for j in range(len(topics)):
                cell = cells[i][j]
                state = cell["color_state"]
                bg = {
                    "green": "#e7f6ef",
                    "yellow": "#fff4db",
                    "red": "#fef3f2",
                    "untouched": "#f5f7fb",
                }[state]
                fg = {
                    "green": "#16855f",
                    "yellow": "#a86600",
                    "red": "#b42318",
                    "untouched": "#5a6470",
                }[state]
                pct = round(cell["mastery"] * 100)
                row_cells_html += (
                    f'<td style="background:{bg};color:{fg};text-align:center;'
                    f'font-weight:600;font-size:12px;padding:6px;'
                    f'border:1px solid #d9e0ea">{pct}%</td>'
                )
            rows.append(
                f'<tr><th style="text-align:left;padding:6px 8px;'
                f'background:#fafafa;border:1px solid #d9e0ea;font-size:13px;'
                f'white-space:nowrap">{html.escape(st["display_name"])}</th>'
                f'{row_cells_html}</tr>'
            )
        rows_html = "".join(rows)
        grid_html = (
            '<div style="overflow-x:auto"><table style="border-collapse:collapse;'
            'background:white;min-width:100%">'
            f'<thead><tr><th style="background:#fafafa;border:1px solid #d9e0ea;'
            f'padding:6px 8px">Student</th>{col_headers}</tr></thead>'
            f'<tbody>{rows_html}</tbody></table></div>'
        )

    body = (
        "<!doctype html><html lang='en'><head>"
        "<meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>Class heat map · {html.escape(board)} Class {grade} — AI Pathshala</title>"
        f"<style>{_BASE_CSS}</style></head><body>"
        f"<h1>Class heat map</h1>"
        f"<p class='sub'>{html.escape(board)} · Class {grade} · "
        f"{len(students)} student{'s' if len(students) != 1 else ''} · "
        f"{len(topics)} topic{'s' if len(topics) != 1 else ''}</p>"
        f"<div class='card'>"
        f"<p>Strong: <b>{summary.get('green', 0)}</b> · "
        f"Review: <b>{summary.get('yellow', 0)}</b> · "
        f"Weak: <b>{summary.get('red', 0)}</b> · "
        f"Untouched: <b>{summary.get('untouched', 0)}</b></p>"
        f"</div>"
        f"<div class='card'>{grid_html}</div>"
        f"<div class='foot'><a href='/home'>← Home</a></div>"
        "</body></html>"
    )
    return HTMLResponse(body)


# ---------- prod-145 — /admin/examples-queue ----------


@router.get("/admin/examples-queue", response_class=HTMLResponse)
def admin_examples_queue_page(
    user: AuthUser | None = Depends(current_user),
) -> HTMLResponse:
    """prod-145 — Admin curator queue for Real-World Examples.

    Lists pending concept_examples with 1-click approve/reject. JS
    POSTs to the existing admin endpoints (gated by the prod-9
    router-level admin dep).
    """
    if user is None:
        return _anon_landing(
            "Curator queue",
            "Admin-only — review pending real-world examples.",
        )

    # Admin check via the existing helper
    from ..api_deps import require_admin_role
    try:
        require_admin_role(user)
    except HTTPException as e:
        return HTMLResponse(
            f"<!doctype html><html><head><style>{_BASE_CSS}</style></head><body>"
            f"<div class='card'><h1>Curator queue</h1>"
            f"<p class='sub'>{html.escape(str(e.detail))}</p>"
            f"<p><a class='btn' href='/home'>← Home</a></p></div></body></html>",
            status_code=e.status_code,
        )

    _ex.migrate()
    pending = _ex.list_pending_queue(limit=100)
    stats = _ex.stats()

    if not pending:
        items_html = (
            "<div class='card empty'>No pending examples to review. "
            "Run the generator from <a href='/admin/'>admin home</a> to add some.</div>"
        )
    else:
        items = []
        for ex in pending:
            slug = quote(ex.concept_slug)
            items.append(
                f'<div class="card" data-id="{ex.id}">'
                f'<p class="sub" style="margin:0 0 6px">'
                f'<a href="/concept/{slug}" target="_blank">'
                f'{html.escape(ex.concept_slug)}</a> · '
                f'locale: <b>{ex.locale}</b> · source: <b>{ex.source}</b></p>'
                f'<div style="white-space:pre-wrap;background:#f5f7fb;'
                f'padding:12px;border-radius:6px;font-size:14px;line-height:1.5">'
                f'{html.escape(ex.example_md)}</div>'
                f'<div style="margin-top:10px">'
                f'<button class="btn success" onclick="reviewIt(\'{ex.id}\',\'approved\')">'
                f'  ✓ Approve</button> '
                f'<button class="btn danger" onclick="reviewIt(\'{ex.id}\',\'rejected\')">'
                f'  ✗ Reject</button>'
                f'</div></div>'
            )
        items_html = "".join(items)

    body = (
        "<!doctype html><html lang='en'><head>"
        "<meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<title>Examples curator queue — AI Pathshala admin</title>"
        f"<style>{_BASE_CSS}</style></head><body>"
        "<h1>Real-World Examples · Curator queue</h1>"
        f"<p class='sub'>Pending: <b>{stats['pending']}</b> · "
        f"Approved: <b>{stats['approved']}</b> · "
        f"Rejected: <b>{stats['rejected']}</b> · "
        f"Total: <b>{stats['total']}</b></p>"
        f"{items_html}"
        "<div class='foot'><a href='/admin/'>← Admin home</a></div>"
        "<script>"
        "async function reviewIt(id, status){"
        "  const tok=localStorage.getItem('pathshala_token');"
        "  if(!tok){alert('Sign in first.');return;}"
        "  const path=status==='approved'?'approve':'reject';"
        "  const r=await fetch('/api/admin/teacher-tools/examples/'+id+'/'+path,"
        "    {method:'POST',headers:{Authorization:'Bearer '+tok,"
        "    'Content-Type':'application/json'},body:'{}'});"
        "  if(r.ok){document.querySelector(`[data-id=\"${id}\"]`).style.opacity='0.3';"
        "  }else{alert('Failed: '+r.status);}"
        "}"
        "</script></body></html>"
    )
    return HTMLResponse(body)
