"""prod-134 — Public server-rendered `/concept/{slug}` SEO surface.

When a student searches Google for "Newton's first law explained" or
"photosynthesis class 10 video", we want them to land on AI Pathshala
— not on YouTube directly. This router renders each curated concept
as a server-rendered HTML page with:

  • Schema.org `VideoObject` JSON-LD markup (helps Google's video
    carousel + featured-snippet placements).
  • Open Graph metadata (og:title, og:description, og:image, og:url)
    so the page renders rich previews on WhatsApp, Facebook, Twitter,
    LinkedIn — important for India where WhatsApp share traffic is
    a large discovery channel.
  • hreflang link tags for all 9 supported languages (en, hi, ta, te,
    kn, ml, mr, bn, gu, pa) — Google routes the right-language user
    to the right URL.
  • Embedded YouTube iframe so visitors can play the video without
    leaving Pathshala.
  • A CTA to sign up / open the math-vision page so SEO traffic
    converts to engaged users.
  • Related concept links for crawl-depth + on-site SEO.

URLs:
    GET /concept/{slug}            English-language fallback
    GET /concept/{slug}?lang=hi    Hindi (or any of the 9 supported)
    GET /concept                   Index of all available concepts

Returns 404 when no concept matches the slug. All routes are public
(no auth) so search-engine crawlers can index them.
"""
from __future__ import annotations

import html
import re
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse

from .. import concept_examples as _ex
from .. import concept_videos as _cv

router = APIRouter()


SUPPORTED_LOCALES = (
    "en", "hi", "ta", "te", "kn", "ml", "mr", "bn", "gu", "pa",
)


def _safe_slug(slug: str) -> str:
    """Normalize a URL slug to the form concept_videos expects."""
    return slug.strip().replace("_", "-").lower()


def _md_to_safe_html(md: str) -> str:
    """Minimal markdown → HTML for example bodies. Escapes everything,
    then converts a tight subset: **bold**, *italic*, blank-line paragraphs,
    and single newlines to <br>. No raw HTML, no images (curator can add
    them via ![alt](url) which we render as <img> with rel=nofollow).

    Safe by default — anything not in the subset stays escaped."""
    if not md:
        return ""
    text = html.escape(md)
    # **bold** (do bold first so it doesn't eat into italic)
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    # *italic*
    text = re.sub(r"(?<!\*)\*([^*\n]+?)\*(?!\*)", r"<em>\1</em>", text)
    # ![alt](url) — restricted to http(s) URLs + escaped attributes
    def _img_replace(m):
        alt = m.group(1)
        url = m.group(2)
        if not url.startswith(("http://", "https://")):
            return m.group(0)
        return (
            f'<img src="{url}" alt="{alt}" '
            'loading="lazy" referrerpolicy="no-referrer" '
            'style="max-width:100%;height:auto;border-radius:6px">'
        )
    text = re.sub(r"!\[([^\]]*)\]\(([^)\s]+)\)", _img_replace, text)
    # paragraph breaks on blank lines
    paragraphs = re.split(r"\n\s*\n", text)
    paragraphs = [p.replace("\n", "<br>") for p in paragraphs if p.strip()]
    return "".join(f"<p>{p}</p>" for p in paragraphs)


def _video_for_slug(slug: str, language: str) -> _cv.ConceptVideo | None:
    """Look up a concept video by slug, preferring the requested
    language, falling back to English.

    prod-180 — verified-only. Previously this fell back to
    `channel_seed`, but those carry unconfirmed/placeholder URLs that
    can 404 ("video unavailable"). A public SEO page must never embed
    a broken video, so we only serve `verified`. A concept whose only
    video is still channel_seed simply 404s here until a curator
    confirms a real URL (better a 404 than a dead embed in Google's
    index)."""
    for lang in (language, "en"):
        v = _cv.get_by_concept_slug(slug, language=lang, quality_tier="verified")
        if v is not None:
            return v
    return None


def _related_concepts(current: str, limit: int = 6) -> list[str]:
    """Return up to N other concept names (alphabetic) for cross-linking.
    These become <a href="/concept/...">crawl edges</a> in the page
    footer — drives SEO depth and helps the user discover adjacent
    topics."""
    try:
        names = _cv.list_concepts(language="en") or []
    except Exception:
        return []
    others = [n for n in names if n != current]
    return others[:limit]


