"""Dedicated new-UI pages for the 13 modules that previously only existed
in the legacy SPA at `/ui-legacy#<hash>`.

Each route serves a self-contained HTML page using the same chrome as
/dashboard (header + nav). All real functionality comes from existing
backend APIs — no preview-response stubs, no iframe wrappers. The pages
do the minimum to give a student a working flow:

  /essay      — Essay grader: rubric selector → submit text → grade result
  /interview  — Mock interview: track selector → turn-by-turn dialogue
  /practice   — Practice tests: exam + subject → generate → take + submit
  /adaptive   — Adaptive packs: list signal-personalised packs
  /math       — Math Vision: upload image → step-by-step solve
  /voice      — Voice tutor: links to /chat with a "voice mode coming" notice
  /live       — Live lecture: list upcoming classes + book / join CTAs
  /recap      — Lesson recap: pick a lesson → listen to audio summary
  /notes      — Notes: list lessons → edit per-lesson notes
  /curriculum — Curriculum: browse NCERT / board chapter coverage
  /path       — Learning paths: generate multi-week plan for target exam
  /library    — Upload library: list every textbook scan you have uploaded
  /school     — School admin: list orgs you are a member of

Each page handles auth gating client-side (checks pathshala_token; redirects
to /landing if missing). Pages emit cache-control: no-store so SW cache
trap from prod-20 does not re-bite us.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

router = APIRouter()


# ---------- shared page chrome ----------

_NO_CACHE = {"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"}

_PAGE_PROLOGUE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>__TITLE__ · AI Pathshala</title>
  <style>
    *{box-sizing:border-box}
    body{margin:0;background:#0f172a;color:#e2e8f0;
      font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
    header{padding:18px 24px;border-bottom:1px solid #334155;
      display:flex;justify-content:space-between;align-items:center;
      background:#1e293b;position:sticky;top:0;z-index:10}
    header h1{margin:0;font-size:20px}
    nav a{color:#fbbf24;margin-left:14px;text-decoration:none;font-size:13px}
    main{padding:24px;max-width:980px;margin:0 auto}
    .section{margin-bottom:22px}
    .card{background:#1e293b;border:1px solid #334155;border-radius:12px;padding:18px}
    h2{margin:0 0 12px;font-size:18px}
    .sub{color:#94a3b8;font-size:13px;margin:0 0 14px}
    label{display:block;color:#cbd5e1;font-size:12px;
      text-transform:uppercase;letter-spacing:.5px;margin:10px 0 6px;font-weight:700}
    input,select,textarea{width:100%;padding:10px 12px;font-size:14px;
      background:#0f172a;color:#e2e8f0;border:1px solid #334155;border-radius:8px;
      font-family:inherit}
    textarea{min-height:140px;resize:vertical}
    input:focus,select:focus,textarea:focus{outline:0;border-color:#fbbf24}
    .btn{background:#fbbf24;color:#0f172a;border:0;padding:10px 18px;
      border-radius:8px;font-weight:800;cursor:pointer;font-size:13px;
      text-decoration:none;display:inline-block}
    .btn:hover{background:#f59e0b}
    .btn.ghost{background:transparent;color:#fbbf24;border:1px solid #fbbf24}
    .btn:disabled{opacity:.55;cursor:not-allowed}
    .grid-2{display:grid;grid-template-columns:repeat(2,1fr);gap:14px}
    .grid-3{display:grid;grid-template-columns:repeat(3,1fr);gap:14px}
    @media(max-width:780px){.grid-2,.grid-3{grid-template-columns:1fr}}
    .chip{display:inline-flex;padding:4px 10px;border-radius:999px;
      font-size:12px;font-weight:700;background:#334155;color:#e2e8f0;
      margin-right:6px;margin-bottom:6px}
    .chip.ok{background:#065f46;color:#a7f3d0}
    .chip.amber{background:#78350f;color:#fde68a}
    .chip.red{background:#7f1d1d;color:#fecaca}
    .empty{color:#64748b;font-size:13px;font-style:italic;padding:18px;text-align:center}
    .spinner{display:inline-block;width:18px;height:18px;border:3px solid #334155;
      border-top-color:#fbbf24;border-radius:50%;animation:spin .8s linear infinite}
    @keyframes spin{to{transform:rotate(360deg)}}
    .row{display:flex;gap:12px;flex-wrap:wrap;align-items:center}
    .row > *{flex:1;min-width:0}
    .row > .btn{flex:0}
    .signin{padding:40px;text-align:center;color:#94a3b8}
    .signin a{color:#fbbf24}
    .result{background:#0f172a;border:1px solid #334155;border-radius:8px;
      padding:14px;margin-top:14px;white-space:pre-wrap;font-size:14px;line-height:1.6}
    .err{color:#fecaca;background:#7f1d1d;padding:10px;border-radius:8px;margin-top:10px}
    .ok{color:#a7f3d0;background:#065f46;padding:10px;border-radius:8px;margin-top:10px}
__NAV_STYLE__
  </style>
</head>
<body>
__NAV__
  <main>
"""

_PAGE_EPILOGUE = """  </main>
  <script>
    var TOK = localStorage.getItem('pathshala_token');
__AUTH_GATE__
    function authH() { return TOK ? { 'Authorization': 'Bearer ' + TOK } : {}; }
    function phLogout() {
      try { localStorage.removeItem('pathshala_token'); } catch (e) {}
      try { localStorage.removeItem('pathshala_email'); } catch (e) {}
      location.href = '/landing';
      return false;
    }
    function escapeHtml(s) {
      return String(s == null ? '' : s)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;')
        .replace(/>/g, '&gt;').replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
    }
    __NAV_SCRIPT__
    __PAGE_SCRIPT__
  </script>
</body>
</html>
"""


# The auth gate replaces the whole <main> with a sign-in prompt when the
# visitor has no token. Pages that only read PUBLIC endpoints (e.g. the
# curriculum catalogue — non-copyrighted chapter metadata) pass
# requires_auth=False so anonymous visitors can browse without an account.
_AUTH_GATE_SNIPPET = """    if (!TOK) {
      document.querySelector('main').innerHTML =
        '<div class="signin">' +
        '<div style="font-size:40px;margin-bottom:8px">\\uD83D\\uDD10</div>' +
        '<h2 style="margin:0 0 6px;color:#e2e8f0">Sign in to continue</h2>' +
        '<p style="margin:0 auto 18px;max-width:420px">This is a personal learning tool. Sign in, or create a free account, to use it.</p>' +
        '<div style="display:flex;gap:10px;justify-content:center;flex-wrap:wrap">' +
        '<a class="btn" href="/landing?auth=login">Sign in</a>' +
        '<a class="btn ghost" href="/landing?auth=signup">Create free account</a>' +
        '<a class="btn ghost" href="/home">Back to home</a>' +
        '</div></div>';
    }"""


def _page(title: str, body: str, script: str, requires_auth: bool = True) -> str:
    from .. import ui_nav as _nav
    prologue = (
        _PAGE_PROLOGUE
        .replace("__TITLE__", title)
        .replace("__NAV_STYLE__", _nav.NAV_STYLE)
        .replace("__NAV__", _nav.NAV_HTML)
    )
    gate = _AUTH_GATE_SNIPPET if requires_auth else ""
    epilogue = (
        _PAGE_EPILOGUE
        .replace("__AUTH_GATE__", gate)
        .replace("__NAV_SCRIPT__", _nav.NAV_SCRIPT)
        .replace("__PAGE_SCRIPT__", script)
    )
    return prologue + body + epilogue


# ---------- 1. Essay grader ----------

_ESSAY_BODY = """
<section class="section">
  <div class="card">
    <h2>Essay grader</h2>
    <p class="sub">Pick a rubric, paste your answer, and get a rubric-aligned AI grade with per-criterion feedback.</p>
    <label for="rubricSel">Rubric</label>
    <select id="rubricSel"><option value="">Loading rubrics…</option></select>
    <label for="essayPrompt">Question / prompt (optional)</label>
    <input id="essayPrompt" placeholder="e.g. Discuss the causes of the 1857 Revolt." />
    <label for="essayText">Your answer</label>
    <textarea id="essayText" placeholder="Write or paste your essay / answer here. Minimum 50 words."></textarea>
    <div style="margin-top:14px">
      <button class="btn" id="gradeBtn" onclick="grade()">Grade my answer</button>
      <span id="gradeStatus" style="margin-left:10px;color:#94a3b8;font-size:13px"></span>
    </div>
    <div id="gradeOut"></div>
  </div>
</section>
"""

_ESSAY_SCRIPT = """
async function loadRubrics() {
  try {
    var r = await fetch('/api/essay/rubrics', { headers: authH() });
    var data = await r.json();
    // Response shape: {rows: [...]} (sometimes legacy: array or {rubrics: [...]}).
    var rubrics = data.rows || data.rubrics || (Array.isArray(data) ? data : []);
    var sel = document.getElementById('rubricSel');
    sel.innerHTML = '<option value="">— pick a rubric —</option>' +
      rubrics.map(function(rb) {
        var label = (rb.exam || rb.exam_key || '') + (rb.paper ? ' · ' + rb.paper : '') +
          (rb.topic ? ' — ' + rb.topic : '');
        return '<option value="' + escapeHtml(rb.id || rb.rubric_id) + '">' +
          escapeHtml(label || rb.id) + '</option>';
      }).join('');
  } catch(e) {
    document.getElementById('rubricSel').innerHTML =
      '<option value="">Could not load rubrics: ' + escapeHtml(e.message) + '</option>';
  }
}
window.grade = async function() {
  var rb = document.getElementById('rubricSel').value;
  var text = document.getElementById('essayText').value.trim();
  var out = document.getElementById('gradeOut');
  var btn = document.getElementById('gradeBtn');
  out.innerHTML = '';
  if (!rb) { out.innerHTML = '<div class="err">Pick a rubric first.</div>'; return; }
  if (text.length < 50) { out.innerHTML = '<div class="err">Write at least 50 characters — the grader needs enough content to score.</div>'; return; }
  btn.disabled = true;
  document.getElementById('gradeStatus').innerHTML = '<span class="spinner"></span> Grading…';
  try {
    var fd = new URLSearchParams();
    fd.set('rubric_id', rb);
    fd.set('text', text);  // backend field name is `text`, not `answer_text`
    fd.set('grade_now', 'true');
    var r = await fetch('/api/essay/submissions', {
      method:'POST',
      headers: Object.assign({'Content-Type':'application/x-www-form-urlencoded'}, authH()),
      body: fd.toString(),
    });
    if (!r.ok) throw new Error('HTTP ' + r.status + ' ' + (await r.text()).slice(0,200));
    var j = await r.json();
    renderGrade(j);
  } catch(e) {
    out.innerHTML = '<div class="err">Grading failed: ' + escapeHtml(e.message) + '</div>';
  } finally {
    btn.disabled = false;
    document.getElementById('gradeStatus').textContent = '';
  }
};
function renderGrade(j) {
  var out = document.getElementById('gradeOut');
  // Response shape: { submission_id, ai_grade: { score, by_criterion, summary, suggestions, method } }
  var g = j.ai_grade || j;
  if (g.error) { out.innerHTML = '<div class="err">Grader error: ' + escapeHtml(g.error) + '</div>'; return; }
  var sugg = g.suggestions || [];
  var method = g.method || '';
  // When the AI grader was skipped (free tier, or daily budget reached) the
  // backend returns a cheap keyword-heuristic number that can read as a
  // broken "0/100". Don't present it as an AI score — show an honest upgrade
  // prompt instead. method is 'budget_premium_feature' | 'budget_over_budget'.
  if (method.indexOf('budget_') === 0) {
    var upgrade = method === 'budget_over_budget'
      ? 'Try again tomorrow, or upgrade for a higher daily limit.'
      : 'Full AI essay grading (per-criterion scoring + model answer) is a premium feature.';
    out.innerHTML =
      '<div class="err" style="line-height:1.55">' +
      '<strong>AI grading needs an upgrade.</strong><br>' +
      escapeHtml(g.summary || upgrade) +
      '<br><a href="/pricing" style="display:inline-block;margin-top:10px;padding:8px 16px;' +
      'background:#2f80ed;color:#fff;border-radius:8px;text-decoration:none;font-weight:700">' +
      'See plans →</a></div>' +
      (sugg.length ? '<div style="margin-top:14px"><strong>General writing tips</strong><ul>' +
        sugg.map(function(s){ return '<li>' + escapeHtml(s) + '</li>'; }).join('') + '</ul></div>' : '');
    return;
  }
  var score = g.score != null ? g.score : '—';
  var byC = g.by_criterion || {};
  var criteria = Array.isArray(byC) ? byC
    : Object.entries(byC).map(function(kv) { return Object.assign({name:kv[0]}, kv[1]); });
  out.innerHTML =
    '<div class="ok"><strong>Overall AI score:</strong> ' + escapeHtml(String(score)) + '/100' +
    (method ? '  <span class="chip">' + escapeHtml(method) + '</span>' : '') + '</div>' +
    (g.summary ? '<div class="result"><strong>Summary</strong>\\n\\n' + escapeHtml(g.summary) + '</div>' : '') +
    (criteria.length ? '<div style="margin-top:14px"><strong>Per-criterion breakdown</strong>' +
      criteria.map(function(c) {
        return '<div class="result"><strong>' + escapeHtml(c.name || 'Criterion') +
          '</strong> — score: ' + escapeHtml(String(c.score != null ? c.score : '—')) +
          (c.weight ? ' (weight ' + escapeHtml(String(c.weight)) + ')' : '') +
          (c.feedback ? '\\n\\n' + escapeHtml(c.feedback) : '') + '</div>';
      }).join('') + '</div>' : '') +
    (sugg.length ? '<div style="margin-top:14px"><strong>Top suggestions</strong><ul>' +
      sugg.map(function(s){ return '<li>' + escapeHtml(s) + '</li>'; }).join('') + '</ul></div>' : '');
}
if (TOK) loadRubrics();
"""

_ESSAY_HTML = _page("Essay grader", _ESSAY_BODY, _ESSAY_SCRIPT)


# ---------- 2. Mock interview ----------

_INTERVIEW_BODY = """
<section class="section">
  <div class="card">
    <h2>Mock interview</h2>
    <p class="sub">Practice with an AI interviewer. Pick a track, answer turn by turn, end the session to get scored feedback.</p>
    <div id="startBox">
      <label for="trackSel">Interview track</label>
      <select id="trackSel">
        <option value="upsc_personality">UPSC Personality Test</option>
        <option value="job_swe">Software engineering job</option>
        <option value="b_school">B-school admission</option>
        <option value="medical_pg">Medical PG admission</option>
        <option value="generic_hr">Generic HR round</option>
      </select>
      <div style="margin-top:14px"><button class="btn" onclick="startInterview()">Start interview</button></div>
    </div>
    <div id="convBox" style="display:none">
      <div id="convLog"></div>
      <label for="ansBox">Your response</label>
      <textarea id="ansBox" placeholder="Type your answer. Aim for 2-4 sentences."></textarea>
      <div style="margin-top:10px">
        <button class="btn" onclick="sendTurn()">Send response</button>
        <button class="btn ghost" onclick="endInterview()" style="margin-left:8px">End interview</button>
      </div>
    </div>
    <div id="finalBox" style="display:none"></div>
  </div>
</section>
"""

_INTERVIEW_SCRIPT = """
var CURRENT_INTERVIEW = null;
var CURRENT_TURN = 0;
window.startInterview = async function() {
  var track = document.getElementById('trackSel').value;
  try {
    var fd = new URLSearchParams(); fd.set('track', track);
    var r = await fetch('/api/mock-interviews', {
      method:'POST',
      headers: Object.assign({'Content-Type':'application/x-www-form-urlencoded'}, authH()),
      body: fd.toString(),
    });
    if (!r.ok) throw new Error('HTTP ' + r.status + ' ' + (await r.text()).slice(0,200));
    var j = await r.json();
    CURRENT_INTERVIEW = j.interview_id || j.id;
    CURRENT_TURN = (j.opener && j.opener.turn_index != null) ? j.opener.turn_index : 0;
    document.getElementById('startBox').style.display = 'none';
    document.getElementById('convBox').style.display = '';
    var firstQ = (j.opener && j.opener.question_text) || 'Tell me about yourself.';
    appendBubble('interviewer', firstQ);
  } catch(e) {
    alert('Could not start: ' + e.message);
  }
};
function appendBubble(who, text) {
  var log = document.getElementById('convLog');
  var bg = who === 'interviewer' ? '#0f172a' : '#1e3a8a';
  log.innerHTML += '<div class="result" style="background:' + bg + '"><strong>' +
    (who === 'interviewer' ? 'Interviewer' : 'You') + ':</strong>\\n\\n' + escapeHtml(text) + '</div>';
  log.scrollTop = log.scrollHeight;
}
window.sendTurn = async function() {
  var ans = document.getElementById('ansBox').value.trim();
  if (!ans) return;
  appendBubble('me', ans);
  document.getElementById('ansBox').value = '';
  try {
    var fd = new URLSearchParams();
    fd.set('turn_index', String(CURRENT_TURN));
    fd.set('answer_text', ans);
    var r = await fetch('/api/mock-interviews/' + encodeURIComponent(CURRENT_INTERVIEW) + '/answer', {
      method:'POST',
      headers: Object.assign({'Content-Type':'application/x-www-form-urlencoded'}, authH()),
      body: fd.toString(),
    });
    if (!r.ok) throw new Error('HTTP ' + r.status + ' ' + (await r.text()).slice(0,200));
    var j = await r.json();
    if (j.feedback) appendBubble('interviewer', '(Feedback) ' + j.feedback);
    if (j.next && j.next.question_text) {
      CURRENT_TURN = j.next.turn_index;
      appendBubble('interviewer', j.next.question_text);
    }
    if (j.interview_ended) endInterview();
  } catch(e) {
    alert('Turn failed: ' + e.message);
  }
};
window.endInterview = async function() {
  try {
    var r = await fetch('/api/mock-interviews/' + encodeURIComponent(CURRENT_INTERVIEW) + '/end', {
      method:'POST', headers: authH(),
    });
    var fb = await r.json();
    document.getElementById('convBox').style.display = 'none';
    var final = document.getElementById('finalBox');
    var critDict = fb.criteria_avg || {};
    var crit = Array.isArray(critDict) ? critDict
      : Object.entries(critDict).map(function(kv){ return {name:kv[0], avg:kv[1]}; });
    final.style.display = '';
    final.innerHTML =
      '<div class="ok"><strong>Interview complete.</strong> Overall: ' +
        escapeHtml(String(fb.overall_score != null ? fb.overall_score.toFixed(1) : '—')) + '</div>' +
      (crit.length ? '<div style="margin-top:14px"><strong>Per-criterion average</strong>' +
        crit.map(function(c) {
          return '<div class="result"><strong>' + escapeHtml(c.name) + '</strong> — ' +
            escapeHtml(String(c.avg != null ? Number(c.avg).toFixed(1) : '—')) + '</div>';
        }).join('') + '</div>' : '') +
      ((fb.top_improvements || []).length ? '<div style="margin-top:14px"><strong>Top improvements</strong><ul>' +
        fb.top_improvements.map(function(t){ return '<li>' + escapeHtml(t) + '</li>'; }).join('') +
        '</ul></div>' : '');
  } catch(e) {
    alert('End failed: ' + e.message);
  }
};
"""

