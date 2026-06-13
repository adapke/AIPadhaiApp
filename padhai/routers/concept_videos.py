"""prod-14 — /api/concept-videos/* router.

Read-API for the curated concept-video catalog. The SPA hits this
when a student asks about a concept; we return embed-friendly URLs
to professional YouTube content (Peekaboo Kidz / Khan / etc).
The AI tutor layer (`/explain/video`, `/api/tutor/*`) remains the
fallback when no curated video matches.

Routes:
  GET  /api/concept-videos              — list/search
  GET  /api/concept-videos/{id}         — single video lookup
  GET  /api/concept-videos/stats        — catalog stats (public,
                                          no PII)

POST/PATCH routes for adding videos live on the admin side (covered
by the router-level admin dep in routers/__init__.py since the path
starts with /api/admin/).
"""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse

from .. import api_deps
from .. import concept_videos as _cv

router = APIRouter()


@router.get("/api/concept-videos")
def list_videos(
    concept: str | None = Query(
        None, description="exact or normalised concept name",
    ),
    language: str = Query("en"),
    subject: str | None = None,
    grade: int | None = None,
    quality_tier: str | None = Query(
        None,
        description="verified | channel_seed | ai_fallback",
    ),
    limit: int = Query(10, ge=1, le=50),
) -> dict:
    rows = _cv.search(
        concept=concept,
        language=language,
        subject=subject,
        grade=grade,
        quality_tier=quality_tier,
        limit=limit,
    )
    return {
        "rows": [_cv.to_dict(r) for r in rows],
        "count": len(rows),
    }


@router.get("/api/concept-videos/stats")
def get_stats() -> dict:
    """Public — no PII, just aggregate counts for the curator
    dashboard and the SPA's "how many concept videos do we cover"
    pitch line on the landing page."""
    return _cv.stats()