def _absolute_url(request: Request, path: str) -> str:
    """Produce an absolute URL for OG/canonical/hreflang. Honours
    the running host so dev/staging/prod each render the right URLs."""
    base = str(request.base_url).rstrip("/")
    return f"{base}{path}"


# ---------------------------------------------------------------------------
# prod-151 — Shared AI Pathshala SPA chrome for /concept + /concept/{slug}
# pages. Top nav (brand + sign-in link), breadcrumb, search box, footer.
# Match the visual style of /home / /mastery / /memory-boost so the
# /concept pages don't look like a different app.
# ---------------------------------------------------------------------------

_SPA_CHROME_CSS = (
    'body{font-family:Inter,system-ui,sans-serif;margin:0;padding:0;'
    'color:#101828;background:#f5f7fb;line-height:1.55}'
    '.topnav{background:#fff;border-bottom:1px solid #e3e6ec;'
    'padding:12px 20px;display:flex;align-items:center;'
    'justify-content:space-between;flex-wrap:wrap;gap:8px}'
    '.brand{font-weight:700;font-size:17px;color:#0b3a8a;'
    'text-decoration:none;letter-spacing:-0.01em}'
    '.brand span{color:#1565d8}'
    '.nav-links{display:flex;gap:16px;flex-wrap:wrap;align-items:center}'
    '.nav-links a{color:#445;text-decoration:none;font-size:14px;font-weight:500}'
    '.nav-links a:hover{color:#1565d8}'
    '.nav-cta{background:#1565d8;color:#fff !important;padding:7px 14px;'
    'border-radius:6px;font-weight:600 !important}'
    '.nav-cta:hover{background:#0e4eb6;color:#fff !important}'
    '.crumb{max-width:1080px;margin:14px auto 0;padding:0 20px;'
    'font-size:13px;color:#5a6470}'
    '.crumb a{color:#1565d8;text-decoration:none}'
    '.crumb a:hover{text-decoration:underline}'
    '.page{max-width:1080px;margin:0 auto;padding:18px 20px 40px}'
    '.foot{max-width:1080px;margin:32px auto 0;padding:24px 20px;'
    'border-top:1px solid #e3e6ec;color:#5a6470;font-size:13px;'
    'display:flex;flex-wrap:wrap;gap:18px}'
    '.foot a{color:#1565d8;text-decoration:none}'
    '.foot a:hover{text-decoration:underline}'
)


def _top_nav() -> str:
    """Render the AI Pathshala top navigation. Same shape as /home so
    a visitor landing from Google sees a consistent app."""
    return (
        '<nav class="topnav" role="navigation">'
        '<a class="brand" href="/home">AI <span>Pathshala</span></a>'
        '<div class="nav-links">'
        '<a href="/concept">Concepts</a>'
        '<a href="/syllabus">Syllabus</a>'
        '<a href="/practice">Practice</a>'
        '<a href="/chat">Tutor</a>'
        '<a class="nav-cta" href="/home">Sign in</a>'
        '</div>'
        '</nav>'
    )


def _footer() -> str:
    return (
        '<footer class="foot">'
        '<a href="/concept">All concepts</a>'
        '<a href="/home">Home</a>'
        '<a href="/syllabus">Syllabus</a>'
        '<a href="/pricing">Pricing</a>'
        '<a href="/privacy">Privacy</a>'
        '<span style="margin-left:auto">'
        'Made for Indian students · CBSE / ICSE / NEET / JEE / UPSC'
        '</span>'
        '</footer>'
    )