_INTERVIEW_HTML = _page("Mock interview", _INTERVIEW_BODY, _INTERVIEW_SCRIPT)


# ---------- 3. Practice tests ----------

_PRACTICE_BODY = """
<section class="section">
  <div class="card">
    <h2>Practice tests</h2>
    <p class="sub">Generate an adaptive test from the question bank. Tests pull from your weak topics first.</p>
    <div class="grid-3">
      <div>
        <label>Exam</label>
        <select id="examSel">
          <optgroup label="School boards">
            <option value="cbse" selected>CBSE</option>
            <option value="icse">ICSE</option>
            <option value="state">State board</option>
          </optgroup>
          <optgroup label="Competitive exams">
            <option value="jee_main">JEE Main</option>
            <option value="jee_advanced">JEE Advanced</option>
            <option value="neet">NEET UG</option>
            <option value="upsc_pre">UPSC Prelims</option>
            <option value="upsc_mains">UPSC Mains</option>
            <option value="cat">CAT</option>
            <option value="gate">GATE</option>
            <option value="sat">SAT (US Digital SAT)</option>
          </optgroup>
        </select>
      </div>
      <div>
        <label>Subject</label>
        <select id="subjectSel">
          <option value="mathematics">Mathematics</option>
          <option value="physics">Physics</option>
          <option value="chemistry">Chemistry</option>
          <option value="biology">Biology</option>
          <option value="english">English</option>
          <option value="history">History</option>
          <option value="geography">Geography</option>
          <option value="polity">Polity</option>
          <option value="economy">Economy</option>
          <option value="quant">Quant / Reasoning</option>
        </select>
      </div>
      <div>
        <label>Questions</label>
        <select id="qSel">
          <option value="5">5</option>
          <option value="10" selected>10</option>
          <option value="20">20</option>
          <option value="30">30</option>
        </select>
      </div>
    </div>
    <div style="margin-top:14px"><button class="btn" id="genBtn" onclick="genTest()">Generate test</button></div>
  </div>
</section>
<section id="testArea" class="section" style="display:none"></section>
"""

_PRACTICE_SCRIPT = """
var TEST = null;
var ANSWERS = {};
// prod-224: make the Subject dropdown exam-aware. SAT has its own two
// sections (Math, Reading & Writing) that map to the seeded question bank;
// every other exam keeps the default Indian-subject list.
(function(){
  var examEl = document.getElementById('examSel');
  var subjEl = document.getElementById('subjectSel');
  if (!examEl || !subjEl) return;
  var defaultOpts = subjEl.innerHTML;
  var SAT_OPTS = '<option value="sat_math">SAT Math</option>'
    + '<option value="sat_reading_writing">SAT Reading &amp; Writing</option>';
  function sync(){
    if (examEl.value === 'sat') {
      if (subjEl.getAttribute('data-mode') !== 'sat') {
        subjEl.innerHTML = SAT_OPTS; subjEl.setAttribute('data-mode','sat');
      }
    } else if (subjEl.getAttribute('data-mode') === 'sat') {
      subjEl.innerHTML = defaultOpts; subjEl.setAttribute('data-mode','default');
    }
  }
  examEl.addEventListener('change', sync);
  sync();
})();
window.genTest = async function() {
  var btn = document.getElementById('genBtn');
  btn.disabled = true; btn.textContent = 'Generating…';
  try {
    var fd = new FormData();
    var nq = parseInt(document.getElementById('qSel').value, 10) || 10;
    fd.append('exam', document.getElementById('examSel').value);
    fd.append('subject', document.getElementById('subjectSel').value);
    fd.append('num_questions', String(nq));
    // Keep target_minutes coherent with the chosen length (~90s/question)
    // so the results header reads sensibly.
    fd.append('target_minutes', String(Math.max(5, Math.min(240, Math.round(nq * 1.5)))));
    var r = await fetch('/api/practice-tests', {method:'POST', headers: authH(), body: fd});
    if (!r.ok) throw new Error('HTTP ' + r.status + ' — ' + (await r.text()).slice(0,200));
    var summary = await r.json();
    await fetch('/api/practice-tests/' + summary.id + '/start', {method:'POST', headers: authH(), body: new FormData()});
    var r3 = await fetch('/api/practice-tests/' + summary.id, {headers: authH()});
    TEST = await r3.json();
    ANSWERS = {};
    renderTest();
  } catch(e) {
    alert('Generate failed: ' + e.message);
  } finally {
    btn.disabled = false; btn.textContent = 'Generate test';
  }
};
function renderTest() {
  var area = document.getElementById('testArea');
  area.style.display = '';
  area.innerHTML = '<div class="card"><h2>' + escapeHtml(TEST.exam) + ' · ' + escapeHtml(TEST.subject) + '</h2>' +
    '<p class="sub">' + TEST.questions.length + ' questions · ' + TEST.target_minutes + ' min target</p>' +
    TEST.questions.map(function(q, i) {
      var opts = (q.options || []).map(function(o, oi) {
        var key = String.fromCharCode(65 + oi);
        return '<label style="display:block;padding:6px 0">' +
          '<input type="radio" name="q' + i + '" value="' + key + '" onclick="ANSWERS[\\'q' + i + '\\']=\\'' + key + '\\'" /> ' +
          '<strong>' + key + '.</strong> ' + escapeHtml(o) + '</label>';
      }).join('');
      return '<div class="result"><strong>Q' + (i+1) + '.</strong> ' + escapeHtml(q.question_text || q.text) +
        '<div style="margin-top:8px">' + opts + '</div></div>';
    }).join('') +
    '<div style="margin-top:14px"><button class="btn" onclick="submitTest()">Submit test</button></div>' +
    '<div id="testResult"></div></div>';
}
window.submitTest = async function() {
  try {
    var fd = new FormData();
    fd.append('answers_json', JSON.stringify(ANSWERS));
    var r = await fetch('/api/practice-tests/' + TEST.id + '/submit', {method:'POST', headers: authH(), body: fd});
    if (!r.ok) throw new Error('HTTP ' + r.status);
    var j = await r.json();
    var pct = j.score ? Math.round(j.score.pct * 100) : 0;
    document.getElementById('testResult').innerHTML =
      '<div class="ok" style="margin-top:14px"><strong>Score:</strong> ' + pct + '% — ' +
      (j.score ? j.score.correct + ' / ' + j.score.total : '—') + ' correct</div>';
  } catch(e) {
    alert('Submit failed: ' + e.message);
  }
};
"""

_PRACTICE_HTML = _page("Practice tests", _PRACTICE_BODY, _PRACTICE_SCRIPT)


# ---------- 4. Adaptive packs ----------

_ADAPTIVE_BODY = """
<section class="section">
  <div class="card">
    <h2>Adaptive practice</h2>
    <p class="sub">Personalised topic packs based on your weak-topic signals. Packs adjust as you study.</p>
    <div id="adaptiveOut"><div class="empty"><span class="spinner"></span> Loading…</div></div>
  </div>
</section>
"""

_ADAPTIVE_SCRIPT = """
async function loadAdaptive() {
  if (!TOK) return;
  var out = document.getElementById('adaptiveOut');
  try {
    var r = await fetch('/api/adaptive-packs/me', { headers: authH() });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    var d = await r.json();
    var packs = d.packs || d.rows || (Array.isArray(d) ? d : []);
    if (!packs.length) {
      out.innerHTML = '<div class="empty">No adaptive packs yet. They appear after you enrol in an exam pack and complete a practice test.</div>' +
        '<div style="margin-top:14px;display:flex;gap:8px;flex-wrap:wrap">' +
          '<a class="btn" href="/dashboard#browse-packs">Browse exam packs →</a>' +
          '<a class="btn ghost" href="/practice">Take a practice test →</a>' +
        '</div>';
      return;
    }
    out.innerHTML = '<div class="grid-2">' + packs.map(function(p) {
      return '<div class="result"><strong>' + escapeHtml(p.base_pack_title || p.title || p.base_pack_code) + '</strong>' +
        '<div class="sub" style="margin-top:6px">Adjusted: ' + escapeHtml(p.rationale || 'Personalised topic weights based on your signals.') + '</div>' +
        '<div style="margin-top:10px"><a class="btn ghost" data-code="' + escapeHtml(p.base_pack_code) +
          '" onclick="loadAdaptiveTopics(this.dataset.code)">View topics →</a></div>' +
        '<div id="adt-' + escapeHtml(p.base_pack_code) + '"></div></div>';
    }).join('') + '</div>';
  } catch(e) {
    out.innerHTML = '<div class="err">Could not load: ' + escapeHtml(e.message) + '</div>';
  }
}
window.loadAdaptiveTopics = async function(packCode) {
  var box = document.getElementById('adt-' + packCode);
  if (!box) return;
  box.innerHTML = '<div class="sub" style="margin-top:8px"><span class="spinner"></span> Loading topics...</div>';
  try {
    var r = await fetch('/api/adaptive-packs/' + encodeURIComponent(packCode) + '/topics', { headers: authH() });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    var d = await r.json();
    var topics = d.topics || d.rows || [];
    box.innerHTML = '<ul class="list" style="margin-top:8px">' + topics.slice(0, 8).map(function(t) {
      var w = t.adjusted_weightage != null ? t.adjusted_weightage : (t.base_weightage || 0);
      var pct = Math.round(w * 100);
      var cls = pct >= 70 ? 'pill red' : pct >= 40 ? 'pill warn' : 'pill ok';
      return '<li><span>' + escapeHtml(t.title || t.topic_code) + '</span><span class="' + cls + '">' + pct + '%</span></li>';
    }).join('') + '</ul>';
  } catch(e) {
    box.innerHTML = '<div class="err">' + escapeHtml(e.message) + '</div>';
  }
};
if (TOK) loadAdaptive();
"""

_ADAPTIVE_HTML = _page("Adaptive practice", _ADAPTIVE_BODY, _ADAPTIVE_SCRIPT)


# ---------- 5. Math Vision ----------

_MATH_BODY = """
<section class="section">
  <div class="card">
    <h2>Math Vision (Doubt clearing)</h2>
    <p class="sub">Snap or upload a math / science problem, or type it. We use Claude Vision to give a step-by-step solve.</p>
    <label for="mathFile">Problem image (PNG / JPG, optional)</label>
    <input id="mathFile" type="file" accept="image/*" />
    <label for="mathText" style="margin-top:14px">Or type the question</label>
    <textarea id="mathText" placeholder="e.g. Solve x^2 + 5x + 6 = 0" style="min-height:80px"></textarea>
    <label for="mathSubject" style="margin-top:14px">Subject</label>
    <select id="mathSubject">
      <option value="mathematics">Mathematics</option>
      <option value="physics">Physics</option>
      <option value="chemistry">Chemistry</option>
      <option value="biology">Biology</option>
    </select>
    <div style="margin-top:14px">
      <button class="btn" id="mvBtn" onclick="solveMath()">Get AI solution</button>
      <span id="mvStatus" style="margin-left:10px;color:#94a3b8;font-size:13px"></span>
    </div>
    <div id="mvOut"></div>
  </div>
</section>
"""

_MATH_SCRIPT = """
window.solveMath = async function() {
  var file = document.getElementById('mathFile').files[0];
  var text = document.getElementById('mathText').value.trim();
  var subject = document.getElementById('mathSubject').value;
  var out = document.getElementById('mvOut');
  var btn = document.getElementById('mvBtn');
  out.innerHTML = '';
  if (!file && !text) {
    out.innerHTML = '<div class="err">Either upload an image or type the question.</div>';
    return;
  }
  btn.disabled = true;
  document.getElementById('mvStatus').innerHTML = '<span class="spinner"></span> Solving (Claude Vision)…';
  try {
    // Step 1: if there's a file, upload it first to get a URL
    var imageUrl = null;
    if (file) {
      var ufd = new FormData();
      ufd.append('file', file);
      var ur = await fetch('/api/uploads', { method:'POST', headers: authH(), body: ufd });
      if (!ur.ok) throw new Error('Upload failed: HTTP ' + ur.status);
      var u = await ur.json();
      imageUrl = u.public_url || u.image_url || u.url || ('/uploads/' + u.id);
    }
    // Step 2: submit instant doubt
    // Backend requires question_text >= 5 chars even when image is provided.
    // If user only uploaded an image, fill a sensible default so they don't
    // have to type anything.
    var effectiveText = text;
    if (!effectiveText && imageUrl) {
      effectiveText = 'Please solve the problem shown in this image. Show full steps.';
    }
    var fd = new URLSearchParams();
    if (imageUrl) fd.set('image_url', imageUrl);
    fd.set('question_text', effectiveText);
    if (subject) fd.set('subject', subject);
    var r = await fetch('/api/doubts/submit-instant', {
      method:'POST',
      headers: Object.assign({'Content-Type':'application/x-www-form-urlencoded'}, authH()),
      body: fd.toString(),
    });
    if (!r.ok) throw new Error('HTTP ' + r.status + ' — ' + (await r.text()).slice(0,200));
    var j = await r.json();
    var ans = j.response_text || '';
    if (!ans && j.ai_error) {
      out.innerHTML = '<div class="err">AI error: ' + escapeHtml(j.ai_error) + '</div>';
      return;
    }
    out.innerHTML = '<div class="ok"><strong>Solved</strong> · status: ' + escapeHtml(j.status || '?') + '</div>' +
      '<div class="result"><strong>AI solution</strong>\\n\\n' + escapeHtml(ans || '(no answer returned)') + '</div>';
  } catch(e) {
    out.innerHTML = '<div class="err">Could not solve: ' + escapeHtml(e.message) + '</div>';
  } finally {
    btn.disabled = false;
    document.getElementById('mvStatus').textContent = '';
  }
};
"""

_MATH_HTML = _page("Math Vision", _MATH_BODY, _MATH_SCRIPT)


# ---------- 6. Voice tutor ----------

_VOICE_BODY = """
<section class="section">
  <div class="card">
    <h2>Voice tutor</h2>
    <p class="sub">Talk to the AI tutor with your voice. We use the browser's built-in speech recognition to capture your question, then read the answer back aloud.</p>
    <div class="row">
      <button class="btn" id="micBtn" onclick="startListen()">🎙 Hold to speak</button>
      <button class="btn ghost" onclick="stopListen()">Stop</button>
    </div>
    <div id="voiceTranscript" class="result" style="display:none;margin-top:14px"></div>
    <div id="voiceAnswer" class="result" style="display:none"></div>
    <p class="sub" style="margin-top:14px">Tip: speech recognition needs a Chromium-based browser (Chrome / Edge / Brave). On unsupported browsers, type your question on <a href="/chat" style="color:#fbbf24">AI Tutor</a> instead.</p>
  </div>
</section>
"""

_VOICE_SCRIPT = """
var recognition = null, listening = false;
window.startListen = function() {
  var SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) {
    document.getElementById('voiceTranscript').style.display = '';
    document.getElementById('voiceTranscript').innerHTML =
      '<div class="err">Browser does not support speech recognition. Use Chrome, Edge, or type on <a href="/chat" style="color:#fbbf24">AI Tutor</a>.</div>';
    return;
  }
  recognition = new SR();
  recognition.lang = 'en-IN';
  recognition.interimResults = false;
  recognition.maxAlternatives = 1;
  listening = true;
  document.getElementById('micBtn').textContent = '🔴 Listening…';
  recognition.onresult = function(e) {
    var text = e.results[0][0].transcript;
    document.getElementById('voiceTranscript').style.display = '';
    document.getElementById('voiceTranscript').innerHTML = '<strong>You said:</strong> ' + escapeHtml(text);
    askTutor(text);
  };
  recognition.onerror = function(e) {
    document.getElementById('voiceTranscript').style.display = '';
    document.getElementById('voiceTranscript').innerHTML =
      '<div class="err">Recognition error: ' + escapeHtml(e.error || 'unknown') + '</div>';
  };
  recognition.onend = function() {
    listening = false;
    document.getElementById('micBtn').textContent = '🎙 Hold to speak';
  };
  recognition.start();
};
window.stopListen = function() {
  if (recognition && listening) recognition.stop();
};
async function askTutor(question) {
  var fd = new URLSearchParams(); fd.set('question', question);
  try {
    var r = await fetch('/chat/general', {
      method:'POST',
      headers: Object.assign({'Content-Type':'application/x-www-form-urlencoded'}, authH()),
      body: fd.toString(),
    });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    var j = await r.json();
    var ans = j.answer || '(no answer)';
    document.getElementById('voiceAnswer').style.display = '';
    document.getElementById('voiceAnswer').innerHTML = '<strong>Tutor:</strong> ' + escapeHtml(ans);
    if ('speechSynthesis' in window) {
      var u = new SpeechSynthesisUtterance(ans.slice(0, 500));
      u.lang = 'en-IN';
      window.speechSynthesis.speak(u);
    }
  } catch(e) {
    document.getElementById('voiceAnswer').style.display = '';
    document.getElementById('voiceAnswer').innerHTML = '<div class="err">Tutor error: ' + escapeHtml(e.message) + '</div>';
  }
}
"""

_VOICE_HTML = _page("Voice tutor", _VOICE_BODY, _VOICE_SCRIPT)


# ---------- 7. Live lecture ----------

_LIVE_BODY = """
<section class="section">
  <div class="card">
    <h2>Live lectures</h2>
    <p class="sub">Upcoming sessions you can join, plus a list of past lectures for replay.</p>
    <div id="liveOut"><div class="empty"><span class="spinner"></span> Loading…</div></div>
  </div>
</section>
"""

