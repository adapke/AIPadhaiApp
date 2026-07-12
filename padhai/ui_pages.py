"""v1.0 — Student-facing UI screens for the core study loop."""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Language and level option tables (shared across screens)
# ---------------------------------------------------------------------------
_LANGUAGE_OPTIONS = [
    ("en", "English"),
    ("hi", "Hindi"),
    ("ta", "Tamil"),
    ("te", "Telugu"),
    ("kn", "Kannada"),
    ("mr", "Marathi"),
    ("bn", "Bengali"),
    ("gu", "Gujarati"),
    ("pa", "Punjabi"),
    ("ml", "Malayalam"),
]

_LEVEL_OPTIONS = [
    ("kg", "Kindergarten (KG)"),
    ("primary", "Primary (Class 1–5)"),
    ("middle", "Middle School (Class 6–8)"),
    ("secondary", "Secondary (Class 9–12)"),
    ("neet_jee", "NEET / JEE Entrance"),
]

_MODE_OPTIONS = [
    ("teaching", "Teaching (Full Lesson)"),
    ("explainer", "Explainer (Concept Overview)"),
    ("revision", "Revision (Quick Recap)"),
    ("kids", "Kids (Fun & Simple)"),
    ("reel", "Reel (60-second Clip)"),
]


def _lang_options_html(selected: str = "en") -> str:
    parts = []
    for val, label in _LANGUAGE_OPTIONS:
        sel = ' selected' if val == selected else ''
        parts.append(f'<option value="{val}"{sel}>{label}</option>')
    return "\n".join(parts)


def _level_options_html(selected: str = "secondary") -> str:
    parts = []
    for val, label in _LEVEL_OPTIONS:
        sel = ' selected' if val == selected else ''
        parts.append(f'<option value="{val}"{sel}>{label}</option>')
    return "\n".join(parts)


def _mode_options_html(selected: str = "teaching") -> str:
    parts = []
    for val, label in _MODE_OPTIONS:
        sel = ' selected' if val == selected else ''
        parts.append(f'<option value="{val}"{sel}>{label}</option>')
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Shared page shell
# ---------------------------------------------------------------------------