def _categorise(name: str) -> str:
    """Best-effort topic → subject bucket for the index-page chips.
    Pure string heuristics so it works at zero cost for any concept."""
    n = name.lower()
    if any(k in n for k in [
        "newton", "force", "motion", "gravity", "energy", "work",
        "power", "wave", "light", "sound", "ohm", "current", "magnet",
        "circuit", "pressure", "friction", "velocity", "acceleration",
        "kinemat", "thermo", "optics", "mechanic",
    ]):
        return "Physics"
    if any(k in n for k in [
        "acid", "base", "molecule", "atom", "reaction", "metal",
        "non-metal", "carbon", "compound", "periodic", "element",
        "salt", "solution",
    ]):
        return "Chemistry"
    if any(k in n for k in [
        "cell", "tissue", "organ", "photosynth", "respir", "circulat",
        "digest", "nervous", "reproduct", "evolution", "genet",
        "ecosystem", "plant", "animal", "human body", "dna", "biology",
    ]):
        return "Biology"
    if any(k in n for k in [
        "equation", "number", "algebra", "geometry", "triangle",
        "circle", "ratio", "fraction", "decimal", "percent", "interest",
        "quadratic", "pythagoras", "trigonometry", "calculus",
        "polynomial", "statistic", "probability", "real number",
    ]):
        return "Mathematics"
    if any(k in n for k in [
        "constitution", "fundamental right", "directive principle",
        "parliament", "judiciary", "executive", "polity",
    ]):
        return "Polity / Civics"
    if any(k in n for k in [
        "river", "mountain", "climate", "monsoon", "geography",
        "soil", "agriculture", "industry", "population",
    ]):
        return "Geography"
    if any(k in n for k in [
        "freedom", "mughal", "british", "gandhi", "nehru", "1857",
        "independence", "history",
    ]):
        return "History"
    return "Other"