_LIVE_SCRIPT = """
async function loadLive() {
  if (!TOK) return;
  var out = document.getElementById('liveOut');
  try {
    var r = await fetch('/api/live/upcoming?limit=20', { headers: authH() });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    var d = await r.json();
    var rows = d.upcoming || d.rows || d;
    if (!rows || !rows.length) {
      // prod-154 — V1 reference dropped; empty state directs to /dashboard for the daily plan instead.
      out.innerHTML = '<div class="empty">No upcoming live lectures scheduled. <a href="/dashboard" style="color:#fbbf24">Open your daily plan →</a></div>';
      return;
    }
    out.innerHTML = rows.map(function(lc) {
      var when = lc.scheduled_at ? new Date(lc.scheduled_at * 1000).toLocaleString() : 'TBD';
      return '<div class="result"><strong>' + escapeHtml(lc.title || 'Untitled lecture') + '</strong>' +
        '<div class="sub">' + escapeHtml(when) + (lc.tutor_name ? ' · ' + escapeHtml(lc.tutor_name) : '') + '</div>' +
        (lc.join_url ? '<a class="btn" href="' + escapeHtml(lc.join_url) + '" target="_blank">Join</a>' :
                       '<span class="chip">Link will appear at start time</span>') +
        '</div>';
    }).join('');
  } catch(e) {
    // prod-154 — V1 reference dropped; offer retry button + dashboard fallback.
    out.innerHTML = '<div class="err">Could not load: ' + escapeHtml(e.message) +
      '<div style="margin-top:8px"><button class="btn ghost" onclick="loadLive()">Retry</button> ' +
      '<a class="btn ghost" href="/dashboard">Open dashboard</a></div></div>';
  }
}
if (TOK) loadLive();
"""

_LIVE_HTML = _page("Live lectures", _LIVE_BODY, _LIVE_SCRIPT)


# ---------- 8. Lesson recap ----------

_RECAP_BODY = """
<section class="section">
  <div class="card">
    <h2>Lesson recap</h2>
    <p class="sub">Pick any lesson you have generated and listen to a podcast-style audio summary.</p>
    <div id="recapOut"><div class="empty"><span class="spinner"></span> Loading lessons…</div></div>
  </div>
</section>
"""

_RECAP_SCRIPT = """
async function loadLessons() {
  if (!TOK) return;
  var out = document.getElementById('recapOut');
  try {
    var r = await fetch('/jobs?limit=20', { headers: authH() });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    var d = await r.json();
    var jobs = (d.jobs || []).filter(function(j){ return j.status === 'succeeded' && j.lesson_id; });
    if (!jobs.length) {
      out.innerHTML = '<div class="empty">No completed lessons yet. <a href="/lessons/new" style="color:#fbbf24">Upload a textbook page</a> to create one — the recap is generated from the lesson plan.</div>';
      return;
    }
    out.innerHTML = jobs.map(function(j) {
      return '<div class="result"><strong>' + escapeHtml(j.topic || j.lesson_id.slice(0,8) + '…') + '</strong>' +
        '<div class="sub">Job ' + escapeHtml(j.id.slice(0,8)) + '… · ' + escapeHtml(j.language_code || 'en') + '</div>' +
        '<div style="margin-top:8px"><button class="btn" data-lid="' + escapeHtml(j.lesson_id) +
          '" onclick="genRecap(this.dataset.lid)">Generate / play recap</button></div>' +
        '<div id="recap-' + escapeHtml(j.lesson_id) + '"></div></div>';
    }).join('');
  } catch(e) {
    out.innerHTML = '<div class="err">Could not load: ' + escapeHtml(e.message) + '</div>';
  }
}
window.genRecap = async function(lid) {
  var box = document.getElementById('recap-' + lid);
  box.innerHTML = '<div class="sub" style="margin-top:6px"><span class="spinner"></span> Generating…</div>';
  try {
    var r = await fetch('/lessons/' + encodeURIComponent(lid) + '/recap', {method:'POST', headers: authH()});
    if (!r.ok) throw new Error('HTTP ' + r.status);
    var j = await r.json();
    box.innerHTML = '<div class="result" style="margin-top:8px">' + escapeHtml(j.text || '(no text)') + '</div>' +
      (j.audio_url ? '<audio controls style="width:100%;margin-top:8px"><source src="' + escapeHtml(j.audio_url) + '" type="audio/mpeg"></audio>' :
                     '<div class="sub">Audio not available' + (j.audio_error ? ' (' + escapeHtml(j.audio_error) + ')' : '') + '</div>');
  } catch(e) {
    box.innerHTML = '<div class="err" style="margin-top:8px">' + escapeHtml(e.message) + '</div>';
  }
};
if (TOK) loadLessons();
"""

_RECAP_HTML = _page("Lesson recap", _RECAP_BODY, _RECAP_SCRIPT)


# ---------- 9. Notes ----------

_NOTES_BODY = """
<section class="section">
  <div class="card">
    <h2>Lesson notes</h2>
    <p class="sub">Personal notes for each lesson you have generated. Stored privately to your account.</p>
    <div id="notesOut"><div class="empty"><span class="spinner"></span> Loading…</div></div>
  </div>
</section>
"""

_NOTES_SCRIPT = """
async function loadNotesLessons() {
  if (!TOK) return;
  var out = document.getElementById('notesOut');
  try {
    var r = await fetch('/jobs?limit=20', { headers: authH() });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    var d = await r.json();
    var jobs = (d.jobs || []).filter(function(j){ return j.status === 'succeeded' && j.lesson_id; });
    if (!jobs.length) {
      out.innerHTML = '<div class="empty">No lessons yet to take notes on. <a href="/lessons/new" style="color:#fbbf24">Upload a textbook page</a> to create your first lesson.</div>';
      return;
    }
    out.innerHTML = jobs.map(function(j) {
      var lid = j.lesson_id;
      return '<div class="result"><strong>' + escapeHtml(j.topic || lid.slice(0,8) + '…') + '</strong>' +
        '<textarea id="n-' + escapeHtml(lid) + '" placeholder="Loading notes…" style="margin-top:8px"></textarea>' +
        '<div style="margin-top:8px"><button class="btn" data-lid="' + escapeHtml(lid) +
          '" onclick="saveNote(this.dataset.lid)">Save notes</button>' +
          '<span id="ns-' + escapeHtml(lid) + '" style="margin-left:10px;color:#a7f3d0;font-size:13px"></span></div></div>';
    }).join('');
    jobs.forEach(function(j) { fetchNote(j.lesson_id); });
  } catch(e) {
    out.innerHTML = '<div class="err">Could not load: ' + escapeHtml(e.message) + '</div>';
  }
}
async function fetchNote(lid) {
  try {
    var r = await fetch('/lessons/' + encodeURIComponent(lid) + '/notes', { headers: authH() });
    if (!r.ok) return;
    var j = await r.json();
    var ta = document.getElementById('n-' + lid);
    if (ta) ta.value = j.notes || '';
  } catch(e) {}
}
window.saveNote = async function(lid) {
  var ta = document.getElementById('n-' + lid);
  var status = document.getElementById('ns-' + lid);
  try {
    var fd = new URLSearchParams(); fd.set('notes', ta.value);
    var r = await fetch('/lessons/' + encodeURIComponent(lid) + '/notes', {
      method:'POST',
      headers: Object.assign({'Content-Type':'application/x-www-form-urlencoded'}, authH()),
      body: fd.toString(),
    });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    status.textContent = '✓ saved';
    setTimeout(function(){ status.textContent = ''; }, 1800);
  } catch(e) {
    status.textContent = 'Save failed: ' + e.message;
    status.style.color = '#fecaca';
  }
};
if (TOK) loadNotesLessons();
"""

_NOTES_HTML = _page("Lesson notes", _NOTES_BODY, _NOTES_SCRIPT)


# ---------- 10. Curriculum browser ----------

_CURRICULUM_BODY = """
<section class="section">
  <div class="card">
    <h2>Curriculum</h2>
    <p class="sub">Browse NCERT / CBSE / state board chapter coverage by class and subject.</p>
    <div class="row">
      <select id="curBoard"><option value="">All boards</option><option value="CBSE">CBSE</option><option value="ICSE">ICSE</option><option value="Maharashtra">Maharashtra</option><option value="TamilNadu">Tamil Nadu</option><option value="Karnataka">Karnataka</option><option value="AP_Telangana">AP / Telangana</option><option value="UP">UP</option><option value="JEE">JEE</option><option value="NEET">NEET</option></select>
      <select id="curGrade"><option value="">All classes</option><option value="6">6</option><option value="7">7</option><option value="8">8</option><option value="9">9</option><option value="10">10</option><option value="11">11</option><option value="12">12</option></select>
      <select id="curSubject"><option value="">All subjects</option><option value="Maths">Maths</option><option value="Physics">Physics</option><option value="Chemistry">Chemistry</option><option value="Biology">Biology</option><option value="Science">Science</option><option value="Physical Science">Physical Science</option></select>
      <button class="btn" onclick="loadCurriculum()">Filter</button>
    </div>
    <div id="curOut" style="margin-top:14px"><div class="empty"><span class="spinner"></span> Loading…</div></div>
  </div>
</section>
"""

_CURRICULUM_SCRIPT = """
window.loadCurriculum = async function() {
  var out = document.getElementById('curOut');
  out.innerHTML = '<div class="empty"><span class="spinner"></span> Loading…</div>';
  var b = document.getElementById('curBoard').value;
  var g = document.getElementById('curGrade').value;
  var s = document.getElementById('curSubject').value;
  var qs = [];
  if (b) qs.push('board=' + encodeURIComponent(b));
  if (g) qs.push('cls=' + encodeURIComponent(g));
  if (s) qs.push('subject=' + encodeURIComponent(s));
  try {
    var r = await fetch('/curriculum/index' + (qs.length ? '?' + qs.join('&') : ''));
    if (!r.ok) throw new Error('HTTP ' + r.status);
    var d = await r.json();
    var rows = d.entries || d.curriculum || d.rows || (Array.isArray(d) ? d : []);
    if (!rows || !rows.length) {
      out.innerHTML = '<div class="empty">No chapters match those filters. Try “All boards / All classes / All subjects”.</div>';
      return;
    }
    var shown = Math.min(rows.length, 120);
    out.innerHTML = '<div class="sub" style="margin-bottom:10px">Showing ' + shown +
      (rows.length > shown ? ' of ' + rows.length : '') + ' chapter' + (rows.length === 1 ? '' : 's') + '</div>' +
      rows.slice(0, 120).map(function(c) {
        // Backend rows use `class` (reserved word in JS — bracket-access)
        var cls = c['class'] != null ? c['class'] : (c.grade != null ? c.grade : '?');
        var summary = c.summary ? '<div class="sub" style="margin-top:6px">' + escapeHtml(c.summary) + '</div>' : '';
        var topics = (c.topics || []).length
          ? '<div style="margin-top:6px">' + c.topics.slice(0, 6).map(function(t) {
              return '<span class="chip">' + escapeHtml(t) + '</span>';
            }).join('') + '</div>'
          : '';
        return '<div class="result"><strong>' +
          (c.chapter_no ? c.chapter_no + '. ' : '') +
          escapeHtml(c.chapter_title || c.title || '(untitled)') + '</strong>' +
          '<div class="sub">' + escapeHtml(c.board || '?') + ' · Class ' + escapeHtml(String(cls)) +
          ' · ' + escapeHtml(c.subject || '?') + (c.level ? ' · ' + escapeHtml(c.level) : '') + '</div>' +
          summary + topics + '</div>';
      }).join('');
  } catch(e) {
    out.innerHTML = '<div class="err">Could not load: ' + escapeHtml(e.message) + '</div>';
  }
};

// Populate a <select> with real values from the catalogue so the filters
// never offer a board/subject the data doesn't actually have.
function _curFill(id, values, allLabel, keep) {
  var sel = document.getElementById(id);
  if (!sel) return;
  var cur = keep ? sel.value : '';
  sel.innerHTML = '<option value="">' + allLabel + '</option>' +
    values.map(function(v) {
      var sv = String(v);
      return '<option value="' + escapeHtml(sv) + '"' + (sv === cur ? ' selected' : '') +
             '>' + escapeHtml(sv) + '</option>';
    }).join('');
  if (cur && values.map(String).indexOf(cur) < 0) sel.value = '';
}

// Refresh the subject dropdown scoped to the current board+class (cascade) so
// you can't pick a subject that board doesn't teach → no dead-end 0 results.
// Deliberately omits the subject filter itself (the endpoint would otherwise
// collapse `subjects` to the single selected value).
async function _curRefreshSubjects() {
  var b = document.getElementById('curBoard').value;
  var g = document.getElementById('curGrade').value;
  var qs = [];
  if (b) qs.push('board=' + encodeURIComponent(b));
  if (g) qs.push('cls=' + encodeURIComponent(g));
  try {
    var r = await fetch('/curriculum/index' + (qs.length ? '?' + qs.join('&') : ''));
    var d = await r.json();
    _curFill('curSubject', d.subjects || [], 'All subjects', true);
  } catch(e) {}
}

(async function initCurriculum() {
  // Populate all three dropdowns from the full catalogue so every option
  // maps to real data (data has more boards than the old hardcoded list).
  try {
    var r = await fetch('/curriculum/index');
    var d = await r.json();
    _curFill('curBoard', d.boards || [], 'All boards', false);
    _curFill('curGrade', d.classes || [], 'All classes', false);
    _curFill('curSubject', d.subjects || [], 'All subjects', false);
  } catch(e) {}
  loadCurriculum();
  // Auto-filter on change; board/class change also re-scopes the subjects.
  var bs = document.getElementById('curBoard');
  var gs = document.getElementById('curGrade');
  var ss = document.getElementById('curSubject');
  if (bs) bs.addEventListener('change', async function(){ await _curRefreshSubjects(); loadCurriculum(); });
  if (gs) gs.addEventListener('change', async function(){ await _curRefreshSubjects(); loadCurriculum(); });
  if (ss) ss.addEventListener('change', loadCurriculum);
})();
"""

# Public page — the curriculum catalogue is non-copyrighted metadata
# (chapter titles + topic tags), browseable without an account.
_CURRICULUM_HTML = _page(
    "Curriculum", _CURRICULUM_BODY, _CURRICULUM_SCRIPT, requires_auth=False,
)


# ---------- 11. Learning paths ----------

_PATH_BODY = """
<section class="section">
  <div class="card">
    <h2>Learning paths</h2>
    <p class="sub">Generate a multi-week study plan with Claude. Cached deterministically — same inputs come back instantly.</p>
    <div class="grid-3">
      <div>
        <label>Class (1-12)</label>
        <select id="pathClass">
          <option value="1">Class 1</option><option value="2">Class 2</option><option value="3">Class 3</option>
          <option value="4">Class 4</option><option value="5">Class 5</option><option value="6">Class 6</option>
          <option value="7">Class 7</option><option value="8">Class 8</option><option value="9">Class 9</option>
          <option value="10" selected>Class 10</option><option value="11">Class 11</option><option value="12">Class 12</option>
        </select>
      </div>
      <div>
        <label>Subjects (comma-separated)</label>
        <input id="pathSubjects" value="Mathematics, Science" />
      </div>
      <div>
        <label>Weeks</label>
        <select id="pathWeeks">
          <option value="2">2 weeks</option>
          <option value="4" selected>4 weeks</option>
          <option value="8">8 weeks</option>
          <option value="12">12 weeks (3 months)</option>
        </select>
      </div>
    </div>
    <label style="margin-top:14px">Daily minutes</label>
    <select id="pathMin" style="max-width:200px">
      <option value="15">15 min/day</option>
      <option value="30" selected>30 min/day</option>
      <option value="60">1 hr/day</option>
      <option value="120">2 hrs/day</option>
    </select>
    <div style="margin-top:14px">
      <button class="btn" id="pathBtn" onclick="genPath()">Generate my learning path</button>
      <span id="pathStatus" style="margin-left:10px;color:#94a3b8;font-size:13px"></span>
    </div>
    <div id="pathOut"></div>
  </div>
</section>
"""

_PATH_SCRIPT = """
// Pre-fill defaults from the user's onboarding when available
fetch('/api/me/dashboard', { headers: authH() }).then(function(r){ return r.json(); }).then(function(d) {
  var onb = (d && d.onboarding) || {};
  var cg = onb.class_grade || '';
  var m = cg.match(/class_(\\d+)/);
  if (m && m[1] >= 1 && m[1] <= 12) document.getElementById('pathClass').value = m[1];
  if (onb.goal_minutes_daily) {
    var minSel = document.getElementById('pathMin');
    [15,30,60,120].forEach(function(v){
      if (Math.abs(v - onb.goal_minutes_daily) < 8) minSel.value = v;
    });
  }
}).catch(function(){});

window.genPath = async function() {
  var btn = document.getElementById('pathBtn');
  var cls = document.getElementById('pathClass').value;
  var subjects = document.getElementById('pathSubjects').value.trim();
  var weeks = document.getElementById('pathWeeks').value;
  var minutes = document.getElementById('pathMin').value;
  if (!subjects) {
    document.getElementById('pathOut').innerHTML = '<div class="err">Enter at least one subject (e.g. Mathematics).</div>';
    return;
  }
  btn.disabled = true;
  document.getElementById('pathStatus').innerHTML = '<span class="spinner"></span> Building plan with Claude (this can take ~30s)…';
  document.getElementById('pathOut').innerHTML = '';
  try {
    var fd = new URLSearchParams();
    fd.set('student_class', cls);
    fd.set('subjects', subjects);
    fd.set('weeks', weeks);
    fd.set('daily_minutes', minutes);
    var r = await fetch('/learning-path', {
      method:'POST',
      headers: Object.assign({'Content-Type':'application/x-www-form-urlencoded'}, authH()),
      body: fd.toString(),
    });
    if (!r.ok) throw new Error('HTTP ' + r.status + ' — ' + (await r.text()).slice(0,300));
    var j = await r.json();
    renderPath(j);
  } catch(e) {
    document.getElementById('pathOut').innerHTML = '<div class="err">Could not generate: ' + escapeHtml(e.message) + '</div>';
  } finally {
    btn.disabled = false;
    document.getElementById('pathStatus').textContent = '';
  }
};
function renderPath(j) {
  var weeks = j.weeks || (j.plan && j.plan.weeks) || [];
  var out = document.getElementById('pathOut');
  if (!weeks.length) {
    out.innerHTML = '<div class="ok"><strong>Plan returned</strong></div>' +
      '<div class="result"><pre style="white-space:pre-wrap;font-size:12px">' +
      escapeHtml(JSON.stringify(j, null, 2)) + '</pre></div>';
    return;
  }
  out.innerHTML = '<div class="ok" style="margin-top:14px"><strong>' + weeks.length + '-week plan ready</strong>' +
    (j.cached ? '  <span class="chip">cached</span>' : '  <span class="chip ok">fresh</span>') + '</div>' +
    '<div style="margin-top:14px">' + weeks.map(function(w, i) {
      return '<div class="result"><strong>Week ' + (w.week_number || (i+1)) + ': ' + escapeHtml(w.theme || w.title || '') + '</strong>' +
        (w.summary || w.description ? '<div class="sub" style="margin-top:6px">' + escapeHtml(w.summary || w.description) + '</div>' : '') +
        (w.daily_tasks || w.daily ? '<ul style="margin:8px 0 0 0">' + (w.daily_tasks || w.daily).map(function(d){
          return '<li>' + escapeHtml(typeof d === 'string' ? d : (d.task || JSON.stringify(d))) + '</li>';
        }).join('') + '</ul>' : '') + '</div>';
    }).join('') + '</div>';
}
"""

