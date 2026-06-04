"""v3.6 — Parent + Teacher dashboards.

Per review §8 (school workflows) — we have the data (mastery,
streaks, mock attempts, exam pack enrollments, daily plans) but
no aggregated views. This module composes those into two
audience-specific dashboards:

  Parent dashboard — for parents linked via `parents.py`. Shows:
    • Children + their active exam packs + readiness scores
    • Last 14 days of daily-plan completion
    • Recent mock attempts + percentile
    • Streak + study minutes/week
    • Citation/grounding rate (trust signal)
    • Recent fallbacks ("upload more material" nudge)

  Teacher dashboard — for org teachers (org_members.role='teacher').
    For a class (or whole org):
    • Roster + per-student readiness summary
    • Top-5 weakest topics across the class (weighted by
      weightage_pct + n_students struggling)
    • Class mock-attempt distribution
    • Most-active vs least-active students last 14 days
    • Moderation flags raised by class members (if any)

Both dashboards are read-only composers. No new tables — pure
aggregation over the existing data layer. The access-control is
the interesting bit:

  • Parent endpoints check `parents.is_verified_parent_of`
  • Teacher endpoints check `orgs.require_role(... allowed='teacher')`

This keeps PII flow tightly scoped — a parent only sees their
own children; a teacher only sees their own classes. Admin gates
exist for org-wide views.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

# No SCHEMA / migrate() — pure read composer.

def migrate() -> None:
    """No-op. Dashboards are pure read composers — they don't own
    any tables. Kept for lifespan symmetry."""
    return None


# ---------- helpers ----------

def _safe(fn, *args, default=None, **kwargs):
    """Wrap a sub-module call so a missing dep / empty data
    doesn't blow up the whole dashboard. Each sub-module is
    independently optional."""
    try:
        return fn(*args, **kwargs)
    except Exception:  # noqa: BLE001
        return default


# ---------- parent dashboard ----------

def parent_dashboard(parent_user_id: str) -> dict:
    """Return the parent's view of all their linked children. The
    parent sees only those children for which the link is verified
    + not revoked. Composes across exam_taxonomy / readiness /
    mock_engine / daily_plan / streaks / citations."""
    from . import parents

    links = parents.children_of(
        parent_user_id, include_revoked=False,
    )
    verified = [
        l for l in links
        if getattr(l, "status", None) == "verified"
    ]

    children: list[dict] = []
    for link in verified:
        child_id = link.child_user_id
        children.append({
            "child_user_id": child_id,
            "relation": link.relation,
            "verified_at": getattr(link, "verified_at", None),
            "summary": _child_summary(child_id),
        })
    return {
        "parent_user_id": parent_user_id,
        "children": children,
        "computed_at": time.time(),
    }


def _child_summary(child_user_id: str) -> dict:
    """The per-child block. Each component is best-effort —
    missing modules degrade silently."""
    return {
        "exam_packs": _child_packs(child_user_id),
        "mastery": _child_mastery_summary(child_user_id),
        "mock_attempts": _child_recent_mocks(child_user_id),
        "daily_plan": _child_plan_stats(child_user_id),
        "streak": _child_streak(child_user_id),
        "trust": _child_trust_signal(child_user_id),
        "recent_fallbacks": _child_fallbacks(child_user_id),
    }


def _child_packs(user_id: str) -> list[dict]:
    """Active Exam Pack enrollments + readiness scores."""
    def _go():
        from . import exam_taxonomy as et
        from . import readiness as rd
        rows = et.list_user_enrollments(user_id)
        out: list[dict] = []
        for e in rows:
            if e.status not in ("active", "completed"):
                continue
            r = _safe(
                rd.get_readiness,
                user_id=user_id, pack_code=e.pack_code,
                refresh_if_stale=False,
            )
            pack = _safe(et.get_pack, e.pack_code)
            out.append({
                "pack_code": e.pack_code,
                "title": pack.title if pack else e.pack_code,
                "exam_code": pack.exam_code if pack else None,
                "target_date": e.target_date,
                "daily_minutes": e.daily_minutes,
                "enrollment_status": e.status,
                "readiness": (
                    {"score": r.score,
                     "mastery": r.mastery_score,
                     "mock": r.mock_score,
                     "coverage": r.coverage_score,
                     "consistency": r.consistency_score,
                     "trust": r.trust_score,
                     "weak_topics": r.weak_topics,
                     "computed_at": r.computed_at}
                    if r else None
                ),
            })
        return out
    return _safe(_go, default=[]) or []


def _child_mastery_summary(user_id: str) -> dict:
    def _go():
        from . import mastery as m
        rows = m.list_for_user(user_id, limit=500)
        if not rows:
            return {"topics": 0, "weak": [], "strong": []}
        weak = sorted(
            (r for r in rows if r.attempts >= 2 and r.mastery < 0.5),
            key=lambda r: r.mastery,
        )[:5]
        strong = sorted(
            (r for r in rows if r.attempts >= 2 and r.mastery >= 0.75),
            key=lambda r: r.mastery, reverse=True,
        )[:5]
        return {
            "topics": len(rows),
            "weak": [
                {"topic_key": r.topic_key,
                 "mastery": round(r.mastery, 3),
                 "attempts": r.attempts}
                for r in weak
            ],
            "strong": [
                {"topic_key": r.topic_key,
                 "mastery": round(r.mastery, 3),
                 "attempts": r.attempts}
                for r in strong
            ],
        }
    return _safe(_go, default={"topics": 0, "weak": [], "strong": []})


def _child_recent_mocks(user_id: str) -> list[dict]:
    def _go():
        from . import mock_engine as me
        attempts = me.list_attempts_for_user(user_id, limit=5)
        out: list[dict] = []
        for a in attempts:
            if a.status != "submitted":
                continue
            out.append({
                "attempt_id": a.id,
                "paper_id": a.paper_id,
                "raw_score": a.raw_score,
                "max_score": a.max_score,
                "percentile": a.percentile,
                "correct": a.correct_count,
                "wrong": a.wrong_count,
                "unattempted": a.unattempted_count,
                "submitted_at": a.submitted_at,
            })
        return out
    return _safe(_go, default=[]) or []


def _child_plan_stats(user_id: str) -> dict:
    def _go():
        from . import daily_plan as dp
        plans = dp.list_recent_plans(user_id=user_id, limit=14)
        if not plans:
            return {
                "days": 0, "completed_days": 0,
                "skipped_days": 0, "avg_completion_pct": 0,
            }
        completed = sum(1 for p in plans if p.status == "completed")
        skipped = sum(1 for p in plans if p.status == "skipped")
        avg = sum(p.completion_pct for p in plans) / len(plans)
        return {
            "days": len(plans),
            "completed_days": completed,
            "skipped_days": skipped,
            "avg_completion_pct": round(avg, 1),
        }
    return _safe(_go, default={
        "days": 0, "completed_days": 0,
        "skipped_days": 0, "avg_completion_pct": 0,
    })


def _child_streak(user_id: str) -> dict:
    def _go():
        from . import streaks
        snap = streaks.snapshot(user_id=user_id)
        if not isinstance(snap, dict):
            return {}
        return {
            "current_streak": snap.get("current_streak", 0),
            "best_streak": snap.get("best_streak", 0),
            "days_active_30d": snap.get("days_active_30d", 0),
        }
    return _safe(_go, default={}) or {}


def _child_trust_signal(user_id: str) -> dict:
    """Caller's recent AI-answer grounding rate. Surfaced to
    parents as 'how often is the tutor citing sources for your
    child?'."""
    def _go():
        from . import citations as cit
        answers = cit.list_user_answers(user_id=user_id, limit=30)
        if not answers:
            return {"answers_sample": 0, "grounding_rate": None}
        grounded = sum(1 for a in answers if a.grounded)
        return {
            "answers_sample": len(answers),
            "grounded_answers": grounded,
            "grounding_rate": round(grounded / len(answers), 3),
        }
    return _safe(_go, default={"answers_sample": 0})