@router.get("/concept", response_class=HTMLResponse)
def concept_index(request: Request) -> HTMLResponse:
    """Index page — lists every concept linked to its /concept/{slug}.
    prod-151 — Now wrapped in the AI Pathshala SPA shell (top nav,
    breadcrumb, search box, category chips, grouped grid). Still
    server-rendered + no-build-step + crawler-friendly."""
    try:
        names = _cv.list_concepts(language="en") or []
    except Exception:
        names = []

    # Group by subject for chip-filterable grid
    grouped: dict[str, list[str]] = {}
    for n in names:
        grouped.setdefault(_categorise(n), []).append(n)
    # Stable order: Mathematics first (broadest appeal), then sciences,
    # then humanities, then Other last
    subject_order = [
        "Mathematics", "Physics", "Chemistry", "Biology",
        "History", "Geography", "Polity / Civics", "Other",
    ]
    grouped_sorted = [
        (s, sorted(grouped[s])) for s in subject_order if s in grouped
    ]

    base = str(request.base_url).rstrip("/")

    # prod-226c/d: build a concept -> YouTube thumbnail map so the index
    # renders as a visual video gallery (was text-only cards). One direct
    # query over the whole verified catalog — search() caps at 250 rows, which
    # silently dropped ~19 concepts to placeholders once the catalog passed
    # 250; this covers every concept.
    thumb_by_norm: dict[str, str] = {}
    try:
        with _cv._conn() as _c:
            rows = _c.execute(
                "SELECT concept, embed_url FROM concept_videos "
                "WHERE language = 'en' AND quality_tier = 'verified' "
                "AND source = 'youtube'"
            ).fetchall()
        for concept, embed_url in rows:
            key = _cv._normalise_concept(concept)
            if key in thumb_by_norm:
                continue
            vid = (embed_url or "").rstrip("/").split("/")[-1]
            if vid:
                thumb_by_norm[key] = vid
    except Exception:
        thumb_by_norm = {}

    def _card_thumb(name: str) -> str:
        vid = thumb_by_norm.get(_cv._normalise_concept(name), "")
        if vid:
            src = f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg"
            return (
                '<div class="ccard-thumb">'
                f'<img loading="lazy" src="{src}" alt="" width="320" height="180">'
                '<span class="ccard-play"></span></div>'
            )
        return '<div class="ccard-thumb no-thumb"><span class="ccard-play"></span></div>'

    # Build category chips
    chips_html = "".join(
        f'<button class="chip" data-cat="{html.escape(subject)}" '
        f'onclick="filterCat(this)">{html.escape(subject)} '
        f'<span class="chip-n">{len(items)}</span></button>'
        for subject, items in grouped_sorted
    )

    # Build the grid — one card per concept, grouped by subject
    grid_sections = []
    for subject, items in grouped_sorted:
        cards = "".join(
            f'<a class="ccard" href="/concept/{quote(_safe_slug(n.replace(" ", "-")))}">'
            + _card_thumb(n) +
            f'<div class="ccard-body">'
            f'<div class="ccard-title">{html.escape(n)}</div>'
            f'<div class="ccard-meta">▶ Watch explainer</div>'
            '</div>'
            '</a>'
            for n in items
        )
        grid_sections.append(
            f'<section class="grp" data-cat="{html.escape(subject)}">'
            f'<h2>{html.escape(subject)} <span class="grp-n">'
            f'({len(items)})</span></h2>'
            f'<div class="grid">{cards}</div>'
            '</section>'
        )
    grid_html = "".join(grid_sections) or (
        '<p class="empty">No curated concepts yet — '
        '<a href="/home">check back soon</a>.</p>'
    )

    body = (
        '<!doctype html><html lang="en"><head>'
        '<meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<title>Concept library — AI Pathshala</title>'
        '<meta name="description" content="Browse every curated concept '
        'explainer in AI Pathshala\'s catalog. Covers CBSE / ICSE / state '
        'boards / NEET / JEE / UPSC.">'
        f'<link rel="canonical" href="{base}/concept">'
        '<link rel="preconnect" href="https://i.ytimg.com" crossorigin>'
        '<link rel="dns-prefetch" href="https://i.ytimg.com">'
        '<style>' + _SPA_CHROME_CSS +
        '.hero{padding:18px 0 8px}'
        '.hero h1{font-size:28px;margin:0 0 8px;line-height:1.2}'
        '.hero p{color:#5a6470;margin:0 0 14px;max-width:720px}'
        '.search{display:flex;gap:8px;margin:12px 0 18px;max-width:520px}'
        '.search input{flex:1;padding:10px 14px;border:1px solid #d0d6de;'
        'border-radius:8px;font-size:14px;outline:none;background:#fff}'
        '.search input:focus{border-color:#1565d8;'
        'box-shadow:0 0 0 3px rgba(21,101,216,0.10)}'
        '.chips{display:flex;flex-wrap:wrap;gap:8px;margin:0 0 22px}'
        '.chip{background:#fff;border:1px solid #d0d6de;color:#101828;'
        'padding:7px 14px;border-radius:999px;font-size:13px;'
        'cursor:pointer;display:inline-flex;align-items:center;gap:6px;'
        'font-family:inherit;font-weight:500}'
        '.chip:hover{border-color:#1565d8;color:#1565d8}'
        '.chip.active{background:#1565d8;color:#fff;border-color:#1565d8}'
        '.chip-n{background:rgba(0,0,0,0.08);padding:1px 7px;'
        'border-radius:10px;font-size:11px;font-weight:600}'
        '.chip.active .chip-n{background:rgba(255,255,255,0.22)}'
        '.grp{margin:24px 0}'
        '.grp h2{font-size:17px;color:#0b3a8a;margin:0 0 12px;'
        'display:flex;align-items:baseline;gap:8px}'
        '.grp-n{color:#9aa3b0;font-size:13px;font-weight:500}'
        '.grid{display:grid;grid-template-columns:repeat(auto-fill,'
        'minmax(220px,1fr));gap:12px}'
        '.ccard{background:#fff;border:1px solid #e3e6ec;border-radius:10px;'
        'text-decoration:none;color:#101828;display:block;overflow:hidden;'
        'transition:border-color 0.15s,box-shadow 0.15s}'
        '.ccard:hover{border-color:#1565d8;'
        'box-shadow:0 2px 10px rgba(21,101,216,0.12)}'
        # thumbnail (16:9) with a YouTube-style play button
        '.ccard-thumb{position:relative;width:100%;aspect-ratio:16/9;'
        'background:#0b3a8a;overflow:hidden}'
        '.ccard-thumb img{width:100%;height:100%;object-fit:cover;display:block}'
        '.ccard-thumb.no-thumb{background:linear-gradient(135deg,#1565d8,#0b3a8a)}'
        '.ccard-play{position:absolute;inset:0;display:flex;'
        'align-items:center;justify-content:center}'
        '.ccard-play::before{content:"";width:48px;height:34px;'
        'border-radius:9px;background:rgba(0,0,0,0.55);transition:background .12s}'
        '.ccard:hover .ccard-play::before{background:#f00}'
        '.ccard-play::after{content:"";position:absolute;border-style:solid;'
        'border-width:8px 0 8px 14px;'
        'border-color:transparent transparent transparent #fff}'
        '.ccard-body{padding:11px 14px}'
        '.ccard-title{font-weight:600;font-size:14px;margin-bottom:4px;'
        'line-height:1.3}'
        '.ccard-meta{font-size:12px;color:#5a6470}'
        '.empty{color:#5a6470;padding:40px 0;text-align:center}'
        '.empty a{color:#1565d8}'
        '.hidden{display:none !important}'
        '</style></head><body>'
        + _top_nav()
        + '<div class="crumb">'
        '<a href="/home">Home</a> &nbsp;›&nbsp; <span>Concepts</span>'
        '</div>'
        '<main class="page">'
        '<section class="hero">'
        '<h1>Concept library</h1>'
        f'<p>{len(names)} curated explainer videos across CBSE, ICSE, '
        'state boards, NEET, JEE and UPSC. Hand-picked by AI Pathshala '
        'educators for Indian students.</p>'
        '<div class="search">'
        '<input type="search" id="q" placeholder="Search concepts… '
        '(e.g. Newton, photosynthesis, quadratic)" oninput="filterText()">'
        '</div>'
        f'<div class="chips"><button class="chip active" data-cat="__all" '
        f'onclick="filterCat(this)">All <span class="chip-n">'
        f'{len(names)}</span></button>{chips_html}</div>'
        '</section>'
        + grid_html +
        '</main>'
        + _footer() +
        '<script>'
        'var activeCat="__all";'
        'function filterCat(btn){'
        ' document.querySelectorAll(".chip").forEach(function(c){'
        '   c.classList.remove("active");'
        ' });'
        ' btn.classList.add("active");'
        ' activeCat=btn.getAttribute("data-cat");'
        ' applyFilters();'
        '}'
        'function filterText(){applyFilters();}'
        'function applyFilters(){'
        ' var q=(document.getElementById("q").value||"").toLowerCase().trim();'
        ' document.querySelectorAll(".grp").forEach(function(g){'
        '   var cat=g.getAttribute("data-cat");'
        '   var catMatch=(activeCat==="__all"||activeCat===cat);'
        '   var anyVis=false;'
        '   g.querySelectorAll(".ccard").forEach(function(c){'
        '     var t=c.textContent.toLowerCase();'
        '     var txtMatch=(!q||t.indexOf(q)>=0);'
        '     var vis=catMatch&&txtMatch;'
        '     c.classList.toggle("hidden",!vis);'
        '     if(vis)anyVis=true;'
        '   });'
        '   g.classList.toggle("hidden",!anyVis);'
        ' });'
        '}'
        '</script>'
        '</body></html>'
    )
    return HTMLResponse(body)