_PATH_HTML = _page("Learning paths", _PATH_BODY, _PATH_SCRIPT)


# ---------- 12. Upload library ----------

_LIBRARY_BODY = """
<section class="section">
  <div class="card">
    <h2>Upload library</h2>
    <p class="sub">Every textbook scan and lesson you have created, in one place.</p>
    <div style="margin-bottom:14px"><a class="btn" href="/lessons/new">+ New upload</a></div>
    <div id="libOut"><div class="empty"><span class="spinner"></span> Loading…</div></div>
  </div>
</section>
"""

_LIBRARY_SCRIPT = """
async function loadLibrary() {
  if (!TOK) return;
  var out = document.getElementById('libOut');
  try {
    var r = await fetch('/jobs?limit=50', { headers: authH() });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    var d = await r.json();
    var jobs = d.jobs || [];
    if (!jobs.length) {
      out.innerHTML = '<div class="empty">No uploads yet. Click <strong>+ New upload</strong> to send a textbook page.</div>';
      return;
    }
    out.innerHTML = '<table style="width:100%;border-collapse:collapse">' +
      '<thead><tr><th style="text-align:left;padding:8px;border-bottom:1px solid #334155">Topic</th>' +
      '<th style="text-align:left;padding:8px;border-bottom:1px solid #334155">Status</th>' +
      '<th style="text-align:left;padding:8px;border-bottom:1px solid #334155">Language</th>' +
      '<th style="text-align:right;padding:8px;border-bottom:1px solid #334155">Actions</th></tr></thead><tbody>' +
      jobs.map(function(j) {
        var cls = j.status === 'succeeded' ? 'chip ok' : j.status === 'failed' ? 'chip red' : 'chip amber';
        return '<tr><td style="padding:10px;border-bottom:1px solid #334155">' +
            escapeHtml(j.topic || '—') + '</td>' +
          '<td style="padding:10px;border-bottom:1px solid #334155"><span class="' + cls + '">' + escapeHtml(j.status) + '</span></td>' +
          '<td style="padding:10px;border-bottom:1px solid #334155">' + escapeHtml(j.language_code || 'en') + '</td>' +
          '<td style="padding:10px;border-bottom:1px solid #334155;text-align:right">' +
            (j.status === 'succeeded' && j.id ? '<a class="btn ghost" href="/lessons/' + escapeHtml(j.id) + '">▶ Watch</a>' : '') +
          '</td></tr>';
      }).join('') + '</tbody></table>';
  } catch(e) {
    out.innerHTML = '<div class="err">Could not load: ' + escapeHtml(e.message) + '</div>';
  }
}
if (TOK) loadLibrary();
"""

_LIBRARY_HTML = _page("Upload library", _LIBRARY_BODY, _LIBRARY_SCRIPT)


# ---------- 13. School admin ----------

# prod-154 — single-design /school page. The V1 ("/ui-legacy#school")
# reference is dropped — every action the user can take from here is
# available via V2 endpoints we already ship (classes via
# /api/orgs/{id}/classes, attendance via /api/orgs/{id}/classes/{cid}/
# attendance, fees via /api/orgs/{id}/fees, timetable via
# /api/orgs/{id}/timetable, exams via /api/orgs/{id}/exams). Each org
# row now exposes a Members/Classes/Attendance/Fees/Timetable action
# grid that hits those V2 endpoints in modal dialogs — no redirects.
_SCHOOL_BODY = """
<section class="section">
  <div class="card">
    <h2>School & orgs</h2>
    <p class="sub">Schools and coaching centres you're a member of. Tap a tile to manage classes, attendance, fees and timetables.</p>
    <div id="schoolOut"><div class="empty"><span class="spinner"></span> Loading…</div></div>
  </div>

  <!-- prod-154 — inline modal that shows whatever the user clicked
       (classes, members, attendance, etc.) without leaving the page -->
  <div id="orgModal" style="display:none;position:fixed;inset:0;
       background:rgba(0,0,0,0.55);z-index:1000;align-items:center;
       justify-content:center;padding:20px">
    <div style="background:#1f2937;color:#e5e7eb;border-radius:10px;
         max-width:760px;width:100%;max-height:85vh;overflow:auto;
         padding:18px 20px;border:1px solid #374151">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:10px">
        <h3 id="orgModalTitle" style="margin:0;font-size:18px">Loading…</h3>
        <button id="orgModalClose" class="btn ghost" style="padding:4px 10px">✕</button>
      </div>
      <div id="orgModalBody"><div class="empty"><span class="spinner"></span> Loading…</div></div>
    </div>
  </div>
</section>
"""

_SCHOOL_SCRIPT = r"""
function openOrgModal(title) {
  document.getElementById('orgModalTitle').textContent = title;
  document.getElementById('orgModalBody').innerHTML =
    '<div class="empty"><span class="spinner"></span> Loading…</div>';
  document.getElementById('orgModal').style.display = 'flex';
}
function closeOrgModal() {
  document.getElementById('orgModal').style.display = 'none';
}
document.addEventListener('DOMContentLoaded', function() {
  var c = document.getElementById('orgModalClose');
  if (c) c.addEventListener('click', closeOrgModal);
  var m = document.getElementById('orgModal');
  if (m) m.addEventListener('click', function(e) {
    if (e.target === m) closeOrgModal();
  });
});

// Render an array of flat objects as a compact table into `body`.
function renderRowsTable(body, rows, kind) {
  if (!Array.isArray(rows)) { rows = (rows == null ? [] : [rows]); }
  if (!rows.length) {
    body.innerHTML = '<div class="empty">No ' + kind + ' yet.</div>';
    return;
  }
  var keys = Object.keys(rows[0]).filter(function(k){
    return ['id','created_at','updated_at','org_id'].indexOf(k) < 0;
  }).slice(0, 7);
  var html = '<table style="width:100%;border-collapse:collapse;font-size:13px">';
  html += '<thead><tr>' + keys.map(function(k){
    return '<th style="text-align:left;padding:6px 8px;background:#374151;'+
           'border-bottom:1px solid #4b5563">' + escapeHtml(k) + '</th>';
  }).join('') + '</tr></thead><tbody>';
  rows.forEach(function(row){
    html += '<tr>' + keys.map(function(k){
      var v = row[k];
      if (v === null || v === undefined) v = '';
      if (typeof v === 'object') { try { v = JSON.stringify(v); } catch(_) { v = String(v); } }
      return '<td style="padding:6px 8px;border-bottom:1px solid #374151;vertical-align:top">'+
             escapeHtml(String(v).slice(0,200)) + '</td>';
    }).join('') + '</tr>';
  });
  html += '</tbody></table>';
  body.innerHTML = html;
}

async function showSection(orgId, kind, title) {
  openOrgModal(title);
  var body = document.getElementById('orgModalBody');
  try {
    // Timetable is stored PER CLASS (/classes/{cid}/timetable) — there is no
    // org-level timetable endpoint. Load the org's classes, then each class's
    // slots, and show them together with a Class column. (Fixes the 404 the
    // old org-level '/timetable' URL produced.)
    if (kind === 'timetable') {
      var cr = await fetch('/api/orgs/' + encodeURIComponent(orgId) + '/classes', { headers: authH() });
      if (!cr.ok) throw new Error('HTTP ' + cr.status);
      var cd = await cr.json();
      var classes = cd.classes || cd.rows || [];
      if (!classes.length) {
        body.innerHTML = '<div class="empty">Timetables are set per class — add a class first (use the Classes tile).</div>';
        return;
      }
      var slots = [];
      for (var i = 0; i < classes.length; i++) {
        var cls = classes[i];
        try {
          var tr = await fetch('/api/orgs/' + encodeURIComponent(orgId) + '/classes/' +
                     encodeURIComponent(cls.id) + '/timetable', { headers: authH() });
          if (tr.ok) {
            var tdd = await tr.json();
            (tdd.slots || []).forEach(function(s){
              slots.push(Object.assign({ 'class': cls.name || cls.id }, s));
            });
          }
        } catch(_) {}
      }
      renderRowsTable(body, slots, 'timetable slots');
      return;
    }

    var endpoints = {
      members:    '/api/orgs/' + encodeURIComponent(orgId) + '/members',
      classes:    '/api/orgs/' + encodeURIComponent(orgId) + '/classes',
      assignments:'/api/orgs/' + encodeURIComponent(orgId) + '/assignments',
      fees:       '/api/orgs/' + encodeURIComponent(orgId) + '/fees/structures',
      exams:      '/api/orgs/' + encodeURIComponent(orgId) + '/exams'
    };
    var url = endpoints[kind];
    var r = await fetch(url, { headers: authH() });
    if (!r.ok) {
      var detail = '';
      try { var ej = await r.json(); detail = ej.detail || ej.error || ''; } catch(_) {}
      throw new Error('HTTP ' + r.status + (detail ? ' — ' + detail : ''));
    }
    var d = await r.json();
    var rows = d.rows || d.items || d.classes || d.members || d.structures ||
               d.invoices || d.exams || d.assignments || d.slots || d;
    renderRowsTable(body, rows, kind);
  } catch(e) {
    body.innerHTML = '<div class="err">Could not load ' + kind + ': ' +
      escapeHtml(e.message) + '</div>';
  }
}

// prod-228 — inline org creation. POST /api/orgs already supports self-serve
// creation (the caller becomes owner + first admin). The old invite-only
// alert() made /school a dead end for anyone not pre-invited; this opens a
// real create form so a fresh user can start a school/coaching centre.
function openCreateOrg() {
  openOrgModal('Create a school / coaching centre');
  document.getElementById('orgModalBody').innerHTML =
    '<label for="newOrgName">Name</label>' +
    '<input id="newOrgName" placeholder="e.g. Sunrise Coaching Centre" />' +
    '<label for="newOrgKind" style="margin-top:10px">Type</label>' +
    '<select id="newOrgKind">' +
      '<option value="coaching">Coaching centre</option>' +
      '<option value="school">School</option>' +
      '<option value="ngo">NGO</option>' +
      '<option value="gov">Government</option>' +
    '</select>' +
    '<label for="newOrgCity" style="margin-top:10px">City (optional)</label>' +
    '<input id="newOrgCity" placeholder="e.g. Pune" />' +
    '<div style="margin-top:14px">' +
      '<button class="btn" id="createOrgBtn">Create</button>' +
      '<span id="createOrgStatus" style="margin-left:10px;color:#94a3b8;font-size:13px"></span>' +
    '</div>';
  document.getElementById('createOrgBtn').addEventListener('click', submitCreateOrg);
  var nm = document.getElementById('newOrgName'); if (nm) nm.focus();
}

async function submitCreateOrg() {
  var name = (document.getElementById('newOrgName').value || '').trim();
  var status = document.getElementById('createOrgStatus');
  if (name.length < 2) { status.textContent = 'Enter a name (at least 2 characters).'; return; }
  var kind = document.getElementById('newOrgKind').value;
  var city = (document.getElementById('newOrgCity').value || '').trim();
  var btn = document.getElementById('createOrgBtn');
  btn.disabled = true;
  status.innerHTML = '<span class="spinner"></span> Creating…';
  try {
    var fd = new FormData();
    fd.append('name', name);
    fd.append('kind', kind);
    if (city) fd.append('city', city);
    var r = await fetch('/api/orgs', { method: 'POST', headers: authH(), body: fd });
    if (!r.ok) {
      var detail = '';
      try { var ej = await r.json(); detail = ej.detail || ej.error || ''; } catch(_) {}
      throw new Error('HTTP ' + r.status + (detail ? ' — ' + detail : ''));
    }
    closeOrgModal();
    loadSchool();
  } catch(e) {
    status.textContent = 'Could not create: ' + e.message;
    btn.disabled = false;
  }
}

async function loadSchool() {
  if (!TOK) return;
  var out = document.getElementById('schoolOut');
  try {
    var r = await fetch('/api/orgs/me', { headers: authH() });
    if (!r.ok) {
      var detail = '';
      try { var ej = await r.json(); detail = ej.detail || ej.error || ''; } catch(_) {}
      throw new Error('HTTP ' + r.status + (detail ? ' — ' + detail : ''));
    }
    var d = await r.json();
    var orgs = d.orgs || d.rows || (Array.isArray(d) ? d : []);
    if (!orgs || !orgs.length) {
      out.innerHTML =
        '<div class="empty">' +
        '<p>You are not a member of any school or coaching centre yet.</p>' +
        '<p style="margin-top:8px">Create one to manage classes, members, attendance, fees, timetables and exams — you\'ll be its admin.</p>' +
        '<div style="margin-top:12px"><button class="btn" onclick="openCreateOrg()">＋ Create a school / coaching centre</button></div>' +
        '</div>';
      return;
    }
    out.innerHTML = orgs.map(function(o) {
      var orgId = o.id || o.org_id || '';
      var role  = o.role || o.my_role || '';
      var isStaff = role === 'admin' || role === 'teacher';
      var okind = o.kind || o.org_type || '';
      var nm = escapeHtml(o.name || o.org_name || orgId);
      var actions = [
        { kind: 'members',     label: 'Members',     icon: '👥' },
        { kind: 'classes',     label: 'Classes',     icon: '🏫' },
        { kind: 'timetable',   label: 'Timetable',   icon: '🗓' },
        { kind: 'assignments', label: 'Assignments', icon: '📝' },
        { kind: 'fees',        label: 'Fees',        icon: '💳' },
        { kind: 'exams',       label: 'Exams',       icon: '📋' }
      ];
      var tiles = actions.map(function(a){
        return '<button class="btn ghost" style="padding:8px 12px;flex:1;min-width:110px;text-align:left" '+
          'onclick="showSection(\'' + orgId + '\',\'' + a.kind + '\',\'' + a.label + ' — ' + nm.replace(/'/g, "\\'") + '\')">'+
          a.icon + ' ' + a.label + '</button>';
      }).join('');
      return '<div class="result"><strong>' + nm + '</strong>' +
        '<div class="sub" style="margin-top:6px">' +
        (okind ? '<span class="chip">' + escapeHtml(okind) + '</span>' : '') +
        (role ? ' <span class="chip">Role: ' + escapeHtml(role) + '</span>' : '') +
        (isStaff ? ' <span class="chip" style="background:#16855f;color:#fff">Staff access</span>' : '') +
        '</div>' +
        '<div style="margin-top:10px;display:flex;flex-wrap:wrap;gap:6px">' + tiles + '</div>' +
        '</div>';
    }).join('') +
      '<div style="margin-top:16px"><button class="btn ghost" onclick="openCreateOrg()">＋ New organisation</button></div>';
  } catch(e) {
    out.innerHTML = '<div class="err">Could not load your orgs: ' + escapeHtml(e.message) +
      '<div style="margin-top:8px"><button class="btn ghost" onclick="loadSchool()">Retry</button></div></div>';
  }
}
if (TOK) loadSchool();
"""

_SCHOOL_HTML = _page("School & orgs", _SCHOOL_BODY, _SCHOOL_SCRIPT)


# ---------- 14. In-app syllabus (no redirects to NCERT / CBSE / NTA) ----------

