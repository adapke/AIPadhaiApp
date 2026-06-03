"""Inline HTML templates for the admin console.

Mirrors the main app's pattern (no Jinja, just Python f-strings) so the
two services share an aesthetic without sharing a runtime template
engine. Style tokens match `padhai/web.py` (navy + saffron + purple).

Each template is a pure function: takes data, returns an HTML string.
No I/O, no DB calls — that's `data.py`'s job.
"""

from __future__ import annotations

import html
import json
from typing import Iterable

from .auth import AdminUser
from .data import DashboardSummary


def _escape(s) -> str:
    return html.escape(str(s) if s is not None else "")


def _fmt_seconds(s: float | None) -> str:
    if s is None:
        return "—"
    if s < 60:
        return f"{s:.1f}s"
    return f"{int(s // 60)}m {int(s % 60)}s"


def _fmt_time(ts: float) -> str:
    from datetime import datetime, timezone
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M")


def _base_css() -> str:
    return """
    :root {
      --brand:#5E60CE; --brand-soft:#EEF0FB; --purple:#7C3AED;
      --saffron:#F59E0B; --navy:#102A43; --ink:#1F2937;
      --muted:#6B7280; --line:#E5E7EB; --bg:#F5F7FA;
      --ok:#16A34A; --warn:#F59E0B; --err:#E11D48; --info:#2E74B5;
    }
    * { box-sizing:border-box; }
    body { margin:0; font:14px/1.5 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
           background:var(--bg); color:var(--ink); }
    a { color:var(--brand); text-decoration:none; }
    a:hover { text-decoration:underline; }
    code { font-family: 'Menlo', 'Monaco', monospace; font-size:12px;
           background:#f3f4f6; padding:1px 5px; border-radius:4px; }
    button, .btn {
      cursor:pointer; padding:8px 14px; border-radius:8px;
      border:1.5px solid var(--line); background:#fff; font:inherit;
      color:var(--ink);
    }
    button:hover, .btn:hover { background:var(--brand-soft); border-color:var(--brand); }
    button.primary { background:var(--brand); color:#fff; border-color:var(--brand); }
    button.primary:hover { background:var(--purple); }
    button.danger { color:var(--err); border-color:#fecdd3; }
    button.danger:hover { background:#fee2e2; border-color:var(--err); }
    table { width:100%; border-collapse:collapse; }
    th, td { text-align:left; padding:10px 12px; border-bottom:1px solid var(--line); }
    th { font-size:12px; text-transform:uppercase; letter-spacing:0.06em;
         color:var(--muted); background:#fafbff; }
    tbody tr:hover { background:#fafbff; }
    .card { background:#fff; border:1px solid var(--line); border-radius:14px;
            padding:18px; margin-bottom:16px; }
    .grid { display:grid; gap:14px; }
    .grid-3 { grid-template-columns: repeat(3, 1fr); }
    .grid-4 { grid-template-columns: repeat(4, 1fr); }
    .grid-2 { grid-template-columns: 1fr 1fr; }
    .stat { padding:18px; border-radius:14px; background:#fff;
            border:1px solid var(--line); }
    .stat .label { font-size:11px; text-transform:uppercase;
                   letter-spacing:0.08em; color:var(--muted); margin-bottom:6px; }
    .stat .value { font-size:30px; font-weight:700; color:var(--navy);
                   font-variant-numeric: tabular-nums; }
    .stat .sub { font-size:12px; color:var(--muted); margin-top:4px; }
    .pill { display:inline-block; padding:3px 9px; border-radius:99px;
            font-size:11px; font-weight:600; text-transform:uppercase;
            letter-spacing:0.04em; }
    .pill.ok       { background:#d1fae5; color:#065f46; }
    .pill.queued   { background:#dbeafe; color:#1e40af; }
    .pill.running  { background:#fef3c7; color:#92400e; }
    .pill.failed   { background:#fee2e2; color:#991b1b; }
    @media (max-width: 800px) {
      .grid-3, .grid-4 { grid-template-columns: 1fr 1fr; }
      .grid-2 { grid-template-columns: 1fr; }
    }
    """


