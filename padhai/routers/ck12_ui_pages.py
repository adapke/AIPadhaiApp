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
from ..web import current_user_optional as current_user

router = APIRouter()


# Shared CSS + chrome (kept minimal — pages all follow concept_seo style)
# prod-160 — Now ships full AI Pathshala chrome (top nav + breadcrumb +
# footer) so /tutor-modes, /memory-boost, /admin/examples-queue all
# visually match /concept, /mastery, /home.
_BASE_CSS = """
body{font-family:Inter,system-ui,sans-serif;margin:0;padding:0;
color:#101828;background:#f5f7fb;line-height:1.55}
.topnav{background:#fff;border-bottom:1px solid #e3e6ec;
padding:12px 20px;display:flex;align-items:center;
justify-content:space-between;flex-wrap:wrap;gap:8px}
.brand{font-weight:700;font-size:17px;color:#0b3a8a;
text-decoration:none;letter-spacing:-0.01em}
.brand span{color:#1565d8}
.nav-links{display:flex;gap:16px;flex-wrap:wrap;align-items:center}
.nav-links a{color:#445;text-decoration:none;font-size:14px;font-weight:500}
.nav-links a:hover{color:#1565d8}
.nav-cta{background:#1565d8;color:#fff !important;padding:7px 14px;
border-radius:6px;font-weight:600 !important}
.crumb{max-width:1100px;margin:14px auto 0;padding:0 20px;
font-size:13px;color:#5a6470}
.crumb a{color:#1565d8;text-decoration:none}
.page{max-width:1100px;margin:0 auto;padding:18px 20px 40px}
.pageftr{max-width:1100px;margin:32px auto 0;padding:24px 20px;
border-top:1px solid #e3e6ec;color:#5a6470;font-size:13px;
display:flex;flex-wrap:wrap;gap:18px}
.pageftr a{color:#1565d8;text-decoration:none}
h1{font-size:26px;margin:6px 0 4px;color:#0b3a8a}
h2{font-size:18px;margin:18px 0 8px;color:#0b3a8a}
.sub{color:#5a6470;font-size:14px;margin:0 0 16px}
.card{background:white;padding:18px;border-radius:8px;margin:12px 0;
border:1px solid #d9e0ea}
.btn{display:inline-block;background:#1565d8;color:white;padding:8px 16px;
border-radius:6px;text-decoration:none;border:0;cursor:pointer;font-size:14px;
font-family:inherit}
.btn:hover{background:#0e4eb6}
.btn.secondary{background:#5a6470}
.btn.danger{background:#b42318}
.btn.success{background:#16855f}
.btn:disabled{opacity:0.5;cursor:not-allowed}
.chip{display:inline-flex;align-items:center;gap:6px;background:white;
border:1px solid #d9e0ea;color:#101828;padding:8px 14px;border-radius:999px;
text-decoration:none;font-size:14px;margin:4px 4px 4px 0;cursor:pointer;
font-family:inherit}
.chip.active{background:#1565d8;color:white;border-color:#1565d8}
.foot{margin-top:24px;color:#5a6470;font-size:13px;text-align:center}
.foot a{color:#1565d8;margin:0 8px}
.empty{color:#5a6470;padding:32px;text-align:center;background:white;
border-radius:8px}
.empty a{color:#1565d8}
.tag{display:inline-block;padding:2px 8px;border-radius:4px;
font-size:11px;text-transform:uppercase;letter-spacing:0.3px;font-weight:600}
.tag-critical{background:#fef3f2;color:#b42318}
.tag-warmup{background:#fff4db;color:#a86600}
.tag-fresh{background:#eaf2ff;color:#1565d8}
input[type=text],input[type=search],input[type=number],select,textarea{
font-family:inherit;font-size:14px;padding:9px 12px;border:1px solid #d0d6de;
border-radius:6px;background:#fff;color:#101828;outline:none}
input:focus,select:focus,textarea:focus{border-color:#1565d8;
box-shadow:0 0 0 3px rgba(21,101,216,0.10)}
"""


def _top_nav() -> str:
    return (
        '<nav class="topnav" role="navigation">'
        '<a class="brand" href="/home">AI <span>Pathshala</span></a>'
        '<div class="nav-links">'
        '<a href="/concept">Concepts</a>'
        '<a href="/syllabus">Syllabus</a>'
        '<a href="/mastery">Mastery</a>'
        '<a href="/memory-boost">Memory Boost</a>'
        '<a href="/tutor-modes">Tutor</a>'
        '<a class="nav-cta" href="/home">Home</a>'
        '</div></nav>'
    )