# Chapter-level outlines per board / class / subject. Curated from the
# official NCERT / CBSE / NTA / UPSC syllabi published for the
# 2025-26 session. We host the chapter LIST + topic outline (which are
# facts about the syllabus, not copyrighted PDFs). For full textbook
# content, the linked lessons / videos / flashcards on this app cover
# the same ground without sending the student off-platform.
_SYLLABUS_DATA = """const SYLLABUS = {
  pre_primary: {
    label: 'Pre-primary (LKG / UKG, age 3–5)',
    classes: {
      'LKG (Lower KG, age 3–4)': [
        'English alphabet — recognise A to Z, uppercase + lowercase',
        'Phonics introduction — sound of each letter',
        'Numbers 1–20 — counting, recognition, writing',
        'Colours — primary (red, blue, yellow) + secondary (green, orange, purple)',
        'Shapes — circle, square, triangle, rectangle, oval',
        'Body parts — head, eyes, nose, ears, hands, legs',
        'Family — parents, siblings, grandparents, family vocabulary',
        'My environment — home, school, my classroom',
        'Animals — pet, wild, farm, water animals (3–4 each)',
        'Fruits and vegetables — common names, colour, taste',
        'Rhymes and songs — Twinkle Twinkle, Itsy Bitsy Spider, Old MacDonald',
        'Pattern making, colouring within outlines, hold-pencil practice',
      ],
      'UKG (Upper KG, age 4–5)': [
        'Phonics — beginning sounds, consonant + short vowel blends',
        'Sight words — first 25 (the, and, is, to, in, of, …)',
        'Numbers 1–100 — count, before / after, missing numbers',
        'Number names 1–20 in words',
        'Simple addition and subtraction (within 10) — using objects',
        'Shapes 2D + introduction to 3D (cube, sphere, cylinder)',
        'My country India — flag, national anthem, capital',
        'Festivals of India — Diwali, Eid, Christmas, Holi, Pongal',
        'Good habits — brushing, hand-washing, healthy food',
        'Helpers in the community — doctor, teacher, postman, farmer, police',
        'Seasons — summer, monsoon, winter, spring',
        'Time concepts — day / night, morning / afternoon / evening',
        'Read 3-letter CVC words — cat, bat, pen, sun, dog',
        'Stories and conversation — picture talk, story telling',
      ],
    },
  },
  primary: {
    label: 'Primary (Class 1–5, age 6–10)',
    classes: {
      'Class 1 (age 6)': [
        'English — phonics, blending sounds, simple sentences, naming words (nouns)',
        'Mathematics — numbers up to 100, addition / subtraction (1-digit), shapes, measurement intro',
        'EVS — me + my family, parts of body, plants + animals around us, food and water',
        'Hindi / regional — varnamala (alphabet), matra signs, 3–4 letter words',
        'GK — birds, flowers, fruits, days of week, months',
        'Drawing + craft — basic shapes, free-hand colouring',
      ],
      'Class 2 (age 7)': [
        'English — action words (verbs), adjectives, this/that/these/those, simple paragraphs',
        'Mathematics — numbers up to 1000, place value, 2-digit addition / subtraction, multiplication tables 2–5',
        'EVS — types of houses, transport, clothes, the world of plants + animals',
        'Hindi / regional — adi-akshar shabd (initial-letter words), short sentences, simple grammar',
        'GK — community helpers, festivals, national symbols, science around us',
      ],
      'Class 3 (age 8)': [
        'English — past / present / future tense, conjunctions, comprehension passages',
        'Mathematics — numbers up to 10,000, multiplication tables 2–10, division intro, fractions intro, money, time',
        'Science (instead of EVS in some boards) — living vs non-living, plants and parts, food + nutrition',
        'Social studies — solar system intro, India map (states + capitals at high level), community',
        'Hindi / regional — vakya rachna (sentence forming), small paragraphs',
        'GK — current affairs basics, sports, inventions',
      ],
      'Class 4 (age 9)': [
        'English — clauses, direct / indirect speech, letter writing (formal + informal)',
        'Mathematics — multi-digit operations, factors / multiples, decimals intro, perimeter + area of squares + rectangles, time + money',
        'Science — animal life cycles, human body systems intro, materials + their properties, force + work',
        'Social studies — climate of India, soils + crops, transport + communication, Indian government basics',
        'Computer science intro — keyboard, mouse, basic word processing',
      ],
      'Class 5 (age 10)': [
        'English — paragraph writing, story writing, comprehension, basic grammar review',
        'Mathematics — fractions + decimals (operations), percentages intro, simple algebra (variables), geometry (angles, polygons), data handling (bar graphs)',
        'Science — solar system + planets, animal kingdom, human body (skeletal, circulatory, digestive intro), states of matter, light + shadows',
        'Social studies — physical features of India + world, ancient civilisations, freedom struggle intro, citizenship + government',
        'Computer science — basic file management, intro to coding (block / Scratch)',
      ],
    },
  },
  cbse_6_8: {
    label: 'CBSE Class 6 to 8',
    classes: {
      'Class 6 — Mathematics': [
        'Knowing our numbers — place value, comparison, estimation',
        'Whole numbers — properties, number line, predecessor / successor',
        'Playing with numbers — factors, multiples, HCF, LCM',
        'Basic geometrical ideas — point, line, ray, segment',
        'Understanding elementary shapes — angles, polygons, 3D shapes',
        'Integers — number line, addition / subtraction',
        'Fractions — equivalent, like / unlike, operations',
        'Decimals — place value, addition / subtraction, multiplication',
        'Data handling — pictograph, bar graph',
        'Mensuration — perimeter, area of squares / rectangles',
        'Algebra — variables, expressions, simple equations',
        'Ratio and proportion — basic ratio, unitary method',
      ],
      'Class 6 — Science': [
        'Food: where does it come from? — sources, plant + animal foods',
        'Components of food — carbohydrates, fats, proteins, vitamins, minerals',
        'Fibre to fabric — cotton, jute, weaving / knitting',
        'Sorting materials into groups — solubility, transparency, magnetism',
        'Separation of substances — handpicking, threshing, sieving, filtration',
        'Changes around us — reversible vs irreversible',
        'Getting to know plants — parts of plant, root types, leaf venation',
        'Body movements — bones, joints, locomotion in animals',
        'The living organisms and their surroundings — habitats, adaptation',
        'Motion and measurement of distances — units, types of motion',
        'Light, shadows, reflection — opaque / transparent, mirrors',
        'Electricity and circuits — simple circuit, conductor / insulator',
        'Fun with magnets — magnetic / non-magnetic, poles, compass',
        'Water — water cycle, conservation',
        'Air around us — composition, oxygen for respiration',
        'Garbage in, garbage out — waste management, compost',
      ],
      'Class 6 — Social Science': [
        'History: What, where, how and when? — sources, BCE / CE',
        'On the trail of the earliest people — hunter-gatherers',
        'From gathering to growing food — agriculture, animal rearing',
        'In the earliest cities — Indus Valley Civilization',
        'What books and burials tell us — Vedic age, Rigveda',
        'Kingdoms, kings and an early republic — Mahajanapadas',
        'New questions and ideas — Buddhism, Jainism, Ashoka',
        'Geography: The earth in the solar system, globe, motions, maps',
        'Civics: Understanding diversity, government, local self-government',
      ],
      'Class 7 — Mathematics': [
        'Integers — properties of addition / multiplication',
        'Fractions and decimals — operations on both',
        'Data handling — mean, median, mode, probability basics',
        'Simple equations — linear in one variable',
        'Lines and angles — pairs of angles, transversals',
        'Triangle and its properties — angle-sum, exterior angle',
        'Congruence of triangles — SSS, SAS, ASA, RHS',
        'Comparing quantities — percentage, profit / loss, simple interest',
        'Rational numbers — definition, operations',
        'Practical geometry — constructions',
        'Perimeter and area — parallelograms, triangles, circles',
        'Algebraic expressions — like terms, monomial / polynomial',
        'Exponents and powers — laws of exponents',
        'Symmetry — line + rotational',
        'Visualising solid shapes — 3D figures',
      ],
      'Class 7 — Science': [
        'Nutrition in plants — photosynthesis, autotrophic / heterotrophic',
        'Nutrition in animals — digestion in humans, ruminants',
        'Fibre to fabric — silk, wool',
        'Heat — temperature, thermometers, conduction / convection / radiation',
        'Acids, bases and salts — indicators, neutralisation',
        'Physical and chemical changes — examples, rusting',
        'Weather, climate, and adaptations — desert, polar adaptations',
        'Winds, storms and cyclones — formation, safety',
        'Soil — types, profile, water absorption',
        'Respiration in organisms — breathing, anaerobic / aerobic',
        'Transportation in animals and plants — circulatory, transpiration',
        'Reproduction in plants — sexual / asexual, pollination',
        'Motion and time — uniform / non-uniform, distance-time graph',
        'Electric current and its effects — heating, magnetic effect, fuses',
        'Light — reflection, lenses, real / virtual images',
        'Water: a precious resource — conservation, rainwater harvesting',
        'Forests: our lifeline — biodiversity, ecosystem',
        'Wastewater story — sewage treatment, sanitation',
      ],
      'Class 8 — Mathematics': [
        'Rational numbers — properties, representation on number line',
        'Linear equations in one variable',
        'Understanding quadrilaterals — angle sums, parallelograms',
        'Practical geometry — constructions of quadrilaterals',
        'Data handling — bar graphs, pie charts, probability',
        'Squares and square roots — properties, finding square roots',
        'Cubes and cube roots — perfect cubes, prime factorisation method',
        'Comparing quantities — percentage, CI, discount',
        'Algebraic expressions and identities — (a+b)², (a-b)², a²-b²',
        'Visualising solid shapes — nets, Euler\\'s formula',
        'Mensuration — area of trapezium, surface area of cubes / cuboids',
        'Exponents and powers — negative exponents, scientific notation',
        'Direct and inverse proportions — practical applications',
        'Factorisation — common factors, regrouping, identities',
        'Introduction to graphs — line graphs, plotting points',
        'Playing with numbers — divisibility tests, generalisation',
      ],
      'Class 8 — Science': [
        'Crop production and management — agricultural practices',
        'Microorganisms: friend and foe — bacteria, viruses, food preservation',
        'Synthetic fibres and plastics — types, properties, environmental issues',
        'Materials: metals and non-metals — properties, displacement reactions',
        'Coal and petroleum — fossil fuels, refining',
        'Combustion and flame — types, zones of a candle flame',
        'Conservation of plants and animals — endangered species, biosphere reserves',
        'Cell — structure and function, plant vs animal cells',
        'Reproduction in animals — sexual / asexual, IVF, metamorphosis',
        'Reaching the age of adolescence — puberty, endocrine glands',
        'Force and pressure — types of forces, atmospheric pressure',
        'Friction — types, advantages / disadvantages, lubricants',
        'Sound — production, audibility, noise pollution',
        'Chemical effects of electric current — electrolysis, electroplating',
        'Some natural phenomena — lightning, earthquakes, charging',
        'Light — reflection, dispersion, human eye',
        'Stars and the solar system — planets, satellites, constellations',
        'Pollution of air and water — causes, prevention, greenhouse effect',
      ],
    },
  },
  cbse_9_10: {
    label: 'CBSE Class 9 & 10 (Board)',
    classes: {
      'Class 9 — Mathematics': [
        'Number Systems — irrational numbers, decimal expansion, laws of exponents',
        'Polynomials — degree, factor theorem, algebraic identities',
        'Coordinate Geometry — Cartesian plane, plotting points',
        'Linear Equations in Two Variables — graphical solution',
        'Introduction to Euclid\\'s Geometry — axioms, postulates',
        'Lines and Angles — pairs of angles, parallel lines and transversal',
        'Triangles — congruence criteria, properties',
        'Quadrilaterals — properties, mid-point theorem',
        'Areas of Parallelograms and Triangles — between same parallels',
        'Circles — chord, arc, cyclic quadrilateral',
        'Constructions — bisectors, angle constructions, triangle constructions',
        'Heron\\'s Formula — area of triangle given 3 sides',
        'Surface Areas and Volumes — sphere, cone, cylinder, hemisphere',
        'Statistics — mean, median, mode of grouped / ungrouped data',
        'Probability — empirical probability',
      ],
      'Class 9 — Science': [
        'Matter in our surroundings — states, change of state, evaporation',
        'Is matter around us pure? — mixtures, separation, compounds',
        'Atoms and molecules — Dalton\\'s theory, atomic mass, mole concept',
        'Structure of the atom — Thomson, Rutherford, Bohr models',
        'The fundamental unit of life — cell structure, organelles',
        'Tissues — plant / animal tissues, types',
        'Diversity in living organisms — classification, taxonomy',
        'Motion — distance / displacement, velocity, equations of motion',
        'Force and laws of motion — Newton\\'s 3 laws, inertia, momentum',
        'Gravitation — universal law, free fall, weight, mass, thrust, buoyancy',
        'Work and energy — work, kinetic / potential energy, conservation',
        'Sound — production, propagation, reflection, echo, SONAR',
        'Why do we fall ill? — health, disease, vaccination',
        'Natural resources — air, water, biogeochemical cycles',
        'Improvement in food resources — crop variety, livestock',
      ],
      'Class 10 — Mathematics': [
        'Real Numbers — Euclid\\'s division lemma, fundamental theorem of arithmetic',
        'Polynomials — geometrical meaning of zeros, division algorithm',
        'Pair of Linear Equations in Two Variables — substitution, elimination, cross-multiplication',
        'Quadratic Equations — factorisation, quadratic formula, nature of roots',
        'Arithmetic Progressions — nth term, sum of n terms',
        'Triangles — similarity criteria, Basic Proportionality Theorem, Pythagoras',
        'Coordinate Geometry — distance formula, section formula, area of triangle',
        'Introduction to Trigonometry — trig ratios, identities, complementary angles',
        'Applications of Trigonometry — heights and distances',
        'Circles — tangent properties, length of tangent',
        'Constructions — division of line, tangent from external point',
        'Areas Related to Circles — sector area, segment area',
        'Surface Areas and Volumes — combinations of solids',
        'Statistics — mean / median / mode of grouped data, cumulative frequency',
        'Probability — classical definition, sample space',
      ],
      'Class 10 — Science': [
        'Chemical reactions and equations — balancing, types, redox',
        'Acids, bases, and salts — pH, indicators, common salts',
        'Metals and non-metals — reactivity series, extraction, corrosion',
        'Carbon and its compounds — bonding, homologous series, soaps',
        'Periodic classification of elements — Mendeleev, modern periodic table',
        'Life processes — nutrition, respiration, transportation, excretion',
        'Control and coordination — nervous system, hormones, tropisms',
        'How do organisms reproduce? — asexual, sexual, human reproduction',
        'Heredity — Mendel\\'s laws, evolution, sex determination',
        'Light: reflection and refraction — mirrors, lenses, ray diagrams, lens formula',
        'Human eye and the colourful world — defects, dispersion, scattering',
        'Electricity — current, V=IR, resistance, power, heating effect',
        'Magnetic effects of electric current — Right-hand rule, electromagnetic induction',
        'Our environment — ecosystems, food chains, ozone depletion',
        'Sustainable management of natural resources — conservation, 3Rs',
      ],
      'Class 10 — Social Science': [
        'History: Nationalism in Europe, Indian nationalism, making of global world',
        'History: Print culture, novels, age of industrialisation',
        'Geography: Resources, agriculture, water, mineral & energy, manufacturing, lifelines',
        'Political Science: Power sharing, federalism, democracy & diversity, gender / religion / caste, popular struggles',
        'Economics: Development, sectors of economy, money & credit, globalisation, consumer rights',
      ],
    },
  },
  cbse_11_12: {
    label: 'CBSE Class 11 & 12 (Board)',
    classes: {
      'Class 11 — Physics': [
        'Physical World, Units and Measurements, Motion in a Straight Line',
        'Motion in a Plane — vectors, projectile, circular motion',
        'Laws of Motion — Newton\\'s laws, friction, banking of roads',
        'Work, Energy, Power — work-energy theorem, conservation',
        'System of Particles and Rotational Motion — centre of mass, torque, moment of inertia',
        'Gravitation — universal law, orbital velocity, escape speed, Kepler\\'s laws',
        'Mechanical Properties of Solids — stress, strain, Young\\'s modulus',
        'Mechanical Properties of Fluids — Bernoulli, viscosity, surface tension',
        'Thermal Properties of Matter — thermal expansion, calorimetry, heat transfer',
        'Thermodynamics — first / second laws, Carnot engine',
        'Kinetic Theory — gas laws, RMS speed, mean free path',
        'Oscillations and Waves — SHM, wave equation, beats, Doppler effect',
      ],
      'Class 11 — Chemistry': [
        'Some Basic Concepts of Chemistry — mole, stoichiometry',
        'Structure of Atom — quantum numbers, electronic configuration',
        'Classification of Elements and Periodicity in Properties',
        'Chemical Bonding and Molecular Structure — VSEPR, hybridization, MO theory',
        'States of Matter — gas laws, intermolecular forces',
        'Thermodynamics — enthalpy, entropy, Gibbs free energy',
        'Equilibrium — chemical + ionic, Le Chatelier, pH, buffers',
        'Redox Reactions — oxidation number, balancing, electrochemical cells',
        'Hydrogen — preparation, properties, hard water',
        'The s-Block Elements — alkali / alkaline earth metals',
        'The p-Block Elements (Groups 13, 14) — boron, carbon families',
        'Organic Chemistry: Basic Principles — nomenclature, isomerism, reaction mechanisms',
        'Hydrocarbons — alkanes, alkenes, alkynes, aromatic',
      ],
      'Class 11 — Mathematics': [
        'Sets, Relations and Functions',
        'Trigonometric Functions — identities, equations, inverse trig (intro)',
        'Principle of Mathematical Induction',
        'Complex Numbers and Quadratic Equations',
        'Linear Inequalities — solving, graphing',
        'Permutations and Combinations',
        'Binomial Theorem — general term, middle term',
        'Sequences and Series — AP, GP, HP, summations',
        'Straight Lines — slope, forms of equation',
        'Conic Sections — circle, parabola, ellipse, hyperbola',
        'Introduction to Three-dimensional Geometry',
        'Limits and Derivatives — first principles, standard limits',
        'Statistics — measures of dispersion (variance, std deviation)',
        'Probability — axiomatic approach, conditional probability',
      ],
      'Class 11 — Biology': [
        'Diversity in the Living World — taxonomy, kingdoms, plant + animal kingdoms',
        'Structural Organisation in Animals and Plants — tissues, morphology, anatomy',
        'Cell Structure and Function — cell theory, biomolecules, cell cycle',
        'Plant Physiology — transport, mineral nutrition, photosynthesis, respiration, growth',
        'Human Physiology — digestion, breathing, body fluids, excretion, locomotion, neural, chemical coordination',
      ],
      'Class 12 — Physics': [
        'Electric Charges and Fields — Coulomb\\'s law, Gauss\\'s law',
        'Electrostatic Potential and Capacitance',
        'Current Electricity — Ohm\\'s law, Kirchhoff\\'s laws, cells',
        'Moving Charges and Magnetism — Biot-Savart, Ampere\\'s law',
        'Magnetism and Matter — magnetic properties, hysteresis',
        'Electromagnetic Induction — Faraday\\'s law, Lenz\\'s law',
        'Alternating Current — LCR circuits, transformers',
        'Electromagnetic Waves — spectrum, properties',
        'Ray Optics and Optical Instruments — mirrors, lenses, telescopes',
        'Wave Optics — interference, diffraction, polarization',
        'Dual Nature of Radiation and Matter — photoelectric, de Broglie',
        'Atoms and Nuclei — Rutherford, Bohr, radioactivity, fission / fusion',
        'Semiconductor Electronics — diodes, transistors, logic gates',
      ],
      'Class 12 — Chemistry': [
        'The Solid State — types of solids, unit cell, defects',
        'Solutions — concentration, Raoult\\'s law, colligative properties',
        'Electrochemistry — Nernst equation, conductance, batteries',
        'Chemical Kinetics — rate law, order, half-life',
        'Surface Chemistry — adsorption, colloids, catalysis',
        'General Principles and Processes of Isolation of Elements',
        'The p-Block Elements (Groups 15-18)',
        'The d- and f-Block Elements — transition metals, lanthanoids, actinoids',
        'Coordination Compounds — Werner\\'s theory, isomerism, bonding',
        'Haloalkanes and Haloarenes — SN1, SN2, elimination',
        'Alcohols, Phenols and Ethers',
        'Aldehydes, Ketones and Carboxylic Acids',
        'Amines — primary / secondary / tertiary, diazonium salts',
        'Biomolecules — carbs, proteins, nucleic acids, vitamins',
      ],
      'Class 12 — Mathematics': [
        'Relations and Functions, Inverse Trigonometric Functions',
        'Matrices and Determinants — operations, inverse, system of equations',
        'Continuity and Differentiability',
        'Applications of Derivatives — rate of change, maxima / minima, tangents',
        'Integrals — substitution, by parts, partial fractions, definite integrals',
        'Applications of Integrals — area under curves',
        'Differential Equations — order, degree, first order solutions',
        'Vector Algebra — dot / cross product, projections',
        'Three Dimensional Geometry — direction cosines, line, plane',
        'Linear Programming — graphical method, feasible region',
        'Probability — Bayes\\' theorem, random variables, binomial distribution',
      ],
      'Class 12 — Biology': [
        'Reproduction — sexual / asexual, human reproductive health',
        'Genetics and Evolution — Mendel, molecular basis of inheritance, evolution theories',
        'Biology in Human Welfare — health, disease, microbes in human welfare',
        'Biotechnology — principles + applications, recombinant DNA',
        'Ecology — organisms + environment, ecosystems, biodiversity, environmental issues',
      ],
    },
  },
  jee: {
    label: 'JEE Main & Advanced',
    classes: {
      'JEE Physics': [
        'Units, Dimensions, Measurement, Kinematics — 1D and 2D motion, projectile, relative motion',
        'Laws of Motion, Friction, Circular Motion, Work-Energy-Power',
        'Centre of Mass, Collisions, Rotational Motion, Rigid body dynamics',
        'Gravitation — universal law, satellites, escape velocity',
        'Elasticity, Fluid Mechanics, Surface Tension, Viscosity',
        'Heat, Thermodynamics, Kinetic Theory of Gases',
        'Simple Harmonic Motion, Waves, Sound, Doppler effect',
        'Electrostatics, Capacitors, Gauss\\'s law',
        'Current Electricity, Heating effect, Kirchhoff\\'s laws',
        'Magnetism, Biot-Savart, Ampere\\'s law, Electromagnetic Induction, AC circuits',
        'Ray Optics, Wave Optics (interference, diffraction)',
        'Modern Physics — photoelectric effect, Bohr model, X-rays, nuclear physics, semiconductor devices',
      ],
      'JEE Chemistry': [
        'Physical: Atomic Structure, Periodicity, Bonding, Thermodynamics, Equilibrium (ionic + chemical), Electrochemistry, Kinetics, Solid State, Solutions, Surface chemistry',
        'Inorganic: s-block, p-block (groups 13-18), d-block transition metals, f-block, Coordination compounds, Isolation of metals',
        'Organic: GOC (general organic chemistry — IUPAC, isomerism, reaction mechanisms), Hydrocarbons, Halides, Alcohols / Phenols / Ethers, Carbonyl, Carboxylic acids + derivatives, Amines, Biomolecules, Polymers, Chemistry in everyday life',
      ],
      'JEE Mathematics': [
        'Algebra — Sets, Relations, Functions, Complex Numbers, Quadratic, Sequences, Permutations & Combinations, Binomial, Matrices, Determinants, Mathematical Reasoning',
        'Trigonometry — Identities, Trigonometric equations, Inverse trig functions, Solution of triangles',
        'Calculus — Limits, Continuity, Differentiability, Applications of derivatives, Indefinite + Definite integrals, Application of integrals, Differential equations',
        'Coordinate Geometry — Straight line, Circles, Parabola, Ellipse, Hyperbola',
        '3D Geometry, Vector Algebra',
        'Statistics, Probability — Conditional, Bayes, distributions',
      ],
    },
  },
  neet: {
    label: 'NEET UG',
    classes: {
      'NEET Physics': [
        'Class 11 chapters: Kinematics, Laws of motion, Work-energy, Rotation, Gravitation, Properties of bulk matter, Thermodynamics, Oscillations & waves',
        'Class 12 chapters: Electrostatics, Current electricity, Magnetic effects, EM induction & AC, EM waves, Optics, Dual nature of matter, Atoms & nuclei, Electronic devices',
      ],
      'NEET Chemistry': [
        'Physical: Basic concepts, Atomic structure, States of matter, Thermodynamics, Equilibrium, Redox, Solutions, Electrochemistry, Kinetics, Surface chemistry',
        'Inorganic: Periodicity, Bonding, Hydrogen, s-block, p-block, d/f-block, Coordination compounds, Environmental chemistry',
        'Organic: GOC, Hydrocarbons, Haloalkanes, Alcohols/Phenols/Ethers, Aldehydes/Ketones/Carboxylic acids, Amines, Biomolecules, Polymers, Chemistry in everyday life',
      ],
      'NEET Biology — Botany': [
        'Diversity in the living world — taxonomy, plant kingdom classification',
        'Structural organisation in plants — morphology, anatomy',
        'Cell biology — cell structure, biomolecules, cell cycle',
        'Plant physiology — transport, mineral nutrition, photosynthesis, respiration, plant growth & development',
        'Genetics and evolution — heredity, molecular basis of inheritance, evolution',
        'Ecology — organisms + populations, ecosystems, biodiversity, environmental issues',
        'Biotechnology — principles, applications',
      ],
      'NEET Biology — Zoology': [
        'Animal kingdom — classification of phyla up to class level',
        'Structural organisation in animals — tissues, morphology of cockroach',
        'Human physiology — digestion, breathing, body fluids & circulation, excretion, locomotion, neural & chemical coordination, reproduction',
        'Reproduction in organisms, human reproduction, reproductive health',
        'Genetics — Mendel, sex determination, mutations, human genetic disorders',
        'Evolution — Darwin, theories of evolution, human evolution',
        'Biology in human welfare — health & disease, microbes in human welfare',
        'Biotechnology — applications in medicine, industry, agriculture',
      ],
    },
  },
  upsc: {
    label: 'UPSC Civil Services',
    classes: {
      'Prelims — General Studies Paper 1': [
        'History of India and Indian National Movement',
        'Indian and World Geography — physical, social, economic',
        'Indian Polity and Governance — Constitution, Political system, Panchayati Raj, Public Policy, Rights issues',
        'Economic and Social Development — Sustainable Development, Poverty, Inclusion, Demographics, Social Sector initiatives',
        'General Issues on Environmental Ecology, Bio-diversity and Climate Change',
        'General Science',
        'Current events of national and international importance',
      ],
      'Prelims — CSAT (Paper 2, Qualifying)': [
        'Comprehension',
        'Interpersonal skills including communication',
        'Logical reasoning and analytical ability',
        'Decision making and problem solving',
        'General mental ability',
        'Basic numeracy (Class 10 level) — numbers, ratios, time-distance, percentages',
        'Data interpretation (Class 10 level)',
      ],
      'Mains — GS Paper 1 (Indian Heritage, History, Geography, Society)': [
        'Indian culture — art forms, literature, architecture',
        'Modern Indian history — mid-18th century to present',
        'The Freedom Struggle — its various stages',
        'Post-independence consolidation and reorganisation',
        'History of the world — 18th century events (industrial revolution, world wars, redrawal of national boundaries)',
        'Salient features of Indian Society, Diversity',
        'Role of women, population, poverty, urbanisation, globalisation effects',
        'Salient features of world\\'s physical geography',
        'Distribution of key natural resources, factors for location of industries',
        'Geophysical phenomena — earthquakes, tsunamis, cyclones, geographical features',
      ],
      'Mains — GS Paper 2 (Governance, Constitution, Polity, International Relations)': [
        'Indian Constitution — historical underpinnings, evolution, features, amendments',
        'Functions and responsibilities of Union and States, federal structure',
        'Separation of powers, dispute redressal mechanisms',
        'Parliament and State legislatures — structure, functioning, powers, issues',
        'Executive, Judiciary — structure, organisation, functioning',
        'RPA — salient features',
        'Statutory, regulatory and quasi-judicial bodies (CAG, ECI, UPSC, Lokpal, NHRC, etc.)',
        'Government policies and interventions, development processes',
        'Welfare schemes for vulnerable sections',
        'Issues relating to health, education, human resources',
        'India and its neighborhood — relations',
        'Bilateral, regional and global groupings (G20, BRICS, SCO, etc.)',
        'Effect of policies and politics of developed and developing countries on India\\'s interests',
        'Indian diaspora, important international institutions (UN, WHO, IMF, etc.)',
      ],
      'Mains — GS Paper 3 (Economy, Environment, Security, Disaster Management)': [
        'Indian Economy — planning, mobilisation of resources, growth, development, employment',
        'Inclusive growth and issues arising from it',
        'Government Budgeting',
        'Major crops — cropping patterns, irrigation, marketing, e-technology',
        'Issues of buffer stocks, food security, food processing, land reforms',
        'Effects of liberalisation on the economy',
        'Infrastructure — energy, ports, roads, airports, railways',
        'Investment models',
        'Science and Technology — developments, applications, IT, biotech, nanotech',
        'Awareness in space, computers, robotics, nuclear, IPR',
        'Environment, conservation, environmental pollution and degradation, EIA',
        'Disaster and disaster management',
        'Linkages between development and extremism',
        'Internal security — challenges, agencies, role of media / social networking',
        'Security challenges in border areas, organised crime, terrorism',
      ],
      'Mains — GS Paper 4 (Ethics, Integrity, Aptitude)': [
        'Ethics and Human Interface — essence, determinants, consequences',
        'Attitude — content, structure, function, moral & political attitudes',
        'Aptitude and foundational values for Civil Service — integrity, impartiality, objectivity, dedication, empathy',
        'Emotional intelligence — concepts, utilities, applications',
        'Contributions of moral thinkers and philosophers from India and the world',
        'Public / civil service values — accountability, transparency, RTI',
        'Ethical issues in international relations and funding',
        'Corporate governance',
        'Probity in governance — Citizen Charters, Codes of Ethics, work culture',
        'Case studies on above issues',
      ],
    },
  },
  sat: {
    label: 'SAT — US College Admissions (Digital)',
    classes: {
      'SAT Math': [
        'Algebra — linear equations & inequalities, systems of linear equations, linear functions and word problems',
        'Advanced Math — quadratics, polynomials, exponents & radicals, rational and nonlinear equations, function notation',
        'Problem-Solving & Data Analysis — ratios, rates, proportions, percentages, units, mean/median/mode, scatterplots, probability',
        'Geometry & Trigonometry — lines & angles, triangles, circles, area & volume, right-triangle trigonometry, the Pythagorean theorem',
        'Calculator allowed across the whole Math section (built-in Desmos graphing calculator)',
      ],
      'SAT Reading & Writing': [
        'Craft & Structure — words in context (vocabulary), text structure and purpose, cross-text connections',
        'Information & Ideas — central ideas & details, command of evidence (textual and quantitative), inferences',
        'Standard English Conventions — sentence boundaries, subject-verb agreement, pronouns, punctuation, modifiers, verb tense, parallelism',
        'Expression of Ideas — rhetorical synthesis, transitions and logical flow, word choice and concision',
        'Format — short passages, one question each, two section-adaptive modules',
      ],
    },
  },
  icse: {
    label: 'ICSE / ISC (CISCE)',
    classes: {
      'ICSE Class 10 — Mathematics': [
        'Commercial mathematics — GST, Banking (recurring deposits), Shares and dividends',
        'Linear inequations in one variable',
        'Quadratic equations in one variable — solutions, problems',
        'Ratio and proportion, factorization',
        'Matrices — order, operations',
        'Arithmetic progression — nth term, sum',
        'Coordinate geometry — section formula, slope, equation of a line',
        'Reflection (in x-axis, y-axis, origin)',
        'Similarity — properties, applications',
        'Loci — locus of points, constructions',
        'Circles — chord properties, tangent properties, cyclic quadrilateral',
        'Constructions of circles, triangles',
        'Mensuration — cylinder, cone, sphere, hemisphere (surface area + volume)',
        'Trigonometry — identities, heights and distances, table values',
        'Statistics — graphical representation, mean / median / mode',
        'Probability — random experiments, classical definition',
      ],
      'ICSE Class 10 — Physics': [
        'Force, Work, Energy and Power — moment of force, equilibrium, work done by force, energy, principle of conservation of energy, power',
        'Machines — types of levers, principle of moments, mechanical advantage',
        'Refraction of light at plane surfaces — Snell\\'s law, total internal reflection',
        'Refraction through a prism, dispersion',
        'Spectrum, scattering of light',
        'Sound — propagation, reflection (echoes), natural / forced / resonant vibrations',
        'Current electricity — Ohm\\'s law, series / parallel, EMF, internal resistance, household electricity',
        'Electromagnetism — electromagnetic induction, electric power transmission, fuses, three-pin plug',
        'Heat — calorimetry, specific heat capacity, latent heat',
        'Modern physics — radioactivity, nuclear fission and fusion',
      ],
      'ICSE Class 10 — Chemistry': [
        'Periodic properties and variations of properties — physical and chemical',
        'Chemical bonding — ionic, covalent, coordinate; electrovalent compounds',
        'Study of acids, bases and salts — preparation, properties',
        'Analytical chemistry — uses of NH₄OH, NaOH; flame test',
        'Mole concept and stoichiometry — Avogadro\\'s law, gas equation',
        'Electrolysis — Faraday\\'s laws (not in detail), electroplating',
        'Metallurgy — extraction of aluminium and iron',
        'Study of compounds — hydrogen chloride, ammonia, nitric acid, sulphuric acid',
        'Organic chemistry — hydrocarbons (alkanes, alkenes, alkynes), alcohols, carboxylic acids',
      ],
      'ICSE Class 10 — Biology': [
        'Basic biology — cell cycle, cell division (mitosis), structure of chromosome',
        'Genetics — Mendel\\'s monohybrid + dihybrid cross, sex linkage',
        'Plant physiology — absorption by roots, transpiration, photosynthesis',
        'Human anatomy and physiology — circulatory system, excretory system, nervous system, sense organs, endocrine system, reproductive system',
        'Population — population explosion in India',
        'Human evolution — overview',
        'Pollution — types, sources, effects, control measures',
      ],
    },
  },
  // prod-150 — Per-state syllabus buckets (was a single squashed "state" bucket).
  // Each state board now gets its own dropdown entry, so the user can pick
  // their specific state board and see its NCERT-aligned chapter list. All
  // 13 major Indian state boards covered: Maharashtra, Tamil Nadu, Karnataka,
  // Andhra Pradesh, Telangana, Gujarat, Kerala, Punjab, West Bengal, Uttar
  // Pradesh, Haryana, Odisha, Assam, Bihar.
  state_mh: {
    label: 'Maharashtra Board (MSBSHSE)',
    classes: {
      'Class 10 (SSC)': [
        'Marathi — Padhya, Gadya, Vyakaran (grammar), letter writing',
        'English — Communicative, comprehension, writing skills',
        'Mathematics Part I — Linear equations, quadratic equations, AP, financial planning, probability',
        'Mathematics Part II — Similarity, Pythagoras theorem, circles, geometric constructions, coordinate geometry, trigonometry, mensuration',
        'Science Part I — Gravitation, periodic classification, chemical reactions, electric current, heat',
        'Science Part II — School of elements, life processes in plants, control & coordination, environmental management, disaster management',
        'Social Sciences — History (Indian National Movement), Political Science (Constitution + Government), Geography (Physical + Economic geography of India)',
      ],
      'Class 12 (HSC)': [
        'English — Yuvakbharati (prose, poetry, drama, writing skills)',
        'Mathematics & Statistics — Mathematical logic, matrices, trigonometric functions, vectors, 3D geometry, line and plane, continuity, differentiation, applications of derivatives, integration, applications, differential equations',
        'Physics — Rotational dynamics, mechanical properties of fluids, kinetic theory of gases and radiation, thermodynamics, oscillations, superposition of waves, wave optics, electrostatics, current electricity, magnetic fields, electromagnetic induction, AC circuits, dual nature of matter, structure of atom, semiconductors, communication systems',
        'Chemistry — Solid state, solutions, ionic equilibria, chemical thermodynamics, electrochemistry, chemical kinetics, elements of groups 16/17/18, transition and inner transition elements, coordination compounds, halogen derivatives, alcohols/phenols/ethers, aldehydes/ketones/carboxylic acids, amines, biomolecules, polymers, green chemistry',
        'Biology — Reproduction in lower and higher plants, human reproduction, principles of inheritance, evolution, human health and disease, microbes in human welfare, biotechnology, organisms and populations, ecosystems, biodiversity, environmental issues',
      ],
    },
  },
  state_tn: {
    label: 'Tamil Nadu Board (Samacheer Kalvi)',
    classes: {
      'Class 10 (SSLC)': [
        'Tamil and English — Prose, poetry, supplementary, grammar',
        'Mathematics — Relations and functions, numbers and sequences, algebra, geometry, coordinate geometry, trigonometry, mensuration, statistics & probability',
        'Science — Physics: Laws of motion, optics, thermal physics, electricity, atoms and molecules; Chemistry: periodic classification, types of chemical reactions, solutions, atoms and molecules, carbon and its compounds; Biology: human physiology, nervous system, plant physiology, reproduction, transportation in plants and circulation in animals, conservation of plants and animals',
        'Social Science — History (rise of nationalism, freedom struggle, Tamil Nadu in post-independence), Geography (India: agriculture, industries, population), Civics (democracy, Indian Constitution), Economics (national income, consumer rights)',
      ],
      'Class 12 (HSC)': [
        'Tamil & English — Literature, prose, grammar, composition',
        'Mathematics — Applications of matrices and determinants, complex numbers, theory of equations, inverse trigonometric functions, two-dimensional analytical geometry, applications of vector algebra, applications of differential calculus, differentials and partial derivatives, applications of integration, ordinary differential equations, probability distributions, discrete mathematics',
        'Physics — Electrostatics, current electricity, magnetism, electromagnetic induction, electromagnetic waves, ray optics, wave optics, dual nature of radiation, atomic and nuclear physics, semiconductor electronics, communication systems, recent developments',
        'Chemistry — Metallurgy, p-block elements (groups 13-18), transition and inner transition elements, coordination chemistry, solid state, solutions, electrochemistry, chemical kinetics, surface chemistry, hydroxy compounds, ethers, carbonyl compounds, carboxylic acids, organic nitrogen compounds, biomolecules, chemistry in everyday life',
        'Biology — Reproduction (plants and humans), genetics, molecular basis of inheritance, evolution, human health and disease, biotechnology, microbes in human welfare, organisms and populations, ecosystems, biodiversity, environmental issues',
      ],
    },
  },
  state_ka: {
    label: 'Karnataka State Board (KSEEB)',
    classes: {
      'Class 10 (SSLC)': [
        'Mathematics — Arithmetic progressions, triangles, pair of linear equations, circles, area related to circles, constructions, coordinate geometry, real numbers, polynomials, quadratic equations, introduction to trigonometry, applications of trigonometry, statistics, surface areas and volumes, probability',
        'Science — Light: reflection and refraction, human eye, electricity, magnetic effects of electric current, sources of energy, life processes, control & coordination, heredity & evolution, chemical reactions, acids/bases/salts, metals & non-metals, carbon and its compounds, periodic classification, our environment, sustainable management of natural resources',
        'Social Science — History (advent of Europeans, freedom struggle, post-independence India), Geography (Karnataka: physical, agricultural, industrial), Political Science (Indian Constitution), Economics (development, government and taxes), Business Studies, Sociology',
      ],
      'Class 12 (PUC)': [
        'Kannada / English — Prescribed textbooks',
        'Mathematics — Relations and functions, inverse trigonometric functions, matrices, determinants, continuity and differentiability, application of derivatives, integrals, application of integrals, differential equations, vector algebra, three-dimensional geometry, linear programming, probability',
        'Physics — Electric charges and fields, electrostatic potential, current electricity, moving charges, magnetism, electromagnetic induction, AC, EM waves, ray optics, wave optics, dual nature, atoms, nuclei, semiconductor electronics, communication systems',
        'Chemistry — Solid state, solutions, electrochemistry, chemical kinetics, surface chemistry, general metallurgy, p-block elements, d and f block, coordination compounds, haloalkanes and haloarenes, alcohols/phenols/ethers, aldehydes/ketones/carboxylic acids, amines, biomolecules, polymers, chemistry in everyday life',
        'Biology — Reproduction in flowering plants and humans, principles of inheritance, molecular basis of inheritance, evolution, human health, microbes in human welfare, biotechnology, organisms and populations, ecosystems, biodiversity, environmental issues',
      ],
    },
  },
  state_ap: {
    label: 'Andhra Pradesh Board (BIEAP / BSEAP)',
    classes: {
      'Class 10 (SSC)': [
        'Telugu — Padyalu, Gadyalu, vyakaranam, upavachakam',
        'Hindi — composition, grammar, prose and poetry',
        'English — passages, poetry, drama, writing tasks',
        'Mathematics — Real numbers, sets, polynomials, pair of linear equations, quadratic equations, progressions, coordinate geometry, similar triangles, tangents and secants, mensuration, trigonometry, applications of trigonometry, probability, statistics',
        'General Science (Physics) — Reflection of light at curved surfaces, refraction of light at plane and curved surfaces, human eye, electric current, electromagnetism, principles of metallurgy',
        'General Science (Biology) — Nutrition, respiration, transportation, excretion, coordination, reproduction, heredity, our environment, natural resources',
        'Social Studies — India: relief features, monsoon climate, production sectors, food security, sustainable development, biodiversity, migration, urbanisation; Modern World: industrial revolution, world between wars; National movement and post-independence India',
      ],
      'Class 12 (Intermediate)': [
        'English & Sanskrit/Telugu — prescribed textbooks',
        'Mathematics IIA & IIB — Complex numbers, de Moivre, quadratic expressions, theory of equations, permutations and combinations, binomial theorem, partial fractions, probability, random variables; circle, system of circles, parabola, ellipse, hyperbola, integration, definite integrals, differential equations',
        'Physics — Waves, ray optics, wave optics, electric charges, current electricity, magnetism, EM induction, AC, EM waves, dual nature, atoms, nuclei, semiconductor devices, communication systems',
        'Chemistry — Solid state, solutions, electrochemistry, chemical kinetics, surface chemistry, general metallurgy, p-block elements (13-18), d and f block, coordination, haloalkanes and haloarenes, alcohols, phenols, ethers, aldehydes and ketones, carboxylic acids, organic nitrogen compounds, biomolecules, chemistry in everyday life, polymers',
        'Botany & Zoology — Plant physiology, reproduction in plants, microbiology, genetics, ecology; human anatomy and physiology, reproduction, evolution, human health, animal husbandry, biotechnology, ecosystems',
      ],
    },
  },
  state_ts: {
    label: 'Telangana Board (TSBIE / SCERT-TS)',
    classes: {
      'Class 10 (SSC)': [
        'Telugu — Padyalu, Gadyalu, vyakaranam',
        'Hindi — Prose, poetry, grammar, composition',
        'English — Reading, writing, grammar, literature',
        'Mathematics — Real numbers, sets, polynomials, pair of linear equations, quadratic equations, progressions, coordinate geometry, similar triangles, tangents and secants to a circle, mensuration, trigonometry, applications of trigonometry, probability, statistics',
        'Physical Science — Reflection of light at curved surfaces, refraction of light at plane and curved surfaces, human eye, electric current, electromagnetism, principles of metallurgy',
        'Biological Science — Nutrition, respiration, transportation, excretion, coordination, reproduction, heredity, our environment, natural resources',
        'Social Studies — Telangana state, India, world geography & history, political science, economics, disaster management, contemporary world',
      ],
      'Class 12 (Intermediate)': [
        'Mathematics, Physics, Chemistry, Biology (Botany + Zoology) — same NCERT-aligned syllabus as Andhra Pradesh BIEAP (shared textbook framework)',
      ],
    },
  },
  state_gj: {
    label: 'Gujarat Board (GSEB)',
    classes: {
      'Class 10 (SSC)': [
        'Gujarati / Hindi / English — Prose, poetry, grammar, composition',
        'Mathematics — Real numbers, polynomials, pair of linear equations, quadratic equations, AP, triangles, coordinate geometry, introduction to trigonometry, applications of trigonometry, circles, constructions, areas related to circles, surface areas and volumes, statistics, probability',
        'Science — Chemical reactions, acids/bases/salts, metals and non-metals, carbon and its compounds, periodic classification, life processes, control and coordination, reproduction, heredity and evolution, light: reflection and refraction, human eye, electricity, magnetic effects, sources of energy, our environment, management of natural resources',
        'Social Science — Indian Heritage, freedom struggle, post-independence India, Indian Constitution, democracy, economic development, agriculture and industry, transport and trade, India: physical, climate, natural vegetation, manufacturing, planning',
      ],
      'Class 12 (HSC)': [
        'Gujarati / English — Literature, composition',
        'Mathematics — Same as CBSE Class 12 NCERT syllabus (relations and functions, matrices, determinants, calculus, vectors, 3D geometry, linear programming, probability)',
        'Physics, Chemistry, Biology — Same as CBSE Class 12 NCERT (electric charges through communication systems; solid state through everyday-life chemistry; reproduction through environmental issues)',
      ],
    },
  },
  state_kl: {
    label: 'Kerala Board (SCERT-KL / DHSE)',
    classes: {
      'Class 10 (SSLC)': [
        'Malayalam — Padyalu, gadyalu, vyakaranam',
        'English — Reading, writing, grammar, literature',
        'Hindi — Prose, poetry, grammar',
        'Mathematics — Arithmetic sequences, circles, mathematics of chance, second-degree equations, trigonometry, coordinates, tangents, solids, statistics, polynomials',
        'Physics — Electromagnetic induction, electric current, refraction, wave motion, energy management',
        'Chemistry — Periodic table, mole concept, metallurgy, carbon and its compounds (organic chemistry)',
        'Biology — Plant biology, sensory inputs, nervous system, reproduction, heredity and evolution, life on earth',
        'Social Science — Geography (India: physical, climate, agriculture), History (India under Britishers, freedom struggle, modern India), Civics (Indian Constitution), Economics (development, public finance)',
      ],
      'Class 12 (Plus Two / DHSE)': [
        'Malayalam / English — prescribed textbooks',
        'Mathematics, Physics, Chemistry, Biology — Same NCERT-aligned syllabus as CBSE Class 12',
      ],
    },
  },
  state_pb: {
    label: 'Punjab Board (PSEB)',
    classes: {
      'Class 10': [
        'Punjabi — Prescribed textbook, grammar, composition',
        'English — Reading, writing, grammar, prose and poetry',
        'Hindi — Prose, poetry, grammar',
        'Mathematics — Real numbers, polynomials, pair of linear equations, quadratic equations, AP, triangles, coordinate geometry, trigonometry, circles, constructions, areas related to circles, surface areas and volumes, statistics, probability',
        'Science — Chemical reactions, acids/bases/salts, metals and non-metals, carbon and its compounds, periodic classification, life processes, control and coordination, reproduction, heredity and evolution, light, electricity, magnetic effects, sources of energy, environment, natural resources',
        'Social Science — History (modern India), Geography (India: physical, resources, agriculture), Political Science (Indian Constitution), Economics (development, sectors of economy)',
      ],
      'Class 12': [
        'Mathematics, Physics, Chemistry, Biology — Same NCERT-aligned syllabus as CBSE Class 12',
      ],
    },
  },
  state_wb: {
    label: 'West Bengal Board (WBBSE / WBCHSE)',
    classes: {
      'Class 10 (Madhyamik)': [
        'Bengali / English — Prose, poetry, grammar, composition',
        'Mathematics — Real numbers, polynomials, quadratic equations, ratio and proportion, AP, simple and compound interest, similarity, theorems on circles, coordinate geometry, trigonometry, mensuration, statistics and probability',
        'Physical Science — Concerns about environment, behaviour of gases, thermal phenomena, light, current electricity, electromagnetism, atomic nucleus, periodic table, chemical calculations, ionic and covalent bonding, metallurgy, atomic structure, inorganic chemistry, organic chemistry',
        'Life Science — Control and coordination in living organisms, continuity of life, heredity and evolution, environment and its resources',
        'History — Modern India: nationalism, freedom struggle, post-independence India',
        'Geography — Earth processes, atmospheric phenomena, India: physical setting, agriculture, industries, transport and communication',
      ],
      'Class 12 (HS)': [
        'Bengali / English — Literature, composition',
        'Mathematics, Physics, Chemistry, Biology — WBCHSE syllabus aligned with NCERT, with regional emphasis on industrial geography and Bengal-rooted case studies',
      ],
    },
  },
  state_up: {
    label: 'UP Board (UPMSP)',
    classes: {
      'Class 10 (High School)': [
        'Hindi — Prose, poetry, grammar',
        'English — Prose, poetry, supplementary, grammar',
        'Mathematics — Real numbers, polynomials, pair of linear equations, quadratic equations, AP, triangles, coordinate geometry, trigonometry, circles, areas related to circles, surface areas and volumes, statistics, probability',
        'Science — Chemical reactions, acids/bases/salts, metals and non-metals, carbon and its compounds, periodic classification, life processes, control and coordination, reproduction, heredity and evolution, light, electricity, magnetic effects, sources of energy, environment',
        'Social Science — History (modern India, freedom struggle), Geography (India: physical, resources, agriculture, industry), Civics (Indian Constitution), Economics (sectors of economy, money and credit)',
      ],
      'Class 12 (Intermediate)': [
        'Hindi / English — Literature, composition',
        'Mathematics, Physics, Chemistry, Biology — NCERT-aligned (same chapter list as CBSE Class 12)',
      ],
    },
  },
  state_hr: {
    label: 'Haryana Board (BSEH)',
    classes: {
      'Class 10': [
        'Hindi / English — Literature, grammar, composition',
        'Mathematics — Real numbers, polynomials, pair of linear equations, quadratic equations, AP, triangles, coordinate geometry, trigonometry, circles, areas related to circles, surface areas and volumes, statistics, probability',
        'Science — Same NCERT-aligned content as CBSE Class 10',
        'Social Science — History, Geography, Political Science, Economics — Haryana-specific case studies plus national NCERT chapters',
      ],
      'Class 12': [
        'Mathematics, Physics, Chemistry, Biology — NCERT-aligned (same chapter list as CBSE Class 12)',
      ],
    },
  },
  state_od: {
    label: 'Odisha Board (BSE Odisha / CHSE)',
    classes: {
      'Class 10 (HSC)': [
        'Odia — Prose, poetry, grammar',
        'English — Reading, writing, grammar, literature',
        'Mathematics — Real numbers, polynomials, pair of linear equations, quadratic equations, AP, triangles, coordinate geometry, trigonometry, circles, constructions, areas, surface areas and volumes, statistics, probability',
        'General Science — Chemical reactions, acids/bases/salts, metals and non-metals, periodic classification, life processes, control and coordination, reproduction, heredity and evolution, light, electricity, magnetic effects, sources of energy, environment',
        'Social Science — History (modern India), Geography (India: physical, resources, agriculture), Political Science (Indian Constitution), Economics (development, sectors)',
      ],
      'Class 12 (+2)': [
        'Mathematics, Physics, Chemistry, Biology — NCERT-aligned (same as CBSE Class 12)',
      ],
    },
  },
  state_as: {
    label: 'Assam Board (SEBA / AHSEC)',
    classes: {
      'Class 10 (HSLC)': [
        'Assamese / English — Prose, poetry, grammar, composition',
        'Mathematics — Real numbers, polynomials, pair of linear equations, quadratic equations, AP, triangles, coordinate geometry, trigonometry, circles, areas related to circles, surface areas and volumes, statistics, probability',
        'Science — Chemical reactions, acids/bases/salts, metals and non-metals, carbon and its compounds, periodic classification, life processes, control and coordination, reproduction, heredity and evolution, light, electricity, magnetic effects, sources of energy, environment',
        'Social Science — History (modern India), Geography (India: physical, resources, agriculture; Assam: regional geography), Political Science (Constitution), Economics (sectors, money and credit)',
      ],
      'Class 12 (HS / +2)': [
        'Mathematics, Physics, Chemistry, Biology — NCERT-aligned (same as CBSE Class 12)',
      ],
    },
  },
  state_br: {
    label: 'Bihar Board (BSEB)',
    classes: {
      'Class 10 (Matric)': [
        'Hindi / English / Sanskrit — Literature, grammar, composition',
        'Mathematics — Real numbers, polynomials, pair of linear equations, quadratic equations, AP, triangles, coordinate geometry, trigonometry, circles, areas related to circles, surface areas and volumes, statistics, probability',
        'Science — Same NCERT-aligned content as CBSE Class 10',
        'Social Science — History (modern India, freedom struggle), Geography (India: physical, resources, agriculture; Bihar: regional emphasis), Political Science (Constitution), Economics (sectors of economy, banking)',
      ],
      'Class 12 (Intermediate)': [
        'Mathematics, Physics, Chemistry, Biology — NCERT-aligned (same as CBSE Class 12)',
      ],
    },
  },
  bank_ssc: {
    label: 'Bank / SSC / Government exams',
    classes: {
      'SSC CGL — Tier 1': [
        'General Intelligence and Reasoning — Analogies, classification, series, coding-decoding, blood relations, direction sense, ranking, syllogisms, statement & conclusions, mirror images, paper folding',
        'General Awareness — Current affairs, history, geography, polity, economy, science, sports, awards, books, important days',
        'Quantitative Aptitude — Number system, simplification, percentages, ratio & proportion, averages, profit & loss, simple & compound interest, time & work, time-speed-distance, mensuration, geometry, trigonometry, data interpretation',
        'English Comprehension — Reading comprehension, sentence improvement, error spotting, vocabulary (synonyms, antonyms, idioms), fill in the blanks, cloze test, para-jumbles',
      ],
      'IBPS PO / Clerk — Prelims': [
        'English Language — Reading comprehension, cloze test, error spotting, sentence rearrangement, fill in the blanks, vocabulary',
        'Quantitative Aptitude — Simplification, number series, data interpretation, quadratic equations, profit and loss, simple/compound interest, time and work, speed-distance-time, mensuration, probability, mixture and allegations',
        'Reasoning Ability — Seating arrangement, puzzles, syllogisms, coding-decoding, inequalities, direction sense, blood relations, alphanumeric series, machine input-output, data sufficiency',
      ],
      'IBPS PO — Mains (additional)': [
        'General/Economy/Banking Awareness — Current affairs, banking history, financial awareness, monetary policy, RBI functions',
        'English Language — Essay, letter writing, precis',
        'Computer Aptitude — MS Office basics, internet, networking, hardware components, operating systems',
      ],
      'RBI Grade B — Phase 2': [
        'Economic and Social Issues — Growth and development, Indian economy, sustainable development, sectors of Indian economy, inflation, social structure, social issues',
        'Finance and Management — Financial system, financial markets, banking and non-banking financial institutions, risk management, derivatives, management theory, organisational behaviour',
        'English — Essay writing, precis, reading comprehension',
      ],
    },
  },
  undergraduate: {
    label: 'Undergraduate (BTech / BSc / BCom / BA / MBBS / LLB)',
    classes: {
      'BTech / BE — Engineering (4 years)': [
        'Year 1 (common) — Engineering Mathematics I + II (calculus, linear algebra, ODE), Physics, Chemistry, Programming in C / Python, Engineering Graphics / CAD, Workshop',
        'Year 2 (CSE) — Data Structures, Algorithms, Discrete Math, Digital Logic Design, Computer Organisation, Object-Oriented Programming, DBMS basics',
        'Year 2 (ECE) — Network Analysis, Electronic Devices, Signals & Systems, Digital Electronics, Electromagnetic Theory',
        'Year 2 (Mech) — Engineering Mechanics, Thermodynamics, Fluid Mechanics, Material Science, Manufacturing Processes',
        'Year 2 (Civil) — Surveying, Building Materials, Strength of Materials, Fluid Mechanics, Soil Mechanics intro',
        'Year 3 (CSE) — Operating Systems, Computer Networks, Compiler Design, DBMS advanced, Theory of Computation, Software Engineering, Machine Learning intro',
        'Year 3 (ECE) — Communication Systems, Control Systems, Microprocessors, VLSI Design, Antenna & Wave Propagation',
        'Year 4 — Electives (AI/ML, Cybersecurity, IoT, Cloud Computing), Major Project, Internship, Industry-oriented courses',
      ],
      'BSc (Honours) — Pure Sciences (3 years)': [
        'BSc Physics — Classical Mechanics, Thermodynamics & Statistical Mechanics, Electromagnetism, Quantum Mechanics, Atomic & Molecular Physics, Solid State Physics, Nuclear & Particle Physics, Mathematical Physics, Lab work',
        'BSc Chemistry — Inorganic (s/p/d/f-block + Coordination), Organic (mechanisms, named reactions, biomolecules), Physical (thermodynamics, kinetics, quantum, spectroscopy), Analytical, Lab work',
        'BSc Mathematics — Real Analysis, Abstract Algebra (groups, rings, fields), Linear Algebra, Differential Equations (ODE + PDE), Complex Analysis, Topology, Numerical Methods, Statistics',
        'BSc Biology / Botany / Zoology — Cell biology, Genetics, Molecular biology, Biochemistry, Ecology, Evolution, Plant / Animal physiology, Microbiology, Biotechnology, Bioinformatics',
        'BSc Computer Science — Programming, Data Structures + Algorithms, DBMS, OS, Networks, Discrete Math, Web Tech, AI/ML intro, Software Engineering',
      ],
      'BCom / BBA — Commerce + Management (3 years)': [
        'BCom Year 1 — Financial Accounting, Business Economics, Business Law, Business Communication, Mathematics for Commerce',
        'BCom Year 2 — Corporate Accounting, Cost Accounting, Macroeconomics, Income Tax Law, Business Statistics',
        'BCom Year 3 — Auditing, Financial Management, Banking & Insurance, GST + Indirect Tax, Marketing Management, Project',
        'BBA Year 1 — Principles of Management, Business Environment, Business Communication, Financial Accounting, Microeconomics, Computer Applications',
        'BBA Year 2 — Marketing Management, HR Management, Operations Management, Macroeconomics, Statistics, Business Ethics',
        'BBA Year 3 — Strategic Management, Entrepreneurship, International Business, Specialisation (Finance / Marketing / HR), Internship + Project',
      ],
      'BA (Hons) — Liberal Arts (3 years)': [
        'BA Economics — Micro + Macro economics, Mathematical methods, Statistics, Indian Economic Development, International Economics, Public Finance, Game Theory',
        'BA Political Science — Indian Government & Politics, Political Theory, Comparative Politics, International Relations, Public Administration',
        'BA History — Ancient + Medieval + Modern India, World History, Historiography, regional history, themed papers (women, environment, …)',
        'BA Sociology — Sociological Theory, Indian Society, Methodology, Family + Kinship, Social Change, Urban Sociology',
        'BA Psychology — Foundations, Cognitive psychology, Social psychology, Developmental psychology, Abnormal psychology, Research methods, Statistics',
        'BA English Literature — Survey of British / American / Indian literature, Literary Theory, Linguistics, Modern Drama, Postcolonial Literature',
      ],
      'MBBS — Medicine (5.5 years incl. internship)': [
        'Pre-clinical (Year 1) — Anatomy, Physiology, Biochemistry',
        'Para-clinical (Year 2) — Pathology, Pharmacology, Microbiology, Forensic Medicine',
        'Clinical (Years 3 + 4) — Medicine, Surgery, Obstetrics & Gynaecology, Paediatrics, Ophthalmology, ENT, Orthopaedics, Community Medicine, Psychiatry, Dermatology, Anaesthesia, Radiology',
        'Internship (1 year) — Rotation through all major departments',
      ],
      'LLB — Law (3 or 5 years integrated)': [
        'Foundational — Jurisprudence, Constitutional Law I + II, Indian Penal Code, Code of Criminal Procedure, Code of Civil Procedure, Contract Law I + II',
        'Core — Family Law, Property Law (Transfer of Property Act), Tort Law, Company Law, Labour Law, Environmental Law, Administrative Law',
        'Advanced — Law of Evidence, Public International Law, Intellectual Property Rights, Tax Law, Banking Law, ADR (mediation + arbitration), Cyber Law',
        'Procedural / clinic — Drafting, Pleading & Conveyancing, Moot Court, Internship, Professional Ethics',
      ],
      'BEd — Education (2 years)': [
        'Childhood + Development — Cognitive + social development, learning theories',
        'Knowledge + Curriculum — Curriculum design, language across subjects',
        'Pedagogy of school subjects — Maths pedagogy, Science pedagogy, Social Science pedagogy, Language pedagogy',
        'School internship + practice teaching — Lesson planning, classroom management, reflective practice',
        'Educational psychology, Inclusive education, Assessment + evaluation, Educational technology',
      ],
    },
  },
  postgraduate: {
    label: 'Postgraduate (MA / MSc / MCom / MTech / MBA / MD / LLM / MEd)',
    classes: {
      'MA / MSc / MCom — Academic PG (2 years)': [
        'MA Economics — Advanced Microeconomics, Advanced Macroeconomics, Econometrics, Mathematical Economics, Indian Economic Policy, Specialisation (International Economics / Development / Public Finance)',
        'MSc Physics — Mathematical Methods, Classical Mechanics, Quantum Mechanics I + II, Statistical Mechanics, Electromagnetic Theory, Condensed Matter / Nuclear Physics, Computational Physics, Dissertation',
        'MSc Chemistry — Inorganic / Organic / Physical advanced, Analytical & Spectroscopic Methods, Biochemistry, Polymer Chemistry, Drug Design, Computational Chemistry, Project',
        'MSc Mathematics — Real Analysis, Algebra, Topology, Functional Analysis, Numerical Analysis, Differential Geometry, PDE, Operations Research, Dissertation',
        'MSc Computer Science — Advanced Algorithms, Theory of Computation, Distributed Systems, Machine Learning, Compiler Design, Cryptography, NLP, Computer Vision, Project',
        'MCom — Advanced Accounting, Strategic Financial Management, Advanced Corporate Tax, International Business, Advanced Marketing, Quantitative Techniques, Research Methodology',
      ],
      'MBA / PGDM — Business (2 years)': [
        'Year 1 (core) — Managerial Economics, Financial Accounting, Marketing Management, Operations Management, HR Management, Business Statistics, Organisational Behaviour, Business Communication, Information Systems',
        'Year 1 (core continued) — Corporate Finance, Strategic Management, Business Law, Macroeconomics, Decision Science, Business Ethics',
        'Year 2 (specialisation) — Finance (Investment Analysis, Banking, Corporate Restructuring), Marketing (Digital, Consumer Behaviour, Branding), HR (Compensation, Talent Management, Industrial Relations), Operations (Supply Chain, Logistics, Quality), Analytics (Big Data, Predictive Modelling)',
        'Year 2 — Capstone Project / Dissertation, Summer Internship report, Industry-immersive electives',
      ],
      'MTech / ME — Engineering PG (2 years)': [
        'MTech CSE — Advanced Algorithms, Distributed Systems, Advanced Databases, Cloud Computing, Machine Learning, Computer Vision, NLP, Cybersecurity, Dissertation',
        'MTech ECE — Advanced VLSI, Wireless Communications, Digital Signal Processing, Embedded Systems, MIMO + 5G, Antenna Design, Project',
        'MTech Mech — Advanced Thermodynamics, Heat Transfer, Manufacturing Systems, Robotics, CFD (Computational Fluid Dynamics), CAD/CAM',
        'MTech Civil — Structural Dynamics, Soil Dynamics, Pavement Engineering, Transportation Planning, Environmental Engineering',
      ],
      'MD / MS — Medical specialisation (3 years)': [
        'MD General Medicine, MD Paediatrics, MD Anaesthesia, MD Pathology, MD Microbiology, MD Pharmacology, MD Radiodiagnosis, MD Psychiatry, MD Community Medicine, MD Dermatology',
        'MS General Surgery, MS Ophthalmology, MS ENT, MS Orthopaedics, MS Obstetrics & Gynaecology',
        'Common framework — Theory papers, clinical / practical posting, log book, thesis / dissertation, evaluation',
        'Super-speciality (DM / MCh, 3 more years) — Cardiology, Neurology, Nephrology, Gastroenterology, Cardiothoracic Surgery, Neurosurgery, Plastic Surgery, Urology',
      ],
      'LLM — Law (1 year)': [
        'Core — Comparative Constitutional Law, Law and Social Transformation, Judicial Process, Legal Research Methodology',
        'Specialisation — Constitutional Law / Criminal Law / Business Law / IPR / International Law / Human Rights / Tax Law',
        'Dissertation + viva',
      ],
      'MEd / MA Education (2 years)': [
        'Year 1 — Philosophy of Education, Psychology of Education, Sociology of Education, Educational Research Methods, Statistics in Education',
        'Year 2 — Curriculum Studies, Teacher Education, Educational Technology, Comparative Education, ICT in Education, Dissertation',
      ],
      'MCA / MSc Computer Science (2 years)': [
        'Year 1 — Discrete Mathematics, Advanced Data Structures + Algorithms, Object-Oriented Programming, DBMS, Operating Systems, Computer Networks, Software Engineering',
        'Year 2 — Web Technologies, AI + Machine Learning, Cloud Computing, Mobile Application Development, Big Data Analytics, Cybersecurity, Major Project',
      ],
    },
  },
  phd: {
    label: 'PhD / Doctoral (3–5+ years)',
    classes: {
      'Common framework (all PhDs)': [
        'Coursework (1st year) — Research Methodology (qualitative + quantitative + mixed methods), Research and Publication Ethics (RPE), Statistics for Research, Discipline-specific advanced coursework',
        'Comprehensive / qualifier exam — written + oral defence of preparation',
        'Research proposal — problem statement, literature review, methodology, expected contribution, timeline',
        'Pre-PhD viva / Departmental Research Committee (DRC) approval',
        'Independent research — experiments / fieldwork / theory development; supervisor + co-supervisor mentoring',
        'Publication requirement — typically 2 peer-reviewed journal papers + conference papers before submission',
        'Thesis writing — chapter structure: Introduction, Literature Review, Methodology, Results, Discussion, Conclusion, References, Appendices',
        'Plagiarism check (UGC requires <10%), open seminar, external + internal examiner review',
        'Public defence (viva voce) — open seminar + Q&A from examiners',
        'Submission of final thesis + award of degree',
      ],
      'PhD in Sciences (Physics / Chemistry / Biology / Math / CS)': [
        'Experimental / theoretical research — design experiments, develop models, run simulations',
        'Instrumentation training — spectroscopy, microscopy, sequencing, HPC clusters (as relevant)',
        'Specialised coursework — depends on lab focus (e.g. quantum computing, structural biology, machine learning theory, partial differential equations)',
        'Conferences — present at 2–4 international conferences during the program',
        'Industry / lab collaboration — possible secondment to industry research lab',
      ],
      'PhD in Engineering': [
        'Application-driven research — prototype development, patent filing, technology transfer',
        'Specialised coursework — advanced topics in the candidate area (e.g. 5G + 6G, advanced robotics, computational mechanics)',
        'Industry collaborations — sponsored research, consultancy',
        'Patent + journal publication target',
      ],
      'PhD in Medicine / Health Sciences': [
        'Clinical research — RCTs (randomised controlled trials), observational studies, cohort design',
        'Biostatistics + epidemiology coursework',
        'Ethics — Institutional Ethics Committee (IEC) approval, informed consent, GCP (Good Clinical Practice)',
        'Translational research — bench to bedside',
      ],
      'PhD in Humanities / Social Sciences': [
        'Qualitative methods — ethnography, interviews, archival research, discourse analysis',
        'Quantitative methods — survey design, regression, structural equation modelling',
        'Literature engagement — critical theory, historiography (history), policy analysis (political science / public policy)',
        'Long-form thesis (~80,000–100,000 words typical)',
      ],
      'PhD in Management (DBA / FPM)': [
        'Specialisation — Finance, Marketing, OB / HR, Operations, Strategy, Information Systems, Public Policy',
        'Research seminars in chosen area; minimum credit requirement before research stage',
        'Empirical research — large-sample quant studies, case studies, behavioural experiments',
        'Publication target — A-list business journals (FT-50 / ABS 4*)',
      ],
    },
  },
};
"""