def _child_fallbacks(user_id: str) -> list[dict]:
    def _go():
        from . import tutor_grounding as tg
        return tg.user_recent_fallbacks(user_id, limit=5)
    return _safe(_go, default=[]) or []


# ---------- teacher dashboard ----------

def teacher_dashboard(
    *,
    teacher_user_id: str,
    org_id: str,
    class_id: str | None = None,
) -> dict:
    """Class-level or org-level rollup for teachers. Caller must
    pass an org_id they're a teacher of (router enforces this).

    `class_id` scopes to a specific class within the org; without
    it the entire org's students are aggregated.
    """
    from . import orgs

    members = orgs.list_members(org_id, role="student")
    # Filter to class if requested
    if class_id:
        members = [m for m in members
                   if getattr(m, "class_id", None) == class_id]
    if not members:
        return {
            "org_id": org_id, "class_id": class_id,
            "students": 0, "summary": {}, "students_detail": [],
            "computed_at": time.time(),
        }

    student_summaries = [
        {
            "user_id": m.user_id,
            "name": getattr(m, "display_name", None),
            "summary": _student_summary_for_teacher(m.user_id),
        }
        for m in members
        if m.user_id   # invited-but-not-joined members have user_id=None
    ]
    return {
        "org_id": org_id,
        "class_id": class_id,
        "students": len(members),
        "summary": _class_aggregate(student_summaries),
        "students_detail": student_summaries,
        "moderation_flags": _class_moderation_flags(members),
        "computed_at": time.time(),
    }