@router.get("/api/concept-videos/badge")
def get_badge() -> dict:
    """prod-66 — Public landing-page badge endpoint.

    Returns the data points the landing-page "verified curator content"
    badge needs: total catalog size, verified count, last verification
    timestamp, and a freshness label. Designed to be cacheable (no auth,
    no PII, no per-user data).

    Shape:
        {
            "total": int,                       # all concept videos
            "verified": int,                    # curator-confirmed count
            "verified_pct": float,              # rounded to 1 decimal
            "channel_seed": int,                # awaiting curation
            "languages": list[str],             # sorted ISO codes
            "subjects": list[str],              # sorted subject slugs
            "last_verified_at": float | None,   # epoch sec, newest
            "last_verified_iso": str | None,    # ISO-8601 for display
            "freshness_label": str,             # "today" / "N days ago" / "never"
        }

    Use cases:
      • Landing-page "X videos curated by Y subjects, last verified Z"
      • Public press / sales decks
      • External crawlers / SEO content
    """
    import datetime as _dt
    s = _cv.stats()
    total = s.get("total", 0)
    by_tier = s.get("by_quality_tier", {}) or {}
    verified = by_tier.get("verified", 0)
    channel_seed = by_tier.get("channel_seed", 0)
    languages = sorted(s.get("by_language", {}).keys())
    subjects = sorted(s.get("by_subject", {}).keys())

    # Pull max(last_verified_at) directly via sqlite — no helper exists
    # because the badge is the only consumer. Cheap idx-only scan.
    last_at: float | None = None
    try:
        with _cv._conn() as conn:
            row = conn.execute(
                "SELECT MAX(last_verified_at) FROM concept_videos "
                "WHERE last_verified_at IS NOT NULL",
            ).fetchone()
            if row and row[0]:
                last_at = float(row[0])
    except Exception:
        last_at = None

    last_iso = None
    freshness = "never"
    if last_at:
        last_iso = _dt.datetime.fromtimestamp(
            last_at, tz=_dt.UTC,
        ).isoformat(timespec="seconds")
        # Computing "days ago" using time.time() so we stay deterministic-ish
        # without time-zone hassle. Same source clock both reads + writes.
        import time as _time
        days_ago = max(0, int((_time.time() - last_at) // 86400))
        if days_ago == 0:
            freshness = "today"
        elif days_ago == 1:
            freshness = "1 day ago"
        else:
            freshness = f"{days_ago} days ago"

    verified_pct = round((verified / total) * 100, 1) if total else 0.0

    return {
        "total": total,
        "verified": verified,
        "verified_pct": verified_pct,
        "channel_seed": channel_seed,
        "languages": languages,
        "subjects": subjects,
        "last_verified_at": last_at,
        "last_verified_iso": last_iso,
        "freshness_label": freshness,
    }


@router.get("/api/concept-videos/by-concept/{slug}")
def get_by_concept(
    slug: str,
    language: str = Query("en"),
    quality_tier: str = Query(
        "verified",
        description="verified | channel_seed | ai_fallback",
    ),
) -> dict:
    """prod-81 — RESTful lookup by normalized concept slug.

    Designed for canonical SEO URLs:
      /api/concept-videos/by-concept/newton-first-law
      /api/concept-videos/by-concept/photosynthesis

    Slug is normalised via the same regex the catalog uses, so dashes
    and spaces are equivalent. Returns the freshest verified row for
    the concept, or 404 if no curator-confirmed video exists.
    """
    v = _cv.get_by_concept_slug(
        slug, language=language, quality_tier=quality_tier,
    )
    if not v:
        raise HTTPException(
            404,
            f"no {quality_tier} concept-video for slug {slug!r}",
        )
    return _cv.to_dict(v)


@router.get("/api/concept-videos/popular")
def get_popular(
    limit: int = Query(10, ge=1, le=50),
    language: str = Query("en"),
    since_days: int | None = Query(7, ge=1, le=365),
) -> dict:
    """prod-70 — most-played videos in the last `since_days` window.
    Public — used on the landing page "trending this week" widget.
    Only verified videos are returned (channel_seed picks are unconfirmed).
    """
    rows = _cv.list_popular(
        limit=limit, language=language,
        since_days=since_days, quality_tier="verified",
    )
    return {
        "rows": [
            {**_cv.to_dict(v), "play_count": cnt}
            for v, cnt in rows
        ],
        "count": len(rows),
        "since_days": since_days,
    }


@router.post("/api/concept-videos/{video_id}/played")
def record_played(video_id: str) -> dict:
    """prod-70 — click-tracking beacon. Public (no auth) — fires when
    a student clicks Play on a concept-video card. Rate-limited only
    by the global API rate-limit middleware; spam is harmless (just
    inflates a counter on a single row).
    """
    ok = _cv.record_play(video_id)
    if not ok:
        raise HTTPException(404, "concept video not found")
    return {"ok": True}


@router.get("/api/concept-videos/{video_id}")
def get_video(video_id: str) -> dict:
    v = _cv.get_by_id(video_id)
    if not v:
        raise HTTPException(404, "concept video not found")
    return _cv.to_dict(v)


# prod-41 — admin curator routes. Path starts with /api/admin/* so the
# router-level admin gate from routers/__init__.py:_inject_admin_dep
# auto-injects auth. No explicit Depends() needed here.

@router.post("/api/admin/concept-videos/{video_id}/verify")
def admin_verify_video(video_id: str) -> dict:
    """Promote a concept video from `channel_seed` (or `ai_fallback`)
    to `verified`. Curator confirms in a browser that the URL plays the
    intended video for the concept, then clicks Verify on the dashboard."""
    v = _cv.set_quality_tier(
        video_id, "verified",
        curator_note="verified by curator (prod-41)",
    )
    if not v:
        raise HTTPException(404, "concept video not found")
    return {"ok": True, "video": _cv.to_dict(v)}


@router.post("/api/admin/concept-videos/{video_id}/update")
def admin_update_video(
    video_id: str,
    payload: dict = Body(...),
) -> dict:
    """prod-42 — curator workflow. Replace a channel_seed row's stub
    title and source_url with the real YouTube watch URL the curator
    found by searching the trusted channel. Optionally also flip the
    quality_tier to 'verified' in a single call.

    Body fields (all optional, but at least one of title/source_url
    must be provided to be useful):
        title         — new video title
        source_url    — new watch URL (embed_url is auto-derived)
        channel       — corrected channel name
        duration_sec  — duration in seconds (for sorting/display)
        curator_note  — appended to curator_note audit trail
        verify        — bool. If true, also set quality_tier=verified.
    """
    title = payload.get("title")
    source_url = payload.get("source_url")
    channel = payload.get("channel")
    duration_sec = payload.get("duration_sec")
    note = payload.get("curator_note") or "updated by curator (prod-42)"
    verify = bool(payload.get("verify"))
    # prod-55 — when only URL is pasted, auto-fill title+channel from
    # YouTube's public oembed endpoint. Caller can disable via
    # auto_fetch_oembed=false in the body. Default: true if a source_url
    # is being set but title is absent.
    auto_fetch = payload.get("auto_fetch_oembed")
    if auto_fetch is None:
        auto_fetch = bool(source_url) and not title

    if not (title or source_url or channel or duration_sec is not None):
        raise HTTPException(
            400,
            "at least one of title / source_url / channel / "
            "duration_sec must be supplied",
        )

    # prod-67/82 — run iframe-block precheck on the NEW URL before saving.
    # We check the derived EMBED form (what the SPA actually iframes),
    # because YouTube serves X-Frame-Options on /watch but not on /embed/.
    # Best-effort — never blocks the update; result rides along in the
    # response so the UI can warn.
    iframe_check: dict | None = None
    if source_url:
        check_url = _cv._derive_embed_url(source_url) or source_url
        iframe_check = _cv.check_iframe_embed(check_url)

    v = _cv.update_video(
        video_id,
        title=title,
        source_url=source_url,
        channel=channel,
        duration_sec=duration_sec,
        curator_note=note,
        auto_fetch_oembed=bool(auto_fetch),
    )
    if not v:
        raise HTTPException(404, "concept video not found")

    if verify:
        v = _cv.set_quality_tier(
            video_id, "verified",
            curator_note="verified by curator (prod-42)",
        )

    return {
        "ok": True,
        "video": _cv.to_dict(v) if v else None,
        "iframe_check": iframe_check,
    }


@router.post("/api/admin/concept-videos/check-iframe")
def admin_check_iframe(payload: dict = Body(...)) -> dict:
    """prod-67 — server-side iframe-block precheck. Admin-only via
    router-level dep injection. Useful for the curator UI to warn
    before saving a URL that wouldn't render on the dashboard.

    Body:
        { "source_url": "https://www.youtube.com/watch?v=..." }

    Response is the dict from `check_iframe_embed()`. SSRF-safe —
    only fetches hosts in the YouTube/Vimeo allowlist.
    """
    url = (payload or {}).get("source_url", "").strip()
    if not url:
        raise HTTPException(400, "source_url required in body")
    return _cv.check_iframe_embed(url)


@router.get("/api/admin/concept-videos/queue")
def admin_curator_queue(
    quality_tier: str = Query("channel_seed"),
    limit: int = Query(50, ge=1, le=200),
) -> dict:
    """prod-42 — list rows the curator needs to verify, oldest-first.
    Includes a YouTube search URL pre-built from concept + channel so
    the curator can click straight through to find the actual video.
    """
    rows = _cv.list_curator_queue(quality_tier=quality_tier, limit=limit)
    import urllib.parse
    out = []
    for r in rows:
        d = _cv.to_dict(r)
        # Build a YouTube search URL the curator can click. We bias the
        # query to the trusted channel by appending its name.
        q = f"{r.concept}"
        if r.channel:
            q = f"{q} {r.channel}"
        d["search_url"] = (
            "https://www.youtube.com/results?search_query="
            + urllib.parse.quote_plus(q)
        )
        out.append(d)
    return {"rows": out, "count": len(out)}


_CURATOR_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Concept-Video Curator</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body { font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
         background: #0b1220; color: #e2e8f0; margin: 0; padding: 24px; }
  h1 { margin: 0 0 4px; font-size: 22px; }
  .sub { color: #94a3b8; font-size: 13px; margin-bottom: 24px; }
  .toolbar { display: flex; gap: 10px; flex-wrap: wrap; margin-bottom: 16px; align-items: center; }
  .toolbar button, .toolbar select {
    background: #1e293b; color: #e2e8f0; border: 1px solid #334155;
    padding: 6px 12px; border-radius: 6px; cursor: pointer; font-size: 13px;
  }
  .toolbar button:hover { background: #334155; }
  .stat-chip { background: #1e293b; padding: 6px 12px; border-radius: 14px;
               font-size: 12px; color: #94a3b8; }
  .stat-chip strong { color: #e2e8f0; }
  .card { background: #1e293b; border: 1px solid #334155; border-radius: 8px;
          padding: 16px; margin-bottom: 12px; }
  .card .row { display: flex; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
  .card h3 { margin: 0 0 4px; font-size: 16px; }
  .meta { color: #94a3b8; font-size: 12px; margin-bottom: 8px; }
  .meta a { color: #fbbf24; }
  .stub { color: #fca5a5; font-style: italic; }
  .form-grid { display: grid; grid-template-columns: 110px 1fr; gap: 8px; align-items: center;
               margin-top: 12px; padding-top: 12px; border-top: 1px solid #334155; }
  .form-grid label { font-size: 12px; color: #94a3b8; }
  .form-grid input { background: #0b1220; color: #e2e8f0; border: 1px solid #334155;
                     padding: 6px 10px; border-radius: 4px; font-size: 13px; width: 100%; }
  .actions { display: flex; gap: 8px; margin-top: 12px; }
  .btn-verify { background: #059669; color: #fff; border: 0; padding: 8px 14px;
                border-radius: 4px; cursor: pointer; }
  .btn-update { background: #2563eb; color: #fff; border: 0; padding: 8px 14px;
                border-radius: 4px; cursor: pointer; }
  .btn-reject { background: #b91c1c; color: #fff; border: 0; padding: 8px 14px;
                border-radius: 4px; cursor: pointer; }
  .btn-verify:disabled, .btn-update:disabled, .btn-reject:disabled {
    opacity: 0.4; cursor: not-allowed;
  }
  .toast { position: fixed; bottom: 20px; right: 20px; background: #1e293b;
           border: 1px solid #334155; padding: 12px 16px; border-radius: 6px;
           display: none; }
  .toast.ok { border-color: #059669; }
  .toast.err { border-color: #b91c1c; }
  .quality-pill { display: inline-block; padding: 2px 8px; border-radius: 10px;
                  font-size: 11px; margin-left: 6px; }
  .pill-channel_seed { background: #f59e0b; color: #1c1917; }
  .pill-verified { background: #10b981; color: #fff; }
  .pill-ai_fallback { background: #64748b; color: #fff; }
  .empty { color: #94a3b8; padding: 40px; text-align: center; font-style: italic; }
</style>
</head>
<body>
  <h1>Concept-Video Curator</h1>
  <div class="sub">
    Confirm channel-seeded videos by clicking the YouTube search link,
    finding the actual video on the trusted channel, then pasting its
    watch URL + title and clicking <b>Update &amp; Verify</b>.
  </div>

  <div class="toolbar">
    <span class="stat-chip">Showing tier: <strong><span id="currentTier">channel_seed</span></strong></span>
    <span class="stat-chip">Pending: <strong id="pendingCount">0</strong></span>
    <select id="tierFilter">
      <option value="channel_seed">channel_seed (default queue)</option>
      <option value="ai_fallback">ai_fallback (rejected / placeholder)</option>
      <option value="verified">verified (already done)</option>
    </select>
    <button onclick="refresh()">Refresh</button>
    <a href="/admin/curator-stats" style="margin-left:auto;color:#fbbf24;font-size:13px">View stats &rsaquo;</a>
  </div>

  <div id="rows"></div>
  <div id="toast" class="toast"></div>

<script>
function authHeaders() {
  // Same token key the SPA uses (CLAUDE.md §5).
  var t = localStorage.getItem('pathshala_token') || '';
  return t ? { 'Authorization': 'Bearer ' + t } : {};
}

function toast(msg, kind) {
  var t = document.getElementById('toast');
  t.textContent = msg;
  t.className = 'toast ' + (kind || 'ok');
  t.style.display = 'block';
  setTimeout(function() { t.style.display = 'none'; }, 2800);
}

async function refresh() {
  var tier = document.getElementById('tierFilter').value;
  document.getElementById('currentTier').textContent = tier;
  var box = document.getElementById('rows');
  box.innerHTML = '<div class="empty">Loading...</div>';

  try {
    var r = await fetch(
      '/api/admin/concept-videos/queue?quality_tier=' + tier + '&limit=200',
      { headers: authHeaders() },
    );
    if (r.status === 401 || r.status === 403) {
      box.innerHTML = '<div class="empty">Admin sign-in required. Sign in as a superuser first.</div>';
      return;
    }
    var d = await r.json();
    document.getElementById('pendingCount').textContent = d.count;
    if (!d.rows.length) {
      box.innerHTML = '<div class="empty">No rows in this tier. Queue clear!</div>';
      return;
    }
    box.innerHTML = '';
    d.rows.forEach(function(row) { box.appendChild(renderRow(row)); });
  } catch (e) {
    box.innerHTML = '<div class="empty">Error loading queue: ' + e.message + '</div>';
  }
}

function renderRow(r) {
  var card = document.createElement('div');
  card.className = 'card';
  card.id = 'card-' + r.id;
  var subj = r.subject || '-';
  var grade = (r.grade_min || '-') + '-' + (r.grade_max || '-');
  var titleHtml = r.title;
  if (r.title && r.title.startsWith('[')) {
    titleHtml = '<span class="stub">' + r.title + '</span>';
  }
  card.innerHTML =
    '<div class="row">' +
      '<div style="flex:1;min-width:300px">' +
        '<h3>' + (r.concept || '(no concept)') +
          '<span class="quality-pill pill-' + r.quality_tier + '">' + r.quality_tier + '</span>' +
        '</h3>' +
        '<div class="meta">' +
          '<b>Channel:</b> ' + (r.channel || '-') +
          ' &nbsp;<b>Subject:</b> ' + subj +
          ' &nbsp;<b>Grade:</b> ' + grade +
          ' &nbsp;<b>Lang:</b> ' + (r.language || 'en') +
        '</div>' +
        '<div class="meta">Current title: ' + titleHtml + '</div>' +
        '<div class="meta">Current URL: <a href="' + r.source_url + '" target="_blank">' + r.source_url + '</a></div>' +
        (r.search_url ? '<div class="meta"><b>YouTube search:</b> <a href="' + r.search_url + '" target="_blank">click to find on ' + (r.channel || 'YouTube') + '</a></div>' : '') +
        (r.curator_note ? '<div class="meta">Note: ' + r.curator_note + '</div>' : '') +
      '</div>' +
    '</div>' +
    '<div class="form-grid">' +
      '<label for="t-' + r.id + '">New title</label>' +
      '<input id="t-' + r.id + '" placeholder="Paste the actual video title here" />' +
      '<label for="u-' + r.id + '">New URL</label>' +
      '<input id="u-' + r.id + '" placeholder="https://www.youtube.com/watch?v=..." />' +
    '</div>' +
    '<div class="actions">' +
      '<button class="btn-update" data-action="update" data-id="' + r.id + '">Update only</button>' +
      '<button class="btn-verify" data-action="verify" data-id="' + r.id + '">Update &amp; Verify</button>' +
      '<button class="btn-reject" data-action="reject" data-id="' + r.id + '">Reject (move to ai_fallback)</button>' +
    '</div>';
  card.addEventListener('click', function(ev) {
    var btn = ev.target;
    if (btn.tagName !== 'BUTTON' || !btn.dataset.action) return;
    if (btn.dataset.action === 'update') doUpdate(btn.dataset.id, false);
    else if (btn.dataset.action === 'verify') doUpdate(btn.dataset.id, true);
    else if (btn.dataset.action === 'reject') doReject(btn.dataset.id);
  });
  return card;
}

async function doUpdate(id, verify) {
  var title = (document.getElementById('t-' + id).value || '').trim();
  var url = (document.getElementById('u-' + id).value || '').trim();
  if (!title && !url) {
    toast('Provide either a new title or URL first', 'err'); return;
  }
  try {
    var r = await fetch(
      '/api/admin/concept-videos/' + id + '/update',
      {
        method: 'POST',
        headers: Object.assign({ 'Content-Type': 'application/json' }, authHeaders()),
        body: JSON.stringify({ title: title || undefined, source_url: url || undefined, verify: verify }),
      },
    );
    if (!r.ok) throw new Error('HTTP ' + r.status + ' ' + r.statusText);
    var resp = await r.json();
    // prod-67 — surface iframe-block precheck result on the same row.
    var ifc = resp && resp.iframe_check;
    if (ifc && ifc.embeddable === false) {
      toast('Saved, but iframe blocked: ' + ifc.reason, 'err');
    } else if (ifc && ifc.embeddable === true) {
      toast(verify ? 'Updated and verified (embed OK)' : 'Updated (embed OK)', 'ok');
    } else {
      toast(verify ? 'Updated and verified' : 'Updated', 'ok');
    }
    setTimeout(refresh, 600);
  } catch (e) {
    toast('Failed: ' + e.message, 'err');
  }
}

async function doReject(id) {
  if (!confirm('Reject this video? It moves to ai_fallback and stops appearing to students.')) return;
  try {
    var reason = prompt('Reason (optional):') || '';
    var r = await fetch(
      '/api/admin/concept-videos/' + id + '/reject',
      {
        method: 'POST',
        headers: Object.assign({ 'Content-Type': 'application/json' }, authHeaders()),
        body: JSON.stringify({ reason: reason }),
      },
    );
    if (!r.ok) throw new Error('HTTP ' + r.status);
    toast('Rejected', 'ok');
    setTimeout(refresh, 600);
  } catch (e) {
    toast('Failed: ' + e.message, 'err');
  }
}

document.getElementById('tierFilter').addEventListener('change', refresh);
refresh();
</script>
</body>
</html>"""


@router.get("/admin/concept-curator", response_class=HTMLResponse)
def admin_curator_page(
    user=Depends(api_deps.make_admin_dep()),  # noqa: ARG001
) -> str:
    """prod-50 — server-rendered admin page for curating concept videos.

    The page is admin-only (gated by `make_admin_dep()` — same gate that
    backs the JSON endpoints under /api/admin/). The JS in the page
    reuses the `pathshala_token` localStorage key the rest of the SPA
    uses, so a curator who's already signed in just navigates here.
    """
    return _CURATOR_HTML


_CURATOR_STATS_HTML = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<title>Curator stats</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  body { font-family: system-ui, sans-serif; background: #0b1220; color: #e2e8f0; padding: 24px; margin: 0; }
  h1 { margin: 0 0 4px; font-size: 22px; }
  .sub { color: #94a3b8; font-size: 13px; margin-bottom: 24px; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; margin-bottom: 20px; }
  .stat { background: #1e293b; border: 1px solid #334155; border-radius: 8px; padding: 16px; }
  .stat .num { font-size: 28px; font-weight: 800; }
  .stat .lbl { color: #94a3b8; font-size: 12px; margin-top: 4px; }
  .stat.good .num { color: #10b981; }
  .stat.warn .num { color: #f59e0b; }
  .stat.bad .num { color: #ef4444; }
  .row { display: flex; gap: 10px; align-items: center; margin-bottom: 12px; }
  select, .btn { background: #1e293b; color: #e2e8f0; border: 1px solid #334155;
                 padding: 6px 12px; border-radius: 6px; font-size: 13px; cursor: pointer; }
  .btn:hover { background: #334155; }
  .meta { color: #94a3b8; font-size: 12px; }
  .meta strong { color: #e2e8f0; }
  .pill { display:inline-block; padding:2px 8px; border-radius:10px; font-size:11px; margin-right:6px; }
  .pill-verified { background:#10b981; color:#fff; }
  .pill-channel_seed { background:#f59e0b; color:#1c1917; }
  .pill-ai_fallback { background:#64748b; color:#fff; }
</style>
</head><body>
  <h1>Concept-video curator stats</h1>
  <div class="sub">Aggregate counters from the curator workflow. Numbers update as soon as you Verify / Update / Reject on the <a href="/admin/concept-curator" style="color:#fbbf24">curator page</a>.</div>

  <div class="row">
    <span>Window:</span>
    <select id="windowSel">
      <option value="7">last 7 days</option>
      <option value="30" selected>last 30 days</option>
      <option value="90">last 90 days</option>
      <option value="365">last year</option>
    </select>
    <button class="btn" onclick="refresh()">Refresh</button>
    <span class="meta" id="updatedAt"></span>
  </div>

  <div class="grid" id="statGrid"></div>
  <div id="tierBreakdown"></div>
  <div id="freshness" style="margin-top:24px"></div>

<script>
function authH() {
  var t = localStorage.getItem('pathshala_token') || '';
  return t ? { 'Authorization': 'Bearer ' + t } : {};
}
async function refresh() {
  var days = document.getElementById('windowSel').value;
  var grid = document.getElementById('statGrid');
  grid.innerHTML = '<div class="stat"><div class="lbl">Loading...</div></div>';
  try {
    var r = await fetch('/api/admin/concept-videos/curator-stats?since_days=' + days, { headers: authH() });
    if (r.status === 401 || r.status === 403) {
      grid.innerHTML = '<div class="stat bad"><div class="num">401</div><div class="lbl">Admin sign-in required</div></div>';
      return;
    }
    var d = await r.json();
    grid.innerHTML =
      '<div class="stat good">' +
        '<div class="num">' + (d.verified_recent || 0) + '</div>' +
        '<div class="lbl">verified in window</div>' +
      '</div>' +
      '<div class="stat">' +
        '<div class="num">' + (d.updated_recent || 0) + '</div>' +
        '<div class="lbl">rows updated in window</div>' +
      '</div>' +
      '<div class="stat">' +
        '<div class="num">' + (d.played_recent_total || 0) + '</div>' +
        '<div class="lbl">total plays in window</div>' +
      '</div>' +
      '<div class="stat">' +
        '<div class="num">' + (d.total || 0) + '</div>' +
        '<div class="lbl">total catalog rows</div>' +
      '</div>';
    // Tier breakdown
    var byTier = d.by_tier || {};
    var tb = document.getElementById('tierBreakdown');
    tb.innerHTML = '<div class="meta"><strong>By quality tier (all-time):</strong></div>' +
      '<div style="display:flex;gap:8px;margin-top:8px;flex-wrap:wrap">' +
        Object.keys(byTier).map(function(k) {
          return '<span class="pill pill-' + k + '">' + k + ': ' + byTier[k] + '</span>';
        }).join('') +
      '</div>';
    // Freshness
    var f = document.getElementById('freshness');
    var freshest = d.freshest_verified_iso || '(never)';
    var oldest = d.oldest_verified_iso || '(none)';
    f.innerHTML =
      '<div class="meta"><strong>Verification freshness</strong></div>' +
      '<div class="meta" style="margin-top:6px">Newest verification: <strong>' + freshest + '</strong></div>' +
      '<div class="meta">Oldest verification still in catalog: <strong>' + oldest + '</strong></div>' +
      '<div class="meta" style="margin-top:14px"><a href="/admin/concept-curator" style="color:#fbbf24">Open curator queue ›</a></div>';
    document.getElementById('updatedAt').textContent = ' • Updated just now';
  } catch (e) {
    grid.innerHTML = '<div class="stat bad"><div class="num">Err</div><div class="lbl">' + e.message + '</div></div>';
  }
}
document.getElementById('windowSel').addEventListener('change', refresh);
refresh();
</script>
</body></html>"""


@router.get("/admin/curator-stats", response_class=HTMLResponse)
def admin_curator_stats_page(
    user=Depends(api_deps.make_admin_dep()),  # noqa: ARG001
) -> str:
    """prod-74 — server-rendered stats dashboard for the curator workflow.
    Admin-only via the same gate as the curator page."""
    return _CURATOR_STATS_HTML


_HEALTH_HTML = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<title>System health</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  body { font-family: system-ui, sans-serif; background: #0b1220; color: #e2e8f0; padding: 24px; margin: 0; }
  h1 { margin: 0 0 4px; font-size: 22px; }
  .sub { color: #94a3b8; font-size: 13px; margin-bottom: 24px; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 12px; margin-bottom: 24px; }
  .card { background: #1e293b; border: 1px solid #334155; border-radius: 8px; padding: 16px; }
  .card h3 { margin: 0 0 8px; font-size: 13px; color: #94a3b8; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px; }
  .num { font-size: 28px; font-weight: 800; }
  .lbl { color: #94a3b8; font-size: 12px; margin-top: 4px; }
  .meta { color: #94a3b8; font-size: 12px; margin-top: 8px; }
  .meta a { color: #fbbf24; text-decoration: none; }
  .good { color: #10b981; }
  .warn { color: #f59e0b; }
  .bad { color: #ef4444; }
  .row { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; margin-bottom: 14px; }
  .btn { background: #1e293b; color: #e2e8f0; border: 1px solid #334155; padding: 6px 12px; border-radius: 6px; cursor: pointer; font-size: 13px; text-decoration: none; display: inline-block; }
  .btn:hover { background: #334155; }
</style>
</head><body>
  <h1>System health</h1>
  <div class="sub">
    Single-page overview of the curator workflow + cost telemetry. All values are
    fetched from the same admin JSON endpoints the dedicated pages use.
    <a href="/admin/concept-curator" style="color:#fbbf24">Curator queue</a> ·
    <a href="/admin/curator-stats" style="color:#fbbf24">Curator stats</a>
  </div>

  <div class="row">
    <button class="btn" onclick="refresh()">Refresh all</button>
    <span class="meta" id="updatedAt"></span>
  </div>

  <div class="grid" id="topGrid"></div>

  <h3 style="margin: 24px 0 8px; font-size: 16px">Catalog coverage</h3>
  <div class="grid" id="badgeGrid"></div>

  <h3 style="margin: 24px 0 8px; font-size: 16px">Curator queue</h3>
  <div class="grid" id="curatorGrid"></div>

<script>
function authH() {
  var t = localStorage.getItem('pathshala_token') || '';
  return t ? { 'Authorization': 'Bearer ' + t } : {};
}

function card(num, lbl, klass) {
  return '<div class="card"><div class="num ' + (klass||'') + '">' + num + '</div><div class="lbl">' + lbl + '</div></div>';
}

async function loadBadge() {
  try {
    var r = await fetch('/api/concept-videos/badge');
    if (!r.ok) return;
    var d = await r.json();
    var verifiedClass = d.verified_pct >= 80 ? 'good' : d.verified_pct >= 30 ? 'warn' : 'bad';
    document.getElementById('badgeGrid').innerHTML =
      card(d.total, 'total concept videos') +
      card(d.verified, 'verified', verifiedClass) +
      card(d.verified_pct + '%', 'verified ratio', verifiedClass) +
      card(d.channel_seed, 'channel_seed (awaiting curator)') +
      card((d.languages || []).length, 'languages') +
      card((d.subjects || []).length, 'subjects') +
      card(d.freshness_label, 'last verification');
  } catch (e) { /* ignore */ }
}

async function loadCuratorStats() {
  try {
    var r = await fetch('/api/admin/concept-videos/curator-stats?since_days=30', { headers: authH() });
    if (r.status === 401 || r.status === 403) {
      document.getElementById('curatorGrid').innerHTML = card('401', 'admin sign-in required', 'bad');
      return;
    }
    var d = await r.json();
    document.getElementById('curatorGrid').innerHTML =
      card(d.verified_recent || 0, 'verified (30d)', 'good') +
      card(d.updated_recent || 0, 'rows updated (30d)') +
      card(d.played_recent_total || 0, 'total plays (30d)') +
      card(d.total || 0, 'total catalog rows');
  } catch (e) { /* ignore */ }
}

async function loadTopGrid() {
  // /healthz is public; gives git_sha + db_status
  var top = '';
  try {
    var r = await fetch('/healthz');
    if (r.ok) {
      var d = await r.json();
      top += card(d.status || '?', 'service status', d.status === 'ok' ? 'good' : 'bad');
      top += card(d.db_status || '?', 'database', d.db_status === 'ok' ? 'good' : 'warn');
      top += card((d.git_sha || 'n/a').slice(0, 7), 'git SHA');
    }
  } catch (e) {
    top += card('Err', 'healthz unreachable', 'bad');
  }
  document.getElementById('topGrid').innerHTML = top;
}

async function refresh() {
  await Promise.all([loadTopGrid(), loadBadge(), loadCuratorStats()]);
  document.getElementById('updatedAt').textContent = '• Updated just now';
}
refresh();
</script>
</body></html>"""


@router.get("/admin/health", response_class=HTMLResponse)
def admin_health_page(
    user=Depends(api_deps.make_admin_dep()),  # noqa: ARG001
) -> str:
    """prod-85 — single-page system-health overview for ops/admins.

    Cross-links to the dedicated curator pages. Aggregates the public
    /healthz response (service+DB status, git SHA), the public badge
    endpoint (catalog ratios), and the admin curator-stats endpoint
    (workflow throughput). Anything that requires deeper investigation
    has a link to the dedicated page.
    """
    return _HEALTH_HTML


@router.get("/api/admin/concept-videos/curator-stats")
def admin_curator_stats(
    since_days: int = Query(30, ge=1, le=365),
) -> dict:
    """prod-74 — JSON sibling of /admin/curator-stats. Powers the page's
    chart fetches. Admin-only via router-level dep (path is under
    /api/admin/*)."""
    return _cv.curator_stats(since_days=since_days)


@router.post("/api/admin/concept-videos/{video_id}/reject")
def admin_reject_video(
    video_id: str,
    payload: dict | None = Body(None),
) -> dict:
    """Flip a video back to `ai_fallback` — used when a curator finds
    the URL plays the wrong video or has been removed. Optional `reason`
    in the body is appended to curator_note for the audit trail."""
    reason = (payload or {}).get("reason") or "rejected by curator"
    v = _cv.set_quality_tier(
        video_id, "ai_fallback",
        curator_note=f"rejected (prod-41): {reason}",
    )
    if not v:
        raise HTTPException(404, "concept video not found")
    return {"ok": True, "video": _cv.to_dict(v)}