@router.get("/concept/{slug}", response_class=HTMLResponse)
def concept_page(
    slug: str,
    request: Request,
    lang: str = Query("en", description="ISO 639-1 lang code; one of SUPPORTED_LOCALES"),
) -> HTMLResponse:
    """prod-134 — Server-rendered concept page for SEO + WhatsApp shares.

    Returns the curated video embed + Open Graph + Schema.org markup
    + hreflang tags for all 9 languages + a sign-up CTA. 404 when no
    concept matches.
    """
    lang = (lang or "en").strip().lower()
    if lang not in SUPPORTED_LOCALES:
        lang = "en"

    slug = _safe_slug(slug)
    video = _video_for_slug(slug, lang)
    if video is None:
        raise HTTPException(404, "concept not found")

    # Compose the page
    canonical_path = f"/concept/{quote(slug)}"
    canonical_url = _absolute_url(request, canonical_path)
    title = html.escape(video.title or video.concept)
    concept_name = html.escape(video.concept)
    channel = html.escape(video.channel or "Curated educator")
    description = (
        f"Watch a curated explainer for {concept_name}. "
        f"Hand-picked by AI Pathshala educators for Indian students "
        f"(CBSE, ICSE, NEET, JEE, UPSC). Free to watch."
    )
    embed_url = html.escape(video.embed_url or "")
    src_url = html.escape(video.source_url or "")

    # hreflang tags for the 9 SUPPORTED_LOCALES
    hreflang_tags = "\n".join(
        f'  <link rel="alternate" hreflang="{loc}" '
        f'href="{_absolute_url(request, canonical_path + ("" if loc == "en" else f"?lang={loc}"))}">'
        for loc in SUPPORTED_LOCALES
    )
    hreflang_tags += (
        f'\n  <link rel="alternate" hreflang="x-default" '
        f'href="{canonical_url}">'
    )

    # Schema.org VideoObject — Google's preferred markup for video pages.
    # Description deliberately concise so it doesn't truncate in SERP.
    video_id = video.embed_url.rstrip("/").split("/")[-1] if video.embed_url else ""
    thumbnail = (
        f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
        if video.source == "youtube" and video_id else ""
    )

    # prod-226: thumbnail facade — the page paints the thumbnail + a play
    # button instantly (great LCP + SEO via og:image below); the heavy iframe
    # loads with autoplay only when the visitor clicks. Falls back to a plain
    # lazy iframe when there's no thumbnail (non-YouTube source).
    if thumbnail and embed_url:
        embed_block = (
            f'<div class="embed lite" id="cvEmbed" role="button" tabindex="0" '
            f'aria-label="Play video: {title}" data-embed="{embed_url}" '
            f'style="background-image:url({thumbnail})">'
            f'<span class="pbtn"></span></div>'
        )
        embed_script = (
            '<script>'
            '(function(){'
            'var e=document.getElementById("cvEmbed");if(!e)return;'
            'function play(){'
            'var u=e.getAttribute("data-embed");'
            'if(!u||e.querySelector("iframe"))return;'
            'u+=(u.indexOf("?")>=0?"&":"?")+"autoplay=1";'
            'e.style.backgroundImage="none";'
            'var f=document.createElement("iframe");f.src=u;'
            'f.setAttribute("allowfullscreen","");'
            'f.setAttribute("allow","autoplay; encrypted-media; picture-in-picture");'
            'e.appendChild(f);'
            '}'
            'e.addEventListener("click",play);'
            'e.addEventListener("keydown",function(ev){'
            'if(ev.key==="Enter"||ev.key===" "){ev.preventDefault();play();}});'
            '})();'
            '</script>'
        )
    else:
        embed_block = (
            f'<div class="embed"><iframe src="{embed_url}" allowfullscreen '
            f'allow="accelerometer; clipboard-write; encrypted-media; '
            f'gyroscope; picture-in-picture" title="{title}" '
            f'loading="lazy"></iframe></div>'
        )
        embed_script = ""
    schema_org = (
        '<script type="application/ld+json">{'
        '"@context": "https://schema.org",'
        '"@type": "VideoObject",'
        f'"name": {_json_str(video.title or video.concept)},'
        f'"description": {_json_str(description)},'
        f'"thumbnailUrl": {_json_str(thumbnail)},'
        f'"uploadDate": "2024-01-01",'
        f'"contentUrl": {_json_str(video.source_url)},'
        f'"embedUrl": {_json_str(video.embed_url)},'
        f'"inLanguage": {_json_str(video.language)},'
        f'"learningResourceType": "explainer",'
        '"educationalLevel": '
        + _json_str(
            f"Grade {video.grade_min or 6}-{video.grade_max or 12}",
        )
        + ","
        '"isFamilyFriendly": true'
        '}</script>'
    )

    # OG markup — WhatsApp / Facebook / LinkedIn rich preview
    og_tags = (
        f'  <meta property="og:type" content="video.other">\n'
        f'  <meta property="og:title" content="{title}">\n'
        f'  <meta property="og:description" content="{html.escape(description)}">\n'
        f'  <meta property="og:url" content="{canonical_url}">\n'
        f'  <meta property="og:locale" content="{_og_locale(video.language)}">\n'
        + (f'  <meta property="og:image" content="{thumbnail}">\n' if thumbnail else "")
        + '  <meta property="og:site_name" content="AI Pathshala">\n'
    )

    # prod-137 — Approved real-world examples for this concept
    examples_html = ""
    try:
        _ex.migrate()
        approved = _ex.list_for_slug(
            video.concept, locale=lang, status="approved", limit=3,
        )
        if not approved and lang != "en":
            # Fall back to English examples when locale-specific aren't seeded yet
            approved = _ex.list_for_slug(
                video.concept, locale="en", status="approved", limit=3,
            )
        if approved:
            items = "".join(
                f'<li>{_md_to_safe_html(ex.example_md)}</li>'
                for ex in approved
            )
            examples_html = (
                '<section class="real-world">'
                '<h2>Real-world examples</h2>'
                f'<ul>{items}</ul>'
                '<p class="meta">Curated for Indian students by AI Pathshala '
                'educators.</p>'
                '</section>'
            )
    except Exception:
        # Examples are bonus content — never fail the page if the table
        # is missing or the read errors out.
        examples_html = ""

    # Related concept crawl-links (used both for the sidebar rail and
    # for crawler-visible <a> links).
    related = _related_concepts(video.concept, limit=6)

    # prod-151 — Body now wrapped in the AI Pathshala SPA shell:
    # top nav, breadcrumb (Home › Concepts › <Concept name>), main
    # column with video + meta + examples, related-concept sidebar
    # rail on desktop. Mobile collapses to a single column.
    crumb_html = (
        '<div class="crumb">'
        '<a href="/home">Home</a> &nbsp;›&nbsp; '
        '<a href="/concept">Concepts</a> &nbsp;›&nbsp; '
        f'<span>{concept_name}</span>'
        '</div>'
    )

    # Build the related-concept sidebar (desktop) / footer section
    # (mobile). Same data, different styling per breakpoint.
    related_aside_html = ""
    if related:
        items = []
        for name in related:
            r_slug = quote(_safe_slug(name.replace(" ", "-")))
            items.append(
                f'<li><a href="/concept/{r_slug}">{html.escape(name)}</a></li>'
            )
        related_aside_html = (
            '<aside class="rail"><h3>Related concepts</h3>'
            f'<ul class="rail-list">{"".join(items)}</ul>'
            '<div class="rail-cta">'
            '<a class="cta-sm" href="/concept">Browse all →</a>'
            '</div>'
            '</aside>'
        )

    body = (
        '<!doctype html>'
        f'<html lang="{lang}"><head>'
        '<meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f'<title>{title} — AI Pathshala</title>'
        f'<meta name="description" content="{html.escape(description)}">'
        f'<link rel="canonical" href="{canonical_url}">\n'
        # prod-226: warm YouTube connections so the thumbnail paints instantly
        # and the first click starts playback fast.
        '<link rel="preconnect" href="https://i.ytimg.com" crossorigin>'
        '<link rel="preconnect" href="https://www.youtube-nocookie.com" crossorigin>'
        '<link rel="dns-prefetch" href="https://i.ytimg.com">'
        + hreflang_tags + "\n"
        + og_tags
        + schema_org
        + '<style>' + _SPA_CHROME_CSS +
        '.layout{display:grid;grid-template-columns:1fr 280px;gap:28px;'
        'margin-top:8px}'
        '@media (max-width:880px){.layout{grid-template-columns:1fr}}'
        '.main h1{font-size:26px;margin:6px 0 10px;line-height:1.25;'
        'color:#0b3a8a}'
        '.main h2{font-size:18px;margin:28px 0 10px;color:#0b3a8a}'
        '.embed{position:relative;width:100%;padding-bottom:56.25%;'
        'background:#0b3a8a center/cover no-repeat;border-radius:10px;'
        'overflow:hidden;margin:14px 0 12px;'
        'box-shadow:0 2px 10px rgba(11,58,138,0.08)}'
        '.embed iframe{position:absolute;inset:0;width:100%;height:100%;'
        'border:0}'
        '.embed.lite{cursor:pointer}'
        '.embed.lite .pbtn{position:absolute;inset:0;display:flex;'
        'align-items:center;justify-content:center}'
        '.embed.lite .pbtn::before{content:"";width:72px;height:50px;'
        'border-radius:14px;background:rgba(0,0,0,0.62);transition:background .12s}'
        '.embed.lite:hover .pbtn::before,.embed.lite:focus .pbtn::before'
        '{background:#f00}'
        '.embed.lite .pbtn::after{content:"";position:absolute;'
        'border-style:solid;border-width:13px 0 13px 22px;'
        'border-color:transparent transparent transparent #fff}'
        '.meta{color:#5a6470;font-size:13px;margin:6px 0;line-height:1.5}'
        '.meta-pill{background:#eef3fc;color:#0b3a8a;padding:3px 10px;'
        'border-radius:999px;font-size:12px;font-weight:600;'
        'display:inline-block;margin-right:6px}'
        '.cta{display:inline-block;background:#1565d8;color:#fff;'
        'padding:11px 20px;border-radius:8px;text-decoration:none;'
        'margin:14px 0 4px;font-weight:600;font-size:14px}'
        '.cta:hover{background:#0e4eb6}'
        '.real-world{background:#fff;border:1px solid #e3e6ec;'
        'border-radius:10px;padding:16px 18px;margin:22px 0}'
        '.real-world h2{margin-top:0;color:#0b3a8a;font-size:16px}'
        '.real-world ul{padding-left:18px;line-height:1.7;color:#101828}'
        '.real-world li{margin:8px 0}'
        '.real-world .meta{margin-top:10px;font-size:12px;color:#9aa3b0}'
        '.rail{background:#fff;border:1px solid #e3e6ec;border-radius:10px;'
        'padding:16px 18px;position:sticky;top:16px;height:fit-content}'
        '.rail h3{margin:0 0 10px;font-size:14px;color:#0b3a8a;'
        'text-transform:uppercase;letter-spacing:0.04em}'
        '.rail-list{padding-left:0;list-style:none;margin:0;'
        'line-height:1.7;font-size:14px}'
        '.rail-list li{margin:5px 0;padding:4px 0;'
        'border-bottom:1px solid #f0f2f7}'
        '.rail-list li:last-child{border-bottom:0}'
        '.rail-list a{color:#1565d8;text-decoration:none}'
        '.rail-list a:hover{text-decoration:underline}'
        '.rail-cta{margin-top:14px;padding-top:12px;'
        'border-top:1px solid #e3e6ec}'
        '.cta-sm{color:#1565d8;font-size:13px;text-decoration:none;'
        'font-weight:600}'
        '.cta-sm:hover{text-decoration:underline}'
        '.src{font-size:12px;color:#5a6470;margin-top:14px;'
        'background:#f0f2f7;padding:10px 14px;border-radius:6px;'
        'line-height:1.5}'
        '.src a{color:#1565d8}'
        '</style>'
        '</head><body>'
        + _top_nav()
        + crumb_html
        + '<main class="page">'
        '<div class="layout">'
        '<article class="main">'
        f'<h1>{concept_name}</h1>'
        f'<div class="meta">'
        f'<span class="meta-pill">{html.escape(video.quality_tier)}</span>'
        f'<span class="meta-pill">{html.escape(video.language)}</span>'
        f' Curated explainer by <b>{channel}</b>'
        '</div>'
        + embed_block +
        '<a class="cta" href="/home">Sign up to track your progress →</a>'
        + examples_html
        + f'<div class="src">Source: <a href="{src_url}" rel="noopener" '
        f'target="_blank">{video.source}</a> · AI Pathshala does not '
        'host or own this video. It is embedded under the educator\'s '
        'public YouTube terms.</div>'
        '</article>'
        + related_aside_html +
        '</div>'
        '</main>'
        + _footer()
        + embed_script
        + '</body></html>'
    )
    return HTMLResponse(body)


def _og_locale(lang: str) -> str:
    """Map ISO 639-1 to Open Graph locale (e.g. 'hi' → 'hi_IN')."""
    return {
        "en": "en_IN",
        "hi": "hi_IN",
        "ta": "ta_IN",
        "te": "te_IN",
        "kn": "kn_IN",
        "ml": "ml_IN",
        "mr": "mr_IN",
        "bn": "bn_IN",
        "gu": "gu_IN",
        "pa": "pa_IN",
    }.get(lang, "en_IN")


def _json_str(s: str | None) -> str:
    """JSON-escape a string for embedding in inline <script>. Conservative:
    encodes quotes + backslashes + angle brackets so the script doesn't
    end early on `</script>` inside a description."""
    import json as _json
    return _json.dumps(s or "")