def _page_shell(*, title: str, body: str, extra_head: str = "", extra_script: str = "") -> str:
    """Shared HTML shell: auth guard, persona-aware nav bar, common CSS."""
    from . import ui_nav as _nav
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} — PadhaiApp</title>
<link rel="manifest" href="/manifest.json">
<meta name="theme-color" content="#1565d8">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="PadhaiApp">
<style>
  :root{{
    --brand:#1565d8; --bg:#f5f7fb; --panel:#fff; --ink:#111827;
    --muted:#667085; --line:#d9e0ea; --green:#16855f; --amber:#a86600;
    --violet:#5b3cc4; --radius:8px;
    --brand-soft:#eaf2ff; --green-soft:#e7f6ef; --amber-soft:#fff4db;
    --violet-soft:#f0ecff; --red:#c0392b; --red-soft:#fdecea;
    --nav:#101828;
  }}
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{background:var(--bg);color:var(--ink);
        font-family:Inter,"Segoe UI",Arial,sans-serif;
        min-height:100vh;display:flex;flex-direction:column}}
  a{{color:var(--brand);text-decoration:none}}
  a:hover{{text-decoration:underline}}
  button,input,select,textarea{{font-family:inherit;font-size:14px}}
  button{{cursor:pointer}}
  button:disabled{{opacity:.55;cursor:not-allowed}}

  /* ---- Top nav ---- */
  .top-nav{{background:var(--nav);color:#eef3ff;
            display:flex;align-items:center;gap:12px;
            padding:0 16px;height:52px;flex-shrink:0}}
  .nav-logo{{width:32px;height:32px;border-radius:8px;
             background:linear-gradient(135deg,#2f80ed,#12b76a);
             display:grid;place-items:center;color:#fff;
             font-weight:900;font-size:15px;flex-shrink:0}}
  .nav-brand{{font-weight:800;font-size:15px;color:#fff;white-space:nowrap}}
  .nav-sep{{color:#3d4f6e;font-size:18px;user-select:none}}
  .nav-breadcrumb{{color:#aeb8cc;font-size:13px;flex:1;
                   white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
  .nav-email{{background:#1d2939;color:#cfd8ea;border-radius:999px;
              padding:5px 11px;font-size:12px;white-space:nowrap;flex-shrink:0}}
  .nav-home{{color:#aeb8cc;font-size:12px;white-space:nowrap;
             text-decoration:none;border:1px solid #3d4f6e;
             border-radius:6px;padding:5px 10px;flex-shrink:0}}
  .nav-home:hover{{background:#1d2939;color:#fff;text-decoration:none}}

  /* ---- Page frame ---- */
  .page{{flex:1;padding:20px;max-width:860px;width:100%;margin:0 auto}}
  .page-wide{{flex:1;padding:20px;max-width:1100px;width:100%;margin:0 auto}}
  .page-title{{font-size:22px;font-weight:800;margin-bottom:4px}}
  .page-sub{{color:var(--muted);font-size:13px;margin-bottom:20px}}

  /* ---- Cards / panels ---- */
  .card{{background:var(--panel);border:1px solid var(--line);
         border-radius:var(--radius);padding:20px}}
  .card+.card{{margin-top:14px}}

  /* ---- Form controls ---- */
  .field{{margin-bottom:14px}}
  .field label{{display:block;font-size:13px;font-weight:700;
                color:var(--ink);margin-bottom:5px}}
  .field input,.field select,.field textarea{{
    width:100%;border:1px solid var(--line);border-radius:var(--radius);
    padding:10px 12px;background:var(--panel);color:var(--ink);
    outline:none;transition:border-color .15s}}
  .field input:focus,.field select:focus,.field textarea:focus{{
    border-color:var(--brand)}}
  .field textarea{{resize:vertical;min-height:80px}}
  .field .hint{{color:var(--muted);font-size:12px;margin-top:4px}}
  .row-2{{display:grid;grid-template-columns:1fr 1fr;gap:14px}}
  .row-3{{display:grid;grid-template-columns:1fr 1fr 1fr;gap:14px}}

  /* ---- Buttons ---- */
  .btn{{display:inline-flex;align-items:center;gap:7px;
        border:1px solid var(--line);background:var(--panel);
        color:var(--ink);border-radius:var(--radius);
        padding:10px 16px;font-weight:700;font-size:14px}}
  .btn:hover{{border-color:#bad4ff}}
  .btn-primary{{background:var(--brand);border-color:var(--brand);color:#fff}}
  .btn-primary:hover{{background:#0b4ec1;border-color:#0b4ec1}}
  .btn-success{{background:var(--green);border-color:var(--green);color:#fff}}
  .btn-danger{{background:var(--red);border-color:var(--red);color:#fff}}
  .btn-sm{{padding:7px 12px;font-size:13px}}
  .btn-block{{width:100%;justify-content:center;margin-top:4px}}

  /* ---- Progress bar ---- */
  .progress-wrap{{height:8px;background:#d7e5fb;border-radius:999px;
                  overflow:hidden;margin:8px 0}}
  .progress-fill{{height:100%;background:var(--brand);
                  border-radius:999px;transition:width .4s ease;width:0%}}

  /* ---- Tags / pills ---- */
  .tag{{display:inline-flex;align-items:center;padding:3px 9px;
        border-radius:999px;font-size:11px;font-weight:800;
        white-space:nowrap}}
  .tag-green{{background:var(--green-soft);color:var(--green)}}
  .tag-amber{{background:var(--amber-soft);color:var(--amber)}}
  .tag-violet{{background:var(--violet-soft);color:var(--violet)}}
  .tag-brand{{background:var(--brand-soft);color:var(--brand)}}
  .tag-red{{background:var(--red-soft);color:var(--red)}}

  /* ---- Spinner ---- */
  @keyframes spin{{to{{transform:rotate(360deg)}}}}
  .spinner{{width:20px;height:20px;border:3px solid var(--line);
            border-top-color:var(--brand);border-radius:50%;
            animation:spin .7s linear infinite;display:inline-block}}

  /* ---- Error / info banners ---- */
  .alert{{border-radius:var(--radius);padding:12px 14px;
          font-size:13px;line-height:1.5;margin-bottom:14px}}
  .alert-error{{background:var(--red-soft);color:var(--red);
                border:1px solid #f5c6c2}}
  .alert-success{{background:var(--green-soft);color:var(--green);
                  border:1px solid #b7e8d5}}
  .alert-info{{background:var(--brand-soft);color:var(--brand);
               border:1px solid #c0d9fa}}

  /* ---- Modal ---- */
  .modal-backdrop{{position:fixed;inset:0;background:rgba(15,23,42,.5);
                   z-index:50;display:none;align-items:center;
                   justify-content:center;padding:16px}}
  .modal-backdrop.open{{display:flex}}
  .modal{{background:var(--panel);border-radius:12px;
          padding:24px;width:100%;max-width:460px;
          max-height:90vh;overflow:auto;
          box-shadow:0 20px 60px rgba(15,23,42,.25)}}
{_nav.NAV_STYLE}
  .modal h2{{font-size:18px;margin-bottom:16px}}
  .modal-close{{float:right;background:none;border:none;
                font-size:22px;color:var(--muted);cursor:pointer;
                line-height:1;padding:0 2px}}

  /* ---- Tabs ---- */
  .tabs{{display:flex;gap:0;border-bottom:2px solid var(--line);
         margin-bottom:20px;overflow-x:auto}}
  .tab-btn{{background:none;border:none;padding:11px 18px;
            font-size:14px;font-weight:700;color:var(--muted);
            border-bottom:2px solid transparent;margin-bottom:-2px;
            white-space:nowrap}}
  .tab-btn.active{{color:var(--brand);border-bottom-color:var(--brand)}}
  .tab-pane{{display:none}}
  .tab-pane.active{{display:block}}

  /* ---- Empty state ---- */
  .empty-state{{text-align:center;padding:48px 20px;color:var(--muted)}}
  .empty-state .icon{{font-size:40px;margin-bottom:12px}}
  .empty-state h3{{font-size:16px;color:var(--ink);margin-bottom:6px}}
  .empty-state p{{font-size:13px;line-height:1.5}}

  /* ---- Responsive ---- */
  @media(max-width:600px){{
    .page,.page-wide{{padding:14px}}
    .row-2,.row-3{{grid-template-columns:1fr}}
    .page-title{{font-size:18px}}
    .tabs{{margin-bottom:14px}}
    .tab-btn{{padding:9px 13px;font-size:13px}}
  }}
</style>
{extra_head}
</head>
<body>
{_nav.NAV_HTML}
{body}
<script>
(function(){{
  // Auth guard — redirect to /landing if no token
  var tok = localStorage.getItem('pathshala_token');
  if (!tok) {{ window.location.replace('/landing'); return; }}

  // Show user email in nav
  async function loadNavEmail() {{
    try {{
      var r = await fetch('/auth/me', {{headers:{{Authorization:'Bearer '+tok}}}});
      if (r.status === 401) {{ localStorage.removeItem('pathshala_token'); window.location.replace('/landing'); return; }}
      var d = await r.json();
      var el = document.getElementById('navEmail');
      if (el && d.email) el.textContent = d.email;
    }} catch(e) {{}}
  }}
  loadNavEmail();
}})();

// Global fetch helper with auth header
async function apiFetch(url, opts) {{
  opts = opts || {{}};
  opts.headers = opts.headers || {{}};
  var tok = localStorage.getItem('pathshala_token');
  if (tok) opts.headers['Authorization'] = 'Bearer ' + tok;
  return fetch(url, opts);
}}

// XSS-safe HTML escape
function escapeHtml(s) {{
  return String(s)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;')
    .replace(/>/g,'&gt;').replace(/"/g,'&quot;')
    .replace(/'/g,'&#39;');
}}

{_nav.NAV_SCRIPT}
{extra_script}
</script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# 1. Lesson generator  /lessons/new
# ---------------------------------------------------------------------------

def get_lesson_new_html() -> str:
    """Lesson generator: upload file, choose options, generate video lesson."""

    body = """
<div class="page">
  <div class="page-title">Create a Lesson</div>
  <div class="page-sub">Upload a document or image and let the AI build a personalised video lesson.</div>

  <div id="errorBanner" class="alert alert-error" style="display:none"></div>

  <form id="lessonForm" autocomplete="off">
    <!-- Drop zone -->
    <div class="card" style="margin-bottom:14px">
      <div id="dropZone"
           style="border:2px dashed var(--line);border-radius:var(--radius);
                  padding:36px 20px;text-align:center;cursor:pointer;
                  transition:border-color .15s,background .15s"
           tabindex="0" role="button" aria-label="Upload file">
        <div style="font-size:32px;margin-bottom:10px">📄</div>
        <div style="font-weight:700;margin-bottom:4px">
          Drop a file here or <span style="color:var(--brand)">click to browse</span>
        </div>
        <div style="color:var(--muted);font-size:12px">
          Accepted: PDF, JPG, JPEG, PNG, PPTX, DOCX — max 20 MB
        </div>
        <div id="fileNameDisplay" style="margin-top:10px;font-weight:700;
             color:var(--brand);display:none"></div>
      </div>
      <input type="file" id="fileInput" name="file"
             accept=".pdf,.jpg,.jpeg,.png,.pptx,.docx"
             style="display:none">
    </div>

    <!-- Options -->
    <div class="card" style="margin-bottom:14px">
      <div class="row-3">
        <div class="field">
          <label for="selLang">Language</label>
          <select id="selLang" name="language">
            <option value="en">English</option>
            <option value="hi">Hindi</option>
            <option value="ta">Tamil</option>
            <option value="te">Telugu</option>
            <option value="kn">Kannada</option>
            <option value="mr">Marathi</option>
            <option value="bn">Bengali</option>
            <option value="gu">Gujarati</option>
            <option value="pa">Punjabi</option>
            <option value="ml">Malayalam</option>
          </select>
        </div>
        <div class="field">
          <label for="selLevel">Level</label>
          <select id="selLevel" name="level">
            <option value="kg">Kindergarten (KG)</option>
            <option value="primary">Primary (Class 1–5)</option>
            <option value="middle">Middle School (Class 6–8)</option>
            <option value="secondary" selected>Secondary (Class 9–12)</option>
            <option value="neet_jee">NEET / JEE Entrance</option>
            <option value="sat">SAT (US Digital SAT)</option>
          </select>
        </div>
        <div class="field">
          <label for="selMode">Mode</label>
          <select id="selMode" name="mode">
            <option value="teaching" selected>Teaching (Full Lesson)</option>
            <option value="explainer">Explainer (Concept Overview)</option>
            <option value="revision">Revision (Quick Recap)</option>
            <option value="kids">Kids (Fun &amp; Simple)</option>
            <option value="reel">Reel (60-second Clip)</option>
          </select>
        </div>
      </div>
      <div class="field" style="margin-bottom:0">
        <label style="display:flex;align-items:center;gap:8px;cursor:pointer;
                      font-weight:700;font-size:13px">
          <input type="checkbox" id="chkQuiz" name="include_quiz"
                 style="width:16px;height:16px;accent-color:var(--brand)"
                 checked>
          Include quiz after lesson
        </label>
      </div>
    </div>

    <button type="submit" class="btn btn-primary btn-block" id="genBtn">
      Generate Lesson
    </button>
  </form>

  <!-- Progress -->
  <div id="progressSection" style="display:none;margin-top:20px">
    <div class="card">
      <div style="font-weight:700;margin-bottom:14px;font-size:15px">
        Generating your lesson…
      </div>
      <!-- Step indicators -->
      <div style="display:flex;gap:0;margin-bottom:16px;overflow-x:auto">
        <div class="step-item" id="step-uploading" data-label="Uploading">
          <div class="step-circle active">1</div>
          <div class="step-label">Uploading</div>
        </div>
        <div class="step-connector"></div>
        <div class="step-item" id="step-processing" data-label="Processing">
          <div class="step-circle">2</div>
          <div class="step-label">Processing</div>
        </div>
        <div class="step-connector"></div>
        <div class="step-item" id="step-rendering" data-label="Rendering">
          <div class="step-circle">3</div>
          <div class="step-label">Rendering</div>
        </div>
        <div class="step-connector"></div>
        <div class="step-item" id="step-done" data-label="Done">
          <div class="step-circle">4</div>
          <div class="step-label">Done</div>
        </div>
      </div>
      <div class="progress-wrap">
        <div class="progress-fill" id="progBar"></div>
      </div>
      <div style="display:flex;justify-content:space-between;
                  font-size:12px;color:var(--muted);margin-top:4px">
        <span id="progStatus">Uploading file…</span>
        <span id="progPct">0%</span>
      </div>
    </div>
  </div>

  <!-- Result -->
  <div id="resultSection" style="display:none;margin-top:20px">
    <div class="card">
      <div style="font-weight:700;font-size:15px;margin-bottom:12px;
                  color:var(--green)">✓ Lesson ready!</div>
      <video id="lessonVideo" controls autoplay
             style="width:100%;border-radius:var(--radius);
                    background:#000;max-height:400px;margin-bottom:16px">
        <p>Your browser does not support HTML5 video.</p>
      </video>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px">
        <button class="btn btn-primary" id="btnFlashcards">
          📚 Study Flashcards
        </button>
        <button class="btn" id="btnQuiz" style="border-color:var(--brand);color:var(--brand)">
          📝 Take Quiz
        </button>
        <button class="btn" id="btnTutor" style="border-color:var(--violet);color:var(--violet)">
          💬 Ask Tutor
        </button>
        <button class="btn" id="btnRecap" style="border-color:var(--green);color:var(--green)">
          🎧 Audio Recap
        </button>
      </div>
      <div id="audioRecapPlayer" style="display:none;margin-top:14px">
        <audio id="recapAudio" controls style="width:100%"></audio>
      </div>
    </div>
  </div>
</div>

<style>
  .step-item{display:flex;flex-direction:column;align-items:center;flex:1;min-width:60px}
  .step-circle{width:30px;height:30px;border-radius:50%;
    border:2px solid var(--line);display:grid;place-items:center;
    font-weight:800;font-size:13px;color:var(--muted);background:var(--panel);
    transition:all .25s}
  .step-circle.active{border-color:var(--brand);color:var(--brand);background:var(--brand-soft)}
  .step-circle.done{border-color:var(--green);background:var(--green);color:#fff}
  .step-label{font-size:11px;color:var(--muted);margin-top:5px;text-align:center}
  .step-connector{flex:1;height:2px;background:var(--line);margin-top:15px;min-width:16px}
</style>
"""

    script = """
(function() {
  var dropZone = document.getElementById('dropZone');
  var fileInput = document.getElementById('fileInput');
  var fileNameDisplay = document.getElementById('fileNameDisplay');
  var lessonForm = document.getElementById('lessonForm');
  var genBtn = document.getElementById('genBtn');
  var progressSection = document.getElementById('progressSection');
  var resultSection = document.getElementById('resultSection');
  var errorBanner = document.getElementById('errorBanner');
  var progBar = document.getElementById('progBar');
  var progStatus = document.getElementById('progStatus');
  var progPct = document.getElementById('progPct');

  var currentJobId = null;
  var currentLessonId = null;
  var pollTimer = null;

  // Drop zone interactions
  dropZone.addEventListener('click', function() { fileInput.click(); });
  dropZone.addEventListener('keydown', function(e) {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); fileInput.click(); }
  });
  dropZone.addEventListener('dragover', function(e) {
    e.preventDefault();
    dropZone.style.borderColor = 'var(--brand)';
    dropZone.style.background = 'var(--brand-soft)';
  });
  dropZone.addEventListener('dragleave', function() {
    dropZone.style.borderColor = '';
    dropZone.style.background = '';
  });
  dropZone.addEventListener('drop', function(e) {
    e.preventDefault();
    dropZone.style.borderColor = '';
    dropZone.style.background = '';
    var files = e.dataTransfer.files;
    if (files.length) setFile(files[0]);
  });
  fileInput.addEventListener('change', function() {
    if (fileInput.files.length) setFile(fileInput.files[0]);
  });

  function setFile(f) {
    fileNameDisplay.textContent = '📎 ' + f.name + ' (' + (f.size/1024).toFixed(0) + ' KB)';
    fileNameDisplay.style.display = 'block';
  }

  function showError(msg) {
    errorBanner.textContent = msg;
    errorBanner.style.display = 'block';
    errorBanner.scrollIntoView({behavior:'smooth', block:'nearest'});
  }
  function clearError() { errorBanner.style.display = 'none'; }

  function setStep(stepName) {
    var order = ['uploading','processing','rendering','done'];
    var idx = order.indexOf(stepName);
    order.forEach(function(s, i) {
      var circ = document.querySelector('#step-'+s+' .step-circle');
      if (!circ) return;
      circ.classList.remove('active','done');
      if (i < idx) circ.classList.add('done');
      else if (i === idx) circ.classList.add('active');
    });
  }

  function setProgress(pct, statusText) {
    progBar.style.width = pct + '%';
    progPct.textContent = pct + '%';
    if (statusText) progStatus.textContent = statusText;
  }

  function startPolling(jobId) {
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = setInterval(function() { pollJob(jobId); }, 3000);
  }

  function stopPolling() {
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
  }

  async function pollJob(jobId) {
    try {
      var r = await apiFetch('/jobs/' + encodeURIComponent(jobId));
      if (!r.ok) return;
      var d = await r.json();
      var status = d.status || '';
      var progress = d.progress || 0;

      if (status === 'queued') {
        setStep('uploading');
        setProgress(10, 'Queued — waiting for GPU slot…');
      } else if (status === 'running') {
        setStep(progress < 50 ? 'processing' : 'rendering');
        setProgress(Math.max(20, progress), d.step_label || 'Generating lesson…');
      } else if (status === 'succeeded') {
        stopPolling();
        setStep('done');
        setProgress(100, 'Done!');
        currentLessonId = d.result && d.result.lesson_id ? d.result.lesson_id : jobId;
        showResult(jobId, currentLessonId);
      } else if (status === 'failed') {
        stopPolling();
        progressSection.style.display = 'none';
        showError('Lesson generation failed: ' + (d.error || 'unknown error'));
        genBtn.disabled = false;
      }
    } catch(e) {}
  }

  function showResult(jobId, lessonId) {
    var video = document.getElementById('lessonVideo');
    video.src = '/jobs/' + encodeURIComponent(jobId) + '/video';
    progressSection.style.display = 'none';
    resultSection.style.display = 'block';
    resultSection.scrollIntoView({behavior:'smooth'});

    document.getElementById('btnFlashcards').onclick = async function() {
      this.disabled = true;
      this.textContent = '…';
      try {
        await apiFetch('/lessons/' + encodeURIComponent(lessonId) + '/flashcards', {method:'POST'});
      } catch(e) {}
      window.location.href = '/flashcards?lesson=' + encodeURIComponent(lessonId);
    };
    document.getElementById('btnQuiz').onclick = function() {
      window.location.href = '/quiz?lesson=' + encodeURIComponent(lessonId);
    };
    document.getElementById('btnTutor').onclick = function() {
      window.location.href = '/chat?lesson=' + encodeURIComponent(lessonId);
    };
    document.getElementById('btnRecap').onclick = async function() {
      this.disabled = true;
      this.textContent = '…';
      try {
        var r = await apiFetch('/lessons/' + encodeURIComponent(lessonId) + '/recap', {method:'POST'});
        var d = await r.json();
        if (d.audio_url) {
          document.getElementById('recapAudio').src = d.audio_url;
          document.getElementById('audioRecapPlayer').style.display = 'block';
        }
      } catch(e) {}
      this.disabled = false;
      this.textContent = '🎧 Audio Recap';
    };
  }

  lessonForm.addEventListener('submit', async function(e) {
    e.preventDefault();
    clearError();

    if (!fileInput.files.length) {
      showError('Please select a file to upload.');
      return;
    }

    genBtn.disabled = true;
    genBtn.textContent = '…';
    progressSection.style.display = 'block';
    resultSection.style.display = 'none';
    setStep('uploading');
    setProgress(5, 'Uploading file…');
    progressSection.scrollIntoView({behavior:'smooth'});

    var fd = new FormData();
    // prod-153 — POST /lessons expects field name `image`, not `file`.
    // Sending the wrong name produced a FastAPI 422 with detail=
    // [{loc,msg,type}, ...] which the catch block then stringified to
    // `[object Object]`. Field name fixed AND error rendering hardened
    // below so future server-side error shapes degrade gracefully.
    fd.append('image', fileInput.files[0]);
    fd.append('language', document.getElementById('selLang').value);
    fd.append('level', document.getElementById('selLevel').value);
    // The endpoint accepts `render_mode` (animated|...), not `mode`.
    // Map "explainer/teaching/etc" to the closest render_mode the
    // server understands; default to "animated" for everything else
    // so the request still validates.
    var modeVal = document.getElementById('selMode').value;
    fd.append('render_mode',
      modeVal === 'reel' ? 'reel' :
      modeVal === 'explainer' ? 'narrated' :
      'animated');
    fd.append('include_quiz', document.getElementById('chkQuiz').checked ? 'true' : 'false');

    function formatServerError(d, status) {
      // FastAPI returns detail as either a string OR an array of
      // {loc, msg, type} validation entries. Stringify the latter
      // into human-readable bullet points so users see what's wrong
      // instead of `[object Object]`.
      if (!d) return 'Upload failed (HTTP ' + status + ')';
      if (typeof d.detail === 'string') return d.detail;
      if (Array.isArray(d.detail)) {
        return d.detail.map(function(e) {
          if (!e || typeof e !== 'object') return String(e);
          var loc = Array.isArray(e.loc) ? e.loc.slice(-1)[0] : (e.loc || '');
          return (loc ? loc + ': ' : '') + (e.msg || JSON.stringify(e));
        }).join(' · ');
      }
      if (typeof d.error === 'string') return d.error;
      if (typeof d.message === 'string') return d.message;
      // Last resort — print the serialised JSON so the user can copy
      // it to a bug report (still readable, never [object Object]).
      try { return JSON.stringify(d); }
      catch (_) { return 'Upload failed (HTTP ' + status + ')'; }
    }

    try {
      var r = await apiFetch('/lessons', {method:'POST', body:fd});
      var d = null;
      try { d = await r.json(); } catch(_) { d = null; }
      if (!r.ok) {
        throw new Error(formatServerError(d, r.status));
      }

      // Cache hit — direct video URL
      if (r.status === 200 && d.lesson_id) {
        currentLessonId = d.lesson_id;
        setProgress(100, 'Cache hit — lesson ready!');
        setStep('done');
        showResult(d.job_id || d.lesson_id, d.lesson_id);
        genBtn.disabled = false;
        genBtn.textContent = 'Generate Lesson';
        return;
      }

      // Job created — start polling
      if (d.job_id) {
        currentJobId = d.job_id;
        setStep('processing');
        setProgress(15, 'Processing…');
        startPolling(d.job_id);
      } else {
        throw new Error('Unexpected server response');
      }
    } catch(err) {
      progressSection.style.display = 'none';
      showError(err.message || 'Something went wrong. Please try again.');
      genBtn.disabled = false;
      genBtn.textContent = 'Generate Lesson';
    }
  });

  // Enter key inside form triggers submit
  lessonForm.addEventListener('keydown', function(e) {
    if (e.key === 'Enter' && e.target.tagName !== 'BUTTON') {
      lessonForm.dispatchEvent(new Event('submit', {cancelable:true}));
    }
  });
})();
"""

    return _page_shell(title="New Lesson", body=body, extra_script=script)


# ---------------------------------------------------------------------------
# 2. Flashcards  /flashcards
# ---------------------------------------------------------------------------

def get_flashcards_html() -> str:
    """Flashcard study screen with SM-2 spaced-repetition ratings."""

    body = """
<div class="page">
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:6px">
    <div class="page-title">Flashcards</div>
    <div class="field" style="margin:0;min-width:160px">
      <select id="deckSelect" aria-label="Filter by deck">
        <option value="">All decks</option>
      </select>
    </div>
  </div>

  <!-- Progress counter -->
  <div id="progressRow" style="display:flex;align-items:center;gap:10px;margin-bottom:14px">
    <span id="cardCounter" style="font-size:13px;color:var(--muted)">Loading…</span>
    <div class="progress-wrap" style="flex:1;margin:0">
      <div class="progress-fill" id="fcProgBar"></div>
    </div>
  </div>

  <!-- Loading state -->
  <div id="loadingState" style="text-align:center;padding:48px 20px">
    <div class="spinner" style="width:32px;height:32px;margin:0 auto 12px"></div>
    <div style="color:var(--muted)">Loading due cards…</div>
  </div>

  <!-- All done state -->
  <div id="allDoneState" style="display:none" class="card">
    <div class="empty-state">
      <div class="icon">🎉</div>
      <h3>All caught up!</h3>
      <p id="comebackMsg">No cards are due right now. Great work!</p>
      <div style="margin-top:16px">
        <a href="/home" class="btn btn-primary">Back to Home</a>
      </div>
    </div>
  </div>

  <!-- Error state -->
  <div id="errorState" style="display:none">
    <div class="alert alert-error" id="fcErrorMsg"></div>
    <button class="btn btn-primary" onclick="loadCards()">Retry</button>
  </div>

  <!-- Card display -->
  <div id="cardArea" style="display:none">
    <div id="cardWrapper"
         style="perspective:1000px;margin-bottom:20px;cursor:pointer"
         tabindex="0" role="button"
         aria-label="Click or press Enter to flip card">
      <div id="cardInner"
           style="position:relative;min-height:220px;
                  transition:transform .5s cubic-bezier(.4,0,.2,1);
                  transform-style:preserve-3d">
        <!-- Front face -->
        <div id="cardFront"
             style="position:absolute;inset:0;backface-visibility:hidden;
                    background:var(--panel);border:2px solid var(--brand);
                    border-radius:12px;padding:28px 24px;
                    display:flex;flex-direction:column;justify-content:center">
          <div style="font-size:11px;font-weight:800;color:var(--muted);
                      text-transform:uppercase;margin-bottom:10px">Question</div>
          <div id="cardFrontText" style="font-size:17px;font-weight:700;
               line-height:1.5;color:var(--ink)"></div>
          <div style="margin-top:16px">
            <button id="hintBtn" class="btn btn-sm"
                    style="font-size:12px;color:var(--muted)">Show hint</button>
            <div id="hintText" style="display:none;font-size:13px;
                 color:var(--amber);margin-top:8px;font-style:italic"></div>
          </div>
          <div style="margin-top:auto;padding-top:20px;
                      font-size:12px;color:var(--muted);text-align:center">
            Tap to reveal answer
          </div>
        </div>
        <!-- Back face -->
        <div id="cardBack"
             style="position:absolute;inset:0;backface-visibility:hidden;
                    background:var(--panel);border:2px solid var(--green);
                    border-radius:12px;padding:28px 24px;
                    display:flex;flex-direction:column;justify-content:center;
                    transform:rotateY(180deg)">
          <div style="font-size:11px;font-weight:800;color:var(--muted);
                      text-transform:uppercase;margin-bottom:10px">Answer</div>
          <div id="cardBackText" style="font-size:16px;line-height:1.6;
               color:var(--ink)"></div>
        </div>
      </div>
    </div>

    <!-- Rating buttons -->
    <div id="ratingRow" style="display:none;
         grid-template-columns:repeat(4,1fr);gap:10px">
      <button class="btn" id="btnAgain"
              style="border-color:var(--red);color:var(--red);
                     justify-content:center;flex-direction:column;gap:3px">
        <span style="font-size:16px">✗</span>
        <span>Again</span>
        <span style="font-size:11px;opacity:.7">&lt;1d</span>
      </button>
      <button class="btn" id="btnHard"
              style="border-color:var(--amber);color:var(--amber);
                     justify-content:center;flex-direction:column;gap:3px">
        <span style="font-size:16px">~</span>
        <span>Hard</span>
        <span style="font-size:11px;opacity:.7">grade 2</span>
      </button>
      <button class="btn" id="btnGood"
              style="border-color:var(--brand);color:var(--brand);
                     justify-content:center;flex-direction:column;gap:3px">
        <span style="font-size:16px">✓</span>
        <span>Good</span>
        <span style="font-size:11px;opacity:.7">grade 4</span>
      </button>
      <button class="btn" id="btnEasy"
              style="border-color:var(--green);color:var(--green);
                     justify-content:center;flex-direction:column;gap:3px">
        <span style="font-size:16px">★</span>
        <span>Easy</span>
        <span style="font-size:11px;opacity:.7">grade 5</span>
      </button>
    </div>
  </div>

  <!-- Session stats (shown at end) -->
  <div id="sessionStats" style="display:none" class="card">
    <div style="font-weight:700;font-size:17px;margin-bottom:14px;
                color:var(--green)">Session complete! 🎉</div>
    <div style="display:grid;grid-template-columns:repeat(2,1fr);gap:12px;
                margin-bottom:18px">
      <div style="text-align:center">
        <div style="font-size:28px;font-weight:900" id="statTotal">—</div>
        <div style="color:var(--muted);font-size:12px">Cards reviewed</div>
      </div>
      <div style="text-align:center">
        <div style="font-size:28px;font-weight:900;color:var(--green)" id="statCorrect">—</div>
        <div style="color:var(--muted);font-size:12px">Correct (Good+Easy)</div>
      </div>
    </div>
    <div style="display:flex;gap:10px;flex-wrap:wrap">
      <button class="btn btn-primary" onclick="loadCards()">Study again</button>
      <a href="/home" class="btn">Back to Home</a>
    </div>
  </div>
</div>
"""

    script = """
(function() {
  var cards = [];
  var currentIdx = 0;
  var isFlipped = false;
  var sessionStats = {total:0, correct:0};
  var ratingDisabled = false;

  var cardWrapper = document.getElementById('cardWrapper');
  var cardInner = document.getElementById('cardInner');
  var cardFrontText = document.getElementById('cardFrontText');
  var cardBackText = document.getElementById('cardBackText');
  var hintBtn = document.getElementById('hintBtn');
  var hintText = document.getElementById('hintText');
  var ratingRow = document.getElementById('ratingRow');
  var cardArea = document.getElementById('cardArea');
  var loadingState = document.getElementById('loadingState');
  var allDoneState = document.getElementById('allDoneState');
  var errorState = document.getElementById('errorState');
  var sessionStatsEl = document.getElementById('sessionStats');
  var deckSelect = document.getElementById('deckSelect');

  function showArea(id) {
    ['loadingState','allDoneState','errorState','cardArea','sessionStats'].forEach(function(n) {
      var el = document.getElementById(n);
      if (el) el.style.display = (n === id ? (n === 'ratingRow' ? 'grid' : 'block') : 'none');
    });
  }

  function updateProgress() {
    var total = cards.length;
    var done = currentIdx;
    var pct = total > 0 ? Math.round((done / total) * 100) : 0;
    document.getElementById('fcProgBar').style.width = pct + '%';
    document.getElementById('cardCounter').textContent = done + ' of ' + total + ' cards';
  }

  window.loadCards = async function() {
    showArea('loadingState');
    sessionStats = {total:0, correct:0};
    currentIdx = 0;
    isFlipped = false;
    var url = '/api/flashcards/due';
    var deck = deckSelect.value;
    if (deck) url += '?deck_id=' + encodeURIComponent(deck);
    try {
      var r = await apiFetch(url);
      if (!r.ok) throw new Error('HTTP ' + r.status);
      var payload = await r.json();
      // Endpoint returns {cards:[...], count:N} — be tolerant of older
      // array-only shapes too.
      cards = Array.isArray(payload) ? payload : (payload.cards || []);
    } catch(e) {
      document.getElementById('fcErrorMsg').textContent = 'Failed to load cards: ' + e.message;
      showArea('errorState');
      return;
    }
    if (!cards.length) {
      // First-time visitor: seed the 9 board/exam starter decks, then
      // reload. The endpoint is idempotent — second call is a no-op.
      try {
        var seedR = await apiFetch('/api/flashcards/seed-starter',
                                   {method:'POST'});
        if (seedR.ok) {
          var seedJson = await seedR.json();
          if (seedJson.seeded || seedJson.deck_count > 0) {
            // Re-fetch decks + due cards now that the SRS has content.
            await loadDecks();
            var r2 = await apiFetch('/api/flashcards/due');
            if (r2.ok) cards = await r2.json();
            // cards is an array OR {cards:[],count:N} — accept both
            if (cards && cards.cards) cards = cards.cards;
            if (cards && cards.length) {
              showArea('cardArea');
              showCard(0);
              return;
            }
          }
        }
      } catch(e) { /* fall through to allDoneState */ }
      showArea('allDoneState');
      return;
    }
    showArea('cardArea');
    showCard(0);
  };

  async function loadDecks() {
    try {
      var r = await apiFetch('/api/flashcards/decks');
      if (!r.ok) return;
      var payload = await r.json();
      // Endpoint returns {decks:[...], count:N} — be tolerant of legacy
      // array-only shapes too.
      var decks = Array.isArray(payload) ? payload : (payload.decks || []);
      // Clear any existing options past the first ("All decks")
      while (deckSelect.options.length > 1) deckSelect.remove(1);
      decks.forEach(function(d) {
        var opt = document.createElement('option');
        opt.value = d.id;
        opt.textContent = escapeHtml(d.title) +
          (d.card_count ? ' (' + d.card_count + ')' : '');
        deckSelect.appendChild(opt);
      });
    } catch(e) {}
  }

  function showCard(idx) {
    if (idx >= cards.length) {
      showSessionStats();
      return;
    }
    var card = cards[idx];
    isFlipped = false;
    cardInner.style.transform = '';
    cardFrontText.textContent = card.front || '(no front)';
    cardBackText.textContent = card.back || '(no back)';
    hintText.style.display = 'none';
    hintText.textContent = card.hint || '';
    hintBtn.style.display = card.hint ? 'inline-flex' : 'none';
    ratingRow.style.display = 'none';
    updateProgress();
  }

  function flipCard() {
    if (ratingDisabled) return;
    isFlipped = !isFlipped;
    cardInner.style.transform = isFlipped ? 'rotateY(180deg)' : '';
    ratingRow.style.display = isFlipped ? 'grid' : 'none';
  }

  cardWrapper.addEventListener('click', function(e) {
    if (e.target.closest('#hintBtn') || e.target.closest('#ratingRow')) return;
    flipCard();
  });
  cardWrapper.addEventListener('keydown', function(e) {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); flipCard(); }
    if (e.key === '1') rate(0);
    if (e.key === '2') rate(2);
    if (e.key === '3') rate(4);
    if (e.key === '4') rate(5);
  });

  hintBtn.addEventListener('click', function(e) {
    e.stopPropagation();
    hintText.style.display = hintText.style.display === 'none' ? 'block' : 'none';
    hintBtn.textContent = hintText.style.display === 'none' ? 'Show hint' : 'Hide hint';
  });

  async function rate(grade) {
    if (!isFlipped || ratingDisabled) return;
    var card = cards[currentIdx];
    ratingDisabled = true;
    document.querySelectorAll('#ratingRow button').forEach(function(b) { b.disabled = true; });
    try {
      await apiFetch('/api/flashcards/' + encodeURIComponent(card.id) + '/review', {
        method:'POST',
        headers:{'Content-Type':'application/x-www-form-urlencoded'},
        body:'grade=' + grade
      });
    } catch(e) {}
    sessionStats.total++;
    if (grade >= 4) sessionStats.correct++;
    currentIdx++;
    ratingDisabled = false;
    document.querySelectorAll('#ratingRow button').forEach(function(b) { b.disabled = false; });
    showCard(currentIdx);
  }

  document.getElementById('btnAgain').onclick = function() { rate(0); };
  document.getElementById('btnHard').onclick = function() { rate(2); };
  document.getElementById('btnGood').onclick = function() { rate(4); };
  document.getElementById('btnEasy').onclick = function() { rate(5); };

  function showSessionStats() {
    document.getElementById('statTotal').textContent = sessionStats.total;
    document.getElementById('statCorrect').textContent = sessionStats.correct;
    cardArea.style.display = 'none';
    sessionStatsEl.style.display = 'block';
    document.getElementById('progressRow').style.display = 'none';
  }

  deckSelect.addEventListener('change', loadCards);

  // Read lesson param from URL — not required, just for context
  var params = new URLSearchParams(window.location.search);
  var lessonId = params.get('lesson');

  loadDecks();
  loadCards();
})();
"""

    return _page_shell(title="Flashcards", body=body, extra_script=script)


# ---------------------------------------------------------------------------
# 3. Quiz  /quiz
# ---------------------------------------------------------------------------

def get_quiz_html() -> str:
    """Quiz screen: per-question MCQ with timer, feedback, and end-of-quiz summary."""

    body = """
<div class="page">
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:6px">
    <div class="page-title">Quiz</div>
    <div style="display:flex;align-items:center;gap:10px">
      <div id="timerDisplay"
           style="font-size:14px;font-weight:700;color:var(--muted);
                  background:var(--brand-soft);color:var(--brand);
                  padding:5px 12px;border-radius:999px;display:none">
        ⏱ <span id="timerVal">30</span>s
      </div>
      <label style="display:flex;align-items:center;gap:6px;
                    font-size:12px;color:var(--muted);cursor:pointer">
        <input type="checkbox" id="chkTimer" checked
               style="accent-color:var(--brand)">
        Timer
      </label>
    </div>
  </div>

  <!-- Loading -->
  <div id="loadingState" style="text-align:center;padding:48px 20px">
    <div class="spinner" style="width:32px;height:32px;margin:0 auto 12px"></div>
    <div style="color:var(--muted)">Loading quiz…</div>
  </div>

  <!-- Error -->
  <div id="errorState" style="display:none">
    <div class="alert alert-error" id="quizError"></div>
    <button class="btn btn-primary" onclick="loadQuiz()">Retry</button>
  </div>

  <!-- Quiz body -->
  <div id="quizBody" style="display:none">
    <!-- Counter + progress -->
    <div style="display:flex;align-items:center;gap:10px;margin-bottom:14px">
      <span id="qCounter" style="font-size:13px;font-weight:700;
            color:var(--muted);white-space:nowrap"></span>
      <div class="progress-wrap" style="flex:1;margin:0">
        <div class="progress-fill" id="quizProgBar"></div>
      </div>
    </div>

    <div class="card">
      <div id="quizTitle" style="font-size:11px;font-weight:800;
           color:var(--muted);text-transform:uppercase;margin-bottom:10px"></div>
      <div id="questionText"
           style="font-size:17px;font-weight:700;line-height:1.5;
                  margin-bottom:20px"></div>

      <div id="optionsGrid" style="display:grid;gap:10px">
        <!-- option buttons injected by JS -->
      </div>

      <!-- Feedback -->
      <div id="feedback" style="display:none;margin-top:16px">
        <div id="feedbackBadge"
             style="font-size:15px;font-weight:800;margin-bottom:8px"></div>
        <div id="explanationText"
             style="font-size:13px;line-height:1.6;color:var(--muted)"></div>
        <button id="nextBtn" class="btn btn-primary"
                style="margin-top:14px">Next question →</button>
      </div>
    </div>
  </div>

  <!-- End screen -->
  <div id="endScreen" style="display:none" class="card">
    <div style="text-align:center;padding:20px 0">
      <div style="font-size:48px;margin-bottom:12px" id="endEmoji">🎓</div>
      <div style="font-size:28px;font-weight:900;margin-bottom:6px">
        <span id="endScore">—</span>
      </div>
      <div style="color:var(--muted);font-size:14px;margin-bottom:8px">
        Time taken: <strong id="endTime">—</strong>
      </div>
      <div id="endMessage" style="color:var(--muted);font-size:13px;margin-bottom:20px"></div>
      <div style="display:flex;gap:10px;justify-content:center;flex-wrap:wrap">
        <button class="btn btn-primary" onclick="loadQuiz()">Try again</button>
        <button class="btn" id="backLessonBtn">← Back to Lesson</button>
      </div>
    </div>
    <!-- Per-question review -->
    <div id="reviewList" style="margin-top:20px;border-top:1px solid var(--line);
                                 padding-top:16px"></div>
  </div>
</div>
"""

    script = """
(function() {
  var params = new URLSearchParams(window.location.search);
  var lessonId = params.get('lesson') || '';
  var questions = [];
  var currentQ = 0;
  var answers = [];
  var startTime = 0;
  var timerInterval = null;
  var timerRemaining = 30;
  var timerEnabled = true;

  var TIMER_SECONDS = 30;

  document.getElementById('backLessonBtn').onclick = function() {
    if (lessonId) window.location.href = '/lessons/new';
    else window.location.href = '/home';
  };

  document.getElementById('chkTimer').addEventListener('change', function() {
    timerEnabled = this.checked;
    document.getElementById('timerDisplay').style.display = timerEnabled ? 'block' : 'none';
  });

  function showArea(id) {
    ['loadingState','errorState','quizBody','endScreen'].forEach(function(n) {
      var el = document.getElementById(n);
      if (el) el.style.display = n === id ? 'block' : 'none';
    });
  }

  window.loadQuiz = async function() {
    if (!lessonId) {
      document.getElementById('quizError').textContent = 'No lesson ID in URL. Go back and open a quiz from a lesson.';
      showArea('errorState');
      return;
    }
    showArea('loadingState');
    try {
      var r = await apiFetch('/lessons/' + encodeURIComponent(lessonId) + '/quiz');
      if (!r.ok) throw new Error('HTTP ' + r.status + ' — quiz not found for this lesson');
      var data = await r.json();
      questions = data.questions || [];
      if (!questions.length) throw new Error('Quiz has no questions');
      var titleEl = document.getElementById('quizTitle');
      titleEl.textContent = escapeHtml(data.title || 'Quiz');
      currentQ = 0;
      answers = [];
      startTime = Date.now();
      showArea('quizBody');
      showQuestion(0);
    } catch(e) {
      document.getElementById('quizError').textContent = e.message;
      showArea('errorState');
    }
  };

  function showQuestion(idx) {
    if (idx >= questions.length) { showEndScreen(); return; }
    var q = questions[idx];
    var total = questions.length;

    document.getElementById('qCounter').textContent = (idx+1) + ' of ' + total;
    document.getElementById('quizProgBar').style.width = (((idx+1)/total)*100) + '%';
    document.getElementById('questionText').textContent = q.question || '—';
    document.getElementById('feedback').style.display = 'none';

    var grid = document.getElementById('optionsGrid');
    grid.innerHTML = '';
    var opts = q.options || {};
    ['A','B','C','D'].forEach(function(key) {
      if (!opts[key]) return;
      var btn = document.createElement('button');
      btn.className = 'btn';
      btn.style.cssText = 'text-align:left;justify-content:flex-start;' +
        'padding:13px 14px;font-size:14px;width:100%;border-radius:8px;';
      btn.innerHTML = '<span style="font-weight:800;margin-right:10px;' +
        'color:var(--brand)">' + escapeHtml(key) + '</span>' + escapeHtml(opts[key]);
      btn.addEventListener('click', function() {
        if (timerInterval) { clearInterval(timerInterval); timerInterval = null; }
        selectAnswer(key, q, btn);
      });
      grid.appendChild(btn);
    });

    // Timer
    if (timerEnabled) {
      timerRemaining = TIMER_SECONDS;
      document.getElementById('timerDisplay').style.display = 'block';
      document.getElementById('timerVal').textContent = timerRemaining;
      document.getElementById('timerDisplay').style.background = 'var(--brand-soft)';
      if (timerInterval) clearInterval(timerInterval);
      timerInterval = setInterval(function() {
        timerRemaining--;
        document.getElementById('timerVal').textContent = timerRemaining;
        if (timerRemaining <= 10) {
          document.getElementById('timerDisplay').style.background = 'var(--red-soft)';
          document.getElementById('timerDisplay').style.color = 'var(--red)';
        }
        if (timerRemaining <= 0) {
          clearInterval(timerInterval); timerInterval = null;
          // Time's up — mark as wrong
          selectAnswer(null, q, null);
        }
      }, 1000);
    }
  }

  function selectAnswer(chosen, q, clickedBtn) {
    // Disable all option buttons
    var grid = document.getElementById('optionsGrid');
    grid.querySelectorAll('button').forEach(function(b) { b.disabled = true; });

    var correct = q.correct;
    var isCorrect = chosen === correct;
    answers.push({q:q, chosen:chosen, correct:correct, isCorrect:isCorrect});

    // Highlight correct and wrong
    grid.querySelectorAll('button').forEach(function(b) {
      var letter = b.querySelector('span') ? b.querySelector('span').textContent.trim() : '';
      if (letter === correct) {
        b.style.borderColor = 'var(--green)';
        b.style.background = 'var(--green-soft)';
      } else if (letter === chosen && !isCorrect) {
        b.style.borderColor = 'var(--red)';
        b.style.background = 'var(--red-soft)';
      }
    });

    // Show feedback
    var fb = document.getElementById('feedback');
    var badge = document.getElementById('feedbackBadge');
    badge.textContent = chosen === null ? "⏱ Time's up! The correct answer was " + correct
      : (isCorrect ? '✓ Correct!' : '✗ Incorrect. Correct answer: ' + correct);
    badge.style.color = isCorrect ? 'var(--green)' : 'var(--red)';
    document.getElementById('explanationText').textContent = q.explanation || '';
    fb.style.display = 'block';
  }

  document.getElementById('nextBtn').addEventListener('click', function() {
    currentQ++;
    showQuestion(currentQ);
  });

  function showEndScreen() {
    if (timerInterval) { clearInterval(timerInterval); timerInterval = null; }
    document.getElementById('timerDisplay').style.display = 'none';
    var elapsed = Math.round((Date.now() - startTime) / 1000);
    var mins = Math.floor(elapsed/60);
    var secs = elapsed % 60;
    var timeStr = (mins > 0 ? mins + 'm ' : '') + secs + 's';
    var correct = answers.filter(function(a) { return a.isCorrect; }).length;
    var total = answers.length;
    var pct = total > 0 ? Math.round((correct/total)*100) : 0;

    document.getElementById('endScore').textContent = correct + ' / ' + total + ' (' + pct + '%)';
    document.getElementById('endTime').textContent = timeStr;
    document.getElementById('endEmoji').textContent = pct >= 80 ? '🌟' : pct >= 50 ? '👍' : '📖';
    document.getElementById('endMessage').textContent =
      pct >= 80 ? 'Excellent work! You really know this topic.' :
      pct >= 50 ? 'Good effort. Review the questions you missed.' :
      'Keep practising — revisit the lesson and try again.';

    // Build review list
    var rl = document.getElementById('reviewList');
    rl.innerHTML = '<div style="font-weight:700;margin-bottom:12px">Review</div>';
    answers.forEach(function(a, i) {
      var div = document.createElement('div');
      div.style.cssText = 'padding:10px 0;border-bottom:1px solid var(--line);font-size:13px';
      var icon = a.isCorrect ? '✓' : (a.chosen === null ? '⏱' : '✗');
      var color = a.isCorrect ? 'var(--green)' : 'var(--red)';
      div.innerHTML = '<div style="display:flex;gap:8px;align-items:flex-start">' +
        '<span style="font-weight:800;color:' + color + ';flex-shrink:0">' + icon + '</span>' +
        '<div><div style="font-weight:600;margin-bottom:4px">' +
        escapeHtml(a.q.question) + '</div>' +
        (a.q.explanation ? '<div style="color:var(--muted)">' + escapeHtml(a.q.explanation) + '</div>' : '') +
        '</div></div>';
      rl.appendChild(div);
    });

    showArea('endScreen');
  }

  // Keyboard: 1/2/3/4 to select answer
  document.addEventListener('keydown', function(e) {
    if (document.getElementById('quizBody').style.display === 'none') return;
    if (document.getElementById('feedback').style.display !== 'none') {
      if (e.key === 'Enter') document.getElementById('nextBtn').click();
      return;
    }
    var map = {'1':'A','2':'B','3':'C','4':'D'};
    if (map[e.key]) {
      var grid = document.getElementById('optionsGrid');
      var buttons = grid.querySelectorAll('button:not([disabled])');
      buttons.forEach(function(b) {
        var letter = b.querySelector('span') ? b.querySelector('span').textContent.trim() : '';
        if (letter === map[e.key]) b.click();
      });
    }
  });

  loadQuiz();
})();
"""

    return _page_shell(title="Quiz", body=body, extra_script=script)


# ---------------------------------------------------------------------------
# 4. AI Tutor Chat  /chat
# ---------------------------------------------------------------------------

def get_chat_html() -> str:
    """AI Tutor chat screen with citation support and lesson context."""

    body = """
<div class="page" style="display:flex;flex-direction:column;height:calc(100vh - 52px);
                           padding:0;max-width:100%">
  <!-- Header bar -->
  <div style="background:var(--panel);border-bottom:1px solid var(--line);
              padding:12px 20px;display:flex;align-items:center;
              justify-content:space-between;flex-shrink:0">
    <div>
      <div style="font-weight:800;font-size:16px">AI Tutor</div>
      <div id="contextLabel" style="font-size:12px;color:var(--muted)">
        Ask me anything about your lesson
      </div>
    </div>
    <a id="backLessonLink" href="/home" class="btn btn-sm">← Lesson</a>
  </div>

  <!-- Welcome message (shown initially) -->
  <div id="welcomeMsg"
       style="padding:20px;background:var(--brand-soft);
              border-bottom:1px solid var(--line);flex-shrink:0">
    <div style="display:flex;gap:12px;align-items:flex-start;max-width:720px;margin:0 auto">
      <div style="width:36px;height:36px;border-radius:50%;
                  background:var(--brand);color:#fff;
                  display:grid;place-items:center;font-size:18px;flex-shrink:0">🤖</div>
      <div>
        <div style="font-weight:700;margin-bottom:4px">
          Hi! I'm your AI Tutor for this lesson.
        </div>
        <div style="font-size:13px;color:var(--muted);line-height:1.6">
          I can explain concepts, answer follow-up questions, and show you exactly
          where the answer appears in the video. Ask me anything — I'll cite
          the relevant scenes so you can jump right to them.
        </div>
      </div>
    </div>
  </div>

  <!-- Messages area -->
  <div id="messages"
       style="flex:1;overflow-y:auto;padding:16px 20px;
              display:flex;flex-direction:column;gap:14px">
  </div>

  <!-- Error banner -->
  <div id="chatError" class="alert alert-error"
       style="display:none;margin:0 12px;flex-shrink:0"></div>

  <!-- Input area -->
  <div style="background:var(--panel);border-top:1px solid var(--line);
              padding:12px 16px;flex-shrink:0">
    <div style="display:flex;gap:10px;max-width:720px;margin:0 auto">
      <input id="chatInput" type="text" placeholder="Ask about this lesson…"
             style="flex:1;border:1px solid var(--line);border-radius:8px;
                    padding:12px 14px;outline:none;font-size:14px"
             autocomplete="off">
      <button id="sendBtn" class="btn btn-primary"
              style="flex-shrink:0;padding:12px 18px">Send</button>
    </div>
    <div style="text-align:center;margin-top:6px;font-size:11px;color:var(--muted)">
      AI may make mistakes — always verify with your textbook.
    </div>
  </div>
</div>

<style>
  .msg-row { display:flex; align-items:flex-end; gap:10px; }
  .msg-row.user { flex-direction:row-reverse; }
  .msg-avatar { width:30px;height:30px;border-radius:50%;
    display:grid;place-items:center;font-size:14px;flex-shrink:0; }
  .msg-bubble { max-width:75%;padding:12px 14px;border-radius:12px;
    font-size:14px;line-height:1.6;word-break:break-word; }
  .msg-row.user .msg-bubble {
    background:var(--brand);color:#fff;border-radius:12px 12px 4px 12px; }
  .msg-row.ai .msg-bubble {
    background:var(--panel);border:1px solid var(--line);
    border-radius:12px 12px 12px 4px; }
  .citations-toggle { margin-top:8px;font-size:12px;color:var(--brand);
    cursor:pointer;font-weight:700;background:none;border:none;
    padding:4px 0;text-decoration:underline; }
  .citations-body { margin-top:8px;font-size:12px;line-height:1.5;
    background:var(--bg);border-radius:6px;padding:10px 12px; }
  .citation-item { margin-bottom:6px; }
  .citation-item:last-child { margin-bottom:0; }
  .citation-scene { font-weight:700;color:var(--brand);margin-right:6px; }
  @media(max-width:600px) {
    .msg-bubble { max-width:88%; font-size:13px; }
  }
</style>
"""

    script = """
(function() {
  var params = new URLSearchParams(window.location.search);
  var lessonId = params.get('lesson') || '';
  var isSending = false;

  if (lessonId) {
    document.getElementById('contextLabel').textContent =
      'Lesson: ' + escapeHtml(lessonId);
    document.getElementById('backLessonLink').href = '/lessons/new';
  }

  var messagesEl = document.getElementById('messages');
  var chatInput = document.getElementById('chatInput');
  var sendBtn = document.getElementById('sendBtn');
  var chatError = document.getElementById('chatError');

  function appendMessage(role, html) {
    var row = document.createElement('div');
    row.className = 'msg-row ' + role;

    var avatar = document.createElement('div');
    avatar.className = 'msg-avatar';
    avatar.style.background = role === 'user' ? 'var(--brand)' : '#e8f0fe';
    avatar.textContent = role === 'user' ? '👤' : '🤖';

    var bubble = document.createElement('div');
    bubble.className = 'msg-bubble';
    bubble.innerHTML = html;

    row.appendChild(avatar);
    row.appendChild(bubble);
    messagesEl.appendChild(row);
    messagesEl.scrollTop = messagesEl.scrollHeight;
    return row;
  }

  function appendTyping() {
    var row = appendMessage('ai',
      '<span class="typing-dots">' +
      '<span style="animation:blink 1.2s infinite">●</span>' +
      '<span style="animation:blink 1.2s .4s infinite">●</span>' +
      '<span style="animation:blink 1.2s .8s infinite">●</span>' +
      '</span>');
    return row;
  }

  function buildAiHtml(answer, citations) {
    var html = '<div>' + escapeHtml(answer).replace(/\\n/g,'<br>') + '</div>';
    if (citations && citations.length) {
      var cId = 'cit-' + Date.now();
      html += '<button class="citations-toggle" onclick="' +
        'var el=document.getElementById(\\'' + cId + '\\');' +
        'var b=this;' +
        'el.style.display=el.style.display==\\'none\\'?\\'block\\':\\'none\\';' +
        'b.textContent=el.style.display==\\'none\\'?\\'▶ Sources ('+citations.length+')\\':' +
        '\\'▼ Sources ('+citations.length+')\\';">▶ Sources (' + citations.length + ')</button>';
      html += '<div id="' + cId + '" class="citations-body" style="display:none">';
      citations.forEach(function(c) {
        html += '<div class="citation-item"><span class="citation-scene">Scene ' +
          escapeHtml(String(c.scene_number || '?')) + '</span>';
        var bullets = c.bullets || [];
        if (bullets.length) {
          html += '<ul style="margin:4px 0 0 16px;padding:0">';
          bullets.forEach(function(b) {
            html += '<li>' + escapeHtml(b) + '</li>';
          });
          html += '</ul>';
        }
        html += '</div>';
      });
      html += '</div>';
    }
    return html;
  }

  async function sendMessage() {
    var text = chatInput.value.trim();
    if (!text || isSending) return;
    chatError.style.display = 'none';
    isSending = true;
    sendBtn.disabled = true;
    chatInput.disabled = true;
    document.getElementById('welcomeMsg').style.display = 'none';

    appendMessage('user', escapeHtml(text).replace(/\\n/g,'<br>'));
    chatInput.value = '';

    var typingRow = appendTyping();

    var endpoint = lessonId
      ? '/chat/' + encodeURIComponent(lessonId)
      : '/chat/general';

    try {
      var r = await apiFetch(endpoint, {
        method:'POST',
        headers:{'Content-Type':'application/x-www-form-urlencoded'},
        body:'question=' + encodeURIComponent(text)
      });
      typingRow.remove();
      if (!r.ok) throw new Error('HTTP ' + r.status);
      var d = await r.json();
      appendMessage('ai', buildAiHtml(d.answer || '(no answer)', d.citations || []));
    } catch(e) {
      typingRow.remove();
      chatError.textContent = 'Failed to get a response. Please try again.';
      chatError.style.display = 'block';
    }
    isSending = false;
    sendBtn.disabled = false;
    chatInput.disabled = false;
    chatInput.focus();
  }

  sendBtn.addEventListener('click', sendMessage);
  chatInput.addEventListener('keydown', function(e) {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
  });
})();
"""

    extra_head = """
<style>
@keyframes blink { 0%,80%,100%{opacity:.1} 40%{opacity:1} }
</style>
"""

    return _page_shell(title="AI Tutor", body=body, extra_head=extra_head, extra_script=script)


# ---------------------------------------------------------------------------
# 5. Profile  /profile
# ---------------------------------------------------------------------------

def get_profile_html() -> str:
    """User profile and preferences: language, level, mode, password change, tier."""

    body = """
<div class="page">
  <div class="page-title">Profile &amp; Preferences</div>
  <div class="page-sub">Manage your account and study preferences.</div>

  <div id="loadingState" style="text-align:center;padding:32px">
    <div class="spinner" style="width:28px;height:28px;margin:0 auto 10px"></div>
    <div style="color:var(--muted)">Loading profile…</div>
  </div>
  <div id="errorState" style="display:none">
    <div class="alert alert-error" id="profileLoadError"></div>
    <button class="btn btn-primary" onclick="loadProfile()">Retry</button>
  </div>

  <div id="profileContent" style="display:none">
    <!-- Account info (read-only) -->
    <div class="card" style="margin-bottom:14px">
      <div style="font-weight:800;font-size:15px;margin-bottom:14px">Account</div>
      <div style="display:flex;align-items:center;gap:14px;flex-wrap:wrap">
        <div style="width:52px;height:52px;border-radius:50%;
                    background:var(--brand);color:#fff;
                    display:grid;place-items:center;font-size:22px;flex-shrink:0">
          👤
        </div>
        <div>
          <div id="profileEmail"
               style="font-size:15px;font-weight:700;margin-bottom:4px">—</div>
          <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
            <span class="tag tag-brand" id="tierTag">—</span>
            <a href="#" id="upgradeLink" style="font-size:12px;font-weight:700">
              Upgrade plan →
            </a>
          </div>
        </div>
      </div>
    </div>

    <!-- Preferences form -->
    <div class="card" style="margin-bottom:14px">
      <div style="font-weight:800;font-size:15px;margin-bottom:14px">Study Preferences</div>
      <div id="prefSuccess" class="alert alert-success" style="display:none"></div>
      <div id="prefError" class="alert alert-error" style="display:none"></div>
      <form id="prefForm" autocomplete="off">
        <div class="field">
          <label for="prefName">Display name</label>
          <input type="text" id="prefName" name="display_name"
                 placeholder="Your name" maxlength="80">
        </div>
        <div class="row-2">
          <div class="field">
            <label for="prefLang">Preferred language</label>
            <select id="prefLang" name="language">
              <option value="en">English</option>
              <option value="hi">Hindi</option>
              <option value="ta">Tamil</option>
              <option value="te">Telugu</option>
              <option value="kn">Kannada</option>
              <option value="mr">Marathi</option>
              <option value="bn">Bengali</option>
              <option value="gu">Gujarati</option>
              <option value="pa">Punjabi</option>
              <option value="ml">Malayalam</option>
            </select>
          </div>
          <div class="field">
            <label for="prefLevel">Preferred level</label>
            <select id="prefLevel" name="level">
              <option value="kg">Kindergarten (KG)</option>
              <option value="primary">Primary (Class 1–5)</option>
              <option value="middle">Middle School (Class 6–8)</option>
              <option value="secondary">Secondary (Class 9–12)</option>
              <option value="neet_jee">NEET / JEE Entrance</option>
            </select>
          </div>
        </div>
        <div class="field">
          <label for="prefMode">Default video mode</label>
          <select id="prefMode" name="video_mode">
            <option value="teaching">Teaching (Full Lesson)</option>
            <option value="explainer">Explainer (Concept Overview)</option>
            <option value="revision">Revision (Quick Recap)</option>
            <option value="kids">Kids (Fun &amp; Simple)</option>
            <option value="reel">Reel (60-second Clip)</option>
          </select>
        </div>
        <button type="submit" class="btn btn-primary" id="savePrefBtn">
          Save preferences
        </button>
      </form>
    </div>

    <!-- Password change -->
    <div class="card">
      <div style="font-weight:800;font-size:15px;margin-bottom:14px">Change Password</div>
      <div id="pwSuccess" class="alert alert-success" style="display:none"></div>
      <div id="pwError" class="alert alert-error" style="display:none"></div>
      <form id="pwForm" autocomplete="off">
        <div class="field">
          <label for="oldPw">Current password</label>
          <input type="password" id="oldPw" name="old_password"
                 autocomplete="current-password" required>
        </div>
        <div class="row-2">
          <div class="field">
            <label for="newPw">New password</label>
            <input type="password" id="newPw" name="new_password"
                   autocomplete="new-password" minlength="8" required>
            <div class="hint">Minimum 8 characters</div>
          </div>
          <div class="field">
            <label for="confirmPw">Confirm new password</label>
            <input type="password" id="confirmPw" name="confirm_password"
                   autocomplete="new-password" required>
          </div>
        </div>
        <button type="submit" class="btn btn-primary" id="changePwBtn">
          Change password
        </button>
      </form>
    </div>
  </div>
</div>
"""

    script = """
(function() {
  var profileContent = document.getElementById('profileContent');
  var loadingState = document.getElementById('loadingState');
  var errorState = document.getElementById('errorState');

  function showArea(id) {
    [loadingState, errorState, profileContent].forEach(function(el) {
      if (el) el.style.display = 'none';
    });
    var target = {
      'loading': loadingState,
      'error': errorState,
      'profile': profileContent
    }[id];
    if (target) target.style.display = 'block';
  }

  window.loadProfile = async function() {
    showArea('loading');
    try {
      var r = await apiFetch('/auth/me');
      if (!r.ok) throw new Error('HTTP ' + r.status);
      var d = await r.json();
      populateProfile(d);
      showArea('profile');
    } catch(e) {
      document.getElementById('profileLoadError').textContent =
        'Failed to load profile: ' + e.message;
      showArea('error');
    }
  };

  function populateProfile(d) {
    document.getElementById('profileEmail').textContent = d.email || '—';
    document.getElementById('navEmail').textContent = d.email || '—';

    var tier = d.tier || 'free';
    var tierTag = document.getElementById('tierTag');
    tierTag.textContent = tier.charAt(0).toUpperCase() + tier.slice(1);
    tierTag.className = 'tag ' + (tier === 'pro' ? 'tag-violet' : tier === 'school' ? 'tag-green' : 'tag-brand');

    if (d.display_name) document.getElementById('prefName').value = d.display_name;
    if (d.language) document.getElementById('prefLang').value = d.language;
    if (d.level) document.getElementById('prefLevel').value = d.level;
    if (d.video_mode) document.getElementById('prefMode').value = d.video_mode;
  }

  // Preferences form
  document.getElementById('prefForm').addEventListener('submit', async function(e) {
    e.preventDefault();
    var btn = document.getElementById('savePrefBtn');
    var successEl = document.getElementById('prefSuccess');
    var errorEl = document.getElementById('prefError');
    successEl.style.display = 'none';
    errorEl.style.display = 'none';
    btn.disabled = true;
    btn.textContent = '…';

    var body = {
      display_name: document.getElementById('prefName').value.trim(),
      language: document.getElementById('prefLang').value,
      level: document.getElementById('prefLevel').value,
      video_mode: document.getElementById('prefMode').value
    };

    try {
      var r = await apiFetch('/api/me/profile', {
        method:'PUT',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify(body)
      });
      if (!r.ok) {
        var d = await r.json().catch(function(){return{};});
        throw new Error(d.detail || 'HTTP ' + r.status);
      }
      successEl.textContent = 'Preferences saved!';
      successEl.style.display = 'block';
    } catch(err) {
      errorEl.textContent = 'Failed to save: ' + err.message;
      errorEl.style.display = 'block';
    }
    btn.disabled = false;
    btn.textContent = 'Save preferences';
  });

  // Password change form
  document.getElementById('pwForm').addEventListener('submit', async function(e) {
    e.preventDefault();
    var btn = document.getElementById('changePwBtn');
    var successEl = document.getElementById('pwSuccess');
    var errorEl = document.getElementById('pwError');
    successEl.style.display = 'none';
    errorEl.style.display = 'none';

    var newPw = document.getElementById('newPw').value;
    var confirmPw = document.getElementById('confirmPw').value;
    if (newPw !== confirmPw) {
      errorEl.textContent = 'New password and confirmation do not match.';
      errorEl.style.display = 'block';
      return;
    }
    if (newPw.length < 8) {
      errorEl.textContent = 'Password must be at least 8 characters.';
      errorEl.style.display = 'block';
      return;
    }

    btn.disabled = true;
    btn.textContent = '…';
    try {
      var r = await apiFetch('/auth/change-password', {
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({
          old_password: document.getElementById('oldPw').value,
          new_password: newPw
        })
      });
      if (!r.ok) {
        var d = await r.json().catch(function(){return{};});
        throw new Error(d.detail || 'HTTP ' + r.status);
      }
      successEl.textContent = 'Password changed successfully.';
      successEl.style.display = 'block';
      document.getElementById('pwForm').reset();
    } catch(err) {
      errorEl.textContent = 'Failed: ' + err.message;
      errorEl.style.display = 'block';
    }
    btn.disabled = false;
    btn.textContent = 'Change password';
  });

  // Escape closes any focused modal area (no modals on this page, but good UX)
  document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape') {
      document.getElementById('prefSuccess').style.display = 'none';
      document.getElementById('prefError').style.display = 'none';
      document.getElementById('pwSuccess').style.display = 'none';
      document.getElementById('pwError').style.display = 'none';
    }
  });

  loadProfile();
})();
"""

    return _page_shell(title="Profile", body=body, extra_script=script)


# ---------------------------------------------------------------------------
# 6. Teacher Home  /teacher
# ---------------------------------------------------------------------------

def get_teacher_html() -> str:
    """Teacher home: classes, assignments, doubt queue, and reports tabs."""

    body = """
<div class="page-wide">
  <div class="page-title">Teacher Dashboard</div>
  <div class="page-sub">Manage your classes, assignments, and student doubts.</div>

  <div id="loadError" class="alert alert-error" style="display:none"></div>

  <div class="tabs">
    <button class="tab-btn active" data-tab="classes">My Classes</button>
    <button class="tab-btn" data-tab="assignments">Assignments</button>
    <button class="tab-btn" data-tab="doubts">Doubt Queue</button>
    <button class="tab-btn" data-tab="reports">Reports</button>
  </div>

  <!-- My Classes tab -->
  <div id="tab-classes" class="tab-pane active">
    <div style="display:flex;align-items:center;justify-content:space-between;
                margin-bottom:14px">
      <div style="font-weight:700">Classes</div>
      <button class="btn btn-primary btn-sm" id="newClassBtn">+ New class</button>
    </div>
    <div id="classesList">
      <div style="text-align:center;padding:32px">
        <div class="spinner" style="width:24px;height:24px;margin:0 auto 10px"></div>
        <div style="color:var(--muted);font-size:13px">Loading classes…</div>
      </div>
    </div>
  </div>

  <!-- Assignments tab -->
  <div id="tab-assignments" class="tab-pane">
    <div style="display:flex;align-items:center;justify-content:space-between;
                margin-bottom:14px">
      <div style="font-weight:700">Assignments</div>
      <button class="btn btn-primary btn-sm" id="newAssignBtn">+ New assignment</button>
    </div>
    <div id="assignmentsList">
      <div style="text-align:center;padding:32px">
        <div class="spinner" style="width:24px;height:24px;margin:0 auto 10px"></div>
        <div style="color:var(--muted);font-size:13px">Loading assignments…</div>
      </div>
    </div>
  </div>

  <!-- Doubt Queue tab -->
  <div id="tab-doubts" class="tab-pane">
    <div style="font-weight:700;margin-bottom:14px">Doubt Queue</div>
    <div id="doubtsList">
      <div style="text-align:center;padding:32px">
        <div class="spinner" style="width:24px;height:24px;margin:0 auto 10px"></div>
        <div style="color:var(--muted);font-size:13px">Loading doubts…</div>
      </div>
    </div>
  </div>

  <!-- Reports tab -->
  <div id="tab-reports" class="tab-pane">
    <div class="empty-state">
      <div class="icon">📊</div>
      <h3>Reports</h3>
      <p>Detailed analytics and reports are coming soon.<br>
         You'll be able to track class performance, engagement, and learning outcomes here.</p>
    </div>
  </div>
</div>

<!-- New class modal -->
<div class="modal-backdrop" id="newClassModal">
  <div class="modal">
    <button class="modal-close" id="closeClassModal">×</button>
    <h2>New Class</h2>
    <div id="newClassError" class="alert alert-error" style="display:none"></div>
    <form id="newClassForm" autocomplete="off">
      <div class="field">
        <label for="className">Class name</label>
        <input type="text" id="className" name="name"
               placeholder="e.g. Class 9 — Science A" required maxlength="120">
      </div>
      <div class="row-2">
        <div class="field">
          <label for="classSection">Section (optional)</label>
          <input type="text" id="classSection" name="section"
                 placeholder="e.g. A" maxlength="20">
        </div>
        <div class="field">
          <label for="classSubject">Subject (optional)</label>
          <input type="text" id="classSubject" name="subject"
                 placeholder="e.g. Physics" maxlength="60">
        </div>
      </div>
      <button type="submit" class="btn btn-primary btn-block" id="createClassBtn">
        Create class
      </button>
    </form>
  </div>
</div>

<!-- New assignment modal -->
<div class="modal-backdrop" id="newAssignModal">
  <div class="modal">
    <button class="modal-close" id="closeAssignModal">×</button>
    <h2>New Assignment</h2>
    <div id="newAssignError" class="alert alert-error" style="display:none"></div>
    <form id="newAssignForm" autocomplete="off">
      <div class="field">
        <label for="assignTitle">Title</label>
        <input type="text" id="assignTitle" name="title"
               placeholder="e.g. Chapter 5 quiz" required maxlength="160">
      </div>
      <div class="field">
        <label for="assignTopic">Topic</label>
        <input type="text" id="assignTopic" name="topic"
               placeholder="e.g. Trigonometry — identities" required maxlength="200">
      </div>
      <div class="field">
        <label for="assignClass">Class</label>
        <select id="assignClass" name="class_id" required>
          <option value="">— select class —</option>
        </select>
      </div>
      <div class="field">
        <label for="assignDue">Due date</label>
        <input type="datetime-local" id="assignDue" name="due_at">
      </div>
      <div class="field">
        <label for="assignDesc">Description (optional)</label>
        <textarea id="assignDesc" name="description"
                  placeholder="Instructions for students…" rows="3"></textarea>
      </div>
      <button type="submit" class="btn btn-primary btn-block" id="createAssignBtn">
        Create assignment
      </button>
    </form>
  </div>
</div>
"""

    script = """
(function() {
  var orgId = null;
  var classesCache = [];

  // Tab switching
  document.querySelectorAll('.tab-btn').forEach(function(btn) {
    btn.addEventListener('click', function() {
      var tab = this.dataset.tab;
      document.querySelectorAll('.tab-btn').forEach(function(b) {
        b.classList.remove('active');
      });
      document.querySelectorAll('.tab-pane').forEach(function(p) {
        p.classList.remove('active');
      });
      this.classList.add('active');
      var pane = document.getElementById('tab-' + tab);
      if (pane) pane.classList.add('active');
      if (tab === 'doubts' && !document.getElementById('doubtsList').dataset.loaded) {
        loadDoubts();
      }
    });
  });

  // Modals
  function openModal(id) {
    var m = document.getElementById(id);
    if (m) { m.classList.add('open'); }
  }
  function closeModal(id) {
    var m = document.getElementById(id);
    if (m) { m.classList.remove('open'); }
  }
  document.getElementById('newClassBtn').onclick = function() { openModal('newClassModal'); };
  document.getElementById('closeClassModal').onclick = function() { closeModal('newClassModal'); };
  document.getElementById('newAssignBtn').onclick = function() {
    populateClassSelect();
    openModal('newAssignModal');
  };
  document.getElementById('closeAssignModal').onclick = function() { closeModal('newAssignModal'); };

  document.querySelectorAll('.modal-backdrop').forEach(function(bd) {
    bd.addEventListener('click', function(e) {
      if (e.target === bd) bd.classList.remove('open');
    });
  });
  document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape') {
      document.querySelectorAll('.modal-backdrop.open').forEach(function(m) {
        m.classList.remove('open');
      });
    }
  });

  async function loadOrg() {
    try {
      var r = await apiFetch('/api/orgs/me');
      if (!r.ok) throw new Error('HTTP ' + r.status);
      var d = await r.json();
      // /api/orgs/me returns {orgs:[...]}
      var orgs = d.orgs || d.rows || (Array.isArray(d) ? d : []);
      var org = orgs[0];
      if (org && org.id) {
        orgId = org.id;
        return org;
      }
    } catch(e) {}
    return null;
  }

  async function loadClasses() {
    if (!orgId) return;
    try {
      var r = await apiFetch('/api/orgs/' + encodeURIComponent(orgId) + '/classes');
      if (!r.ok) throw new Error('HTTP ' + r.status);
      var cd = await r.json();
      var classes = cd.classes || cd.rows || (Array.isArray(cd) ? cd : []);
      classesCache = classes;
      renderClasses(classes);
    } catch(e) {
      document.getElementById('classesList').innerHTML =
        '<div class="alert alert-error">Failed to load classes: ' + escapeHtml(e.message) + '</div>';
    }
  }

  function renderClasses(classes) {
    var el = document.getElementById('classesList');
    if (!classes.length) {
      el.innerHTML = '<div class="empty-state"><div class="icon">🏫</div>' +
        '<h3>No classes yet</h3><p>Create your first class to get started.</p></div>';
      return;
    }
    var html = '<div style="display:grid;gap:10px">';
    classes.forEach(function(c) {
      html += '<div class="card" style="display:flex;align-items:center;' +
        'justify-content:space-between;gap:12px;padding:14px 16px">' +
        '<div>' +
        '<div style="font-weight:700;font-size:15px">' + escapeHtml(c.name || '—') + '</div>' +
        '<div style="color:var(--muted);font-size:12px;margin-top:3px">' +
        (c.subject ? escapeHtml(c.subject) + ' · ' : '') +
        '<span>' + (c.student_count || 0) + ' students</span></div>' +
        '</div>' +
        '<a href="/class/' + escapeHtml(String(c.id)) + '" class="btn btn-sm">View →</a>' +
        '</div>';
    });
    html += '</div>';
    el.innerHTML = html;
  }

  async function loadAssignments() {
    if (!orgId) return;
    try {
      var r = await apiFetch('/api/orgs/' + encodeURIComponent(orgId) + '/assignments');
      if (!r.ok) throw new Error('HTTP ' + r.status);
      var ad = await r.json();
      var assignments = ad.assignments || ad.rows || (Array.isArray(ad) ? ad : []);
      renderAssignments(assignments);
    } catch(e) {
      document.getElementById('assignmentsList').innerHTML =
        '<div class="alert alert-error">Failed to load assignments: ' + escapeHtml(e.message) + '</div>';
    }
  }

  function renderAssignments(assignments) {
    var el = document.getElementById('assignmentsList');
    if (!assignments.length) {
      el.innerHTML = '<div class="empty-state"><div class="icon">📋</div>' +
        '<h3>No assignments yet</h3><p>Create an assignment to send to a class.</p></div>';
      return;
    }
    var html = '<div style="display:grid;gap:10px">';
    assignments.forEach(function(a) {
      var due = a.due_at ? new Date(a.due_at).toLocaleDateString('en-IN', {
        day:'numeric',month:'short',year:'numeric'}) : 'No due date';
      html += '<div class="card" style="padding:14px 16px">' +
        '<div style="display:flex;align-items:flex-start;justify-content:space-between;gap:10px">' +
        '<div>' +
        '<div style="font-weight:700;font-size:14px">' + escapeHtml(a.title || '—') + '</div>' +
        '<div style="color:var(--muted);font-size:12px;margin-top:3px">' +
        'Due: ' + escapeHtml(due) +
        (a.class_name ? ' · ' + escapeHtml(a.class_name) : '') + '</div>' +
        '</div>' +
        '<span class="tag tag-brand">' + (a.submission_count || 0) + ' submitted</span>' +
        '</div></div>';
    });
    html += '</div>';
    el.innerHTML = html;
  }

  async function loadDoubts() {
    document.getElementById('doubtsList').dataset.loaded = '1';
    try {
      var r = await apiFetch('/api/doubts/queue/pending');
      var doubts = [];
      if (r.ok) { var d = await r.json(); doubts = d.rows || d.doubts || (Array.isArray(d) ? d : []); }
      renderDoubts(doubts);
    } catch(e) {
      renderDoubts([]);
    }
  }

  function renderDoubts(doubts) {
    var el = document.getElementById('doubtsList');
    if (!doubts.length) {
      el.innerHTML = '<div class="empty-state"><div class="icon">✅</div>' +
        '<h3>Doubt queue is clear</h3>' +
        '<p>No pending student doubts. Check back later.</p></div>';
      return;
    }
    var html = '<div style="display:grid;gap:10px">';
    doubts.forEach(function(d) {
      html += '<div class="card" style="padding:14px 16px">' +
        '<div style="display:flex;gap:12px;align-items:flex-start">' +
        (d.image_url ? '<img src="' + escapeHtml(d.image_url) +
          '" style="width:60px;height:60px;object-fit:cover;border-radius:6px;flex-shrink:0" alt="">' : '') +
        '<div style="flex:1">' +
        '<div style="font-weight:700;font-size:13px">' + escapeHtml(d.student_name || 'Student') + '</div>' +
        '<div style="font-size:13px;margin-top:4px;line-height:1.5">' + escapeHtml(d.question || '—') + '</div>' +
        '</div>' +
        '<button class="btn btn-sm btn-primary" onclick="respondDoubt(\'' + escapeHtml(d.id || '') + '\')">Respond</button>' +
        '</div></div>';
    });
    html += '</div>';
    el.innerHTML = html;
  }

  // Claim + answer a doubt from the queue (was a dead button).
  window.respondDoubt = async function(id) {
    if (!id) return;
    var text = window.prompt('Type your answer to this student doubt:');
    if (text === null) return;
    if (text.trim().length < 5) { alert('Answer must be at least 5 characters.'); return; }
    try {
      // Claim first (ignore 409 — may already be claimed by you); then answer.
      await apiFetch('/api/doubts/' + encodeURIComponent(id) + '/claim', { method: 'POST' });
      var fd = new FormData();
      fd.append('response_text', text.trim());
      fd.append('method', 'human');
      var r = await apiFetch('/api/doubts/' + encodeURIComponent(id) + '/answer', { method: 'POST', body: fd });
      if (!r.ok) {
        var d = await r.json().catch(function(){ return {}; });
        throw new Error(d.detail || ('HTTP ' + r.status));
      }
      loadDoubts();
    } catch(e) {
      alert('Could not send answer: ' + e.message);
    }
  };

  function populateClassSelect() {
    var sel = document.getElementById('assignClass');
    sel.innerHTML = '<option value="">— select class —</option>';
    classesCache.forEach(function(c) {
      var opt = document.createElement('option');
      opt.value = c.id;
      opt.textContent = escapeHtml(c.name || '—');
      sel.appendChild(opt);
    });
  }

  // New class form
  document.getElementById('newClassForm').addEventListener('submit', async function(e) {
    e.preventDefault();
    if (!orgId) return;
    var btn = document.getElementById('createClassBtn');
    var errEl = document.getElementById('newClassError');
    errEl.style.display = 'none';
    btn.disabled = true; btn.textContent = '…';
    // Endpoint takes Form fields: name (required), grade_level, section.
    var fd = new FormData();
    fd.append('name', document.getElementById('className').value.trim());
    var _sec = document.getElementById('classSection').value.trim();
    if (_sec) fd.append('section', _sec);
    var _sub = document.getElementById('classSubject').value.trim();
    if (_sub) fd.append('grade_level', _sub);
    try {
      var r = await apiFetch('/api/orgs/' + encodeURIComponent(orgId) + '/classes', {
        method:'POST',
        body: fd
      });
      if (!r.ok) {
        var d = await r.json().catch(function(){return{};});
        throw new Error(d.detail || 'HTTP ' + r.status);
      }
      closeModal('newClassModal');
      document.getElementById('newClassForm').reset();
      await loadClasses();
    } catch(err) {
      errEl.textContent = err.message;
      errEl.style.display = 'block';
    }
    btn.disabled = false; btn.textContent = 'Create class';
  });

  // New assignment form
  document.getElementById('newAssignForm').addEventListener('submit', async function(e) {
    e.preventDefault();
    if (!orgId) return;
    var btn = document.getElementById('createAssignBtn');
    var errEl = document.getElementById('newAssignError');
    errEl.style.display = 'none';
    btn.disabled = true; btn.textContent = '…';
    // Endpoint takes Form fields: class_id, title, topic (all required),
    // due_date, notes.
    var fd = new FormData();
    fd.append('class_id', document.getElementById('assignClass').value);
    fd.append('title', document.getElementById('assignTitle').value.trim());
    fd.append('topic', document.getElementById('assignTopic').value.trim());
    var _due = document.getElementById('assignDue').value;
    if (_due) fd.append('due_date', _due);
    var _notes = document.getElementById('assignDesc').value.trim();
    if (_notes) fd.append('notes', _notes);
    try {
      var r = await apiFetch('/api/orgs/' + encodeURIComponent(orgId) + '/assignments', {
        method:'POST',
        body: fd
      });
      if (!r.ok) {
        var d = await r.json().catch(function(){return{};});
        throw new Error(d.detail || 'HTTP ' + r.status);
      }
      closeModal('newAssignModal');
      document.getElementById('newAssignForm').reset();
      await loadAssignments();
    } catch(err) {
      errEl.textContent = err.message;
      errEl.style.display = 'block';
    }
    btn.disabled = false; btn.textContent = 'Create assignment';
  });

  // Enter submits focused form
  document.querySelectorAll('form').forEach(function(f) {
    f.addEventListener('keydown', function(e) {
      if (e.key === 'Enter' && e.target.tagName !== 'TEXTAREA' && e.target.tagName !== 'BUTTON') {
        var submitBtn = f.querySelector('button[type="submit"]');
        if (submitBtn) submitBtn.click();
      }
    });
  });

  // Bootstrap
  (async function() {
    await loadOrg();
    await Promise.all([loadClasses(), loadAssignments()]);
  })();
})();
"""

    return _page_shell(title="Teacher", body=body, extra_script=script)


# ---------------------------------------------------------------------------
# 7. Parent Portal  /parent
# ---------------------------------------------------------------------------

def get_parent_html() -> str:
    """Parent portal: children overview, stats, fee info, and notification settings."""

    body = """
<div class="page">
  <div class="page-title">Parent Portal</div>
  <div class="page-sub">Monitor your children's learning progress and manage account settings.</div>

  <div id="loadingState" style="text-align:center;padding:40px 20px">
    <div class="spinner" style="width:28px;height:28px;margin:0 auto 10px"></div>
    <div style="color:var(--muted)">Loading…</div>
  </div>
  <div id="errorState" style="display:none">
    <div class="alert alert-error" id="parentError"></div>
    <button class="btn btn-primary" onclick="loadParent()">Retry</button>
  </div>

  <div id="parentContent" style="display:none">
    <!-- Children cards + Link button -->
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
      <div style="font-weight:800;font-size:15px">Your Children</div>
      <button id="openLinkChildBtn" class="btn btn-primary btn-sm">+ Link a child</button>
    </div>
    <div id="childrenGrid" style="display:grid;gap:14px;margin-bottom:20px"></div>

    <!-- Link-child modal -->
    <div id="linkChildModal" style="display:none;position:fixed;inset:0;
         z-index:9999;background:rgba(0,0,0,.55);align-items:center;
         justify-content:center;padding:20px">
      <div style="background:#fff;max-width:440px;width:100%;
           border-radius:12px;padding:24px;box-shadow:0 10px 40px rgba(0,0,0,.25)">
        <h3 style="margin:0 0 6px;font-size:18px">Link your child</h3>
        <p style="margin:0 0 16px;color:var(--muted);font-size:13px">
          Enter your child's account email. They'll get a notification to
          confirm the link. Both accounts must already be signed up.
        </p>
        <form id="linkChildForm">
          <label style="display:block;font-weight:700;font-size:13px;margin-bottom:4px"
                 for="linkChildEmail">Child's email</label>
          <input id="linkChildEmail" type="email" required
                 placeholder="child@example.com"
                 style="width:100%;padding:9px 12px;border:1px solid var(--line);
                        border-radius:8px;font-size:14px;margin-bottom:12px">
          <label style="display:block;font-weight:700;font-size:13px;margin-bottom:4px"
                 for="linkChildRelation">Relationship (optional)</label>
          <input id="linkChildRelation" type="text"
                 placeholder="mother, father, guardian…"
                 style="width:100%;padding:9px 12px;border:1px solid var(--line);
                        border-radius:8px;font-size:14px;margin-bottom:14px">
          <div id="linkChildError" class="alert alert-error" style="display:none"></div>
          <div id="linkChildSuccess" class="alert alert-success" style="display:none"></div>
          <div style="display:flex;gap:8px;justify-content:flex-end">
            <button type="button" id="cancelLinkChildBtn" class="btn btn-sm">Cancel</button>
            <button type="submit" id="submitLinkChildBtn" class="btn btn-primary btn-sm">
              Send link request
            </button>
          </div>
        </form>
      </div>
    </div>

    <!-- Expanded child detail -->
    <div id="childDetail" style="display:none">
      <div style="display:flex;align-items:center;gap:10px;margin-bottom:14px">
        <button id="backToList" class="btn btn-sm">← All children</button>
        <span id="childDetailName" style="font-weight:800;font-size:16px"></span>
      </div>
      <div id="childStats">
        <div style="text-align:center;padding:24px">
          <div class="spinner" style="width:22px;height:22px;margin:0 auto 8px"></div>
          <div style="color:var(--muted);font-size:13px">Loading stats…</div>
        </div>
      </div>
    </div>

    <!-- Fees section -->
    <div class="card" style="margin-bottom:14px">
      <div style="font-weight:800;font-size:15px;margin-bottom:12px">Fees &amp; Subscription</div>
      <div id="feesContent">
        <div class="empty-state" style="padding:24px 0">
          <div class="icon">💳</div>
          <h3>No active school plan</h3>
          <p>Your child is on the individual plan. Contact your school to link to a school account.</p>
        </div>
      </div>
    </div>

    <!-- Notification preferences -->
    <div class="card">
      <div style="font-weight:800;font-size:15px;margin-bottom:12px">
        Notification Preferences
      </div>
      <div id="notifSuccess" class="alert alert-success" style="display:none"></div>
      <form id="notifForm">
        <div class="field" style="margin-bottom:10px">
          <label style="display:flex;align-items:center;gap:9px;cursor:pointer;font-weight:700;font-size:13px">
            <input type="checkbox" id="chkWhatsapp"
                   style="width:16px;height:16px;accent-color:var(--brand)">
            WhatsApp notifications
            <span class="tag tag-green" style="font-size:10px">Recommended</span>
          </label>
          <div class="hint">Weekly progress summary + low-streak alerts via WhatsApp</div>
        </div>
        <div class="field" style="margin-bottom:14px">
          <label style="display:flex;align-items:center;gap:9px;cursor:pointer;font-weight:700;font-size:13px">
            <input type="checkbox" id="chkSms"
                   style="width:16px;height:16px;accent-color:var(--brand)">
            SMS notifications
          </label>
          <div class="hint">Critical alerts only (exam reminders)</div>
        </div>
        <button type="submit" class="btn btn-primary btn-sm" id="saveNotifBtn">
          Save notification settings
        </button>
      </form>
    </div>
  </div>
</div>
"""

    script = """
(function() {
  var children = [];

  function showArea(id) {
    ['loadingState','errorState','parentContent'].forEach(function(n) {
      var el = document.getElementById(n);
      if (el) el.style.display = 'none';
    });
    var el = document.getElementById(id);
    if (el) el.style.display = 'block';
  }

  window.loadParent = async function() {
    showArea('loadingState');
    try {
      var r = await apiFetch('/api/parents/children');
      if (!r.ok) throw new Error('HTTP ' + r.status);
      children = await r.json();
      renderChildren(children);
      await loadFees();
      showArea('parentContent');
    } catch(e) {
      document.getElementById('parentError').textContent =
        'Failed to load data: ' + e.message;
      showArea('errorState');
    }
  };

  function renderChildren(list) {
    var grid = document.getElementById('childrenGrid');
    if (!list.length) {
      grid.innerHTML = '<div class="empty-state"><div class="icon">👶</div>' +
        '<h3>No children linked</h3>' +
        "<p>Use the <strong>+ Link a child</strong> button above to send a link request to your child's account, or ask your school admin if your child is enrolled through a school.</p></div>";
      return;
    }
    grid.innerHTML = '';
    list.forEach(function(child) {
      var card = document.createElement('div');
      card.className = 'card';
      card.style.cssText = 'cursor:pointer;transition:border-color .15s';
      card.setAttribute('tabindex','0');
      card.setAttribute('role','button');
      card.setAttribute('aria-label','View details for ' + escapeHtml(child.name || 'child'));

      var streakColor = (child.streak || 0) >= 7 ? 'var(--green)' :
                        (child.streak || 0) >= 3 ? 'var(--amber)' : 'var(--muted)';
      card.innerHTML =
        '<div style="display:flex;align-items:center;gap:14px;margin-bottom:14px">' +
        '<div style="width:44px;height:44px;border-radius:50%;' +
          'background:var(--brand-soft);color:var(--brand);' +
          'display:grid;place-items:center;font-size:20px;flex-shrink:0">👤</div>' +
        '<div>' +
          '<div style="font-weight:800;font-size:15px">' + escapeHtml(child.name || '—') + '</div>' +
          '<div style="color:var(--muted);font-size:12px">' +
            escapeHtml(child.exam_target || 'No exam target') + '</div>' +
        '</div>' +
        '</div>' +
        '<div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px">' +
        '<div style="text-align:center">' +
          '<div style="font-size:22px;font-weight:900;color:' + streakColor + '">' +
            (child.streak || 0) + '</div>' +
          '<div style="font-size:11px;color:var(--muted)">Day streak</div></div>' +
        '<div style="text-align:center">' +
          '<div style="font-size:22px;font-weight:900">' +
            formatTime(child.today_minutes || 0) + '</div>' +
          '<div style="font-size:11px;color:var(--muted)">Today</div></div>' +
        '<div style="text-align:center">' +
          '<div style="font-size:22px;font-weight:900;color:var(--brand)">' +
            (child.readiness_pct || 0) + '%</div>' +
          '<div style="font-size:11px;color:var(--muted)">Readiness</div></div>' +
        '</div>' +
        '<div style="margin-top:14px">' +
          '<div class="progress-wrap" style="margin:0">' +
            '<div class="progress-fill" style="width:' + (child.readiness_pct||0) + '%"></div>' +
          '</div>' +
        '</div>';

      card.addEventListener('click', function() { openChildDetail(child); });
      card.addEventListener('keydown', function(e) {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openChildDetail(child); }
      });
      card.addEventListener('mouseenter', function() {
        this.style.borderColor = 'var(--brand)';
      });
      card.addEventListener('mouseleave', function() {
        this.style.borderColor = 'var(--line)';
      });
      grid.appendChild(card);
    });
  }

  function formatTime(minutes) {
    if (minutes < 60) return minutes + 'm';
    return Math.floor(minutes/60) + 'h ' + (minutes%60) + 'm';
  }

  async function openChildDetail(child) {
    document.getElementById('childrenGrid').style.display = 'none';
    var detail = document.getElementById('childDetail');
    detail.style.display = 'block';
    document.getElementById('childDetailName').textContent = escapeHtml(child.name || '—');

    var statsEl = document.getElementById('childStats');
    statsEl.innerHTML = '<div style="text-align:center;padding:24px">' +
      '<div class="spinner" style="width:22px;height:22px;margin:0 auto 8px"></div>' +
      '<div style="color:var(--muted);font-size:13px">Loading stats…</div></div>';

    try {
      var r = await apiFetch('/api/parents/children/' + encodeURIComponent(child.id) + '/stats');
      if (!r.ok) throw new Error('HTTP ' + r.status);
      var stats = await r.json();
      renderChildStats(stats, child);
    } catch(e) {
      statsEl.innerHTML = '<div class="alert alert-error">Could not load stats: ' +
        escapeHtml(e.message) + '</div>';
    }
  }

  function renderChildStats(stats, child) {
    var el = document.getElementById('childStats');
    var weakSubjects = stats.weak_subjects || [];
    var recentActivity = stats.recent_activity || [];

    var html = '<div style="display:grid;gap:14px">';

    // Weak subjects
    html += '<div class="card"><div style="font-weight:700;margin-bottom:10px">Weak Subjects</div>';
    if (!weakSubjects.length) {
      html += '<div style="color:var(--muted);font-size:13px">No weak subjects identified yet.</div>';
    } else {
      weakSubjects.forEach(function(s) {
        html += '<div style="display:flex;align-items:center;gap:10px;margin-bottom:8px">' +
          '<div style="flex:1;font-size:13px;font-weight:700">' + escapeHtml(s.subject || s) + '</div>' +
          '<div class="progress-wrap" style="flex:2;margin:0">' +
            '<div class="progress-fill" style="width:' + (s.mastery||30) + '%;' +
              'background:' + ((s.mastery||30) < 40 ? 'var(--red)' : 'var(--amber)') + '"></div>' +
          '</div>' +
          '<div style="font-size:12px;color:var(--muted);white-space:nowrap">' +
            (s.mastery||30) + '% mastery</div>' +
          '</div>';
      });
    }
    html += '</div>';

    // Recent activity
    html += '<div class="card"><div style="font-weight:700;margin-bottom:10px">Recent Activity</div>';
    if (!recentActivity.length) {
      html += '<div style="color:var(--muted);font-size:13px">No recent activity recorded.</div>';
    } else {
      recentActivity.slice(0,5).forEach(function(a) {
        var date = a.at ? new Date(a.at).toLocaleDateString('en-IN',{
          day:'numeric',month:'short'}) : '';
        html += '<div style="display:flex;gap:10px;padding:8px 0;' +
          'border-bottom:1px solid var(--line);font-size:13px">' +
          '<span style="color:var(--muted);white-space:nowrap;min-width:60px">' + escapeHtml(date) + '</span>' +
          '<span>' + escapeHtml(a.description || a.type || '—') + '</span>' +
          '</div>';
      });
    }
    html += '</div></div>';

    el.innerHTML = html;
  }

  document.getElementById('backToList').addEventListener('click', function() {
    document.getElementById('childDetail').style.display = 'none';
    document.getElementById('childrenGrid').style.display = 'grid';
  });

  async function loadFees() {
    try {
      var r = await apiFetch('/api/orgs/me');
      if (!r.ok) return;
      var fd = await r.json();
      var org = (fd.orgs || fd.rows || (Array.isArray(fd) ? fd : []))[0];
      var fee = org && org.fee_amount;
      if (fee) {
        document.getElementById('feesContent').innerHTML =
          '<div style="font-size:14px;line-height:1.6">' +
          '<div style="margin-bottom:8px"><strong>School:</strong> ' +
            escapeHtml(org.name || '—') + '</div>' +
          '<div><strong>Fee:</strong> ₹' + escapeHtml(String(fee)) + ' / term</div>' +
          '</div>';
      }
    } catch(e) {}
  }

  document.getElementById('notifForm').addEventListener('submit', async function(e) {
    e.preventDefault();
    var btn = document.getElementById('saveNotifBtn');
    btn.disabled = true; btn.textContent = '…';
    var prefs = {
      whatsapp: document.getElementById('chkWhatsapp').checked,
      sms: document.getElementById('chkSms').checked
    };
    // There is no server-side notification-prefs endpoint yet, so persist
    // locally instead of firing a request that 404s and then falsely showing
    // "saved". Honest + no failing network call. (Backend can sync later.)
    try { localStorage.setItem('padhai_notif_prefs', JSON.stringify(prefs)); } catch(e) {}
    var s = document.getElementById('notifSuccess');
    s.textContent = 'Notification preferences saved on this device.';
    s.style.display = 'block';
    setTimeout(function() { s.style.display = 'none'; }, 3000);
    btn.disabled = false; btn.textContent = 'Save notification settings';
  });

  // ──────── Link-a-child modal flow ───────────────────────────
  var linkModal = document.getElementById('linkChildModal');
  var openBtn = document.getElementById('openLinkChildBtn');
  var cancelBtn = document.getElementById('cancelLinkChildBtn');
  var form = document.getElementById('linkChildForm');
  var errBox = document.getElementById('linkChildError');
  var okBox = document.getElementById('linkChildSuccess');
  var submitBtn = document.getElementById('submitLinkChildBtn');

  function openLinkModal() {
    errBox.style.display = 'none';
    okBox.style.display = 'none';
    form.reset();
    submitBtn.disabled = false;
    submitBtn.textContent = 'Send link request';
    linkModal.style.display = 'flex';
  }
  function closeLinkModal() { linkModal.style.display = 'none'; }
  if (openBtn) openBtn.addEventListener('click', openLinkModal);
  if (cancelBtn) cancelBtn.addEventListener('click', closeLinkModal);
  if (linkModal) linkModal.addEventListener('click', function(e) {
    if (e.target === linkModal) closeLinkModal();
  });

  if (form) form.addEventListener('submit', async function(ev) {
    ev.preventDefault();
    errBox.style.display = 'none';
    okBox.style.display = 'none';
    submitBtn.disabled = true;
    submitBtn.textContent = 'Sending…';
    var email = document.getElementById('linkChildEmail').value.trim();
    var relation = document.getElementById('linkChildRelation').value.trim();
    var fd = new URLSearchParams();
    fd.set('other_email', email);
    fd.set('role', 'parent');
    if (relation) fd.set('relation', relation);
    try {
      var r = await apiFetch('/api/parents/link', {
        method:'POST',
        headers:{'Content-Type':'application/x-www-form-urlencoded'},
        body: fd.toString(),
      });
      if (!r.ok) {
        var t = await r.text();
        var msg = 'HTTP ' + r.status;
        try { var j = JSON.parse(t); if (j.detail) msg = j.detail; } catch(_) {}
        errBox.textContent = msg;
        errBox.style.display = 'block';
        submitBtn.disabled = false;
        submitBtn.textContent = 'Send link request';
        return;
      }
      okBox.textContent = (
        'Link request sent. Your child will receive an in-app ' +
        'notification to confirm. Once they accept, they appear here.'
      );
      okBox.style.display = 'block';
      submitBtn.disabled = false;
      submitBtn.textContent = 'Send another';
      // Refresh the children list in case the link was auto-accepted
      // (some flows skip the confirmation step).
      setTimeout(function() { loadParent(); }, 800);
    } catch (e) {
      errBox.textContent = 'Network error: ' + e.message;
      errBox.style.display = 'block';
      submitBtn.disabled = false;
      submitBtn.textContent = 'Send link request';
    }
  });

  loadParent();
})();
"""

    return _page_shell(title="Parent Portal", body=body, extra_script=script)


# ---------------------------------------------------------------------------
# Screen 9: Lesson Player (direct URL: /lessons/{job_id})
# ---------------------------------------------------------------------------

def get_lesson_player_html() -> str:
    """Video player screen — served at /lessons/{job_id}.
    Reads job_id from URL path, fetches job status, plays video,
    with tabs for quiz, chat, flashcards, notes, and audio recap."""
    head = """
  .player-wrap{max-width:980px;margin:0 auto;padding:20px}
  .player-grid{display:grid;grid-template-columns:1fr 360px;gap:16px;align-items:start}
  @media(max-width:800px){.player-grid{grid-template-columns:1fr}}
  video{width:100%;border-radius:8px;background:#000;display:block;max-height:420px}
  .panel-tabs{display:flex;border-bottom:1px solid var(--line);gap:0;overflow-x:auto}
  .ptab{background:transparent;border:0;border-bottom:3px solid transparent;
        padding:10px 13px;font-size:13px;font-weight:700;cursor:pointer;
        font-family:inherit;color:var(--muted);white-space:nowrap}
  .ptab.active{border-bottom-color:var(--brand);color:var(--brand)}
  .tab-pane{display:none;padding:14px 16px}
  .tab-pane.active{display:block}
  .qopt{display:block;width:100%;text-align:left;padding:10px 12px;margin:5px 0;
        border:1px solid var(--line);border-radius:var(--radius);
        background:#fff;font-size:13px;cursor:pointer;font-family:inherit}
  .qopt:hover:not(:disabled){border-color:var(--brand);background:var(--brand-soft)}
  .qopt.correct{background:var(--green-soft);border-color:var(--green)}
  .qopt.wrong{background:var(--red-soft);border-color:var(--red)}
  .chat-log{height:260px;overflow-y:auto;border:1px solid var(--line);
            border-radius:var(--radius);padding:10px;display:flex;
            flex-direction:column;gap:8px;background:#fafbff;margin-bottom:8px}
  .bubble{max-width:85%;padding:8px 12px;border-radius:10px;font-size:13px;
          line-height:1.45}
  .bubble.user{align-self:flex-end;background:var(--brand);color:#fff;
               border-radius:10px 10px 3px 10px}
  .bubble.ai{align-self:flex-start;background:#fff;border:1px solid var(--line);
              border-radius:10px 10px 10px 3px}
  .fc-flip{border:1px solid var(--line);border-radius:10px;padding:20px;
           text-align:center;cursor:pointer;background:#fff;min-height:130px;
           display:flex;align-items:center;justify-content:center;
           margin-bottom:8px;font-size:15px;font-weight:700}
  .grade-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:6px}
  .gb{padding:8px 4px;border-radius:7px;font-weight:850;font-size:11px;
      cursor:pointer;border:0;font-family:inherit}
  .g0{background:#fdecea;color:var(--red)}
  .g2{background:#fff4db;color:var(--amber)}
  .g4{background:var(--brand-soft);color:var(--brand)}
  .g5{background:var(--green-soft);color:var(--green)}
"""
    body = """
<div class="player-wrap">
  <div id="loadMsg" style="text-align:center;padding:60px;color:var(--muted)">
    Loading lesson…
  </div>
  <div id="main" style="display:none">
    <div class="player-grid">
      <!-- Video column -->
      <div>
        <div class="card" style="padding:0;overflow:hidden">
          <video id="vid" controls></video>
        </div>
        <div style="padding:14px 0">
          <h1 id="title" style="margin:0 0 4px">Lesson</h1>
          <p class="sub" id="meta"></p>
          <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:10px">
            <a id="dlBtn" href="#" class="btn btn-sm">⬇ Download</a>
            <a id="newBtn" href="/lessons/new" class="btn btn-sm">+ New Lesson</a>
          </div>
        </div>
      </div>
      <!-- Sidebar tabs -->
      <div class="card" style="padding:0;overflow:hidden">
        <div class="panel-tabs">
          <button class="ptab active" data-tab="quiz">Quiz</button>
          <button class="ptab" data-tab="chat">Tutor</button>
          <button class="ptab" data-tab="flash">Cards</button>
          <button class="ptab" data-tab="notes">Notes</button>
          <button class="ptab" data-tab="recap">Recap</button>
        </div>
        <!-- Quiz pane -->
        <div class="tab-pane active" id="tab-quiz">
          <button class="btn btn-primary" id="loadQBtn" style="width:100%">Load Quiz</button>
          <div id="qArea" style="margin-top:10px"></div>
        </div>
        <!-- Chat pane -->
        <div class="tab-pane" id="tab-chat">
          <div class="chat-log" id="chatLog">
            <div class="bubble ai">Ask me anything about this lesson!</div>
          </div>
          <div style="display:flex;gap:6px">
            <input id="chatIn" type="text" placeholder="Your question…"
                   style="flex:1;border-radius:7px">
            <button class="btn btn-primary" id="chatSend">→</button>
          </div>
        </div>
        <!-- Flashcards pane -->
        <div class="tab-pane" id="tab-flash">
          <p class="sub" id="fcStatus">Generate flashcards for this lesson.</p>
          <button class="btn btn-primary" id="genFcBtn" style="width:100%">
            Generate Cards
          </button>
          <div id="fcArea" style="margin-top:10px"></div>
        </div>
        <!-- Notes pane -->
        <div class="tab-pane" id="tab-notes">
          <textarea id="notesTA" style="width:100%;height:200px;resize:vertical;
            font-family:inherit;font-size:13px;padding:10px;
            border:1px solid var(--line);border-radius:var(--radius)"
            placeholder="Your notes…"></textarea>
          <button class="btn btn-primary" id="saveNotes"
                  style="width:100%;margin-top:8px">Save</button>
          <div class="ok" id="notesSaved">Saved!</div>
        </div>
        <!-- Recap pane -->
        <div class="tab-pane" id="tab-recap">
          <p class="sub">Podcast-style audio summary of this lesson.</p>
          <button class="btn btn-primary" id="genRecap" style="width:100%">
            Generate Recap
          </button>
          <div id="recapArea" style="margin-top:10px"></div>
        </div>
      </div>
    </div>
  </div>
  <div id="errMsg" style="text-align:center;padding:40px;color:var(--red);display:none"></div>
</div>
"""
    script = r"""
(async function(){
  const $ = id => document.getElementById(id);
  const jobId = location.pathname.split('/').filter(Boolean).pop();
  let lessonId = null;
  let qData = null, qIdx = 0;
  let fcCards = [], fcIdx = 0, fcFlipped = false;

  const jr = await apiFetch('/jobs/' + jobId);
  $('loadMsg').style.display = 'none';
  if(!jr.ok){ $('errMsg').textContent = 'Lesson not found ('+jr.status+').'; $('errMsg').style.display=''; return; }
  const job = jr.body;
  if(job.status !== 'succeeded'){
    $('errMsg').textContent = 'Lesson is ' + job.status + '. Check back when generation is complete.';
    $('errMsg').style.display = '';
    return;
  }
  lessonId = job.lesson_id;
  $('main').style.display = '';
  $('vid').src = '/jobs/' + jobId + '/video';
  $('dlBtn').href = '/jobs/' + jobId + '/video';
  $('meta').textContent = lessonId ? 'Lesson ID: ' + lessonId : '';

  // Tabs
  document.querySelectorAll('.ptab').forEach(b => {
    b.addEventListener('click', () => {
      document.querySelectorAll('.ptab').forEach(x => x.classList.remove('active'));
      document.querySelectorAll('.tab-pane').forEach(x => x.classList.remove('active'));
      b.classList.add('active');
      $('tab-' + b.dataset.tab).classList.add('active');
    });
  });

  // ---- Quiz ----
  $('loadQBtn').addEventListener('click', async () => {
    if(!lessonId) return;
    $('loadQBtn').innerHTML = '<span class="spinner"></span>';
    $('loadQBtn').disabled = true;
    const r = await apiFetch('/api/quiz/' + lessonId);
    $('loadQBtn').style.display = 'none';
    if(!r.ok || !(r.body.questions||[]).length){
      $('qArea').innerHTML = '<p class="sub">No quiz for this lesson.</p>'; return;
    }
    qData = r.body.questions; qIdx = 0;
    _showQ();
  });

  function _showQ(){
    if(!qData||qIdx>=qData.length){ $('qArea').innerHTML = '<p class="sub" style="color:var(--green)">Quiz complete! 🎉</p>'; return; }
    const q = qData[qIdx];
    $('qArea').innerHTML =
      '<p style="font-size:11px;font-weight:700;color:var(--muted)">Q'+(qIdx+1)+'/'+qData.length+'</p>'
      + '<p style="font-size:13px;font-weight:700;margin:0 0 8px">'+esc(q.question)+'</p>'
      + Object.entries(q.options||{}).map(([k,v])=>
          '<button class="qopt" data-k="'+k+'"><b>'+k+'.</b> '+esc(String(v))+'</button>'
        ).join('')
      + '<div id="qfb" style="display:none;font-size:12px;margin-top:8px;padding:8px;border-radius:6px;background:#f5f7fb"></div>'
      + '<div id="qnav" style="display:none;margin-top:8px">'
      + '<button class="btn btn-primary btn-sm" id="qNext">'+(qIdx<qData.length-1?'Next →':'Results')+'</button></div>';
    document.querySelectorAll('.qopt').forEach(b => {
      b.addEventListener('click', ()=>{
        const chosen=b.dataset.k, correct=q.correct;
        document.querySelectorAll('.qopt').forEach(x=>{
          x.disabled=true;
          if(x.dataset.k===correct) x.classList.add('correct');
          else if(x.dataset.k===chosen) x.classList.add('wrong');
        });
        const fb=$('qfb'); fb.style.display='';
        fb.textContent=(chosen===correct?'✓ Correct! ':'✗ Wrong. ')+(q.explanation||'');
        $('qnav').style.display='';
        $('qNext').addEventListener('click',()=>{qIdx++;_showQ();});
      });
    });
  }

  // ---- Chat ----
  async function sendChat(){
    if(!lessonId) return;
    const q = $('chatIn').value.trim(); if(!q) return;
    $('chatIn').value=''; $('chatSend').disabled=true;
    const log=$('chatLog');
    log.innerHTML+='<div class="bubble user">'+esc(q)+'</div>'
      +'<div class="bubble ai" id="chatDots">…</div>';
    log.scrollTop=log.scrollHeight;
    const r=await postForm('/chat/'+lessonId,{question:q});
    const d=$('chatDots'); if(d) d.remove();
    const ans=r.ok?(r.body.answer||JSON.stringify(r.body).slice(0,300)):'Error '+r.status;
    log.innerHTML+='<div class="bubble ai">'+esc(ans)+'</div>';
    log.scrollTop=log.scrollHeight;
    $('chatSend').disabled=false;
  }
  $('chatSend').addEventListener('click',sendChat);
  $('chatIn').addEventListener('keydown',e=>{if(e.key==='Enter')sendChat();});

  // ---- Flashcards ----
  $('genFcBtn').addEventListener('click',async()=>{
    if(!lessonId) return;
    $('genFcBtn').innerHTML='<span class="spinner"></span>'; $('genFcBtn').disabled=true;
    const r=await postForm('/lessons/'+lessonId+'/flashcards',{count:8});
    $('genFcBtn').style.display='none';
    if(!r.ok||!r.body.cards){ $('fcArea').innerHTML='<p class="sub">Could not generate cards.</p>'; return; }
    fcCards=r.body.cards; fcIdx=0;
    $('fcStatus').textContent=fcCards.length+' cards — tap to flip';
    _showFC();
  });

  function _showFC(){
    if(fcIdx>=fcCards.length){ $('fcArea').innerHTML='<p class="sub" style="color:var(--green)">All cards done! 🎉</p>'
      +'<a href="/flashcards" class="btn btn-primary" style="margin-top:8px;width:100%">Full session</a>'; return; }
    const c=fcCards[fcIdx]; fcFlipped=false;
    $('fcArea').innerHTML=
      '<p style="font-size:11px;color:var(--muted);font-weight:700">'+(fcIdx+1)+' / '+fcCards.length+'</p>'
      +'<div class="fc-flip" id="fcFlipCard">'+esc(c.front)+'</div>'
      +'<div class="grade-grid" id="gradeGrid" style="display:none">'
      +'<button class="gb g0" data-g="0">Again</button>'
      +'<button class="gb g2" data-g="2">Hard</button>'
      +'<button class="gb g4" data-g="4">Good</button>'
      +'<button class="gb g5" data-g="5">Easy</button>'
      +'</div>';
    $('fcFlipCard').addEventListener('click',()=>{
      fcFlipped=!fcFlipped;
      $('fcFlipCard').textContent=fcFlipped?c.back:c.front;
      $('gradeGrid').style.display=fcFlipped?'grid':'none';
    });
    document.querySelectorAll('.grade-grid .gb').forEach(b=>{
      b.addEventListener('click',async(e)=>{
        e.stopPropagation();
        const g=parseInt(b.dataset.g);
        if(c.id) postForm('/api/flashcards/'+c.id+'/review',{grade:g}).catch(()=>{});
        fcIdx++; _showFC();
      });
    });
  }

  // ---- Notes ----
  if(lessonId){
    const nr=await apiFetch('/lessons/'+lessonId+'/notes');
    if(nr.ok&&nr.body.notes) $('notesTA').value=nr.body.notes;
  }
  $('saveNotes').addEventListener('click',async()=>{
    if(!lessonId) return;
    const tok=localStorage.getItem('pathshala_token');
    const h={...(tok?{'Authorization':'Bearer '+tok}:{}),'Content-Type':'application/x-www-form-urlencoded'};
    const r=await fetch('/lessons/'+lessonId+'/notes',{method:'POST',credentials:'include',headers:h,
      body:'notes='+encodeURIComponent($('notesTA').value)});
    if(r.ok){$('notesSaved').classList.add('show');setTimeout(()=>$('notesSaved').classList.remove('show'),2000);}
  });

  // ---- Recap ----
  $('genRecap').addEventListener('click',async()=>{
    if(!lessonId) return;
    $('genRecap').innerHTML='<span class="spinner"></span>'; $('genRecap').disabled=true;
    const r=await postForm('/lessons/'+lessonId+'/recap',{});
    $('genRecap').style.display='none';
    const ra=$('recapArea');
    if(r.ok&&r.body){
      const b=r.body;
      ra.innerHTML=(b.text?'<p style="font-size:12px;color:var(--muted);margin-bottom:8px">'+esc(b.text.slice(0,300))+'…</p>':'')
        +(b.audio_url?'<audio controls style="width:100%"><source src="'+b.audio_url+'"></audio>':'')
        +(b.audio_error?'<p class="err show">'+esc(b.audio_error)+'</p>':'');
    } else { ra.innerHTML='<p class="err show">Could not generate recap.</p>'; }
  });
})();
"""
    return _page_shell(title="Lesson Player", body=body, extra_head=head, extra_script=script)