def render_layout(title: str, body: str, user: AdminUser | None = None) -> str:
    # Paths are relative to the admin mount point ("/admin") in the
    # student app. Keeping them as `/admin/...` works whether the admin
    # is mounted under the main app or runs as its own service later.
    nav_items = [
        ("/admin/", "Dashboard"),
        ("/admin/jobs", "Jobs"),
        ("/admin/topics", "Topics & Languages"),
        ("/admin/llm-costs", "LLM Costs"),
    ]
    nav_html = "".join(
        f'<a href="{path}" class="nav-link">{_escape(label)}</a>'
        for path, label in nav_items
    )
    user_chip = (
        f'<div class="user-chip">{_escape(user.display_name or user.email)} '
        f'<a href="/admin/logout" class="logout-link" title="Sign out">⏻</a></div>'
        if user else ""
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{_escape(title)} — AI Pathshala Admin</title>
  <style>
    {_base_css()}
    header {{
      background:#fff; border-bottom:1px solid var(--line);
      padding:14px 24px; display:flex; align-items:center; gap:24px;
      position:sticky; top:0; z-index:10;
    }}
    .brand {{
      display:flex; align-items:center; gap:10px;
      font-weight:700; color:var(--navy); font-size:16px;
    }}
    .brand .logo {{
      width:32px; height:32px; border-radius:8px;
      background:linear-gradient(135deg,var(--brand),var(--purple));
      color:#fff; display:flex; align-items:center; justify-content:center;
      font-weight:700;
    }}
    .brand small {{ color:var(--saffron); font-size:11px; font-weight:600;
                   margin-left:2px; }}
    nav.top {{ display:flex; gap:6px; flex:1; }}
    nav.top .nav-link {{
      padding:8px 14px; border-radius:8px; color:var(--ink);
      font-weight:500; font-size:14px;
    }}
    nav.top .nav-link:hover {{ background:var(--brand-soft); text-decoration:none; }}
    .user-chip {{
      font-size:13px; color:var(--muted);
      display:flex; align-items:center; gap:8px;
    }}
    .logout-link {{
      display:inline-flex; align-items:center; justify-content:center;
      width:26px; height:26px; border-radius:99px;
      background:var(--brand-soft); color:var(--brand);
      text-decoration:none; font-size:14px;
    }}
    .logout-link:hover {{ background:var(--brand); color:#fff; }}
    main {{ max-width:1280px; margin:0 auto; padding:24px; }}
    h1 {{ font-size:24px; color:var(--navy); margin:0 0 18px; }}
    h2 {{ font-size:16px; color:var(--navy); margin:0 0 12px;
          text-transform:uppercase; letter-spacing:0.04em; }}
  </style>
</head>
<body>
  <header>
    <div class="brand">
      <div class="logo">A</div>
      <span>AI Pathshala <small>ADMIN</small></span>
    </div>
    <nav class="top">{nav_html}</nav>
    {user_chip}
  </header>
  <main>
    {body}
  </main>
</body>
</html>
"""


def render_login(*, allow_signup: bool, error: str = "") -> str:
    """Login page. When the admin_users table is empty, `allow_signup`
    is True and the page also shows a one-shot bootstrap signup form
    (first signup wins — becomes the founding admin)."""
    err_block = (
        f'<div class="alert error">{_escape(error)}</div>'
        if error else ""
    )

    signup_block = ""
    if allow_signup:
        signup_block = """
        <div class="bootstrap-banner">
          <strong>First-time setup.</strong>
          No admin users exist yet. The first signup becomes the
          founding admin — additional admins must be invited from
          inside the console after that.
        </div>

        <div class="tab-bar">
          <button type="button" class="tab active" data-tab="signin">Sign in</button>
          <button type="button" class="tab" data-tab="signup">Create first admin</button>
        </div>
        """

    body = f"""
    <div class="card" style="max-width:440px; margin:80px auto;">
      <h1 style="margin-top:0;">Admin Console</h1>
      <p style="color:var(--muted); margin:-6px 0 18px; font-size:13px;">
        Authentication is independent of the student app. Sign in with
        an admin account.
      </p>
      {err_block}
      {signup_block}

      <form id="signin-form" method="post" action="/admin/login" class="auth-form">
        <label>Email</label>
        <input type="email" name="email" autocomplete="email" required
               placeholder="you@org.com">
        <label>Password</label>
        <input type="password" name="password" autocomplete="current-password"
               required minlength="8">
        <button class="primary" type="submit">Sign in</button>
      </form>

      {'<form id="signup-form" method="post" action="/admin/signup" class="auth-form" style="display:none;">' if allow_signup else ''}
      {'<label>Email</label>' if allow_signup else ''}
      {'<input type="email" name="email" autocomplete="email" required placeholder="founder@org.com">' if allow_signup else ''}
      {'<label>Display name <span class="hint">(optional)</span></label>' if allow_signup else ''}
      {'<input type="text" name="display_name" autocomplete="name">' if allow_signup else ''}
      {'<label>Password <span class="hint">(min 8 chars)</span></label>' if allow_signup else ''}
      {'<input type="password" name="password" autocomplete="new-password" required minlength="8">' if allow_signup else ''}
      {'<button class="primary" type="submit">Create admin &amp; sign in</button>' if allow_signup else ''}
      {'</form>' if allow_signup else ''}
    </div>

    <style>
      .auth-form label {{
        display:block; margin:12px 0 4px; font-size:13px; font-weight:600;
        color:var(--ink);
      }}
      .auth-form .hint {{ color:var(--muted); font-weight:400; font-size:12px; }}
      .auth-form input {{
        width:100%; padding:11px 13px; border:1.5px solid var(--line);
        border-radius:8px; font-size:14px;
        font-family:inherit;
      }}
      .auth-form input:focus {{
        outline:none; border-color:var(--brand);
        box-shadow:0 0 0 3px rgba(94,96,206,0.15);
      }}
      .auth-form button {{ width:100%; padding:12px; margin-top:18px; }}
      .alert.error {{
        background:#fee2e2; color:#991b1b; padding:10px 14px;
        border-radius:8px; margin-bottom:14px; font-size:14px;
      }}
      .bootstrap-banner {{
        background:#fffbeb; border:1px solid #fde68a; color:#78350f;
        padding:12px 14px; border-radius:10px; margin-bottom:14px;
        font-size:13px;
      }}
      .tab-bar {{
        display:flex; gap:6px; margin-bottom:14px;
        border-bottom:1px solid var(--line);
      }}
      .tab {{
        background:none; border:none; padding:8px 14px;
        font-size:13px; font-weight:600; color:var(--muted);
        border-bottom:2px solid transparent; margin-bottom:-1px;
        cursor:pointer;
      }}
      .tab.active {{ color:var(--brand); border-bottom-color:var(--brand); }}
    </style>

    <script>
      document.querySelectorAll('.tab').forEach(tab => {{
        tab.addEventListener('click', () => {{
          document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
          tab.classList.add('active');
          const target = tab.dataset.tab;
          document.getElementById('signin-form').style.display = (target === 'signin' ? 'block' : 'none');
          const su = document.getElementById('signup-form');
          if (su) su.style.display = (target === 'signup' ? 'block' : 'none');
        }});
      }});
    </script>
    """
    return render_layout("Sign in", body, user=None)


def render_dashboard(*, user, summary: DashboardSummary,
                     languages, topics, daily) -> str:
    s = summary

    stats = f"""
    <div class="grid grid-4">
      <div class="stat">
        <div class="label">Total jobs</div>
        <div class="value">{s.total_jobs:,}</div>
        <div class="sub">all-time</div>
      </div>
      <div class="stat">
        <div class="label">In flight</div>
        <div class="value">{s.queued + s.running}</div>
        <div class="sub">{s.queued} queued · {s.running} running</div>
      </div>
      <div class="stat">
        <div class="label">Today</div>
        <div class="value">{s.succeeded_today:,}</div>
        <div class="sub">{s.failed_today} failed · {s.succeeded_today / max(1, s.succeeded_today + s.failed_today) * 100:.0f}% ok</div>
      </div>
      <div class="stat">
        <div class="label">Cache artifacts</div>
        <div class="value">{s.cache_artifacts_total:,}</div>
        <div class="sub">across 9 tiers</div>
      </div>
    </div>
    <div class="grid grid-2" style="margin-top:14px;">
      <div class="stat">
        <div class="label">Avg render time</div>
        <div class="value">{_fmt_seconds(s.avg_render_seconds)}</div>
        <div class="sub">last 200 successful renders</div>
      </div>
      <div class="stat">
        <div class="label">p95 render time</div>
        <div class="value">{_fmt_seconds(s.p95_render_seconds)}</div>
        <div class="sub">95th percentile · target &lt; 180s</div>
      </div>
    </div>
    """

    daily_rows = "".join(
        f"<tr><td>{_escape(d['day'])}</td>"
        f"<td>{d['created']}</td><td>{d['succeeded']}</td>"
        f"<td>{d['failed']}</td></tr>"
        for d in daily
    ) or "<tr><td colspan='4' style='color:var(--muted);'>No activity in the last 14 days.</td></tr>"

    topic_items = "".join(
        f"<li><span>{_escape(t['topic'])}</span>"
        f"<strong>{t['count']}</strong></li>"
        for t in topics[:10]
    ) or "<li style='color:var(--muted);'>No topics yet.</li>"

    lang_items = "".join(
        f"<li><span>{_escape(l['language'])}</span>"
        f"<strong>{l['count']}</strong></li>"
        for l in languages[:10]
    ) or "<li style='color:var(--muted);'>No language data yet.</li>"

    body = f"""
    <h1>Operations dashboard</h1>
    {stats}

    <div class="grid grid-2" style="margin-top:18px;">
      <div class="card">
        <h2>Last 14 days</h2>
        <table>
          <thead><tr><th>Day</th><th>Created</th><th>OK</th><th>Failed</th></tr></thead>
          <tbody>{daily_rows}</tbody>
        </table>
      </div>
      <div class="card">
        <h2>Top topics this week</h2>
        <ul class="rank-list">{topic_items}</ul>
        <hr style="border:none; border-top:1px solid var(--line); margin:14px 0;">
        <h2>Languages requested this week</h2>
        <ul class="rank-list">{lang_items}</ul>
      </div>
    </div>

    <style>
      .rank-list {{
        list-style:none; padding:0; margin:0;
      }}
      .rank-list li {{
        display:flex; justify-content:space-between; padding:6px 0;
        border-bottom:1px solid var(--line); font-size:14px;
      }}
      .rank-list li:last-child {{ border-bottom:none; }}
      .rank-list strong {{ color:var(--brand); font-variant-numeric:tabular-nums; }}
    </style>
    """
    return render_layout("Dashboard", body, user=user)


def render_jobs_queue(*, user, jobs, filter_status: str | None) -> str:
    filter_chips = "".join(
        f'<a href="/admin/jobs{"?status=" + s if s else ""}" '
        f'class="filter-chip {"active" if filter_status == s or (s is None and filter_status is None) else ""}">'
        f'{_escape(label)}</a>'
        for s, label in [
            (None, "All"),
            ("queued", "Queued"),
            ("running", "Running"),
            ("succeeded", "Succeeded"),
            ("failed", "Failed"),
        ]
    )

    rows = []
    for j in jobs:
        status = j["status"]
        progress = ""
        if j.get("progress_step") and status == "running":
            progress = (
                f"<div class='progress-line'>"
                f"<span>{_escape(j['progress_step'])}</span>"
                f"<span>{j['progress_percent']}%</span></div>"
            )
        actions = []
        if status == "failed":
            actions.append(
                f'<button class="primary retry-btn" data-job="{_escape(j["id"])}">Retry</button>'
            )
        if status in ("queued", "running"):
            actions.append(
                f'<button class="danger cancel-btn" data-job="{_escape(j["id"])}">Cancel</button>'
            )
        actions_html = " ".join(actions) or "—"

        rows.append(f"""
          <tr>
            <td><code>{_escape(j['id'][:10])}…</code></td>
            <td><span class="pill {status}">{_escape(status)}</span>{progress}</td>
            <td>{_escape(j.get('video_mode') or j.get('kind') or '—')}</td>
            <td>{_escape(j.get('language'))} · {_escape(j.get('level'))}</td>
            <td>{_fmt_time(j['created_at'])}</td>
            <td>{_fmt_seconds(j.get('duration_seconds'))}</td>
            <td>{_escape(j.get('topic') or '—')}</td>
            <td>{actions_html}</td>
          </tr>
        """)
    rows_html = "".join(rows) or '<tr><td colspan="8" style="color:var(--muted); padding:20px;">No jobs match this filter.</td></tr>'

    body = f"""
    <h1>Jobs queue</h1>
    <div class="filter-bar">{filter_chips}</div>

    <div class="card" style="padding:0; overflow:hidden;">
      <table>
        <thead>
          <tr>
            <th>ID</th><th>Status</th><th>Mode</th>
            <th>Lang / level</th><th>Created</th><th>Duration</th>
            <th>Topic</th><th>Actions</th>
          </tr>
        </thead>
        <tbody id="jobs-tbody">{rows_html}</tbody>
      </table>
    </div>

    <style>
      .filter-bar {{ margin-bottom:14px; display:flex; gap:6px; }}
      .filter-chip {{
        padding:6px 14px; border-radius:99px; background:#fff;
        border:1.5px solid var(--line); color:var(--muted);
        font-size:13px; font-weight:600;
      }}
      .filter-chip:hover {{ background:var(--brand-soft); text-decoration:none; }}
      .filter-chip.active {{
        background:var(--brand); color:#fff; border-color:var(--brand);
      }}
      .progress-line {{
        font-size:11px; color:var(--muted); margin-top:4px;
        display:flex; gap:6px; align-items:center;
      }}
    </style>

    <script>
      async function postAction(jobId, action) {{
        try {{
          const r = await fetch(`/admin/api/jobs/${{jobId}}/${{action}}`, {{
            method:'POST', credentials:'include',
          }});
          if (!r.ok) {{
            const j = await r.json().catch(()=>({{}}));
            throw new Error(j.detail || `HTTP ${{r.status}}`);
          }}
          location.reload();
        }} catch (ex) {{
          alert(action + ' failed: ' + ex.message);
        }}
      }}
      document.querySelectorAll('.retry-btn').forEach(b =>
        b.addEventListener('click', () => postAction(b.dataset.job, 'retry'))
      );
      document.querySelectorAll('.cancel-btn').forEach(b =>
        b.addEventListener('click', () => {{
          if (confirm('Cancel this job?')) postAction(b.dataset.job, 'cancel');
        }})
      );
    </script>
    """
    return render_layout("Jobs", body, user=user)


def render_topics(*, user, topics, languages) -> str:
    topic_rows = "".join(
        f"<tr><td>{i+1}</td><td>{_escape(t['topic'])}</td>"
        f"<td>{t['count']}</td></tr>"
        for i, t in enumerate(topics)
    ) or "<tr><td colspan='3' style='color:var(--muted); padding:20px;'>No topics yet.</td></tr>"

    lang_rows = "".join(
        f"<tr><td>{i+1}</td><td>{_escape(l['language'])}</td>"
        f"<td>{l['count']}</td></tr>"
        for i, l in enumerate(languages)
    ) or "<tr><td colspan='3' style='color:var(--muted); padding:20px;'>No language data yet.</td></tr>"

    body = f"""
    <h1>Topics &amp; languages</h1>
    <p style="color:var(--muted); margin-top:-12px;">
      Last 7 days. Drives the pre-render queue + 'where to invest the next neural voice' decision.
    </p>

    <div class="grid grid-2">
      <div class="card">
        <h2>Top 50 topics</h2>
        <table>
          <thead><tr><th>#</th><th>Topic</th><th>Requests</th></tr></thead>
          <tbody>{topic_rows}</tbody>
        </table>
      </div>
      <div class="card">
        <h2>Languages by demand</h2>
        <table>
          <thead><tr><th>#</th><th>Language</th><th>Requests</th></tr></thead>
          <tbody>{lang_rows}</tbody>
        </table>
      </div>
    </div>
    """
    return render_layout("Topics & languages", body, user=user)


def render_llm_costs(
    *,
    user: AdminUser,
    stats: dict,
    selected_hours: int,
    alerts: list[dict] | None = None,
) -> str:
    """LLM usage + cost dashboard. Reads from llm_calls (written by
    padhai/llm_obs.py) — see admin/data.py:llm_cost_stats."""

    def _fmt_int(n: int) -> str:
        return f"{n:,}"

    def _fmt_inr(amount: float) -> str:
        return f"&#8377;{amount:,.2f}"

    window_options = [(24, "Last 24h"), (24 * 7, "Last 7d"), (24 * 30, "Last 30d")]
    window_html = "".join(
        f'<a href="/admin/llm-costs?hours={hours}" '
        f'class="window-chip{" active" if hours == selected_hours else ""}">'
        f'{label}</a>'
        for hours, label in window_options
    )

    module_rows = "".join(
        f"<tr><td>{_escape(m['module'])}</td>"
        f"<td>{_fmt_int(m['calls'])}</td>"
        f"<td>{_fmt_int(m['tokens'])}</td>"
        f"<td>{_fmt_inr(m['cost_inr'])}</td></tr>"
        for m in stats["by_module"]
    ) or '<tr><td colspan="4" style="color:var(--muted); padding:20px;">No LLM calls yet in this window.</td></tr>'

    model_rows = "".join(
        f"<tr><td><code>{_escape(m['model'])}</code></td>"
        f"<td>{_fmt_int(m['calls'])}</td>"
        f"<td>{_fmt_int(m['tokens'])}</td>"
        f"<td>{_fmt_inr(m['cost_inr'])}</td></tr>"
        for m in stats["by_model"]
    ) or '<tr><td colspan="4" style="color:var(--muted); padding:20px;">No LLM calls yet in this window.</td></tr>'

    user_rows = "".join(
        f"<tr><td><code>{_escape(u['user_id'][:14])}…</code></td>"
        f"<td>{_fmt_int(u['calls'])}</td>"
        f"<td>{_fmt_inr(u['cost_inr'])}</td></tr>"
        for u in stats["top_users"]
    ) or '<tr><td colspan="3" style="color:var(--muted); padding:20px;">No per-user attribution yet.</td></tr>'

    body = f"""
    <h1>LLM costs</h1>
    <p style="color:var(--muted); margin-top:-12px;">
      Per-prompt token + INR cost tracking sourced from <code>llm_calls</code>.
      Drives daily caps + the cost-saving review. Cached calls get the
      90% input-token discount baked into the rollup.
    </p>

    <div class="window-bar" style="display:flex; gap:8px; margin-bottom:16px;">
      {window_html}
    </div>

    <div class="grid grid-4">
      <div class="stat">
        <div class="label">Total calls</div>
        <div class="value">{_fmt_int(stats['total_calls'])}</div>
        <div class="sub">Cache hits: {stats['cache_hit_pct']}%</div>
      </div>
      <div class="stat">
        <div class="label">Total cost</div>
        <div class="value">{_fmt_inr(stats['total_cost_inr'])}</div>
        <div class="sub">All Anthropic models</div>
      </div>
      <div class="stat">
        <div class="label">Tokens (in / out)</div>
        <div class="value" style="font-size:22px;">
          {_fmt_int(stats['tokens_in'])} / {_fmt_int(stats['tokens_out'])}
        </div>
        <div class="sub">Across all calls</div>
      </div>
      <div class="stat">
        <div class="label">Avg latency</div>
        <div class="value">{_fmt_int(stats['avg_latency_ms'])}<span style="font-size:14px; color:var(--muted);"> ms</span></div>
        <div class="sub">P50 across calls</div>
      </div>
    </div>

    <div class="grid grid-2" style="margin-top:18px;">
      <div class="card">
        <h2>By module</h2>
        <table>
          <thead><tr><th>Module</th><th>Calls</th><th>Tokens</th><th>Cost (INR)</th></tr></thead>
          <tbody>{module_rows}</tbody>
        </table>
      </div>
      <div class="card">
        <h2>By model</h2>
        <table>
          <thead><tr><th>Model</th><th>Calls</th><th>Tokens</th><th>Cost (INR)</th></tr></thead>
          <tbody>{model_rows}</tbody>
        </table>
      </div>
    </div>

    <div class="card" style="margin-top:18px;">
      <h2>Top 10 users by spend</h2>
      <table>
        <thead><tr><th>User</th><th>Calls</th><th>Cost (INR)</th></tr></thead>
        <tbody>{user_rows}</tbody>
      </table>
    </div>

    {_render_alerts_block(alerts or [])}

    <style>
      .window-chip {{
        padding:6px 12px; border-radius:99px; border:1px solid var(--line);
        background:#fff; color:var(--ink); font-size:13px; font-weight:500;
      }}
      .window-chip.active {{
        background:var(--brand); color:#fff; border-color:var(--brand);
      }}
      .window-chip:hover {{ background:var(--brand-soft); text-decoration:none; }}
      .window-chip.active:hover {{ background:var(--purple); color:#fff; }}
      .bucket-pill {{ display:inline-block; padding:2px 10px;
        border-radius:99px; font-size:11px; font-weight:600;
        letter-spacing:0.04em; }}
      .bucket-80 {{ background:#fef3c7; color:#92400e; }}
      .bucket-100 {{ background:#fee2e2; color:#991b1b; }}
    </style>
    """
    return render_layout("LLM costs", body, user=user)


def _render_alerts_block(alerts: list[dict]) -> str:
    """Section under the LLM-costs dashboard listing recent
    threshold-crossing alerts. Empty list → no section rendered."""
    if not alerts:
        return ""

    def _fmt_user(uid):
        return _escape((uid or "")[:14] + ("..." if uid and len(uid) > 14 else ""))

    def _bucket_pill(bucket):
        cls = f"bucket-pill bucket-{_escape(bucket)}"
        return f'<span class="{cls}">{_escape(bucket)}%</span>'

    rows = "".join(
        f"<tr>"
        f"<td>{_fmt_time(a['created_at'])}</td>"
        f"<td><code>{_fmt_user(a['user_id'])}</code></td>"
        f"<td>{_escape(a.get('subscription_tier') or '—')}</td>"
        f"<td>{_bucket_pill(a['bucket'])}</td>"
        f"<td>&#8377;{a['spent_inr_at_crossing']:,.2f} / "
        f"&#8377;{a['cap_inr']:,.2f}</td>"
        f"</tr>"
        for a in alerts
    )

    n_at_cap = sum(1 for a in alerts if a["bucket"] == "100")
    n_warn = sum(1 for a in alerts if a["bucket"] == "80")
    summary_chip = (
        f'<span class="bucket-pill bucket-80">{n_warn} approaching</span>  '
        f'<span class="bucket-pill bucket-100">{n_at_cap} blocked</span>'
    )

    return f"""
    <div class="card" style="margin-top:18px;">
      <h2>Users approaching / over budget {summary_chip}</h2>
      <p style="color:var(--muted); margin-top:-12px;">
        Soft alert at 80% of daily cap; hard alert at 100% (user is
        being refused new AI calls). Idempotent — one row per user per
        day per bucket. Tunable via <code>PADHAI_LLM_ALERT_PCT</code>.
      </p>
      <table>
        <thead><tr>
          <th>When</th><th>User</th><th>Tier</th><th>Bucket</th><th>Spent / Cap</th>
        </tr></thead>
        <tbody>{rows}</tbody>
      </table>
    </div>
    """