_SYLLABUS_BODY = """
<section class="section">
  <div class="card">
    <h2>Syllabus</h2>
    <p class="sub">Full chapter-level syllabus for every major board and exam. Click any chapter to see the topics it covers. Content stays in the app — no jumps to NCERT / CBSE / NTA / state-board websites.</p>
    <label for="syBoard">Board / exam</label>
    <select id="syBoard"></select>
    <div id="syClassNav" style="display:flex;gap:6px;flex-wrap:wrap;margin:14px 0"></div>
    <div id="syChapters"></div>
  </div>
</section>
"""

_SYLLABUS_SCRIPT = _SYLLABUS_DATA + """
var CURRENT_BOARD = null, CURRENT_CLASS = null;
function renderBoardSel() {
  var sel = document.getElementById('syBoard');
  sel.innerHTML = Object.keys(SYLLABUS).map(function(k) {
    return '<option value="' + k + '">' + escapeHtml(SYLLABUS[k].label) + '</option>';
  }).join('');
  sel.addEventListener('change', function() { switchBoard(sel.value); });
}
function switchBoard(k) {
  CURRENT_BOARD = k;
  var board = SYLLABUS[k];
  var classes = Object.keys(board.classes);
  var nav = document.getElementById('syClassNav');
  nav.innerHTML = classes.map(function(c, i) {
    return '<button class="chip" data-cls="' + escapeHtml(c) +
      '" onclick="switchClass(this.dataset.cls)" style="cursor:pointer;border:0">' +
      escapeHtml(c) + '</button>';
  }).join(' ');
  if (classes.length) switchClass(classes[0]);
}
window.switchClass = function(c) {
  CURRENT_CLASS = c;
  // Highlight active chip
  document.querySelectorAll('#syClassNav .chip').forEach(function(b) {
    if (b.dataset.cls === c) b.style.background = '#fbbf24', b.style.color = '#0f172a';
    else b.style.background = '', b.style.color = '';
  });
  var chapters = SYLLABUS[CURRENT_BOARD].classes[c] || [];
  var out = document.getElementById('syChapters');
  if (!chapters.length) { out.innerHTML = '<div class="empty">No chapters in this section yet.</div>'; return; }
  out.innerHTML = chapters.map(function(ch, i) {
    // prod-149 — pass selected board/class/chapter as query params so the
    // destination pages can pre-filter to the student's chosen scope
    // instead of resetting to the generic catalog.
    var chapterTitle = ch.split(' — ')[0];
    var qs = '?board=' + encodeURIComponent(CURRENT_BOARD) +
             '&class=' + encodeURIComponent(c) +
             '&chapter=' + encodeURIComponent(chapterTitle);
    var chatQs = qs + '&q=' + encodeURIComponent(
      'Teach me ' + chapterTitle + ' (' + c + ', ' + CURRENT_BOARD + ')'
    );
    return '<div class="result"><strong>' + (i+1) + '. ' + escapeHtml(chapterTitle) + '</strong>' +
      (ch.indexOf(' — ') > 0 ? '<div class="sub" style="margin-top:6px">' + escapeHtml(ch.split(' — ').slice(1).join(' — ')) + '</div>' : '') +
      '<div style="margin-top:8px;display:flex;gap:6px;flex-wrap:wrap">' +
        '<a class="btn ghost" href="/practice' + qs + '">Practice this →</a>' +
        '<a class="btn ghost" href="/flashcards' + qs + '">Flashcards →</a>' +
        '<a class="btn ghost" href="/chat' + chatQs + '">Ask AI tutor →</a>' +
      '</div></div>';
  }).join('');
};
renderBoardSel();
// Default the selector to the user's onboarding board if present
fetch('/api/me/dashboard', { headers: authH() }).then(function(r){ return r.json(); }).then(function(d) {
  var onb = d.onboarding || {};
  var defaultKey = 'cbse_9_10';
  var cg = onb.class_grade || '';
  if (onb.board === 'icse') defaultKey = 'icse';
  // prod-150 — Each state board is its own bucket now; pass the onboarding
  // board key straight through when it's a recognised state_* key, else
  // fall back to Maharashtra (the first state board we documented).
  else if (onb.board && onb.board.indexOf('state_') === 0) {
    if (SYLLABUS[onb.board]) defaultKey = onb.board;
    else defaultKey = 'state_mh';
  }
  else if (onb.target_exam === 'jee_main' || onb.target_exam === 'jee_advanced') defaultKey = 'jee';
  else if (onb.target_exam === 'neet_ug' || onb.target_exam === 'neet') defaultKey = 'neet';
  else if (onb.target_exam === 'upsc_cse' || onb.target_exam === 'upsc') defaultKey = 'upsc';
  else if (['ssc_cgl','ibps_po','cat','gate'].indexOf(onb.target_exam) >= 0) defaultKey = 'bank_ssc';
  else if (cg === 'lkg' || cg === 'ukg' || cg === 'pre_primary') defaultKey = 'pre_primary';
  else if (['class_1','class_2','class_3','class_4','class_5'].indexOf(cg) >= 0) defaultKey = 'primary';
  else if (['class_6','class_7','class_8'].indexOf(cg) >= 0) defaultKey = 'cbse_6_8';
  else if (['class_9','class_10'].indexOf(cg) >= 0) defaultKey = 'cbse_9_10';
  else if (['class_11','class_12'].indexOf(cg) >= 0) defaultKey = 'cbse_11_12';
  else if (['undergraduate','ug','college','btech','bsc','bcom','ba','mbbs','llb'].indexOf(cg) >= 0) defaultKey = 'undergraduate';
  else if (['postgraduate','pg','ma','msc','mcom','mtech','mba','md','llm','medm','mca'].indexOf(cg) >= 0) defaultKey = 'postgraduate';
  else if (cg === 'phd' || cg === 'doctoral' || cg === 'research') defaultKey = 'phd';
  else if (cg === 'professional') defaultKey = 'undergraduate';
  document.getElementById('syBoard').value = defaultKey;
  switchBoard(defaultKey);
}).catch(function(){ switchBoard('cbse_9_10'); });
"""