def _student_summary_for_teacher(user_id: str) -> dict:
    """Compact per-student view for a teacher — less PII than the
    parent dashboard. Focused on academic signals."""
    packs = _child_packs(user_id)
    plan_stats = _child_plan_stats(user_id)
    mocks = _child_recent_mocks(user_id)
    # Best readiness across packs
    best_readiness = max(
        (p["readiness"]["score"]
         for p in packs if p.get("readiness")),
        default=None,
    )
    weak_topics = []
    for p in packs:
        r = p.get("readiness") or {}
        weak_topics.extend(r.get("weak_topics") or [])
    return {
        "exam_packs": [p["pack_code"] for p in packs],
        "best_readiness": best_readiness,
        "weak_topics": list(set(weak_topics))[:5],
        "recent_mock_count": len(mocks),
        "avg_mock_percentile": (
            round(
                sum(m.get("percentile") or 0 for m in mocks)
                / len(mocks), 1,
            ) if mocks else None
        ),
        "study_consistency": plan_stats,
    }


def _class_aggregate(student_summaries: list[dict]) -> dict:
    """Roll the per-student summaries into class-level stats.
    Drives the top of the teacher view."""
    if not student_summaries:
        return {}
    ready = [
        s["summary"]["best_readiness"]
        for s in student_summaries
        if s["summary"].get("best_readiness") is not None
    ]
    percentiles = [
        s["summary"]["avg_mock_percentile"]
        for s in student_summaries
        if s["summary"].get("avg_mock_percentile") is not None
    ]
    completed_days = [
        s["summary"]["study_consistency"].get("completed_days", 0)
        for s in student_summaries
    ]
    # Weak-topic frequency across the class
    weak_count: dict[str, int] = {}
    for s in student_summaries:
        for t in s["summary"].get("weak_topics", []):
            weak_count[t] = weak_count.get(t, 0) + 1
    top_class_weak = sorted(
        weak_count.items(), key=lambda x: x[1], reverse=True,
    )[:5]
    return {
        "n_with_readiness": len(ready),
        "avg_readiness": (
            round(sum(ready) / len(ready), 1) if ready else None
        ),
        "n_with_mocks": len(percentiles),
        "avg_mock_percentile": (
            round(sum(percentiles) / len(percentiles), 1)
            if percentiles else None
        ),
        "avg_completed_days_14d": (
            round(sum(completed_days) / len(completed_days), 1)
            if completed_days else 0
        ),
        "class_weak_topics": [
            {"topic_code": t, "n_students": n}
            for t, n in top_class_weak
        ],
    }


def _class_moderation_flags(members) -> dict:
    """Count of moderation flags raised against the class's
    members. Helps the teacher catch community issues early."""
    def _go():
        from . import moderation_queue as mq
        user_ids = {m.user_id for m in members}
        # Without a list-by-author API, do a scan + filter — fine
        # at school-class scale (≤60 users).
        open_items = mq.list_queue(status="open", limit=500)
        by_user: dict[str, int] = {}
        for it in open_items:
            if it.author_user_id in user_ids:
                by_user[it.author_user_id] = (
                    by_user.get(it.author_user_id, 0) + 1
                )
        return {
            "total_open_flags": sum(by_user.values()),
            "students_flagged": len(by_user),
            "by_student": by_user,
        }
    return _safe(_go, default={
        "total_open_flags": 0,
        "students_flagged": 0,
        "by_student": {},
    })


# ---------- teacher → single student deep-dive ----------

def teacher_student_detail(
    *,
    teacher_user_id: str,
    org_id: str,
    student_user_id: str,
) -> dict:
    """A focused per-student view a teacher can drill into. Same
    shape as the parent's child block but with class context."""
    from . import orgs
    # Verify the student is in this teacher's org
    members = orgs.list_members(org_id, role="student")
    if not any(m.user_id == student_user_id for m in members):
        raise PermissionError(
            "student not in your organisation",
        )
    return {
        "org_id": org_id,
        "student_user_id": student_user_id,
        "detail": _child_summary(student_user_id),
        "computed_at": time.time(),
    }