def _crumb(label: str) -> str:
    return (
        '<div class="crumb">'
        '<a href="/home">Home</a> &nbsp;›&nbsp; '
        f'<span>{html.escape(label)}</span>'
        '</div>'
    )


def _footer() -> str:
    return (
        '<footer class="pageftr">'
        '<a href="/mastery">Mastery map</a>'
        '<a href="/memory-boost">Memory Boost</a>'
        '<a href="/tutor-modes">Tutor modes</a>'
        '<a href="/concept">Concept videos</a>'
        '<a href="/syllabus">Syllabus</a>'
        '<span style="margin-left:auto">Made for Indian students</span>'
        '</footer>'
    )


def _anon_landing(title: str, sub: str) -> HTMLResponse:
    body = (
        "<!doctype html><html lang='en'><head>"
        "<meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>{html.escape(title)} — AI Pathshala</title>"
        f"<style>{_BASE_CSS}</style></head><body>"
        + _top_nav()
        + _crumb(title)
        + "<main class='page'>"
        f"<div class='card'><h1>{html.escape(title)}</h1>"
        f"<p class='sub'>{html.escape(sub)}</p>"
        "<p><a class='btn' href='/home'>Sign in to AI Pathshala</a></p>"
        "</div></main>"
        + _footer()
        + "</body></html>"
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

    # prod-158 — CK-12 Flexi-inspired ask interface. The user picks a
    # tutor lens (mode chip) AND optionally one of several input
    # affordances: Challenge AI, Rephrase, Paste Answer, Draw Equation,
    # Take Picture, Upload Image, Math Keyboard. Each affordance maps
    # to a different intent prefix that the tutor system prompt picks
    # up. Image/draw affordances send to the doubt-vision endpoint
    # rather than tutor-text, so they get OCR + diagram parsing.

    chips_html = "".join(
        f'<button class="mode-chip" type="button" data-mode="{m["key"]}" '
        f'data-label="{html.escape(m["label_en"])}">'
        f'<span style="font-size:22px">{m["icon"]}</span>'
        f'<span><b>{html.escape(m["label_en"])}</b>'
        f'<span class="mode-hint">{html.escape(m["one_line_en"])}</span></span>'
        f'</button>'
        for m in _modes.list_modes()
    )

    body = (
        "<!doctype html><html lang='en'><head>"
        "<meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<title>Tutor — AI Pathshala</title>"
        f"<style>{_BASE_CSS}"
        # Mode chip grid
        ".mode-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));"
        "gap:8px;margin-bottom:6px}"
        ".mode-chip{display:flex;flex-direction:row;align-items:flex-start;gap:10px;"
        "background:white;border:2px solid #d9e0ea;color:#101828;"
        "padding:10px 12px;border-radius:8px;cursor:pointer;font-family:inherit;"
        "text-align:left;line-height:1.3;transition:all 0.15s;font-size:13px}"
        ".mode-chip:hover{border-color:#1565d8;transform:translateY(-1px);"
        "box-shadow:0 2px 6px rgba(0,0,0,0.06)}"
        ".mode-chip.active{background:#1565d8;color:white;border-color:#1565d8}"
        ".mode-chip.active .mode-hint{color:#cdd9eb}"
        ".mode-chip .mode-hint{display:block;font-size:11px;font-weight:400;"
        "color:#5a6470;margin-top:2px}"
        # Ask options (Flexi-style)
        ".ask-row{display:flex;flex-wrap:wrap;gap:6px;margin:10px 0 12px}"
        ".ask-btn{background:#eef3fc;border:1px solid #cdd9eb;color:#0b3a8a;"
        "padding:7px 12px;border-radius:6px;cursor:pointer;font-family:inherit;"
        "font-size:13px;font-weight:600;display:inline-flex;align-items:center;gap:6px}"
        ".ask-btn:hover{background:#1565d8;color:white;border-color:#1565d8}"
        ".ask-btn.active{background:#1565d8;color:white;border-color:#1565d8}"
        # Textarea
        "textarea{width:100%;min-height:120px;resize:vertical;box-sizing:border-box}"
        # Math keyboard panel
        ".math-kb{display:none;background:#f0f2f7;padding:10px;border-radius:6px;"
        "margin-top:8px;flex-wrap:wrap;gap:4px}"
        ".math-kb.open{display:flex}"
        ".math-kb button{background:white;border:1px solid #d0d6de;padding:6px 10px;"
        "border-radius:4px;cursor:pointer;font-family:'Cambria Math',serif;font-size:14px;"
        "min-width:36px}"
        ".math-kb button:hover{background:#1565d8;color:white;border-color:#1565d8}"
        # Reply display
        "#reply{white-space:pre-wrap;padding:14px;background:#f5f7fb;"
        "border-radius:6px;margin-top:12px;font-size:14px;line-height:1.6;"
        "border:1px solid #e3e6ec;min-height:40px}"
        "#reply:empty{display:none}"
        # Image preview
        "#imagePreview{margin-top:8px;max-width:200px;border-radius:6px;"
        "border:1px solid #d0d6de;display:none}"
        "#imagePreview.shown{display:block}"
        # Draw canvas
        "#drawWrap{display:none;margin-top:8px}"
        "#drawWrap.shown{display:block}"
        "#drawCanvas{border:1px solid #d0d6de;border-radius:6px;background:white;"
        "cursor:crosshair;touch-action:none;width:100%;max-width:600px}"
        "</style></head><body>"
        + _top_nav()
        + _crumb("Tutor")
        + "<main class='page'>"
        "<h1>AI Tutor</h1>"
        "<p class='sub'>Pick a teaching lens, then ask anything. Use the Flexi-style options below to challenge, rephrase, paste, draw or photograph your doubt.</p>"

        # Mode chip grid
        "<div class='card'>"
        "<h2 style='margin-top:0'>Teaching lens</h2>"
        f"<div class='mode-grid'>{chips_html}</div>"
        "</div>"

        # Ask interface
        "<div class='card'>"
        "<h2 style='margin-top:0'>Ask your question</h2>"

        # Flexi-style action chips
        "<div class='ask-row' role='toolbar' aria-label='Ask options'>"
        "<button type='button' class='ask-btn' id='btn-challenge' title='Push back on the AI - ask it to defend or improve its last answer'>⚡ Challenge AI</button>"
        "<button type='button' class='ask-btn' id='btn-rephrase' title='Ask the AI to explain the same thing differently'>🔄 Rephrase</button>"
        "<button type='button' class='ask-btn' id='btn-paste' title='Paste your draft answer for the AI to evaluate'>📋 Paste Answer</button>"
        "<button type='button' class='ask-btn' id='btn-draw' title='Draw an equation or diagram with your finger'>✏️ Draw Equation</button>"
        "<button type='button' class='ask-btn' id='btn-photo' title='Snap a photo of the question'>📷 Take Picture</button>"
        "<button type='button' class='ask-btn' id='btn-upload' title='Upload an image of the question'>🖼️ Upload Image</button>"
        "<button type='button' class='ask-btn' id='btn-math' title='Open math keyboard'>√ Math Keyboard</button>"
        "</div>"

        # Hidden inputs for image affordances
        "<input type='file' id='photoInput' accept='image/*' capture='environment' style='display:none'>"
        "<input type='file' id='uploadInput' accept='image/*' style='display:none'>"
        "<img id='imagePreview' alt='preview'>"

        # Math keyboard
        "<div class='math-kb' id='mathKb'>"
        + "".join(
            f'<button type="button" data-sym="{s}">{s}</button>'
            for s in [
                "²", "³", "ⁿ", "√", "∛", "π", "θ", "α", "β", "γ", "Δ",
                "∞", "≠", "≤", "≥", "±", "×", "÷", "→", "←", "⇒",
                "∫", "∑", "∂", "∇", "∈", "∉", "⊂", "⊆",
                "(", ")", "[", "]", "{", "}",
                "sin", "cos", "tan", "log", "ln", "e^",
            ]
        )
        + "</div>"

        # Draw canvas
        "<div id='drawWrap'>"
        "<canvas id='drawCanvas' width='600' height='200'></canvas>"
        "<div style='margin-top:6px'>"
        "<button type='button' class='ask-btn' onclick='clearDraw()'>Clear</button>"
        "<button type='button' class='ask-btn' onclick='submitDraw()'>Send drawing →</button>"
        "</div>"
        "</div>"

        # Text area
        "<p class='sub' id='mode-indicator' style='margin-top:14px'>Pick a teaching lens above first (or skip to use the default tutor).</p>"
        "<textarea id='q' placeholder=\"Ask anything — e.g. 'Explain Newton first law', 'Solve x² - 5x + 6 = 0', 'How do plants make food?'\"></textarea>"
        "<div style='margin-top:10px;display:flex;gap:8px;flex-wrap:wrap'>"
        "<button class='btn' onclick='askTutor()' id='askBtn'>Ask tutor →</button>"
        "<span class='sub' style='font-size:12px;align-self:center'>Or use one of the Flexi options above to start.</span>"
        "</div>"
        "<div id='reply' aria-live='polite'></div>"
        "</div>"

        "</main>"
        + _footer() +
        "<script>"
        # ----- mode chip state -----
        "var selectedMode=null;var lastAnswer='';var lastQuestion='';"
        "document.querySelectorAll('.mode-chip').forEach(function(c){"
        "  c.addEventListener('click',function(){"
        "    document.querySelectorAll('.mode-chip').forEach(function(x){x.classList.remove('active');});"
        "    c.classList.add('active');"
        "    selectedMode=c.dataset.mode;"
        "    document.getElementById('mode-indicator').textContent='Mode: '+c.dataset.label;"
        "  });"
        "});"
        # ----- helpers -----
        "function setReply(text){document.getElementById('reply').textContent=text||'';}"
        "function getQ(){return document.getElementById('q').value.trim();}"
        "function setQ(v){document.getElementById('q').value=v;document.getElementById('q').focus();}"
        "function getTok(){var t=localStorage.getItem('pathshala_token');if(!t){alert('Sign in first.');location.href='/home';}return t;}"
        # ----- ask tutor (text) -----
        "async function askTutor(intent){"
        "  var q=getQ();if(!q){alert('Type a question first.');return;}"
        "  var tok=getTok();if(!tok)return;"
        "  lastQuestion=q;"
        "  var prefix=intent?'['+intent+'] ':'';"
        "  document.getElementById('askBtn').disabled=true;setReply('Thinking…');"
        "  try{"
        "    var s=await fetch('/api/tutor/sessions',{method:'POST',headers:{Authorization:'Bearer '+tok}});"
        "    if(!s.ok){setReply('Could not start tutor session (HTTP '+s.status+')');return;}"
        "    var sd=await s.json();"
        "    var f=new FormData();f.append('text',prefix+q);"
        "    if(selectedMode)f.append('mode',selectedMode);"
        "    var r=await fetch('/api/tutor/sessions/'+sd.session_id+'/message',"
        "      {method:'POST',headers:{Authorization:'Bearer '+tok},body:f});"
        "    var d={};try{d=await r.json();}catch(_){d={detail:'invalid response'};}"
        "    if(!r.ok){setReply('Error: '+(d.detail||d.error||'HTTP '+r.status));return;}"
        "    lastAnswer=d.reply||JSON.stringify(d);"
        "    setReply(lastAnswer);"
        "  }catch(e){setReply('Network error: '+e.message);}"
        "  finally{document.getElementById('askBtn').disabled=false;}"
        "}"
        # ----- Flexi action handlers -----
        "document.getElementById('btn-challenge').onclick=function(){"
        "  if(!lastAnswer){alert('Ask a question first, then challenge the AI on its answer.');return;}"
        "  var prev=lastQuestion?'Earlier I asked: '+lastQuestion+'. ':'';"
        "  setQ(prev+'You said: \"'+lastAnswer.slice(0,500)+'\". Critically defend OR correct that — what could be wrong, edge cases, or a more rigorous explanation?');"
        "  askTutor('CHALLENGE');"
        "};"
        "document.getElementById('btn-rephrase').onclick=function(){"
        "  if(!lastAnswer){alert('Ask a question first; the rephrase button explains the LAST answer differently.');return;}"
        "  setQ('Explain the same thing again, but more simply / with a different analogy / in a different style.');"
        "  askTutor('REPHRASE');"
        "};"
        "document.getElementById('btn-paste').onclick=function(){"
        "  var ans=prompt('Paste your draft answer below — the AI will evaluate, mark and suggest improvements.');"
        "  if(!ans||!ans.trim())return;"
        "  setQ('Please evaluate my answer below. Mark out of 10, list mistakes, suggest improvements. My answer:\\n\\n'+ans);"
        "  askTutor('EVALUATE');"
        "};"
        "document.getElementById('btn-draw').onclick=function(){"
        "  var w=document.getElementById('drawWrap');"
        "  w.classList.toggle('shown');"
        "  if(w.classList.contains('shown'))initDraw();"
        "};"
        "document.getElementById('btn-photo').onclick=function(){document.getElementById('photoInput').click();};"
        "document.getElementById('btn-upload').onclick=function(){document.getElementById('uploadInput').click();};"
        "document.getElementById('btn-math').onclick=function(){"
        "  document.getElementById('mathKb').classList.toggle('open');"
        "};"
        # Math keyboard buttons insert into textarea
        "document.querySelectorAll('#mathKb button').forEach(function(b){"
        "  b.onclick=function(){"
        "    var ta=document.getElementById('q');var start=ta.selectionStart;"
        "    var end=ta.selectionEnd;var v=ta.value;"
        "    ta.value=v.slice(0,start)+b.dataset.sym+v.slice(end);"
        "    ta.focus();ta.selectionStart=ta.selectionEnd=start+b.dataset.sym.length;"
        "  };"
        "});"
        # Image upload handlers
        "function handleImage(file){"
        "  if(!file){return;}"
        "  var reader=new FileReader();"
        "  reader.onload=function(e){"
        "    var img=document.getElementById('imagePreview');"
        "    img.src=e.target.result;img.classList.add('shown');"
        "  };"
        "  reader.readAsDataURL(file);"
        "  uploadImageToTutor(file);"
        "}"
        "document.getElementById('photoInput').onchange=function(e){handleImage(e.target.files[0]);};"
        "document.getElementById('uploadInput').onchange=function(e){handleImage(e.target.files[0]);};"
        # prod-162 — Two-step image flow: upload the file to /api/uploads
        # (which accepts UploadFile and returns upload_id), then call
        # /api/uploads/{id}/analyze (Claude vision) to extract topic +
        # subject + a short summary. The summary becomes the tutor's
        # first reply; the student can then keep asking text questions.
        # Was: single POST to /api/doubts with field name `text` and a
        # file under `image` — both fields wrong (server expects
        # `question_text` + `image_url` string, NOT a file).
        "async function uploadImageToTutor(file){"
        "  var tok=getTok();if(!tok)return;"
        "  setReply('Uploading image…');"
        "  var fu=new FormData();fu.append('file',file);"
        "  try{"
        "    var ru=await fetch('/api/uploads',{method:'POST',headers:{Authorization:'Bearer '+tok},body:fu});"
        "    var du={};try{du=await ru.json();}catch(_){du={};}"
        "    if(!ru.ok){"
        "      var emsg=du.detail||du.error||'HTTP '+ru.status;"
        "      if(Array.isArray(emsg))emsg=emsg.map(function(e){return e.msg||JSON.stringify(e);}).join(' · ');"
        "      setReply('Upload failed: '+emsg);return;"
        "    }"
        "    var uid=du.upload_id||du.id;"
        "    setReply('Analyzing the image (this takes ~10s)…');"
        "    var ra=await fetch('/api/uploads/'+encodeURIComponent(uid)+'/analyze',"
        "      {method:'POST',headers:{Authorization:'Bearer '+tok}});"
        "    var da={};try{da=await ra.json();}catch(_){da={};}"
        "    if(!ra.ok){"
        "      var em=da.detail||da.error||'HTTP '+ra.status;"
        "      if(Array.isArray(em))em=em.map(function(e){return e.msg||JSON.stringify(e);}).join(' · ');"
        "      setReply('Analysis failed: '+em);return;"
        "    }"
        "    var summary=da.summary||da.description||da.text||'';"
        "    var topic=da.topic||da.subject_hint||'';"
        "    var grade=da.grade||da.grade_band||'';"
        "    lastAnswer=(topic?'Detected topic: '+topic+(grade?' (Class '+grade+')':'')+'\\n\\n':'')+(summary||'(no summary returned)');"
        "    setReply(lastAnswer);"
        # If the user typed a question alongside the image, follow up
        # via the tutor with the analysis as context.
        "    var followUp=getQ();"
        "    if(followUp){"
        "      setReply(lastAnswer+'\\n\\n--- Asking tutor your follow-up: '+followUp+' ---\\n');"
        "      setQ('About this image (topic: '+(topic||'see analysis')+'): '+followUp);"
        "      askTutor('IMAGE_FOLLOWUP');"
        "    }"
        "  }catch(e){setReply('Upload failed: '+e.message);}"
        "}"
        # Drawing canvas
        "var drawCtx=null,drawing=false;"
        "function initDraw(){"
        "  var c=document.getElementById('drawCanvas');"
        "  drawCtx=c.getContext('2d');drawCtx.lineWidth=2;drawCtx.lineCap='round';drawCtx.strokeStyle='#101828';"
        "  function pos(e){var r=c.getBoundingClientRect();var t=e.touches?e.touches[0]:e;"
        "    return{x:(t.clientX-r.left)*(c.width/r.width),y:(t.clientY-r.top)*(c.height/r.height)};}"
        "  function start(e){e.preventDefault();drawing=true;var p=pos(e);drawCtx.beginPath();drawCtx.moveTo(p.x,p.y);}"
        "  function move(e){if(!drawing)return;e.preventDefault();var p=pos(e);drawCtx.lineTo(p.x,p.y);drawCtx.stroke();}"
        "  function end(){drawing=false;}"
        "  c.onmousedown=start;c.onmousemove=move;c.onmouseup=end;c.onmouseleave=end;"
        "  c.ontouchstart=start;c.ontouchmove=move;c.ontouchend=end;"
        "}"
        "function clearDraw(){if(drawCtx){var c=document.getElementById('drawCanvas');drawCtx.clearRect(0,0,c.width,c.height);}}"
        "function submitDraw(){"
        "  var c=document.getElementById('drawCanvas');"
        "  c.toBlob(function(blob){if(blob)uploadImageToTutor(blob);},'image/png');"
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
    """prod-143 — Daily 3-question drill UI.

    prod-157 — Rewrite with:
      • Board / Class picker (was hardcoded CBSE/10)
      • Live answer-button feedback (no `alert()` interruptions)
      • Show-answer toggle so the user can self-mark
      • Proper error handling — `[object Object]` style bugs avoided
      • SPA chrome (top nav, breadcrumb, footer)
      • Auto-refresh streak after each answer
    """
    if user is None:
        return _anon_landing(
            "Memory Boost",
            "Your daily 3-question drill — one weak topic, one warmup, one fresh. "
            "Builds a daily streak.",
        )

    _mb.migrate()
    try:
        picks = _mb.get_or_create_pack(user_id=user.id, board=board, grade=grade)
        hydrated = _mb.hydrate_picks(picks)
    except Exception as e:
        # Degrade gracefully when the underlying pool is empty / corrupted
        # — don't 500 the page.
        hydrated = []
        _err = type(e).__name__
    else:
        _err = ""

    streak = _mb.get_streak(user.id)

    if not hydrated:
        cards_html = (
            "<div class='empty'>"
            f"<p><b>No questions in the pool yet for {html.escape(board)} Class {grade}.</b></p>"
            "<p style='margin-top:10px'>Try a different board or class:</p>"
            "<form method='get' action='/memory-boost' "
            "style='display:flex;gap:8px;justify-content:center;flex-wrap:wrap;margin-top:14px'>"
            + _board_select(board) + _grade_select(grade) +
            "<button class='btn' type='submit'>Try this combination</button>"
            "</form>"
            "</div>"
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
            correct = item.get("correct_answer", "")
            opts_html = ""
            if options:
                opt_items = "".join(
                    f'<label style="display:block;padding:6px 0;cursor:pointer">'
                    f'<input type="radio" name="q_{entry["pick_id"]}" '
                    f'value="{html.escape(o)}"> {html.escape(o)}</label>'
                    for o in options
                )
                opts_html = f'<div style="margin-top:8px">{opt_items}</div>'

            answer_block = ""
            if correct:
                answer_block = (
                    f'<div class="answer-reveal" id="ans_{entry["pick_id"]}" '
                    f'style="display:none;background:#e7f6ef;color:#16855f;'
                    f'padding:10px 14px;border-radius:6px;margin-top:10px;'
                    f'font-weight:600">'
                    f'✓ Correct answer: {html.escape(correct)}'
                    f'</div>'
                )

            cards.append(
                f'<div class="card pick-card" data-pick="{entry["pick_id"]}">'
                f'<div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:6px;margin-bottom:8px">'
                f'<span class="tag {tag_class}">{tag_label}</span>'
                f'<span class="sub" style="font-size:12px;margin:0">'
                f'{subject}{" · " + chapter if chapter else ""}</span>'
                f'</div>'
                f'<p style="margin:8px 0 0;font-size:15px;line-height:1.5">{q_text}</p>'
                f'{opts_html}'
                f'{answer_block}'
                f'<div style="margin-top:12px;display:flex;gap:8px;flex-wrap:wrap">'
                f'  <button class="btn success" onclick="answer(\'{entry["pick_id"]}\',true,this)">'
                f'    ✓ Got it right</button>'
                f'  <button class="btn danger" onclick="answer(\'{entry["pick_id"]}\',false,this)">'
                f'    ✗ Got it wrong</button>'
                f'  <button class="btn secondary" onclick="reveal(\'{entry["pick_id"]}\')">'
                f'    👀 Show answer</button>'
                f'</div>'
                f'<div class="card-msg" id="msg_{entry["pick_id"]}" '
                f'style="margin-top:8px;font-size:13px;display:none"></div>'
                f'</div>'
            )
        cards_html = "".join(cards) or "<div class='empty'>All caught up for today — come back tomorrow for a fresh pack.</div>"

    # Contiguous "N day(s)" label so screen readers + tests can find it
    # as one string; the visual layout still uses a big number on the right.
    _streak_text = f'{streak["current_streak"]} day{"s" if streak["current_streak"] != 1 else ""}'
    streak_html = (
        '<div class="card" id="streakCard">'
        '<div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:10px">'
        '<div><h2 style="margin:0;color:#0b3a8a">🔥 Your streak</h2>'
        '<p class="sub" style="margin:6px 0 0">'
        f'Longest: <b id="longest">{streak["longest_streak"]}</b> · '
        f'Last active: <span id="lastActive">{html.escape(streak.get("last_active_date") or "never")}</span>'
        '</p>'
        # Contiguous label — also serves as the assertable string in tests
        f'<p class="sub" style="margin:6px 0 0" aria-label="Current streak {_streak_text}">'
        f'Current: <b id="streakLabel">{_streak_text}</b></p>'
        '</div>'
        '<div style="text-align:right">'
        f'<div style="font-size:36px;font-weight:800;color:#1565d8;line-height:1" id="streakNum">{streak["current_streak"]}</div>'
        f'<div class="sub" style="margin:2px 0">day{"s" if streak["current_streak"] != 1 else ""}</div>'
        '</div></div></div>'
    )

    # Board/grade switcher (always shown)
    switcher_html = (
        '<form method="get" action="/memory-boost" '
        'style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;'
        'margin-bottom:14px">'
        + _board_select(board) + _grade_select(grade) +
        '<button class="btn" type="submit">Switch</button>'
        '</form>'
    )

    err_banner = ""
    if _err:
        err_banner = (
            '<div class="card" style="background:#fef3f2;border-color:#f8b4ab">'
            '<b>⚠ Couldn\'t load today\'s pack.</b> '
            f'<span class="sub" style="margin:0">({_err})</span>'
            ' Try a different board / class above.'
            '</div>'
        )

    body = (
        "<!doctype html><html lang='en'><head>"
        "<meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<title>Memory Boost — AI Pathshala</title>"
        f"<style>{_BASE_CSS}"
        ".pick-card{transition:opacity 0.3s,background 0.2s}"
        ".pick-card.done{background:#f0f7f4}"
        ".card-msg.ok{display:block !important;color:#16855f;font-weight:600}"
        ".card-msg.err{display:block !important;color:#b42318;font-weight:600}"
        "</style></head><body>"
        + _top_nav()
        + _crumb("Memory Boost")
        + "<main class='page'>"
        "<h1>Memory Boost</h1>"
        f"<p class='sub'>Your daily 3-question drill — {html.escape(board)} Class {grade}. "
        "One critical-recall topic, one warm-up, one fresh introduction.</p>"
        + switcher_html
        + err_banner
        + streak_html
        + cards_html
        + "</main>"
        + _footer() +
        "<script>"
        # Reveal answer (toggle)
        "function reveal(pickId){"
        "  var el=document.getElementById('ans_'+pickId);"
        "  if(el){el.style.display=el.style.display==='none'?'block':'none';}"
        "}"
        # Answer submission — inline feedback, no alert()
        "async function answer(pickId, wasCorrect, btn){"
        "  var tok=localStorage.getItem('pathshala_token');"
        "  if(!tok){location.href='/home';return;}"
        "  var card=document.querySelector('[data-pick=\"'+pickId+'\"]');"
        "  var msg=document.getElementById('msg_'+pickId);"
        "  card.querySelectorAll('button').forEach(function(b){b.disabled=true;});"
        "  msg.textContent='Recording…';msg.className='card-msg';msg.style.display='block';"
        "  try{"
        "    var r=await fetch('/api/me/memory-boost/answer',"
        "      {method:'POST',headers:{Authorization:'Bearer '+tok,"
        "        'Content-Type':'application/json'},"
        "       body:JSON.stringify({pick_id:pickId,was_correct:wasCorrect})});"
        "    var d={};try{d=await r.json();}catch(_){d={};}"
        "    if(!r.ok){"
        "      var emsg=d.detail||d.error||'HTTP '+r.status;"
        "      if(Array.isArray(emsg))emsg=emsg.map(function(e){return e.msg||JSON.stringify(e);}).join(' · ');"
        "      msg.textContent='Could not record: '+emsg;msg.className='card-msg err';"
        "      card.querySelectorAll('button').forEach(function(b){b.disabled=false;});"
        "      return;"
        "    }"
        "    card.classList.add('done');card.style.opacity='0.7';"
        "    var s=d.streak||{};"
        "    msg.textContent=(wasCorrect?'✓ Recorded as correct.':'✗ Recorded as wrong.')+ "
        "      ' Streak: '+(s.current_streak||0)+' day(s).';"
        "    msg.className='card-msg ok';"
        # Update the streak card live
        "    if(s.current_streak!==undefined){document.getElementById('streakNum').textContent=s.current_streak;}"
        "    if(s.longest_streak!==undefined){document.getElementById('longest').textContent=s.longest_streak;}"
        "    if(s.last_active_date){document.getElementById('lastActive').textContent=s.last_active_date;}"
        # Auto-reveal correct answer if available
        "    var ans=document.getElementById('ans_'+pickId);if(ans)ans.style.display='block';"
        "  }catch(e){"
        "    msg.textContent='Network error: '+e.message;msg.className='card-msg err';"
        "    card.querySelectorAll('button').forEach(function(b){b.disabled=false;});"
        "  }"
        "}"
        "</script></body></html>"
    )
    return HTMLResponse(body)


# Board + grade pickers used by /memory-boost. Kept top-level so they
# can be shared with future picker-driven pages.
# Values are the exact lowercase keys stored in question_bank so the pack
# lookup matches (search is also case-insensitive as of prod-237). Every
# entry below has questions in the bank — school boards + the national
# competitive-exam patterns.
_SUPPORTED_BOARDS = [
    ("cbse", "CBSE"),
    ("icse", "ICSE"),
    ("maharashtra", "Maharashtra"),
    ("tamilnadu", "Tamil Nadu"),
    ("karnataka", "Karnataka"),
    ("ap_telangana", "Andhra / Telangana"),
    ("kerala", "Kerala"),
    ("westbengal", "West Bengal"),
    ("gujarat", "Gujarat"),
    ("bihar", "Bihar"),
    # National entrance / competitive-exam patterns (grade-agnostic)
    ("jee", "JEE — Engineering"),
    ("neet", "NEET — Medical"),
    ("upsc", "UPSC"),
    ("ssc", "SSC"),
    ("cat", "CAT — MBA"),
    ("gate", "GATE"),
    ("bank_po", "Bank PO"),
    ("rrb", "Railways (RRB)"),
    ("sat", "SAT — US"),
]


def _board_select(current: str) -> str:
    opts = "".join(
        f'<option value="{html.escape(v)}"'
        f'{" selected" if v.lower() == current.lower() else ""}>{html.escape(label)}</option>'
        for v, label in _SUPPORTED_BOARDS
    )
    return (
        '<label style="font-size:13px;color:#5a6470">Board:'
        f'<select name="board" style="margin-left:6px">{opts}</select>'
        '</label>'
    )


def _grade_select(current: int) -> str:
    opts = "".join(
        f'<option value="{g}"{" selected" if g == current else ""}>Class {g}</option>'
        for g in range(1, 13)
    )
    return (
        '<label style="font-size:13px;color:#5a6470">Grade:'
        f'<select name="grade" style="margin-left:6px">{opts}</select>'
        '</label>'
    )


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