_SYLLABUS_HTML = _page("Syllabus", _SYLLABUS_BODY, _SYLLABUS_SCRIPT)


# ---------- routes ----------

_PAGES = [
    ("/essay",      _ESSAY_HTML),
    ("/interview",  _INTERVIEW_HTML),
    ("/practice",   _PRACTICE_HTML),
    ("/adaptive",   _ADAPTIVE_HTML),
    ("/math",       _MATH_HTML),
    ("/voice",      _VOICE_HTML),
    ("/live",       _LIVE_HTML),
    ("/recap",      _RECAP_HTML),
    ("/notes",      _NOTES_HTML),
    ("/curriculum", _CURRICULUM_HTML),
    ("/path",       _PATH_HTML),
    ("/library",    _LIBRARY_HTML),
    ("/school",     _SCHOOL_HTML),
    ("/syllabus",   _SYLLABUS_HTML),
]


def _page_locale(request: Request) -> str:
    """prod-200 — resolve locale the same way web.py does for /home:
    ?lang= query -> padhai_lang cookie -> Accept-Language -> en."""
    from ..i18n import normalise_locale
    qp = request.query_params.get("lang")
    if qp:
        return normalise_locale(qp)
    cookie = request.cookies.get("padhai_lang")
    if cookie:
        return normalise_locale(cookie)
    return normalise_locale(request.headers.get("accept-language", ""))


def _make_handler(html_body: str):
    # prod-200 — localize the module page server-side from the resolved locale,
    # so the language switcher (cookie + reload) applies app-wide, not just /home.
    from ..i18n import localize_template

    def handler(request: Request):
        return HTMLResponse(
            localize_template(html_body, _page_locale(request)),
            headers=_NO_CACHE,
        )
    return handler


for _path, _html in _PAGES:
    router.add_api_route(
        _path,
        _make_handler(_html),
        methods=["GET"],
        response_class=HTMLResponse,
    )
