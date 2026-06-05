"""v3.17 — Student home aggregator.

Per gap-review §26 + the HTML mockup — the first screen should
be goal-led. This module composes the data the mockup needs into
a single API call, so the frontend doesn't fan out to a dozen
endpoints on home-screen load.

Output shape mirrors the mockup's "Exam Hub" layout:

  hero        — headline + sub + actions
  exam_pack   — active pack + readiness card
  metrics     — top 3 stat cards
  daily_flow  — today's plan blocks (numbered)
  community   — top room hints
  fallbacks   — recent fallbacks → "upload more material" nudge
  modules     — categorised module list (drives mockup's chip grid)

The aggregator is **defensive**: every sub-module call wrapped in
_safe so missing data degrades silently. Useful for early-adopter
users where most signals are empty.

Single endpoint exposed via the router: `GET /api/home/me/dashboard`.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

# No SCHEMA / migrate() — pure read composer (mirrors v3.6
# dashboards.py).

def migrate() -> None:
    return None


def _safe(fn, *args, default=None, **kwargs):
    try:
        return fn(*args, **kwargs)
    except Exception:
        return default


def _resolve_active_pack(user_id: str) -> str | None:
    """Pick the user's primary Exam Pack — first active
    enrollment, falling back to first enrollment regardless of
    status."""
    try:
        from . import exam_taxonomy as et
        rows = et.list_user_enrollments(user_id)
        if not rows:
            return None
        active = next(
            (e for e in rows if e.status == "active"), None,
        )
        return (active or rows[0]).pack_code
    except Exception:
        return None


def _readiness_summary(
    *, user_id: str, pack_code: str | None,
) -> dict:
    if not pack_code:
        return {}
    try:
        from . import readiness as rd
        r = rd.get_readiness(
            user_id=user_id, pack_code=pack_code,
            refresh_if_stale=False,
        )
    except Exception:
        r = None
    if not r:
        return {}
    return {
        "score": r.score,
        "mastery_score": r.mastery_score,
        "mock_score": r.mock_score,
        "coverage_score": r.coverage_score,
        "consistency_score": r.consistency_score,
        "trust_score": r.trust_score,
        "weak_topics": r.weak_topics[:5],
        "computed_at": r.computed_at,
    }


def _pack_meta(pack_code: str | None) -> dict:
    if not pack_code:
        return {}
    try:
        from . import exam_taxonomy as et
        p = et.get_pack(pack_code)
    except Exception:
        p = None
    if not p:
        return {"pack_code": pack_code}
    return {
        "pack_code": p.code,
        "title": p.title,
        "exam_code": p.exam_code,
        "year": p.year,
        "pattern_summary": p.pattern_summary,
        "cutoff_summary": p.cutoff_summary,
        "estimated_hours": p.estimated_hours,
    }


def _daily_flow(*, user_id: str, pack_code: str | None) -> dict:
    if not pack_code:
        return {"plan_id": None, "blocks": []}
    try:
        from . import daily_plan as dp
        plan = dp.get_or_generate(
            user_id=user_id, pack_code=pack_code,
        )
    except Exception:
        plan = None
    if not plan:
        return {"plan_id": None, "blocks": []}
    return {
        "plan_id": plan.id,
        "plan_date": plan.plan_date,
        "total_minutes": plan.total_minutes,
        "completion_pct": plan.completion_pct,
        "blocks": [
            {"position": b.position, "kind": b.kind,
             "title": b.title, "estimated_min": b.estimated_min,
             "completed": b.completed,
             "ref_kind": b.ref_kind, "ref_id": b.ref_id}
            for b in plan.blocks
        ],
    }


def _weak_topics_headline(weak_codes: list[str]) -> dict:
    """Turn weak topic codes into a hero sentence:
    'Your X path is behind in A, B; strong in C'.
    Caller supplies the readiness 'weak_topics' list; we look up
    a small set of recent-strong topics from mastery."""
    return {
        "weak": [
            c.replace("_", " ").title() for c in weak_codes[:3]
        ],
    }


def _strong_topics(*, user_id: str, exam_code: str | None) -> list[str]:
    if not exam_code:
        return []
    try:
        from . import mastery as m
        rows = m.list_for_user(user_id, limit=200)
    except Exception:
        return []
    # Filter to this exam, take top-3 mastery ≥ 0.75 with ≥2 attempts
    strong = []
    for r in rows:
        if r.attempts < 2 or r.mastery < 0.75:
            continue
        if r.topic_key.startswith(f"{exam_code}::"):
            strong.append(r.topic_key.split("::", 1)[1])
    strong.sort()
    return [s.replace("_", " ").title() for s in strong[:3]]


def _recent_fallbacks(user_id: str, *, limit: int = 5) -> list[dict]:
    try:
        from . import tutor_grounding as tg
        return tg.user_recent_fallbacks(user_id, limit=limit)
    except Exception:
        return []


def _next_mock(*, user_id: str, pack_code: str | None) -> dict | None:
    """Best next mock for the user. Reuses daily_plan's selector
    logic via mock_engine directly."""
    if not pack_code:
        return None
    try:
        from . import mock_engine as me
        papers = me.list_papers(
            pack_code=pack_code, status="published",
        )
        attempts = me.list_attempts_for_user(user_id, limit=500)
        done = {
            a.paper_id for a in attempts if a.status == "submitted"
        }
        candidates = [p for p in papers if p.id not in done]
        if not candidates:
            return None
        candidates.sort(
            key=lambda p: (p.mode != "sectional", p.total_time_min),
        )
        p = candidates[0]
        return {
            "paper_id": p.id, "title": p.title,
            "mode": p.mode, "time_min": p.total_time_min,
            "total_questions": p.total_questions,
        }
    except Exception:
        return None


def _community_hint(*, user_id: str, pack_code: str | None) -> dict:
    """Pick one community room to surface in the home hero. For
    now we just point at the pack's leaderboard — future versions
    can plug in `forums.py` rooms keyed on the exam code."""
    if not pack_code:
        return {}
    try:
        from . import readiness as rd
        lb = rd.pack_leaderboard(pack_code, limit=3)
    except Exception:
        lb = []
    return {
        "pack_code": pack_code,
        "leaderboard_top": lb,
    }


def _trust_signal(user_id: str) -> dict:
    """% of recent AI answers carrying citations. Surfaced as
    'X% answered with sources' microcopy in the hero."""
    try:
        from . import citations as cit
        answers = cit.list_user_answers(user_id=user_id, limit=30)
    except Exception:
        answers = []
    if not answers:
        return {"sample_size": 0, "grounded_rate": None}
    grounded = sum(1 for a in answers if a.grounded)
    return {
        "sample_size": len(answers),
        "grounded_rate": round(grounded / len(answers), 3),
    }


def _srs_due_count(user_id: str) -> int:
    try:
        from . import spaced_repetition as srs
        stats = srs.user_stats(user_id)
        return int(stats.get("due_now") or 0)
    except Exception:
        return 0


def _hero(
    *,
    user_id: str,
    pack: dict,
    readiness: dict,
    exam_code: str | None,
) -> dict:
    """Compose the headline + sub + suggested actions."""
    weak = readiness.get("weak_topics", [])
    strong = _strong_topics(user_id=user_id, exam_code=exam_code)
    pack_title = pack.get("title") or "your active path"
    score = readiness.get("score")
    if weak or strong:
        bits = []
        if weak:
            bits.append(
                "behind in " + ", ".join(
                    w.replace("_", " ").title() for w in weak[:2]
                ),
            )
        if strong:
            bits.append("strong in " + ", ".join(strong[:2]))
        headline = f"Your {pack_title} path is " + "; ".join(bits) + "."
    elif score is not None:
        headline = (
            f"Your {pack_title} readiness is {score:.0f}%. "
            "Keep the streak going."
        )
    else:
        headline = (
            f"Welcome — pick where to start in {pack_title}."
        )
    actions = [
        {"title": "Continue today's plan",
         "endpoint": "/api/daily-plan/me/{pack_code}",
         "kind": "primary"},
        {"title": "Open Study Studio",
         "endpoint": "/api/srs/decks/me",
         "kind": "secondary"},
        {"title": "Take 20-min mock",
         "endpoint": "/api/mock/papers",
         "kind": "secondary"},
        {"title": "Ask AI tutor",
         "endpoint": "/api/tutor/sessions",
         "kind": "secondary"},
    ]
    return {"headline": headline, "actions": actions}


def _modules_grouped() -> list[dict]:
    """Returns the chip grid from the mockup — every existing
    module the user has access to, labelled 'keep' per §26."""
    try:
        from . import navigation as nav
        manifest = nav.get_manifest()
    except Exception:
        return []
    out = []
    for s in manifest["sections"]:
        out.append({
            "title": s["title"],
            "slug": s["slug"],
            "features": [
                {"title": f["title"], "endpoint": f["endpoint"],
                 "badge": f["badge"]}
                for f in s["features"]
            ],
        })
    return out


def build_dashboard(*, user_id: str) -> dict:
    """The aggregator. Returns the full goal-led home view for
    the user."""
    pack_code = _resolve_active_pack(user_id)
    pack = _pack_meta(pack_code)
    exam_code = pack.get("exam_code")
    readiness = _readiness_summary(
        user_id=user_id, pack_code=pack_code,
    )
    daily_flow = _daily_flow(
        user_id=user_id, pack_code=pack_code,
    )
    hero = _hero(
        user_id=user_id, pack=pack,
        readiness=readiness, exam_code=exam_code,
    )
    return {
        "user_id": user_id,
        "computed_at": time.time(),
        "hero": hero,
        "exam_pack": pack,
        "readiness": readiness,
        "metrics": {
            "due_flashcards": _srs_due_count(user_id),
            "weak_topic_count": len(readiness.get("weak_topics") or []),
            "fallback_count_recent":
                len(_recent_fallbacks(user_id)),
        },
        "daily_flow": daily_flow,
        "next_mock": _next_mock(
            user_id=user_id, pack_code=pack_code,
        ),
        "community": _community_hint(
            user_id=user_id, pack_code=pack_code,
        ),
        "trust": _trust_signal(user_id),
        "recent_fallbacks": _recent_fallbacks(user_id, limit=5),
        "modules": _modules_grouped(),
    }
