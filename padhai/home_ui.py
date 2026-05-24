"""v3.19 — Goal-led home UI, UX fixes.

v3.18 painted the §26 mockup but had three real UX bugs the user
caught immediately:

  1. Clicking a chip / sidebar item navigated to the raw API URL
     and dumped JSON in the browser.
  2. The sidebar listed 24+ feature items (3 per section × 8
     sections) — too cluttered.
  3. The landing page's "Sign in" link pointed at `/auth/login`
     which doesn't exist as a GET HTML page (only POST exists).

This release ships:

  • Sidebar shows the 8 §26 section titles ONLY. Clicking a
    section scrolls to that section's chip group + highlights it.
  • Chips no longer navigate. Clicking a chip opens an inline
    drawer with the feature title + description + 'Try it' button.
    The 'Try it' button calls the API and shows a friendly
    summary (JSON pretty-printed) — instead of replacing the
    whole page with raw JSON.
  • Landing page's "Sign in" replaced with an inline form that
    POSTs to /auth/login (existing endpoint). Same for sign-up.

Same two files (HOME_HTML / LANDING_HTML) — module API
unchanged from v3.18. Routes unchanged.
"""

from __future__ import annotations


def migrate() -> None:
    return None


HOME_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PadhaiApp — Your study OS</title>
<!-- PWA install affordance — preserved from legacy SPA -->
<link rel="manifest" href="/manifest.json">
<meta name="theme-color" content="#1565d8">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="PadhaiApp">
<link rel="apple-touch-icon" href="/static/icon-180.png">
<style>
  :root {
    --bg:#f5f7fb; --panel:#ffffff; --ink:#111827; --muted:#667085;
    --line:#d9e0ea; --nav:#101828; --nav2:#1d2939;
    --brand:#1565d8; --brand-soft:#eaf2ff;
    --green:#16855f; --green-soft:#e7f6ef;
    --amber:#a86600; --amber-soft:#fff4db;
    --violet:#5b3cc4; --violet-soft:#f0ecff;
    --teal:#027a8a; --teal-soft:#e7f8fb;
    --radius:8px;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--ink);
       font-family:Inter,Segoe UI,Arial,sans-serif}
  .app{min-height:100vh;display:grid;
       grid-template-columns:230px 1fr 330px}
  aside{background:var(--nav);color:#eef3ff;padding:18px 14px;
        display:flex;flex-direction:column;gap:14px}
  .brand{display:flex;gap:10px;align-items:center;
         padding:2px 4px 14px;
         border-bottom:1px solid rgba(255,255,255,.12)}
  .logo{width:34px;height:34px;border-radius:8px;
        background:linear-gradient(135deg,#2f80ed,#12b76a);
        display:grid;place-items:center;color:white;font-weight:850}
  .brand b{display:block;font-size:15px}
  .brand span{display:block;color:#aeb8cc;font-size:11px;margin-top:2px}
  .sidenav{display:flex;flex-direction:column;gap:2px;margin-top:6px}
  .sidenav-item{display:flex;align-items:center;justify-content:space-between;
                background:transparent;border:0;border-radius:8px;
                color:#d7e0f4;text-align:left;padding:11px 12px;
                font-size:13px;cursor:pointer;font-weight:600;
                width:100%;font-family:inherit}
  .sidenav-item:hover{background:#1d2a44}
  .sidenav-item.active{background:var(--nav2);color:#fff}
  .sidenav-item .count{color:#99a8c3;font-size:11px;font-weight:500}
  .sidenav-item .dot{width:8px;height:8px;border-radius:50%;
                     background:var(--brand);margin-right:8px;
                     display:inline-block;flex-shrink:0}
  .sidenav-spacer{flex:1}
  .signin-card{border:1px solid rgba(255,255,255,.18);
               background:#162033;border-radius:10px;padding:12px;
               color:#cfd8ea;font-size:12px;line-height:1.45}
  .signin-card a{color:#fff;text-decoration:underline;font-weight:700}
  main{padding:20px;overflow:auto;scroll-behavior:smooth}
  .topbar{display:flex;align-items:center;justify-content:space-between;
          gap:12px;margin-bottom:16px}
  .search{flex:1;min-width:220px;background:var(--panel);
          border:1px solid var(--line);border-radius:var(--radius);
          padding:12px 14px;color:var(--muted)}
  .user-pill{background:var(--panel);border:1px solid var(--line);
             border-radius:999px;padding:9px 12px;font-size:13px;
             color:var(--muted);white-space:nowrap}
  .hero{background:var(--panel);border:1px solid var(--line);
        border-radius:var(--radius);padding:18px;
        box-shadow:0 14px 32px rgba(15,23,42,.08);
        display:grid;grid-template-columns:1fr auto;gap:20px;
        align-items:center;margin-bottom:16px}
  h1{font-size:24px;line-height:1.18;margin:0 0 8px}
  .sub{color:var(--muted);font-size:13px;line-height:1.5;margin:0}
  .actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:14px}
  button,.btn{border:1px solid var(--line);background:var(--panel);
              color:var(--ink);border-radius:7px;padding:10px 13px;
              font-weight:750;font-size:13px;cursor:pointer;
              font-family:inherit}
  button:hover,.btn:hover{border-color:#bad4ff}
  .primary{background:var(--brand);border-color:var(--brand);color:#fff}
  .primary:hover{background:#0b4ec1}
  .exam-card{width:230px;background:#f8fbff;border:1px solid #cfe0ff;
             border-radius:var(--radius);padding:14px}
  .label{color:var(--muted);font-size:11px;font-weight:800;
         text-transform:uppercase}
  .exam{font-size:22px;font-weight:850;margin:8px 0 4px}
  .progress{height:8px;border-radius:999px;background:#d7e5fb;
            overflow:hidden;margin:12px 0 8px}
  .progress span{display:block;height:100%;background:var(--green);
                 width:0%;transition:width .4s ease}
  .tag{display:inline-flex;align-items:center;white-space:nowrap;
       padding:5px 8px;border-radius:999px;font-size:11px;font-weight:850}
  .green{background:var(--green-soft);color:var(--green)}
  .amber{background:var(--amber-soft);color:var(--amber)}
  .violet{background:var(--violet-soft);color:var(--violet)}
  .teal{background:var(--teal-soft);color:var(--teal)}
  .cards3{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;
          margin-bottom:16px}
  .metric{background:var(--panel);border:1px solid var(--line);
          border-radius:var(--radius);padding:13px}
  .metric .num{font-size:22px;font-weight:850;margin-bottom:3px}
  .metric .cap{color:var(--muted);font-size:12px}
  .panel{background:var(--panel);border:1px solid var(--line);
         border-radius:var(--radius);padding:16px;
         box-shadow:0 10px 26px rgba(15,23,42,.06);
         margin-bottom:16px;scroll-margin-top:20px}
  .panel h2{margin:0 0 8px;font-size:16px}
  .section-sub{margin:0 0 12px;color:var(--muted);font-size:13px;
               line-height:1.45}
  .panel.highlight{box-shadow:0 0 0 3px var(--brand-soft);
                   border-color:var(--brand)}
  .module-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:8px}
  .module-chip{border:1px solid var(--line);background:#fbfcff;
               border-radius:7px;padding:9px 10px;min-height:42px;
               font-size:12px;font-weight:800;color:#334155;
               display:flex;align-items:center;justify-content:space-between;
               gap:8px;cursor:pointer;text-align:left;width:100%;
               font-family:inherit}
  .module-chip:hover{border-color:#bad4ff;background:#eaf2ff}
  .module-chip .badge{font-size:10px;font-weight:850;color:var(--green);
                      background:var(--green-soft);border-radius:999px;
                      padding:3px 6px}
  .module-chip .badge.new{color:var(--brand);background:var(--brand-soft)}
  .module-chip .badge.admin{color:var(--violet);background:var(--violet-soft)}
  .two-col{display:grid;grid-template-columns:1.12fr .88fr;gap:16px;
           align-items:start}
  .study-step{display:grid;grid-template-columns:34px 1fr auto;gap:10px;
              align-items:center;padding:12px 0;
              border-bottom:1px solid var(--line)}
  .study-step:last-child{border-bottom:0}
  .study-step.done{opacity:.55}
  .step-dot{width:30px;height:30px;border-radius:7px;
            background:var(--brand-soft);color:var(--brand);
            display:grid;place-items:center;font-weight:850;font-size:12px}
  .step-title{font-weight:780;font-size:13px;margin-bottom:3px}
  .step-meta{color:var(--muted);font-size:12px}
  .rightbar{border-left:1px solid var(--line);background:#fbfcff;
            padding:20px 16px;overflow:auto}
  .rightbar h2{font-size:15px;margin:0 0 12px}
  .side-card{background:var(--panel);border:1px solid var(--line);
             border-radius:var(--radius);padding:13px;margin-bottom:12px}
  .side-card b{display:block;font-size:13px;margin-bottom:4px}
  .side-card p{margin:0;color:var(--muted);font-size:12px;line-height:1.4}

  /* drawer for feature details (replaces JSON-dump navigation) */
  .drawer-backdrop{position:fixed;inset:0;background:rgba(15,23,42,.45);
                   display:none;align-items:flex-end;justify-content:center;
                   z-index:20;padding:24px}
  .drawer-backdrop.open{display:flex}
  .drawer{background:var(--panel);border-radius:14px 14px 0 0;
          max-width:640px;width:100%;max-height:80vh;overflow:auto;
          padding:24px;border:1px solid var(--line);
          box-shadow:0 -20px 60px rgba(15,23,42,.18)}
  @media(min-width:760px){
    .drawer-backdrop{align-items:center}
    .drawer{border-radius:14px}
  }
  .drawer h3{margin:0 0 8px;font-size:18px}
  .drawer .endpoint{font-family:ui-monospace,monospace;font-size:12px;
                    background:#f1f4f8;padding:6px 8px;border-radius:6px;
                    word-break:break-all;color:#334155}
  .drawer .row{display:flex;gap:8px;margin:14px 0;flex-wrap:wrap}
  .drawer pre{background:#0f1c33;color:#cfe0ff;padding:12px;
              border-radius:8px;overflow:auto;font-size:12px;
              max-height:280px;margin:0;line-height:1.45}
  .drawer-close{background:transparent;border:0;color:var(--muted);
                font-size:24px;cursor:pointer;float:right;line-height:1}
  .pill{background:var(--brand-soft);color:var(--brand);font-weight:850;
        font-size:11px;padding:4px 8px;border-radius:999px}
  .pill.admin{background:var(--violet-soft);color:var(--violet)}

  .mobile-bottom-nav{display:none}
  @media(max-width:1180px){
    .app{grid-template-columns:200px 1fr}
    .rightbar{grid-column:1/-1;border-left:0;
              border-top:1px solid var(--line);
              display:grid;grid-template-columns:repeat(3,1fr);gap:12px}
    .rightbar h2{grid-column:1/-1}
    .module-grid{grid-template-columns:repeat(3,1fr)}
    .two-col{grid-template-columns:1fr}
  }
  @media(max-width:780px){
    .app{display:block;padding-bottom:64px}
    aside{position:sticky;top:0;z-index:2;padding:10px}
    .brand{padding-bottom:8px}
    .signin-card{display:none}
    .sidenav{flex-direction:row;overflow:auto;gap:6px;
             scrollbar-width:none}
    .sidenav::-webkit-scrollbar{display:none}
    .sidenav-item{white-space:nowrap;width:auto;background:#1d2a44;
                  padding:8px 11px;font-size:12px}
    .sidenav-item .count{display:none}
    main{padding:14px}
    .topbar{display:block}
    .search{margin-bottom:10px}
    .hero{grid-template-columns:1fr}
    .exam-card{width:100%}
    .cards3,.module-grid{grid-template-columns:1fr}
    .study-step{grid-template-columns:34px 1fr}
    .study-step .tag{grid-column:2}
    .rightbar{display:block;padding:14px}
    h1{font-size:21px}
    .mobile-bottom-nav{position:fixed;bottom:0;left:0;right:0;
                       background:#0f1c33;
                       display:grid;grid-template-columns:repeat(5,1fr);
                       gap:1px;z-index:10;padding:6px 4px}
    .mobile-bottom-nav button{color:#cfd8ea;background:transparent;
                              border:0;text-align:center;
                              font-size:10px;font-weight:850;
                              padding:6px 2px;cursor:pointer;
                              font-family:inherit}
    .mobile-bottom-nav button.active{color:#fff}
  }
</style>
</head>
<body>
<div class="app">
  <aside>
    <div class="brand">
      <div class="logo">P</div>
      <div><b>PadhaiApp</b><span>India learning OS</span></div>
    </div>
    <!-- 8 section titles only — major points, no per-feature
         clutter. Click → scroll to chip group + filter. -->
    <div class="sidenav" id="sidenav">
      <div class="sub" style="color:#aeb8cc;font-size:12px;padding:0 8px">
        Loading sections…
      </div>
    </div>
    <div class="sidenav-spacer"></div>
    <div class="signin-card" id="signinCard" style="display:none">
      Not signed in. <a href="/landing">Sign in →</a>
    </div>
  </aside>

  <main>
    <div class="topbar">
      <div class="search">
        Search NCERT, UPSC polity, SSC reasoning, JEE physics, college notes…
      </div>
      <div class="user-pill" id="userPill">Low-data mode aware</div>
    </div>

    <section class="hero" id="hero">
      <div>
        <h1 id="heroHeadline">Loading your study plan…</h1>
        <p class="sub" id="heroSub">
          Personalised based on readiness, weak topics, and recent mocks.
        </p>
        <div class="actions" id="heroActions"></div>
      </div>
      <div class="exam-card" id="examCard" style="display:none">
        <div class="label">Active Exam Pack</div>
        <div class="exam" id="examTitle">—</div>
        <p class="sub" id="examMeta">—</p>
        <div class="progress"><span id="readinessBar"></span></div>
        <div class="tag amber" id="readinessTag">Readiness —</div>
      </div>
    </section>

    <div class="cards3" id="metrics">
      <div class="metric"><div class="num" id="m1">—</div>
        <div class="cap">Due flashcards (SRS)</div></div>
      <div class="metric"><div class="num" id="m2">—</div>
        <div class="cap">Weak topics needing revision</div></div>
      <div class="metric"><div class="num" id="m3">—</div>
        <div class="cap">Cited-answer rate (last 30 AI answers)</div></div>
    </div>

    <div class="two-col">
      <section class="panel" id="panel-today">
        <h2>Today's study flow</h2>
        <p class="section-sub" id="planMeta">Loading your daily plan…</p>
        <div id="planBlocks"></div>
      </section>

      <section class="panel" id="panel-next">
        <h2>What's next?</h2>
        <p class="section-sub">Pick the highest-leverage action.</p>
        <div id="nextActions"></div>
      </section>
    </div>

    <!-- Section-grouped chip grid — chips open an inline drawer
         instead of navigating to raw API URLs. -->
    <div id="sectionGroups"></div>
  </main>

  <section class="rightbar">
    <h2>Community + Trust</h2>
    <div class="side-card">
      <b>Exam community</b>
      <p id="communityText">Join your exam-pack's discussion room.</p>
    </div>
    <div class="side-card">
      <b>Trust signal</b>
      <p id="trustText">Citation rate measured across your recent AI answers.</p>
    </div>
    <div class="side-card">
      <b>Recent fallbacks</b>
      <p id="fallbackText">Times the AI declined to answer because no source matched.</p>
    </div>
    <div class="side-card">
      <b>Expert verification</b>
      <p>
        Flag any AI answer for expert review — verified content
        gets a teacher badge.
      </p>
    </div>
    <div class="side-card">
      <b>Mobile + offline</b>
      <p>Set quality tier, download exam-pack chapters, study without data.</p>
    </div>
  </section>
</div>

<!-- Drawer (chip details + Try-it) -->
<div class="drawer-backdrop" id="drawer" onclick="if(event.target===this)closeDrawer()">
  <div class="drawer">
    <button class="drawer-close" onclick="closeDrawer()" aria-label="Close">×</button>
    <span class="pill" id="drawerBadge">feature</span>
    <h3 id="drawerTitle">—</h3>
    <p class="sub" id="drawerDesc">—</p>
    <div class="endpoint" id="drawerEndpoint">—</div>
    <div class="row">
      <button class="primary" id="drawerTryBtn">Try it</button>
      <button onclick="closeDrawer()">Close</button>
    </div>
    <div id="drawerResult" style="display:none">
      <p class="sub">API response (truncated):</p>
      <pre id="drawerJson">—</pre>
    </div>
  </div>
</div>

<!-- Mobile bottom nav — 5 tabs (per mockup) -->
<nav class="mobile-bottom-nav" id="mobileBottomNav"></nav>

<script>
(function(){
  const $ = id => document.getElementById(id);
  let manifest = null;

  function authHeaders(base){
    const headers = Object.assign({}, base || {});
    try {
      const token = localStorage.getItem('pathshala_token');
      if(token) headers.Authorization = 'Bearer ' + token;
    } catch(_) {}
    return headers;
  }

  async function getJSON(url){
    try{
      const r = await fetch(url, {
        credentials:'include',
        headers: authHeaders(),
      });
      if(!r.ok) return null;
      return await r.json();
    } catch(_){ return null; }
  }

  function escapeHtml(s){
    return String(s==null?'':s)
      .replace(/&/g,'&amp;').replace(/</g,'&lt;')
      .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  }

  // -------- Sidebar: 8 sections only --------
  function renderSidebar(m){
    const wrap = $('sidenav');
    if(!m || !m.sections){
      wrap.innerHTML = '<div class="sub" style="color:#aeb8cc;font-size:12px;padding:0 8px">No navigation</div>';
      return;
    }
    wrap.innerHTML = '';
    m.sections.forEach((s, i) => {
      const btn = document.createElement('button');
      btn.className = 'sidenav-item' + (i===0 ? ' active' : '');
      btn.dataset.slug = s.slug;
      btn.innerHTML =
        '<span>' + escapeHtml(s.title) + '</span>'
        + '<span class="count">' + s.features.length + '</span>';
      btn.addEventListener('click', () => {
        document.querySelectorAll('.sidenav-item').forEach(
          b => b.classList.remove('active'),
        );
        btn.classList.add('active');
        const target = $('group-' + s.slug);
        if(target){
          target.scrollIntoView({behavior:'smooth', block:'start'});
          target.classList.add('highlight');
          setTimeout(() => target.classList.remove('highlight'), 1400);
        }
      });
      wrap.appendChild(btn);
    });
    // Mobile bottom-nav uses the same data
    const mb = $('mobileBottomNav');
    mb.innerHTML = '';
    (m.mobile_bottom_nav || []).forEach(t => {
      const b = document.createElement('button');
      b.textContent = t.title;
      b.addEventListener('click', () => {
        const target = $('group-' + t.section_slug);
        if(target) target.scrollIntoView({behavior:'smooth'});
      });
      mb.appendChild(b);
    });
  }

  // -------- Section groups: one panel per §26 section,
  //          each with its own chip grid --------
  function renderSectionGroups(m){
    const host = $('sectionGroups');
    if(!m || !m.sections){ host.innerHTML = ''; return; }
    host.innerHTML = m.sections.map(s => (
      '<section class="panel" id="group-' + escapeHtml(s.slug) + '">'
      + '<h2>' + escapeHtml(s.title) + '</h2>'
      + '<p class="section-sub">' + escapeHtml(s.description) + '</p>'
      + '<div class="module-grid" data-slug="' + escapeHtml(s.slug) + '">'
      + s.features.map((f, fi) => (
          '<button class="module-chip" data-section="'
          + escapeHtml(s.slug) + '" data-i="' + fi + '">'
          + '<span>' + escapeHtml(f.title) + '</span>'
          + (f.badge
              ? '<span class="badge ' + escapeHtml(f.badge) + '">'
                + escapeHtml(f.badge) + '</span>'
              : '')
          + '</button>'
        )).join('')
      + '</div></section>'
    )).join('');
    // Wire chip clicks → drawer (no navigation)
    document.querySelectorAll('.module-chip').forEach(chip => {
      chip.addEventListener('click', () => {
        const sec = manifest.sections.find(
          s => s.slug === chip.dataset.section,
        );
        if(!sec) return;
        const feature = sec.features[parseInt(chip.dataset.i, 10)];
        openDrawer(feature);
      });
    });
  }

  // -------- Drawer --------
  let currentFeature = null;
  function openDrawer(f){
    currentFeature = f;
    $('drawerTitle').textContent = f.title;
    $('drawerDesc').textContent = f.description || 'No description.';
    $('drawerEndpoint').textContent =
      (f.http_method || 'GET') + '  ' + f.endpoint;
    const badge = $('drawerBadge');
    badge.textContent = f.badge || 'feature';
    badge.className = 'pill' + (f.badge === 'admin' ? ' admin' : '');
    $('drawerResult').style.display = 'none';
    $('drawerJson').textContent = '';
    $('drawerTryBtn').textContent = 'Try it';
    $('drawerTryBtn').disabled = false;
    $('drawer').classList.add('open');
  }
  window.closeDrawer = function(){
    $('drawer').classList.remove('open');
    currentFeature = null;
  };
  $('drawerTryBtn').addEventListener('click', async () => {
    if(!currentFeature) return;
    const btn = $('drawerTryBtn');
    btn.disabled = true;
    btn.textContent = 'Calling…';
    let url = currentFeature.endpoint;
    // Skip endpoints with unfilled path params (e.g. {id})
    if(/\\{[a-zA-Z_]+\\}/.test(url)){
      $('drawerJson').textContent =
        'This endpoint needs a parameter (e.g. {id}). '
        + 'Open it from the relevant detail page so the right '
        + 'id is supplied.';
      $('drawerResult').style.display = '';
      btn.disabled = false;
      btn.textContent = 'Try it';
      return;
    }
    const method = (currentFeature.http_method || 'GET').toUpperCase();
    let res;
    try {
      const payload = currentFeature.sample_payload;
      const bodyStr = (method !== 'GET' && payload)
        ? Object.entries(payload)
            .map(([k,v]) => encodeURIComponent(k) + '=' + encodeURIComponent(v))
            .join('&')
        : null;
      res = await fetch(url, {
        method, credentials: 'include',
        headers: authHeaders(method !== 'GET' ?
          {'Content-Type':'application/x-www-form-urlencoded'} : {}),
        body: bodyStr || undefined,
      });
    } catch(e) {
      $('drawerJson').textContent = 'Network error: ' + e.message;
      $('drawerResult').style.display = '';
      btn.disabled = false;
      btn.textContent = 'Try again';
      return;
    }
    let body = '';
    try {
      const j = await res.json();
      body = JSON.stringify(j, null, 2);
    } catch(_) {
      body = await res.text();
    }
    if(body.length > 4000) body = body.slice(0, 4000) + '\\n…(truncated)';
    if(!res.ok){
      $('drawerJson').textContent =
        'HTTP ' + res.status + ' ' + res.statusText
        + (res.status === 401
            ? '\\n\\nYou need to sign in to use this endpoint.'
            : '')
        + '\\n\\n' + body;
    } else {
      $('drawerJson').textContent = body;
    }
    $('drawerResult').style.display = '';
    btn.disabled = false;
    btn.textContent = 'Try again';
  });
  // Esc closes
  document.addEventListener('keydown', e => {
    if(e.key === 'Escape') closeDrawer();
  });

  // -------- Hero + dashboard --------
  function renderHero(dash){
    if(!dash || !dash.hero){
      $('heroHeadline').textContent = 'Sign in to see your study plan';
      $('heroSub').innerHTML =
        'Or open the <a href="/landing">public landing</a> '
        + 'to create an account.';
      $('signinCard').style.display = '';
      $('heroActions').innerHTML =
        '<a class="btn primary" href="/landing">Sign in</a>'
        + '<button onclick="document.getElementById(\\'group-study-studio\\')'
        + '.scrollIntoView({behavior:\\'smooth\\'})">'
        + 'Browse Study Studio</button>';
      return;
    }
    $('heroHeadline').textContent = dash.hero.headline || 'Welcome';
    $('heroActions').innerHTML = (dash.hero.actions || []).map((a, i) => (
      '<button class="' + (a.kind === 'primary' ? 'primary' : '')
      + '" data-i="' + i + '">' + escapeHtml(a.title) + '</button>'
    )).join('');
    document.querySelectorAll('#heroActions button').forEach(b => {
      const idx = parseInt(b.dataset.i, 10);
      const a = dash.hero.actions[idx];
      b.addEventListener('click', () => {
        // Hero actions are big-deal CTAs — they scroll to the
        // most relevant section instead of dumping raw JSON.
        const map = {
          'Continue today\\'s plan': 'today',
          'Open Study Studio': 'study-studio',
          'Take 20-min mock': 'mocks',
          'Ask AI tutor': 'ai-tutor',
        };
        const scrollSlug = map[a.title];
        if(scrollSlug){
          const t = $('group-' + scrollSlug) || $('panel-' + scrollSlug);
          if(t){ t.scrollIntoView({behavior:'smooth'}); return; }
        }
        // Fallback: open drawer
        openDrawer({
          title: a.title,
          description: 'Run this action.',
          endpoint: a.endpoint || '/',
          http_method: 'GET',
          badge: 'action',
        });
      });
    });
    const pack = dash.exam_pack || {};
    const r = dash.readiness || {};
    if(pack.title){
      $('examTitle').textContent = pack.title;
      $('examMeta').textContent = pack.pattern_summary || pack.exam_code || '';
      $('examCard').style.display = '';
      const score = r.score || 0;
      $('readinessBar').style.width = Math.max(2, Math.min(100, score)) + '%';
      $('readinessTag').textContent =
        'Readiness ' + (r.score != null ? r.score.toFixed(0) + '%' : '—');
    }
  }

  function renderMetrics(dash){
    const m = (dash && dash.metrics) || {};
    $('m1').textContent = m.due_flashcards != null ? m.due_flashcards : '—';
    $('m2').textContent = m.weak_topic_count != null ? m.weak_topic_count : '—';
    const t = dash && dash.trust;
    $('m3').textContent = (t && t.grounded_rate != null)
      ? (t.grounded_rate * 100).toFixed(0) + '%'
      : '—';
  }

  function kindTagClass(kind){
    return ({practice:'violet', read:'green', mock:'amber',
             revise:'teal', current_affairs:'amber'})[kind] || 'green';
  }

  function renderPlan(dash){
    const wrap = $('planBlocks');
    const meta = $('planMeta');
    if(!dash || !dash.daily_flow || !dash.daily_flow.blocks
              || !dash.daily_flow.blocks.length){
      meta.textContent =
        'No active Exam Pack yet — enroll in one to get a daily plan.';
      wrap.innerHTML = '';
      return;
    }
    const f = dash.daily_flow;
    meta.textContent = (f.total_minutes || 0) + ' min target · '
                     + (f.completion_pct || 0).toFixed(0) + '% complete';
    wrap.innerHTML = f.blocks.map(b => (
      '<div class="study-step' + (b.completed ? ' done' : '') + '">'
      + '<div class="step-dot">' + b.position + '</div>'
      + '<div><div class="step-title">' + escapeHtml(b.title) + '</div>'
      + '<div class="step-meta">' + escapeHtml(b.kind) + ' · '
      + (b.estimated_min || 0) + ' min</div></div>'
      + '<div class="tag ' + kindTagClass(b.kind) + '">'
      + escapeHtml(b.kind) + '</div>'
      + '</div>'
    )).join('');
  }

  function renderNext(dash){
    const wrap = $('nextActions');
    const cards = [];
    const nm = dash && dash.next_mock;
    if(nm){
      cards.push(
        '<div class="study-step">'
        + '<div class="step-dot">M</div>'
        + '<div><div class="step-title">Mock — '
        + escapeHtml(nm.title || 'pending') + '</div>'
        + '<div class="step-meta">' + escapeHtml(nm.mode || 'full')
        + ' · ' + (nm.time_min || 0) + ' min · '
        + (nm.total_questions || 0) + ' questions</div></div>'
        + '<div class="tag violet">mock</div></div>'
      );
    }
    cards.push(
      '<div class="study-step" onclick="document.getElementById('
      + '\\'group-study-studio\\').scrollIntoView({behavior:\\'smooth\\'})" '
      + 'style="cursor:pointer">'
      + '<div class="step-dot">S</div>'
      + '<div><div class="step-title">Open Study Studio</div>'
      + '<div class="step-meta">Upload, notes, flashcards, quiz, '
      + 'audio recap.</div></div>'
      + '<div class="tag teal">tools</div></div>'
      + '<div class="study-step" onclick="document.getElementById('
      + '\\'group-ai-tutor\\').scrollIntoView({behavior:\\'smooth\\'})" '
      + 'style="cursor:pointer">'
      + '<div class="step-dot">T</div>'
      + '<div><div class="step-title">Ask the AI Tutor with citations</div>'
      + '<div class="step-meta">Source-grounded answers with page-level '
      + 'provenance.</div></div>'
      + '<div class="tag green">cite</div></div>'
    );
    wrap.innerHTML = cards.join('');
  }

  function renderRightbar(dash){
    if(!dash) return;
    const pack = dash.exam_pack || {};
    if(pack.exam_code){
      $('communityText').textContent = (pack.exam_code).toUpperCase()
        + ' room — share notes, debate PYQ traps, get mentor reviews.';
    }
    const t = dash.trust || {};
    if(t.sample_size){
      $('trustText').textContent =
        (t.grounded_rate * 100).toFixed(0) + '% of your last '
        + t.sample_size + ' AI answers carried citations.';
    }
    const fb = (dash.recent_fallbacks || []).length;
    $('fallbackText').textContent = fb > 0
      ? fb + ' recent declines — upload more material so the tutor '
            + 'can ground its answers.'
      : 'No recent fallbacks. The tutor is finding sources for '
        + 'your questions.';
  }

  async function boot(){
    manifest = await getJSON('/api/navigation/manifest');
    renderSidebar(manifest);
    renderSectionGroups(manifest);
    const dash = await getJSON('/api/home/me/dashboard');
    renderHero(dash);
    renderMetrics(dash);
    renderPlan(dash);
    renderNext(dash);
    renderRightbar(dash);
  }
  boot();
})();

// Service worker — offline-pack support (review §17, v3.12)
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch(() => {});
  });
}

// applyBranding — recolour CSS vars from /api/orgs/{id}/branding
(function applyBranding(){
  try {
    const orgId = (document.cookie.match(/org_id=([^;]+)/) || [])[1];
    if (!orgId) return;
    fetch('/api/orgs/' + encodeURIComponent(orgId) + '/branding',
          {credentials:'include'})
      .then(r => r.ok ? r.json() : null)
      .then(b => {
        if (!b) return;
        const root = document.documentElement.style;
        if (b.primary_color) root.setProperty('--brand', b.primary_color);
        if (b.logo_url) {
          const logo = document.querySelector('.brand .logo');
          if (logo) {
            logo.style.background =
              'center/contain no-repeat url(' + b.logo_url + ')';
            logo.textContent = '';
          }
        }
      })
      .catch(() => {});
  } catch(_) {}
})();
</script>
</body>
</html>
"""


LANDING_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PadhaiApp — Sign in</title>
<link rel="manifest" href="/manifest.json">
<meta name="theme-color" content="#1565d8">
<style>
  *{box-sizing:border-box}
  body{font-family:Inter,Segoe UI,Arial,sans-serif;margin:0;
       background:#0f1c33;color:#fff;min-height:100vh;
       display:flex;align-items:center;justify-content:center;
       padding:24px}
  .card{max-width:440px;width:100%;background:#ffffff10;
        border:1px solid #ffffff22;border-radius:14px;padding:32px}
  h1{margin:0 0 8px;font-size:24px;line-height:1.2}
  .lead{color:#cdd6e5;line-height:1.5;font-size:14px;margin:0 0 22px}
  .tabs{display:flex;gap:4px;background:#ffffff0e;padding:4px;
        border-radius:8px;margin-bottom:18px}
  .tab{flex:1;background:transparent;border:0;color:#cdd6e5;
       padding:10px;font-size:14px;font-weight:700;cursor:pointer;
       border-radius:6px;font-family:inherit}
  .tab.active{background:#1565d8;color:#fff}
  label{display:block;font-size:12px;color:#aab5cc;
        margin:10px 0 6px;font-weight:700}
  input{width:100%;padding:10px 12px;border:1px solid #ffffff22;
        background:#ffffff14;color:#fff;border-radius:8px;
        font-size:14px;font-family:inherit}
  input:focus{outline:none;border-color:#1565d8}
  button.cta{width:100%;background:#1565d8;color:#fff;border:0;
             padding:12px;border-radius:8px;font-weight:800;
             font-size:14px;cursor:pointer;margin-top:18px;
             font-family:inherit}
  button.cta:hover{background:#0b4ec1}
  .alt{margin-top:18px;text-align:center;font-size:13px;color:#9aa6c0}
  .alt a{color:#fff;text-decoration:underline;font-weight:700}
  .err{margin-top:12px;color:#ffb4b4;font-size:13px;display:none}
  .err.show{display:block}
</style>
</head>
<body>
<div class="card">
  <h1>PadhaiApp</h1>
  <p class="lead">
    India's AI study, exam &amp; career platform. Sign in to see
    your goal-led Exam Hub.
  </p>
  <div class="tabs">
    <button class="tab active" data-mode="login">Sign in</button>
    <button class="tab" data-mode="signup">Create account</button>
  </div>
  <form id="authForm">
    <label for="email">Email</label>
    <input id="email" type="email" autocomplete="email" required>
    <label for="password">Password</label>
    <input id="password" type="password" autocomplete="current-password"
           required minlength="8">
    <div id="signupOnly" style="display:none">
      <label for="display_name">Your name</label>
      <input id="display_name" type="text" autocomplete="name">
    </div>
    <button class="cta" id="submitBtn">Sign in</button>
    <div class="err" id="errBox"></div>
  </form>
  <div class="alt">
    <a href="/home">Open Exam Hub</a>
    &nbsp;·&nbsp;
    <a href="/ui-legacy">Legacy dashboard</a>
  </div>
</div>
<script>
(function(){
  let mode = 'login';
  const tabs = document.querySelectorAll('.tab');
  const submitBtn = document.getElementById('submitBtn');
  const signupOnly = document.getElementById('signupOnly');
  const errBox = document.getElementById('errBox');
  tabs.forEach(t => {
    t.addEventListener('click', () => {
      mode = t.dataset.mode;
      tabs.forEach(b => b.classList.toggle('active', b === t));
      submitBtn.textContent =
        mode === 'login' ? 'Sign in' : 'Create account';
      signupOnly.style.display = mode === 'login' ? 'none' : '';
      errBox.classList.remove('show');
    });
  });
  document.getElementById('authForm').addEventListener(
    'submit', async (ev) => {
      ev.preventDefault();
      errBox.classList.remove('show');
      const fd = new FormData();
      fd.append('email', document.getElementById('email').value);
      fd.append('password', document.getElementById('password').value);
      if (mode === 'signup') {
        const dn = document.getElementById('display_name').value;
        if (dn) fd.append('display_name', dn);
      }
      submitBtn.disabled = true;
      submitBtn.textContent = mode === 'login'
        ? 'Signing in…' : 'Creating…';
      try {
        const r = await fetch('/auth/' + mode, {
          method: 'POST', body: fd, credentials: 'include',
        });
        if (r.ok) {
          try {
            const data = await r.json();
            if (data && data.token) {
              localStorage.setItem('padhai_token', data.token);
            }
          } catch(_) {}
          location.href = '/home';
          return;
        }
        let msg = 'Auth failed (HTTP ' + r.status + ').';
        try {
          const j = await r.json();
          if (j && (j.detail || j.message)) {
            msg = j.detail || j.message;
          }
        } catch(_) {}
        errBox.textContent = msg;
        errBox.classList.add('show');
      } catch (e) {
        errBox.textContent = 'Network error: ' + e.message;
        errBox.classList.add('show');
      } finally {
        submitBtn.disabled = false;
        submitBtn.textContent =
          mode === 'login' ? 'Sign in' : 'Create account';
      }
    },
  );
})();
</script>
</body>
</html>
"""


def get_home_html() -> str:
    """The goal-led home that consumes /api/navigation/manifest +
    /api/home/me/dashboard."""
    return HOME_HTML


def get_landing_html() -> str:
    """Public landing for unauthed visitors. Carries an inline
    sign-in/signup form that POSTs to /auth/login or /auth/signup
    (existing endpoints)."""
    return LANDING_HTML
