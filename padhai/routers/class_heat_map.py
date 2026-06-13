"""prod-140 — Class Heat Map (CK-12 Teacher Assistant pattern).

CK-12's Teacher Assistant shows a heat map: students on rows,
concepts/topics on columns, color intensity = mastery. One glance
surfaces which students need intervention and which topics the
WHOLE CLASS is struggling with. Drives "re-teach this topic
tomorrow" decisions.

Pathshala adapts: reuse the prod-135 mastery aggregator over an
existing class roster. Returns a 2D matrix suitable for the teacher
Capacitor shell to render as a colored grid.

Endpoint:
    GET /api/orgs/{org_id}/classes/{class_id}/heat-map
        ?board=CBSE&grade=10&subject=Math&window=7d
        → 2D students × topics matrix with mastery cells

The route lives under /api/orgs/{org_id} so the existing org
role gate (teacher/admin of that org) gates it. Pure read-side
aggregation — no new tables.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from .. import mastery_aggregate, orgs
from ..api_deps import require_org_role, require_user
from ..auth import AuthUser
from ..web import current_user

router = APIRouter()


@router.get("/api/orgs/{org_id}/classes/{class_id}/heat-map")
def class_heat_map(
    org_id: str,
    class_id: str,
    board: str = Query(..., description="CBSE / ICSE / state-board key"),
    grade: int = Query(..., ge=1, le=12),
    subject: str | None = Query(None, description="Optional subject filter"),
    user: AuthUser | None = Depends(current_user),
) -> dict:
    """prod-140 — Class Heat Map. Returns students × topics mastery
    matrix for the teacher dashboard.

    Authorization: caller must be a member of `org_id` with role
    `admin` or `teacher` (students never see classmate mastery).

    Returns:
        {
          "org_id": "...",
          "class_id": "...",
          "board": "CBSE", "grade": 10, "subject": "Math",
          "students": [{"user_id", "display_name"}, ...],
          "topics":   [{"topic_key", "title", "subject"}, ...],
          "cells":    [[{"mastery": 0..1, "color_state", "decay_state"}, ...], ...],
          "class_summary": {"green": N, "yellow": N, "red": N, "untouched": N}
        }

    `cells[i][j]` is student i's mastery on topic j.
    """
    user = require_user(user)
    # Org role gate — teachers + admins only
    require_org_role(org_id, user.id, {"admin", "teacher"})

    # Verify the class belongs to this org
    classes = orgs.list_classes(org_id)
    if not any(c.id == class_id for c in classes):
        raise HTTPException(404, "class not found in this org")

    # Roster — students only (skip teachers/admins in the same org)
    members = orgs.list_members(org_id, role="student", limit=500)
    students = [
        m for m in members
        if m.class_id == class_id and m.user_id
    ]
    if not students:
        return {
            "org_id": org_id,
            "class_id": class_id,
            "board": board,
            "grade": grade,
            "subject": subject,
            "students": [],
            "topics": [],
            "cells": [],
            "class_summary": {"green": 0, "yellow": 0, "red": 0, "untouched": 0},
        }

    # Build the topic axis from the FIRST student's mastery map (every
    # student in the class shares the same curriculum scope).
    first_rows = mastery_aggregate.build_mastery_map(
        user_id=students[0].user_id, board=board, grade=grade, subject=subject,
    )
    topic_axis = [
        {"topic_key": r.topic_key, "title": r.title, "subject": r.subject}
        for r in first_rows
    ]
    topic_key_to_idx = {t["topic_key"]: i for i, t in enumerate(topic_axis)}

    students_axis: list[dict] = []
    cells: list[list[dict]] = []
    class_summary = {"green": 0, "yellow": 0, "red": 0, "untouched": 0}

    for student in students:
        if student.user_id == students[0].user_id:
            rows = first_rows  # reuse — already computed
        else:
            rows = mastery_aggregate.build_mastery_map(
                user_id=student.user_id, board=board, grade=grade, subject=subject,
            )

        # Build a sparse map keyed by topic_key for this student
        by_key = {r.topic_key: r for r in rows}

        row_cells: list[dict] = []
        for t in topic_axis:
            r = by_key.get(t["topic_key"])
            if r is None:
                cell = {
                    "mastery": 0.0,
                    "color_state": "untouched",
                    "decay_state": "untouched",
                }
            else:
                cell = {
                    "mastery": r.mastery,
                    "color_state": r.color_state,
                    "decay_state": r.decay_state,
                }
            row_cells.append(cell)
            # Roll up into the class summary
            class_summary[cell["color_state"]] = (
                class_summary.get(cell["color_state"], 0) + 1
            )

        students_axis.append({
            "user_id": student.user_id,
            "display_name": student.display_name or student.invited_email
                or f"Student {student.user_id[:6]}",
        })
        cells.append(row_cells)

    return {
        "org_id": org_id,
        "class_id": class_id,
        "board": board,
        "grade": grade,
        "subject": subject,
        "students": students_axis,
        "topics": topic_axis,
        "cells": cells,
        "class_summary": class_summary,
    }


@router.get("/api/orgs/{org_id}/classes/{class_id}/heat-map/weak-topics")
def class_weak_topics(
    org_id: str,
    class_id: str,
    board: str = Query(...),
    grade: int = Query(..., ge=1, le=12),
    subject: str | None = Query(None),
    top_n: int = Query(5, ge=1, le=20),
    user: AuthUser | None = Depends(current_user),
) -> dict:
    """prod-140 — Surface the N topics the whole class is weakest on.
    "Re-teach this tomorrow" suggestion feed for the teacher.

    Returns:
        {
          "weak_topics": [
            {"topic_key", "title", "red_count", "yellow_count",
             "green_count", "untouched_count", "students_total",
             "class_weakness_score": 0..1},
            ...
          ]
        }

    weakness_score = (red*2 + yellow*1 + untouched*0.5) / (students_total*2)
    Higher = more students struggle.
    """
    user = require_user(user)
    require_org_role(org_id, user.id, {"admin", "teacher"})

    # Pull the full heat map and aggregate vertically (per-topic across students)
    heat = class_heat_map(
        org_id, class_id, board=board, grade=grade, subject=subject, user=user,
    )
    students_total = len(heat["students"])
    if students_total == 0 or not heat["topics"]:
        return {"weak_topics": [], "students_total": 0}

    topic_stats: list[dict] = []
    for j, topic in enumerate(heat["topics"]):
        counts = {"red": 0, "yellow": 0, "green": 0, "untouched": 0}
        for i in range(students_total):
            counts[heat["cells"][i][j]["color_state"]] += 1
        weakness = (
            counts["red"] * 2 + counts["yellow"] + counts["untouched"] * 0.5
        ) / (students_total * 2)
        topic_stats.append({
            "topic_key": topic["topic_key"],
            "title": topic["title"],
            "subject": topic["subject"],
            "red_count": counts["red"],
            "yellow_count": counts["yellow"],
            "green_count": counts["green"],
            "untouched_count": counts["untouched"],
            "students_total": students_total,
            "class_weakness_score": round(weakness, 3),
        })

    topic_stats.sort(key=lambda t: -t["class_weakness_score"])
    return {
        "weak_topics": topic_stats[:top_n],
        "students_total": students_total,
    }
