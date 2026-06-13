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
    language. Falls back to English when the locale-specific row
    doesn't exist. Tries `verified` first, then `channel_seed`."""
    for lang in (language, "en"):
        for tier in ("verified", "channel_seed"):
            v = _cv.get_by_concept_slug(slug, language=lang, quality_tier=tier)
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


@router.get("/concept", response_class=HTMLResponse)
def concept_index(request: Request) -> HTMLResponse:
    """Index page — lists every concept linked to its /concept/{slug}.
    Cheap server-rendered list, no JS required, fast for crawlers."""
    try:
        names = _cv.list_concepts(language="en") or []
    except Exception:
        names = []
    lines = []
    for n in sorted(names):
        slug = _safe_slug(n.replace(" ", "-"))
        lines.append(
            f'<li><a href="/concept/{quote(slug)}">{html.escape(n)}</a></li>'
        )
    base = str(request.base_url).rstrip("/")
    body = (
        '<!doctype html><html lang="en"><head>'
        '<meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<title>Concept library — AI Pathshala</title>'
        '<meta name="description" content="Browse every curated concept '
        'explainer in AI Pathshala\'s catalog. Covers CBSE / ICSE / state '
        'boards / NEET / JEE / UPSC.">'
        f'<link rel="canonical" href="{base}/concept">'
        '<style>body{font-family:Inter,system-ui,sans-serif;max-width:760px;'
        'margin:24px auto;padding:0 16px;color:#101828}'
        'h1{font-size:24px}ul{padding-left:18px;line-height:1.8}'
        'a{color:#1565d8;text-decoration:none}a:hover{text-decoration:underline}'
        '.foot{margin-top:32px;color:#5a6470;font-size:13px}</style>'
        '</head><body>'
        '<h1>Concept library</h1>'
        f'<p>{len(names)} curated concepts across CBSE, ICSE, state boards, '
        'NEET, JEE and UPSC.</p>'
        f'<ul>{"".join(lines)}</ul>'
        '<div class="foot"><a href="/home">← AI Pathshala home</a></div>'
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

    # Related concept crawl-links
    related = _related_concepts(video.concept, limit=6)
    related_html = ""
    if related:
        items = []
        for name in related:
            r_slug = quote(_safe_slug(name.replace(" ", "-")))
            items.append(
                f'<li><a href="/concept/{r_slug}">{html.escape(name)}</a></li>'
            )
        related_html = (
            '<section class="related"><h2>Related concepts</h2>'
            f'<ul>{"".join(items)}</ul></section>'
        )

    # Body
    body = (
        '<!doctype html>'
        f'<html lang="{lang}"><head>'
        '<meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f'<title>{title} — AI Pathshala</title>'
        f'<meta name="description" content="{html.escape(description)}">'
        f'<link rel="canonical" href="{canonical_url}">\n'
        + hreflang_tags + "\n"
        + og_tags
        + schema_org
        + '<style>'
        'body{font-family:Inter,system-ui,sans-serif;max-width:840px;'
        'margin:0 auto;padding:18px 16px;color:#101828;background:#f5f7fb}'
        'h1{font-size:28px;margin:6px 0 14px;line-height:1.25}'
        'h2{font-size:18px;margin:24px 0 8px}'
        '.embed{position:relative;width:100%;padding-bottom:56.25%;'
        'background:#000;border-radius:8px;overflow:hidden}'
        '.embed iframe{position:absolute;inset:0;width:100%;height:100%;'
        'border:0}'
        '.meta{color:#5a6470;font-size:14px;margin:8px 0}'
        '.cta{display:inline-block;background:#1565d8;color:#fff;'
        'padding:10px 18px;border-radius:8px;text-decoration:none;'
        'margin:18px 0}'
        '.related ul{padding-left:18px;line-height:1.8}'
        '.foot{margin-top:24px;color:#5a6470;font-size:13px}'
        'a{color:#1565d8}'
        '</style>'
        '</head><body>'
        f'<h1>{concept_name}</h1>'
        f'<div class="meta">Curated explainer by <b>{channel}</b> · '
        f'language: {video.language} · '
        f'quality: {video.quality_tier}</div>'
        f'<div class="embed">'
        f'<iframe src="{embed_url}" allowfullscreen '
        f'allow="accelerometer; clipboard-write; encrypted-media; '
        f'gyroscope; picture-in-picture" '
        f'title="{title}"></iframe></div>'
        '<a class="cta" href="/home">Sign up to track your progress →</a>'
        f'<p class="meta">Source: <a href="{src_url}" rel="noopener" '
        f'target="_blank">{video.source}</a> · '
        f'AI Pathshala does not host or own this video. '
        f'It is embedded under the educator\'s public YouTube terms.</p>'
        + examples_html
        + related_html
        + '<div class="foot">'
        '<a href="/concept">All concepts</a> · '
        '<a href="/home">AI Pathshala home</a> · '
        '<a href="/pricing">Pricing</a>'
        '</div>'
        '</body></html>'
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
