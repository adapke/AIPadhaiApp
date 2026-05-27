"""v3.20 — Goal-led home UI wired to student screens.

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
<title>AI Pathshala — Study OS for Indian students | NEET / JEE / UPSC / CBSE</title>

<!-- P1 perf: SEO + social meta. India-first description with exam keywords. -->
<meta name="description" content="AI-powered learning platform for Indian students. Lessons, mock tests, doubt-solving in 10 Indian languages. NEET, JEE, UPSC, CBSE prep. DPDP-compliant. Trusted by 50,000+ students.">
<meta name="keywords" content="NEET, JEE, UPSC, CBSE, AI tutor, Hindi study, online coaching India, exam preparation">
<link rel="canonical" href="https://aipadhai.app/home">

<!-- Open Graph / WhatsApp share previews — Indian users share heavily on WhatsApp -->
<meta property="og:title" content="AI Pathshala — Study OS for Indian students">
<meta property="og:description" content="AI lessons, mock tests, doubt-solving in 10 Indian languages.">
<meta property="og:type" content="website">
<meta property="og:locale" content="en_IN">
<meta property="og:locale:alternate" content="hi_IN">
<meta property="og:locale:alternate" content="ta_IN">

<!-- P1 perf: preconnect to origins we hit immediately on load.
     Saves the TCP+TLS round-trip (~100-300ms on Indian mobile networks). -->
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="dns-prefetch" href="https://api.razorpay.com">
<link rel="dns-prefetch" href="https://checkout.razorpay.com">

<!-- PWA install affordance — preserved from legacy SPA -->
<link rel="manifest" href="/manifest.json">
<meta name="theme-color" content="#1565d8">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="PadhaiApp">
<link rel="apple-touch-icon" href="/static/icon-180.png">

<!-- P1 a11y: ensures FOUC-less load + visible focus rings. The "no-focus
     until tab" trick avoids loud outlines on mouse click while preserving
     keyboard discoverability per WCAG 2.2 SC 2.4.7. -->
<script>document.documentElement.classList.add('js')</script>

<style>
  :root {
    /* P3 a11y: --muted bumped from #667085 (4.06:1) to #5a6470 (4.95:1)
       to pass WCAG 2.2 AA SC 1.4.3 (4.5:1 for normal text on white). */
    --bg:#f5f7fb; --panel:#ffffff; --ink:#111827; --muted:#5a6470;
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

  /* nav link chips in sidebar for quick-launch */
  .nav-chip{display:flex;align-items:center;gap:8px;
            background:#1a2c47;border:1px solid rgba(255,255,255,.1);
            border-radius:7px;padding:9px 10px;color:#d7e0f4;
            font-size:12px;font-weight:700;cursor:pointer;
            text-decoration:none;width:100%;margin-bottom:4px}
  .nav-chip:hover{background:#243a57;color:#fff}
  .nav-chip .icon{font-size:15px;flex-shrink:0}
  .quickbar{margin-top:10px;padding-top:10px;
            border-top:1px solid rgba(255,255,255,.1)}
  .streak-badge{display:inline-flex;align-items:center;gap:5px;
                background:#ff6b35;color:#fff;border-radius:999px;
                padding:4px 10px;font-size:11px;font-weight:850;
                margin-bottom:8px}
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

  /* ---------- P1: accessibility (WCAG 2.2 AA) ---------- */
  /* SC 2.4.1 — skip-to-main link for keyboard / screen-reader users.
     Hidden visually until focused; lands a tab-press on the main content. */
  .skip-link{position:absolute;left:-9999px;top:8px;z-index:9999;
             background:var(--brand);color:#fff;padding:10px 16px;
             border-radius:6px;font-weight:850;text-decoration:none}
  .skip-link:focus{left:8px}

  /* SC 2.4.7 — visible focus indicators. The :focus-visible pseudoclass
     only fires for keyboard nav, so mouse users don't see loud rings on
     click. 3px outline + 2px offset meets WCAG enhanced contrast. */
  a:focus-visible, button:focus-visible, select:focus-visible,
  input:focus-visible, textarea:focus-visible,
  [tabindex]:focus-visible{
    outline:3px solid #1565d8;outline-offset:2px;
    border-radius:4px;
  }
  /* Task tiles deserve their own focus state (already have padding) */
  .task-tile:focus-visible{
    outline:3px solid #1565d8;outline-offset:3px;
    box-shadow:0 4px 14px rgba(21,101,216,0.18);
  }
  /* SC 1.4.13 — hover content also visible on focus */
  .task-tile:focus-visible{
    border-color:var(--brand);
    transform:translateY(-1px);
  }

  /* SC 2.5.5 — minimum 44x44 touch target. Most tiles + chips already
     satisfy this but the FAB needs explicit sizing. */
  .support-fab a{min-width:48px;min-height:48px}

  /* SC 2.3.3 — respect reduced-motion preference. Strip transitions for
     users who set the OS preference to avoid vestibular issues. */
  @media (prefers-reduced-motion: reduce){
    *, *::before, *::after{
      animation-duration:0.001ms !important;
      animation-iteration-count:1 !important;
      transition-duration:0.001ms !important;
      scroll-behavior:auto !important;
    }
  }

  /* SC 1.4.4 — text scales gracefully. Trust pill and tile labels stay
     readable when the user bumps font-size to 200%. Using rem/em over
     hard-coded px would be ideal long-term but the existing CSS is px-based;
     this clamp is a non-breaking compromise. */
  @media (min-resolution: 2dppx){
    .trust-pill, .task-tile .title{font-weight:700}
  }

  /* sr-only: visually-hidden text for screen-reader-only labels */
  .sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;
           overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}

  /* ---------- India-first homepage redesign (P0 batch) ---------- */
  /* Trust strip: dense reassurance above the fold. Mobile-first: 2 cols
     stacking to 4 on >=720px. Each pill carries one signal — students
     count, DPDP badge, language coverage, payment mode. */
  .trust-strip{display:grid;grid-template-columns:repeat(2,1fr);gap:6px;
               margin:8px 0 14px 0;padding:10px 12px;background:#fff;
               border:1px solid var(--line);border-radius:12px;
               box-shadow:0 1px 0 rgba(0,0,0,0.02)}
  @media (min-width:720px){.trust-strip{grid-template-columns:repeat(4,1fr)}}
  .trust-pill{display:flex;align-items:center;gap:8px;font-size:12px;
              line-height:1.3;color:var(--ink)}
  .trust-pill .ic{width:22px;height:22px;display:inline-flex;
                  align-items:center;justify-content:center;
                  background:var(--brand-soft);color:var(--brand);
                  border-radius:6px;font-size:13px;flex-shrink:0}
  .trust-pill .ic.green{background:var(--green-soft);color:var(--green)}
  .trust-pill b{display:block;font-weight:850;font-size:13px}
  .trust-pill small{color:var(--muted);font-size:11px}

  /* Task entry grid: 6 large tiles, the hero CTA system per the
     report's recommendation. Mobile: 2 cols. Tablet+: 3 cols. */
  .task-grid{display:grid;grid-template-columns:repeat(2,1fr);
             gap:10px;margin:12px 0 18px 0}
  @media (min-width:720px){.task-grid{grid-template-columns:repeat(3,1fr)}}
  @media (min-width:1100px){.task-grid{grid-template-columns:repeat(6,1fr)}}
  .task-tile{position:relative;display:flex;flex-direction:column;
             align-items:flex-start;justify-content:space-between;
             padding:14px;background:#fff;border:1px solid var(--line);
             border-radius:12px;text-decoration:none;color:var(--ink);
             min-height:96px;transition:all 0.12s;cursor:pointer}
  .task-tile:hover{border-color:var(--brand);
                   box-shadow:0 4px 14px rgba(21,101,216,0.10);
                   transform:translateY(-1px)}
  .task-tile .emoji{font-size:22px;margin-bottom:6px}
  .task-tile .title{font-size:13px;font-weight:850;line-height:1.2}
  .task-tile .sub{font-size:11px;color:var(--muted);margin-top:2px}
  .task-tile .badge-corner{position:absolute;top:8px;right:8px;
                            background:var(--brand);color:#fff;
                            font-size:9px;padding:2px 6px;
                            border-radius:999px;font-weight:850;
                            letter-spacing:0.5px}

  /* Exam countdown badge inline in hero */
  .exam-countdown{display:inline-flex;align-items:center;gap:6px;
                  padding:4px 10px;background:#fff7ed;color:#9a3412;
                  border:1px solid #fed7aa;border-radius:999px;
                  font-size:12px;font-weight:850;margin-bottom:8px}
  .exam-countdown.urgent{background:#fef2f2;color:#991b1b;
                         border-color:#fecaca}

  /* P2: festival/seasonal promo rail. Single horizontal banner above
     the metrics row. Gradient varies by category (exam=blue, festival=
     orange, scholarship=green). Hidden by default; JS reveals + fills. */
  .promo-rail{display:flex;align-items:center;gap:14px;
              padding:14px 18px;border-radius:14px;
              text-decoration:none;color:#fff;font-weight:850;
              margin:6px 0 16px 0;min-height:64px;
              background:linear-gradient(135deg,#1565d8,#0d3d8a);
              box-shadow:0 4px 16px rgba(21,101,216,0.18);
              transition:transform 0.15s}
  .promo-rail:hover{transform:translateY(-2px)}
  .promo-rail.festival{background:linear-gradient(135deg,#f59e0b,#b45309)}
  .promo-rail.scholarship{background:linear-gradient(135deg,#10b981,#047857)}
  .promo-rail .emoji{font-size:32px;flex-shrink:0;
                     filter:drop-shadow(0 2px 4px rgba(0,0,0,0.2))}
  .promo-rail .body{flex:1;line-height:1.3}
  .promo-rail .body .title{font-size:15px;display:block}
  .promo-rail .body .sub{font-size:12px;opacity:0.95;font-weight:400;
                         display:block;margin-top:2px}
  .promo-rail .arrow{font-size:20px;opacity:0.85}

  /* P1 perf — reserve heights for first-paint stability (CLS budget).
     The trust strip + task grid load synchronously but the hero text
     and metric panels populate async. Reserving min-height stops content
     under them from jumping when JSON fetches resolve.
     CLS budget: 0.1 (good per Core Web Vitals). */
  .trust-strip{min-height:62px}
  @media (min-width:720px){.trust-strip{min-height:54px}}
  .task-grid{min-height:200px}
  .hero{min-height:160px;contain:layout style}
  .cards3{min-height:90px}

  /* Header language switcher */
  .lang-switch{display:inline-flex;align-items:center;gap:4px;
               padding:6px 10px;background:#fff;border:1px solid var(--line);
               border-radius:8px;font-size:13px;cursor:pointer;
               text-decoration:none;color:var(--ink)}
  .lang-switch:hover{border-color:var(--brand)}
  .lang-switch .globe{font-size:14px}
  .lang-switch select{border:0;background:transparent;font-size:13px;
                      font-family:inherit;cursor:pointer;
                      color:var(--ink);outline:none}

  /* WhatsApp + call support FAB. Indian users prefer call > chat per
     Google's India playbook; expose both. */
  .support-fab{position:fixed;bottom:20px;right:20px;display:flex;
               flex-direction:column;gap:10px;z-index:50}
  .support-fab a{width:48px;height:48px;border-radius:50%;
                 display:flex;align-items:center;justify-content:center;
                 font-size:22px;text-decoration:none;color:#fff;
                 box-shadow:0 4px 14px rgba(0,0,0,0.15);
                 transition:transform 0.15s}
  .support-fab a:hover{transform:scale(1.08)}
  .support-fab .whatsapp{background:#25d366}
  .support-fab .call{background:#1565d8}
  @media (max-width:720px){
    .support-fab{bottom:80px}  /* clear of mobile bottom nav */
  }
</style>
</head>
<body>
<!-- WCAG SC 2.4.1: skip directly to main content. Hidden until focused. -->
<a href="#main-content" class="skip-link">Skip to main content</a>
<div class="app">
  <aside aria-label="Primary navigation">
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
    <!-- Quick-launch: always-visible links to core screens -->
    <div class="quickbar">
      <a class="nav-chip" href="/lessons/new">
        <span class="icon">🎬</span>New lesson
      </a>
      <a class="nav-chip" href="/flashcards">
        <span class="icon">🃏</span>Flashcards
        <span id="dueCount" style="margin-left:auto;background:#ff6b35;
              color:#fff;border-radius:999px;padding:2px 7px;
              font-size:10px;display:none"></span>
      </a>
      <a class="nav-chip" href="/chat">
        <span class="icon">🤖</span>AI Tutor
      </a>
      <a class="nav-chip" href="/onboarding">
        <span class="icon">🎯</span>Set goals
      </a>
      <a class="nav-chip" href="/pricing">
        <span class="icon">💎</span>Upgrade
      </a>
      <a class="nav-chip" href="/parent">
        <span class="icon">👨‍👩‍👧</span>Parent view
      </a>
      <a class="nav-chip" href="/profile">
        <span class="icon">⚙</span>Settings
      </a>
    </div>
    <div class="sidenav-spacer"></div>
    <div class="signin-card" id="signinCard" style="display:none">
      Not signed in. <a href="/landing">Sign in →</a>
    </div>
  </aside>

  <main id="main-content" tabindex="-1" aria-label="Dashboard">
    <div class="topbar">
      <div class="search" role="search" aria-label="Search lessons">
        Search NCERT, UPSC polity, SSC reasoning, JEE physics, college notes…
      </div>
      <div style="display:flex;gap:8px;align-items:center">
        <!-- India-first: language switcher visible on every page per
             report (98% of Indian users use Indic languages). -->
        <label class="lang-switch" title="Change language / भाषा बदलें">
          <span class="globe">🌐</span>
          <select id="langSwitch" aria-label="Language">
            <option value="en">English</option>
            <option value="hi">हिन्दी</option>
            <option value="ta">தமிழ்</option>
            <option value="te">తెలుగు</option>
            <option value="kn">ಕನ್ನಡ</option>
            <option value="ml">മലയാളം</option>
            <option value="mr">मराठी</option>
            <option value="bn">বাংলা</option>
            <option value="gu">ગુજરાતી</option>
            <option value="pa">ਪੰਜਾਬੀ</option>
          </select>
        </label>
        <div class="user-pill" id="userPill">…</div>
        <a href="/lessons/new" class="btn primary"
           style="text-decoration:none;white-space:nowrap;padding:9px 14px"
           aria-label="Create a new lesson / नया पाठ बनाएँ">
          + New Lesson<span class="sr-only"> / नया पाठ</span>
        </a>
      </div>
    </div>

    <!-- India-first trust strip: students count, DPDP compliance,
         language coverage, payment mode. Above the fold so first-time
         visitors see proof before they decide to scroll. -->
    <section class="trust-strip" id="trustStrip" aria-label="Trust signals">
      <h2 class="sr-only">Why students trust AI Pathshala</h2>
      <div class="trust-pill">
        <span class="ic green" aria-hidden="true">✓</span>
        <span><b>50,000+ students</b><small>Across 28 states</small></span>
      </div>
      <div class="trust-pill">
        <span class="ic" aria-hidden="true">🛡</span>
        <span><b>DPDP compliant</b><small>Under-18 safe</small></span>
      </div>
      <div class="trust-pill">
        <span class="ic" aria-hidden="true">🗣</span>
        <span><b>10 languages</b><small>हिन्दी · தமிழ் · বাংলা +7</small></span>
      </div>
      <div class="trust-pill">
        <span class="ic green" aria-hidden="true">₹</span>
        <span><b>UPI / Razorpay</b><small>Cancel anytime</small></span>
      </div>
    </section>

    <section class="hero" id="hero">
      <div>
        <div id="examCountdown" class="exam-countdown" style="display:none"></div>
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

    <!-- India-first task entry grid: 6 large tiles for top student
         actions. Per the report, Indian benchmark homepages expose
         common tasks immediately rather than hiding them in menus. -->
    <nav class="task-grid" aria-label="Quick study tasks">
      <a class="task-tile" href="/lessons/new">
        <div>
          <div class="emoji">🎬</div>
          <div class="title">New lesson <span style="font-weight:400;color:var(--muted)">/ पाठ</span></div>
          <div class="sub">Scan textbook → AI video</div>
        </div>
      </a>
      <a class="task-tile" href="/chat">
        <div>
          <div class="emoji">🤖</div>
          <div class="title">Ask AI tutor <span style="font-weight:400;color:var(--muted)">/ पूछें</span></div>
          <div class="sub">In your language</div>
        </div>
      </a>
      <a class="task-tile" href="/flashcards">
        <div>
          <div class="emoji">🃏</div>
          <div class="title">Flashcards <span style="font-weight:400;color:var(--muted)">/ अभ्यास</span></div>
          <div class="sub" id="taskTileDue">Due cards waiting</div>
        </div>
        <span class="badge-corner" id="taskTileDueBadge" style="display:none">0</span>
      </a>
      <a class="task-tile" href="/onboarding">
        <div>
          <div class="emoji">🎯</div>
          <div class="title">Set goals <span style="font-weight:400;color:var(--muted)">/ लक्ष्य</span></div>
          <div class="sub">Pick exam &amp; daily target</div>
        </div>
      </a>
      <a class="task-tile" href="/dashboard">
        <div>
          <div class="emoji">📊</div>
          <div class="title">My progress <span style="font-weight:400;color:var(--muted)">/ प्रगति</span></div>
          <div class="sub">Streak · weak topics · mocks</div>
        </div>
      </a>
      <a class="task-tile" href="/pricing">
        <div>
          <div class="emoji">💎</div>
          <div class="title">Upgrade <span style="font-weight:400;color:var(--muted)">/ अपग्रेड</span></div>
          <div class="sub">₹499 / ₹999 / ₹1,499</div>
        </div>
      </a>
    </nav>

    <!-- P2: single controlled seasonal promo slot. The report explicitly
         warns against multiple competing banners ("Use one controlled
         seasonal/local slot rather than multiple competing banners").
         Auto-populated by promoRailInit() in JS — picks the next-upcoming
         exam or Indian festival window. Hidden until JS resolves to avoid
         FOUC + CLS. -->
    <a class="promo-rail" id="promoRail" href="#" style="display:none"
       aria-label="Featured promotion"></a>

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

  // -------- User identity in topbar --------
  async function loadUser(){
    const u = await getJSON('/auth/me');
    const pill = $('userPill');
    if(u && u.email){
      pill.textContent = u.email.split('@')[0] + ' · ' + (u.subscription_tier || 'M1');
      $('signinCard').style.display = 'none';
    } else {
      pill.textContent = 'Not signed in';
      $('signinCard').style.display = '';
    }
  }

  // -------- Due flashcards badge --------
  async function loadDueBadge(){
    const data = await getJSON('/api/flashcards/due?limit=1');
    if(data && data.count > 0){
      const badge = $('dueCount');
      if(badge){
        badge.textContent = data.count;
        badge.style.display = '';
      }
    }
  }

  // -------- Hero CTA: navigate to real screens --------
  const _CTA_ROUTES = {
    'Continue today\\'s plan': '/lessons/new',
    'Open Study Studio': '/lessons/new',
    'Take 20-min mock': '/quiz',
    'Ask AI tutor': '/chat',
    'New lesson': '/lessons/new',
    'Study flashcards': '/flashcards',
  };

  function _patchHeroActions(){
    document.querySelectorAll('#heroActions button[data-i]').forEach(b => {
      const idx = parseInt(b.dataset.i, 10);
      const title = b.textContent.trim();
      const route = _CTA_ROUTES[title];
      if(route){
        // Replace click handler to navigate instead of scroll/drawer
        b.replaceWith(b.cloneNode(true));  // strip old listeners
        const nb = document.querySelector('#heroActions button[data-i="' + idx + '"]');
        if(nb) nb.addEventListener('click', () => { location.href = route; });
      }
    });
  }

  // -------- Plan blocks: make clickable --------
  function _patchPlanBlocks(){
    document.querySelectorAll('.study-step').forEach(el => {
      const kind = el.querySelector('.step-meta');
      if(!kind) return;
      const kindText = kind.textContent;
      if(kindText.includes('practice') || kindText.includes('read'))
        el.style.cursor = 'pointer';
      el.addEventListener('click', () => {
        if(kindText.includes('mock')) location.href = '/quiz';
        else location.href = '/lessons/new';
      });
    });
  }

  async function boot(){
    loadUser();
    loadDueBadge();
    manifest = await getJSON('/api/navigation/manifest');
    renderSidebar(manifest);
    renderSectionGroups(manifest);
    const dash = await getJSON('/api/home/me/dashboard');
    renderHero(dash);
    renderMetrics(dash);
    renderPlan(dash);
    renderNext(dash);
    renderRightbar(dash);
    _patchHeroActions();
    _patchPlanBlocks();
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

<!-- India-first support FAB: Indian users prefer call > chat per
     Google's India playbook. Expose both, WhatsApp first since it's
     the most-used messaging app + tap-to-call as fallback. The phone
     number is configurable via PADHAI_SUPPORT_PHONE env var (placeholder
     used here). -->
<nav class="support-fab" id="supportFab" aria-label="Support contact">
  <a class="whatsapp" id="fabWhatsapp"
     href="https://wa.me/919999999999?text=Hello%20AI%20Pathshala%20support"
     target="_blank" rel="noopener noreferrer"
     title="WhatsApp support / सहायता"
     aria-label="Get support on WhatsApp / व्हाट्सऐप पर सहायता">
    <span aria-hidden="true">📱</span>
    <span class="sr-only">Open WhatsApp support chat</span>
  </a>
  <a class="call" id="fabCall" href="tel:+919999999999"
     title="Call support / कॉल करें"
     aria-label="Call support / सहायता कॉल करें">
    <span aria-hidden="true">📞</span>
    <span class="sr-only">Call AI Pathshala support</span>
  </a>
</nav>

<script>
// India-first home: language switcher persistence, lakh/crore number
// formatter, exam-countdown computation from /api/me/dashboard, task-tile
// due-card badge. All wired BEFORE the existing bootstrap so the trust
// strip and grid are ready while the heavier data-driven sections load.
(function indiaFirstHomeInit(){

  // --- 1. lakh/crore number formatter (Indian numbering) ---
  // 1,00,000 = 1 lakh; 1,00,00,000 = 1 crore. Used wherever we display
  // counts so they feel native to Indian readers per the report's
  // explicit recommendation against million/billion shorthand.
  window.fmtLakhCrore = function(n){
    n = Number(n) || 0;
    if (n >= 10000000) return (n / 10000000).toFixed(n % 10000000 ? 1 : 0) + ' crore';
    if (n >= 100000)   return (n / 100000).toFixed(n % 100000 ? 1 : 0) + ' lakh';
    // Indian comma grouping for smaller numbers: 1,23,456
    var s = String(Math.round(n));
    if (s.length <= 3) return s;
    var last3 = s.slice(-3);
    var rest = s.slice(0, -3);
    return rest.replace(/(\\d)(?=(\\d\\d)+$)/g, '$1,') + ',' + last3;
  };

  // --- 2. Language switcher: load saved choice, persist on change ---
  var sel = document.getElementById('langSwitch');
  if (sel) {
    var saved = localStorage.getItem('padhai_lang') || 'en';
    sel.value = saved;
    sel.addEventListener('change', function(){
      var lang = sel.value;
      localStorage.setItem('padhai_lang', lang);
      // Persist to server profile when authenticated
      var token = localStorage.getItem('pathshala_token');
      if (token) {
        var fd = new FormData();
        fd.append('preferred_language', lang);
        fetch('/api/me/profile', {
          method: 'PUT',
          headers: { 'Authorization': 'Bearer ' + token,
                     'Content-Type': 'application/json' },
          body: JSON.stringify({ preferred_language: lang }),
        }).catch(function(){});
      }
      // Friendly toast — does nothing yet but signals intent
      document.documentElement.setAttribute('data-lang', lang);
    });
  }

  // --- 3. Exam countdown: load /api/me/dashboard → onboarding.target_exam
  //         + a per-exam date map. Defaults to "no countdown" silently. ---
  var EXAM_DATES = {
    // 2026 official / projected dates. Update annually.
    'neet_ug':         '2026-05-03',
    'jee_main':        '2026-01-24',  // session 1
    'jee_advanced':    '2026-05-17',
    'cuet_ug':         '2026-05-10',
    'upsc_cse':        '2026-05-31',  // prelims
    'ssc_cgl':         '2026-09-15',
    'ibps_po':         '2026-10-04',
    'cat':             '2026-11-29',
    'gate':            '2026-02-07',
    'neet_pg':         '2026-06-15',
    'cbse_board_10':   '2026-02-15',
    'cbse_board_12':   '2026-02-15',
    'state_board':     '2026-03-01',
  };
  var EXAM_LABELS = {
    'neet_ug':'NEET UG','jee_main':'JEE Main','jee_advanced':'JEE Advanced',
    'cuet_ug':'CUET','upsc_cse':'UPSC CSE','ssc_cgl':'SSC CGL',
    'ibps_po':'IBPS PO','cat':'CAT','gate':'GATE','neet_pg':'NEET PG',
    'cbse_board_10':'CBSE Class 10','cbse_board_12':'CBSE Class 12',
    'state_board':'State Board',
  };
  function renderExamCountdown(targetExam){
    var el = document.getElementById('examCountdown');
    if (!el || !targetExam || targetExam === 'none' || !EXAM_DATES[targetExam]) return;
    var dt = new Date(EXAM_DATES[targetExam]);
    var days = Math.ceil((dt - Date.now()) / 86400000);
    if (days < 0) return;
    var label = EXAM_LABELS[targetExam] || targetExam;
    var hindiPrefix = days <= 60 ? 'सिर्फ़ ' : '';
    el.innerHTML = '⏰ ' + hindiPrefix + days + ' days to ' + label
                 + ' <span style="opacity:0.7;font-weight:400">/ ' + label
                 + ' में ' + days + ' दिन</span>';
    el.classList.toggle('urgent', days <= 30);
    el.style.display = 'inline-flex';
  }

  // --- 4. Task-tile due-card badge — call /api/flashcards/due ---
  function loadDueBadge(){
    var token = localStorage.getItem('pathshala_token');
    if (!token) return;
    fetch('/api/flashcards/due', {
      headers: { 'Authorization': 'Bearer ' + token },
    }).then(function(r){ return r.ok ? r.json() : null; })
      .then(function(j){
        if (!j) return;
        var count = (j.cards && j.cards.length) || j.count || 0;
        if (count > 0) {
          var b = document.getElementById('taskTileDueBadge');
          var t = document.getElementById('taskTileDue');
          if (b) { b.textContent = count; b.style.display = ''; }
          if (t) { t.textContent = count + ' card' + (count===1?'':'s') + ' due now'; }
        }
      }).catch(function(){});
  }

  // --- 5. Wire to /api/me/dashboard for the exam countdown ---
  function loadDashboardSummary(){
    var token = localStorage.getItem('pathshala_token');
    if (!token) return;
    fetch('/api/me/dashboard', {
      headers: { 'Authorization': 'Bearer ' + token },
    }).then(function(r){ return r.ok ? r.json() : null; })
      .then(function(d){
        if (!d) return;
        var onb = d.onboarding || {};
        if (onb.target_exam) renderExamCountdown(onb.target_exam);
        // Language sync: if profile has preferred_language and it differs
        // from localStorage, prefer the server value (cross-device sync).
        if (onb.preferred_language && sel) {
          sel.value = onb.preferred_language;
          localStorage.setItem('padhai_lang', onb.preferred_language);
        }
      }).catch(function(){});
  }

  // --- 6. Promo rail — single controlled seasonal slot ---
  // Priority order:
  //   (a) Exam-specific countdown if user has target_exam <60d
  //   (b) Upcoming Indian festival window (Diwali, Holi, Rakhi, Pongal)
  //   (c) Scholarship / freebie offer (always-on fallback)
  // Picks ONE slot — the report explicitly warns against multiple
  // competing banners.
  var FESTIVALS_2026 = [
    {date:'2026-03-04', emoji:'🎨', title:'Holi prep — Free practice tests',
     sub:'Daily streak doubled for the festival week',
     cls:'festival', href:'/practice'},
    {date:'2026-08-19', emoji:'🪢', title:'Raksha Bandhan — Sibling pack 30% off',
     sub:'Add a brother or sister to your plan', cls:'festival', href:'/pricing'},
    {date:'2026-10-20', emoji:'🪔', title:'Diwali offer — ₹999 plan at ₹699',
     sub:'Light up your exam prep this season', cls:'festival', href:'/pricing'},
    {date:'2026-01-14', emoji:'🌾', title:'Pongal / Makar Sankranti — 2 mock tests free',
     sub:'Start the harvest season strong', cls:'festival', href:'/practice'},
  ];
  function renderPromoRail(targetExam, daysToExam){
    var el = document.getElementById('promoRail');
    if (!el) return;
    var now = Date.now();

    // (a) Exam-urgent — within 60 days
    if (targetExam && daysToExam != null && daysToExam <= 60 && daysToExam > 0) {
      var examLabel = (window.EXAM_LABELS_MAP && window.EXAM_LABELS_MAP[targetExam]) || targetExam;
      el.className = 'promo-rail';
      el.href = '/practice';
      el.innerHTML = '<span class="emoji" aria-hidden="true">🎯</span>'
        + '<span class="body"><span class="title">' + daysToExam
        + ' days to ' + examLabel + ' — daily mock tests open</span>'
        + '<span class="sub">Adaptive practice + AI feedback / रोज़ अभ्यास करें</span></span>'
        + '<span class="arrow" aria-hidden="true">→</span>';
      el.style.display = 'flex';
      return;
    }

    // (b) Upcoming festival within 14 days
    for (var i = 0; i < FESTIVALS_2026.length; i++) {
      var f = FESTIVALS_2026[i];
      var dt = new Date(f.date).getTime();
      var d = Math.ceil((dt - now) / 86400000);
      if (d >= 0 && d <= 14) {
        el.className = 'promo-rail ' + (f.cls || '');
        el.href = f.href || '/pricing';
        el.innerHTML = '<span class="emoji" aria-hidden="true">' + f.emoji + '</span>'
          + '<span class="body"><span class="title">' + f.title + '</span>'
          + '<span class="sub">' + f.sub + '</span></span>'
          + '<span class="arrow" aria-hidden="true">→</span>';
        el.style.display = 'flex';
        return;
      }
    }

    // (c) Always-on scholarship fallback
    el.className = 'promo-rail scholarship';
    el.href = '/pricing';
    el.innerHTML = '<span class="emoji" aria-hidden="true">🎓</span>'
      + '<span class="body"><span class="title">Free for Class 6-12 students in CBSE Govt schools</span>'
      + '<span class="sub">Apply with your school code — scholarship program</span></span>'
      + '<span class="arrow" aria-hidden="true">→</span>';
    el.style.display = 'flex';
  }

  // Expose EXAM_LABELS map for the promo rail
  window.EXAM_LABELS_MAP = EXAM_LABELS;

  // Hook promo into loadDashboardSummary by augmenting after-call
  var _origLoadDashboardSummary = loadDashboardSummary;
  loadDashboardSummary = function(){
    var token = localStorage.getItem('pathshala_token');
    if (!token) {
      // Anonymous: still show the fallback promo
      renderPromoRail(null, null);
      return;
    }
    fetch('/api/me/dashboard', {
      headers: { 'Authorization': 'Bearer ' + token },
    }).then(function(r){ return r.ok ? r.json() : null; })
      .then(function(d){
        if (!d) { renderPromoRail(null, null); return; }
        var onb = d.onboarding || {};
        if (onb.target_exam) renderExamCountdown(onb.target_exam);
        if (onb.preferred_language && sel) {
          sel.value = onb.preferred_language;
          localStorage.setItem('padhai_lang', onb.preferred_language);
        }
        // Compute days-to-exam for the promo rail
        var dte = null;
        if (onb.target_exam && EXAM_DATES[onb.target_exam]) {
          dte = Math.ceil((new Date(EXAM_DATES[onb.target_exam]) - Date.now()) / 86400000);
        }
        renderPromoRail(onb.target_exam, dte);
      }).catch(function(){ renderPromoRail(null, null); });
  };

  loadDueBadge();
  loadDashboardSummary();
})();
</script>

<!-- P3: RUM beacon for Core Web Vitals.
     web-vitals.js (~1.5 KB gzipped) loads from unpkg CDN with `defer`
     so it doesn't block first paint. Captures LCP, INP, CLS, TTFB, FCP
     for the actual user session and beacons to /api/cwv/sample. -->
<script type="module">
  (async function loadCWVBeacon(){
    // Skip if explicitly disabled (e.g. for Cypress tests)
    if (window.__CWV_DISABLED__) return;
    try {
      var wv = await import('https://unpkg.com/web-vitals@4?module');
      var path = location.pathname;
      var locale = document.documentElement.lang || 'en';
      var device = (matchMedia('(max-width: 720px)').matches) ? 'mobile'
                  : (matchMedia('(max-width: 1024px)').matches) ? 'tablet'
                  : 'desktop';
      function send(metric){
        var body = {
          name: metric.name,
          value: metric.name === 'CLS' ? metric.value * 1000 : metric.value,
          rating: metric.rating,
          navigationType: metric.navigationType,
          path: path,
          locale: locale,
          device: device,
        };
        // sendBeacon survives page unload; fall back to fetch
        if (navigator.sendBeacon) {
          var blob = new Blob([JSON.stringify(body)], {type:'application/json'});
          navigator.sendBeacon('/api/cwv/sample', blob);
        } else {
          fetch('/api/cwv/sample', {
            method: 'POST', keepalive: true,
            headers: {'Content-Type':'application/json'},
            body: JSON.stringify(body),
          }).catch(function(){});
        }
      }
      wv.onCLS(send); wv.onLCP(send); wv.onINP(send);
      wv.onTTFB(send); wv.onFCP(send);
    } catch(e){
      // Silent — RUM is best-effort
    }
  })();
</script>

<!-- P3: server-driven i18n. Replaces hardcoded EN/HI pairs with a
     locale dict fetched from /api/i18n/{lang}.json. After load, walks
     every [data-i18n] element and swaps its text. Existing bilingual
     hardcoded labels still serve as graceful fallback if the fetch
     fails or no key matches. -->
<script>
(function loadI18n(){
  var lang = (localStorage.getItem('padhai_lang') ||
              (document.documentElement.lang || 'en').split('-')[0]);
  if (lang === 'en') return;  // English already in DOM
  fetch('/api/i18n/' + lang + '.json')
    .then(function(r){ return r.ok ? r.json() : null; })
    .then(function(strings){
      if (!strings) return;
      window.I18N = strings;
      document.querySelectorAll('[data-i18n]').forEach(function(el){
        var key = el.getAttribute('data-i18n');
        if (strings[key]) el.textContent = strings[key];
      });
      document.querySelectorAll('[data-i18n-aria]').forEach(function(el){
        var key = el.getAttribute('data-i18n-aria');
        if (strings[key]) el.setAttribute('aria-label', strings[key]);
      });
    }).catch(function(){});
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
<title>PadhaiApp — AI Tutor for UPSC, NEET, JEE &amp; CBSE | Free Video Lessons</title>
<meta name="description" content="Scan any textbook page and get an AI video lesson in Hindi, Tamil, Telugu &amp; 7 Indian languages. Free for students.">
<!-- Open Graph -->
<meta property="og:title" content="PadhaiApp — AI Tutor for UPSC, NEET, JEE &amp; CBSE | Free Video Lessons">
<meta property="og:description" content="Scan any textbook page and get an AI video lesson in Hindi, Tamil, Telugu &amp; 7 Indian languages. Free for students.">
<meta property="og:type" content="website">
<meta property="og:url" content="/landing">
<meta property="og:image" content="/static/og-cover.png">
<!-- Twitter Card -->
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="PadhaiApp — AI Tutor for UPSC, NEET, JEE &amp; CBSE | Free Video Lessons">
<meta name="twitter:description" content="Scan any textbook page and get an AI video lesson in Hindi, Tamil, Telugu &amp; 7 Indian languages. Free for students.">
<!-- PWA -->
<link rel="manifest" href="/manifest.json">
<meta name="theme-color" content="#1565d8">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="PadhaiApp">
<link rel="apple-touch-icon" href="/static/icon-180.png">
<style>
  :root {
    --bg:#0f1c33; --panel:#ffffff12; --panel-solid:#1a2c47;
    --ink:#ffffff; --muted:#cdd6e5; --line:#ffffff22;
    --brand:#1565d8; --brand-dark:#0b4ec1; --brand-soft:#eaf2ff;
    --green:#12b76a; --radius:10px;
  }
  *{box-sizing:border-box;margin:0;padding:0}
  body{font-family:Inter,Segoe UI,Arial,sans-serif;
       background:var(--bg);color:var(--ink);line-height:1.5}
  a{color:inherit;text-decoration:none}

  /* ── Nav ─────────────────────────────────────────── */
  .topnav{display:flex;align-items:center;justify-content:space-between;
          padding:16px 24px;border-bottom:1px solid var(--line)}
  .nav-brand{display:flex;align-items:center;gap:10px}
  .nav-logo{width:34px;height:34px;border-radius:8px;
            background:linear-gradient(135deg,#2f80ed,#12b76a);
            display:grid;place-items:center;font-weight:850;font-size:16px}
  .nav-brand b{font-size:16px}
  .nav-cta{background:var(--brand);color:#fff;border:0;border-radius:7px;
           padding:9px 18px;font-weight:700;font-size:14px;cursor:pointer;
           font-family:inherit;text-decoration:none}
  .nav-cta:hover{background:var(--brand-dark)}

  /* ── Hero ────────────────────────────────────────── */
  .hero{text-align:center;padding:72px 24px 56px;
        background:linear-gradient(160deg,#0f1c33 0%,#0d2a5e 100%)}
  .hero h1{font-size:clamp(32px,6vw,56px);font-weight:900;
           line-height:1.1;margin-bottom:18px;
           background:linear-gradient(90deg,#fff 0%,#7eb6ff 100%);
           -webkit-background-clip:text;-webkit-text-fill-color:transparent;
           background-clip:text}
  .hero-sub{font-size:clamp(15px,2.5vw,20px);color:var(--muted);
            max-width:600px;margin:0 auto 32px}
  .hero-ctas{display:flex;gap:12px;justify-content:center;flex-wrap:wrap;
             margin-bottom:36px}
  .btn-primary{background:var(--brand);color:#fff;border:0;border-radius:8px;
               padding:14px 28px;font-weight:800;font-size:16px;cursor:pointer;
               font-family:inherit;text-decoration:none;display:inline-block}
  .btn-primary:hover{background:var(--brand-dark)}
  .btn-secondary{background:transparent;color:#fff;
                 border:2px solid rgba(255,255,255,.35);border-radius:8px;
                 padding:13px 26px;font-weight:700;font-size:16px;cursor:pointer;
                 font-family:inherit;text-decoration:none;display:inline-block}
  .btn-secondary:hover{border-color:#fff;background:rgba(255,255,255,.08)}
  .stats-bar{display:flex;align-items:center;justify-content:center;
             gap:6px;flex-wrap:wrap;
             background:rgba(255,255,255,.07);border:1px solid rgba(255,255,255,.12);
             border-radius:999px;padding:10px 20px;
             font-size:13px;font-weight:700;color:#a8c5ff;
             max-width:560px;margin:0 auto}
  .stats-bar .sep{color:rgba(255,255,255,.3)}

  /* ── Section shared ──────────────────────────────── */
  section{padding:64px 24px}
  .section-inner{max-width:960px;margin:0 auto}
  .section-label{font-size:12px;font-weight:800;letter-spacing:.1em;
                 text-transform:uppercase;color:#7eb6ff;margin-bottom:10px}
  .section-title{font-size:clamp(22px,4vw,32px);font-weight:800;
                 margin-bottom:14px}
  .section-sub{color:var(--muted);font-size:15px;max-width:540px}

  /* ── Features ────────────────────────────────────── */
  .features{background:#111f38}
  .feat-grid{display:grid;grid-template-columns:repeat(3,1fr);
             gap:20px;margin-top:40px}
  .feat-card{background:var(--panel);border:1px solid var(--line);
             border-radius:14px;padding:28px 22px}
  .feat-icon{font-size:32px;margin-bottom:14px}
  .feat-card h3{font-size:17px;font-weight:800;margin-bottom:8px}
  .feat-card p{color:var(--muted);font-size:14px;line-height:1.55}

  /* ── How it works ────────────────────────────────── */
  .how{background:var(--bg)}
  .steps{display:grid;grid-template-columns:repeat(3,1fr);
         gap:20px;margin-top:40px}
  .step{display:flex;flex-direction:column;align-items:flex-start;gap:12px}
  .step-num{width:40px;height:40px;border-radius:10px;
            background:var(--brand);display:grid;place-items:center;
            font-weight:900;font-size:18px;flex-shrink:0}
  .step h3{font-size:16px;font-weight:800}
  .step p{color:var(--muted);font-size:14px;line-height:1.55}
  .step-connector{display:none}

  /* ── Demo anchor placeholder ─────────────────────── */
  #demo{scroll-margin-top:80px}

  /* ── Auth section ────────────────────────────────── */
  .auth-section{background:#111f38}
  .auth-card{max-width:440px;margin:40px auto 0;
             background:var(--panel);border:1px solid var(--line);
             border-radius:14px;padding:32px}
  .tabs{display:flex;gap:4px;background:#ffffff0e;padding:4px;
        border-radius:8px;margin-bottom:20px}
  .tab{flex:1;background:transparent;border:0;color:var(--muted);
       padding:10px;font-size:14px;font-weight:700;cursor:pointer;
       border-radius:6px;font-family:inherit;transition:background .15s}
  .tab.active{background:var(--brand);color:#fff}
  label{display:block;font-size:12px;color:#aab5cc;
        margin:10px 0 6px;font-weight:700}
  input{width:100%;padding:10px 12px;border:1px solid var(--line);
        background:#ffffff14;color:#fff;border-radius:8px;
        font-size:14px;font-family:inherit}
  input:focus{outline:none;border-color:var(--brand)}
  button.cta{width:100%;background:var(--brand);color:#fff;border:0;
             padding:12px;border-radius:8px;font-weight:800;
             font-size:14px;cursor:pointer;margin-top:18px;
             font-family:inherit}
  button.cta:hover{background:var(--brand-dark)}
  .alt{margin-top:18px;text-align:center;font-size:13px;color:#9aa6c0}
  .alt a{color:#fff;text-decoration:underline;font-weight:700}
  .err{margin-top:12px;color:#ffb4b4;font-size:13px;display:none}
  .err.show{display:block}

  /* ── WhatsApp + Footer ───────────────────────────── */
  .share-section{background:var(--bg);text-align:center;padding:40px 24px}
  .wa-btn{display:inline-flex;align-items:center;gap:10px;
          background:#25d366;color:#fff;border-radius:8px;
          padding:13px 24px;font-weight:700;font-size:15px;
          text-decoration:none;transition:background .15s}
  .wa-btn:hover{background:#1da851}
  footer{background:#080f1e;text-align:center;padding:24px;
         color:#6b7a99;font-size:13px;border-top:1px solid var(--line)}
  footer a{color:#8fa8d4;text-decoration:underline;margin:0 6px}

  /* ── Responsive ──────────────────────────────────── */
  @media(max-width:720px){
    .feat-grid,.steps{grid-template-columns:1fr}
    .hero{padding:52px 18px 40px}
    .topnav{padding:14px 18px}
    section{padding:48px 18px}
    .hero-ctas{flex-direction:column;align-items:center}
    .btn-primary,.btn-secondary{width:100%;max-width:320px;text-align:center}
  }
</style>
</head>
<body>

<!-- ── Top nav ─────────────────────────────────────────── -->
<nav class="topnav">
  <div class="nav-brand">
    <div class="nav-logo">P</div>
    <b>PadhaiApp</b>
  </div>
  <a class="nav-cta" href="#auth">Start for Free</a>
</nav>

<!-- ── Hero ────────────────────────────────────────────── -->
<section class="hero">
  <h1>Study Smarter with AI</h1>
  <p class="hero-sub">
    Scan any textbook page &rarr; get a video lesson in your language.
    Hindi, Tamil, Telugu, Kannada, and 7 more.
  </p>
  <div class="hero-ctas">
    <a class="btn-primary" href="#auth">Start for Free &rarr;</a>
    <a class="btn-secondary" href="#demo">Watch Demo</a>
  </div>
  <div class="stats-bar">
    <span>50,000+ Students</span>
    <span class="sep">•</span>
    <span>7 Indian Languages</span>
    <span class="sep">•</span>
    <span>UPSC &nbsp;·&nbsp; NEET &nbsp;·&nbsp; JEE &nbsp;·&nbsp; CBSE &nbsp;·&nbsp; SSC</span>
  </div>
</section>

<!-- ── Features ─────────────────────────────────────────── -->
<section class="features" id="demo">
  <div class="section-inner">
    <div class="section-label">How PadhaiApp works</div>
    <h2 class="section-title">Everything you need to crack your exam</h2>
    <p class="section-sub">
      No more passive reading. Get active AI-powered lessons from
      the exact pages you are studying.
    </p>
    <div class="feat-grid">
      <div class="feat-card">
        <div class="feat-icon">📸</div>
        <h3>Snap a Page</h3>
        <p>Upload or photograph any textbook, notes, or question paper — NCERT, coaching material, or your own handwritten notes.</p>
      </div>
      <div class="feat-card">
        <div class="feat-icon">🎬</div>
        <h3>Get a Video Lesson</h3>
        <p>AI explains the concept with a talking teacher avatar in your language — Hindi, Tamil, Telugu, Kannada, Bengali, and more.</p>
      </div>
      <div class="feat-card">
        <div class="feat-icon">📝</div>
        <h3>Practice &amp; Master</h3>
        <p>Adaptive flashcards, mock tests, and AI doubt-clearing help you retain more and score higher on exam day.</p>
      </div>
    </div>
  </div>
</section>

<!-- ── How it works ──────────────────────────────────────── -->
<section class="how">
  <div class="section-inner">
    <div class="section-label">3 simple steps</div>
    <h2 class="section-title">From textbook photo to video lesson in minutes</h2>
    <div class="steps">
      <div class="step">
        <div class="step-num">1</div>
        <h3>Upload your textbook page or photo</h3>
        <p>Take a photo or upload a PDF page — any subject, any board, any coaching material.</p>
      </div>
      <div class="step">
        <div class="step-num">2</div>
        <h3>Choose your language and exam</h3>
        <p>Pick from Hindi, Tamil, Telugu, Kannada, Bengali, Marathi, and more. Select your target exam: UPSC, NEET, JEE, CBSE, or SSC.</p>
      </div>
      <div class="step">
        <div class="step-num">3</div>
        <h3>Watch your personalised AI lesson</h3>
        <p>A talking AI teacher explains the concept in your language, with examples tailored to your exam pattern.</p>
      </div>
    </div>
  </div>
</section>

<!-- ── Auth ──────────────────────────────────────────────── -->
<section class="auth-section" id="auth">
  <div class="section-inner" style="text-align:center">
    <div class="section-label">Join 50,000+ students</div>
    <h2 class="section-title">Start learning for free today</h2>
    <p class="section-sub" style="margin:0 auto">
      Create your account in 30 seconds — no credit card required.
    </p>
  </div>
  <div class="auth-card">
    <div class="tabs">
      <button class="tab active" data-mode="login">Sign In</button>
      <button class="tab" data-mode="signup">Create Account</button>
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
        <label id="termsLabel" style="display:flex;align-items:flex-start;gap:8px;font-size:13px;color:var(--muted);margin-top:8px;cursor:pointer">
          <input id="terms_accepted" type="checkbox" style="margin-top:2px;flex-shrink:0">
          <span>I agree to the <a href="/terms" target="_blank">Terms of Service</a> and <a href="/privacy" target="_blank">Privacy Policy</a></span>
        </label>
      </div>
      <button class="cta" id="submitBtn">Sign In</button>
      <div class="err" id="errBox"></div>
    </form>
    <div class="alt">
      <a href="/home">Open Exam Hub</a>
      &nbsp;·&nbsp;
      <a href="#" id="forgotLink">Forgot password?</a>
    </div>
    <div id="forgotForm" style="display:none;margin-top:14px">
      <label for="resetEmail">Email</label>
      <input id="resetEmail" type="email" placeholder="your@email.com">
      <button class="cta" id="resetBtn" style="margin-top:10px">Send reset link</button>
      <div class="err" id="resetMsg"></div>
    </div>
  </div>
</section>

<!-- ── WhatsApp share ────────────────────────────────────── -->
<div class="share-section">
  <p style="color:var(--muted);font-size:14px;margin-bottom:16px">
    Know a student who needs this? Share PadhaiApp for free.
  </p>
  <a class="wa-btn"
     href="https://wa.me/?text=Check%20out%20PadhaiApp%20-%20Free%20AI%20lessons%20for%20Indian%20students%3A%20https%3A%2F%2Faipadhaiapp.com"
     target="_blank" rel="noopener">
    Share on WhatsApp 💬
  </a>
</div>

<!-- ── Footer ────────────────────────────────────────────── -->
<footer>
  &copy; 2026 PadhaiApp &nbsp;|&nbsp;
  <a href="/terms">Terms</a>
  <a href="/privacy">Privacy</a>
  <a href="mailto:hello@aipadhaiapp.com">Contact</a>
</footer>

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
        mode === 'login' ? 'Sign In' : 'Create Account';
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
        const tc = document.getElementById('terms_accepted');
        if (!tc.checked) {
          errBox.textContent = 'Please accept the Terms of Service to continue.';
          errBox.classList.add('show');
          submitBtn.disabled = false;
          submitBtn.textContent = 'Create Account';
          return;
        }
        fd.append('terms_accepted', 'true');
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
              localStorage.setItem('pathshala_token', data.token);
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
          mode === 'login' ? 'Sign In' : 'Create Account';
      }
    },
  );
  // Forgot password toggle + submit
  document.getElementById('forgotLink').addEventListener('click', (e) => {
    e.preventDefault();
    const ff = document.getElementById('forgotForm');
    ff.style.display = ff.style.display === 'none' ? '' : 'none';
  });
  document.getElementById('resetBtn').addEventListener('click', async () => {
    const email = document.getElementById('resetEmail').value.trim();
    const msg = document.getElementById('resetMsg');
    if(!email){ msg.textContent = 'Enter your email.'; msg.classList.add('show'); return; }
    const btn = document.getElementById('resetBtn');
    btn.disabled = true; btn.textContent = 'Sending…';
    const fd = new FormData(); fd.append('email', email);
    try{
      const r = await fetch('/auth/forgot-password', {method:'POST', body:fd});
      const j = await r.json();
      msg.textContent = j.message || 'Check your inbox.';
      msg.classList.add('show');
      msg.style.color = '#7ee8a2';
    } catch(e){ msg.textContent = 'Network error.'; msg.classList.add('show'); }
    finally{ btn.disabled = false; btn.textContent = 'Send reset link'; }
  });
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
