"""Class leaderboard router — seventh web.py slice.

One endpoint:
  GET /api/orgs/{org_id}/classes/{class_id}/leaderboard

Reads `period` (week / month / alltime) and `limit`, then resolves the
class roster via `_orgs.list_members` and hands it to
`_streaks.leaderboard()` which aggregates XP across the scoped users.

Lives in its own slice (not `orgs_classes.py`) because the streaks /
XP / leaderboard subsystem is independent of the class-CRUD surface
— different module owner (`_streaks` vs `_orgs`), different role gate
(students can read leaderboards but not the class roster directly),
and different cache pattern (the rollup is reused on the home screen).
Keeping it separate makes the I4 streaks slab easier to lift wholesale
when the rest of streaks ever extracts.

Late-imports `web` for the shared globals — same pattern as
orgs_classes.py, orgs_api.py, parents.py, multipage.py.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..auth import AuthUser
from ..web import current_user

router = APIRouter()


@router.get("/api/orgs/{org_id}/classes/{class_id}/leaderboard")
def class_leaderboard_route(
    org_id: str, class_id: str,
    period: str = "alltime",
    limit: int = 50,
    user: AuthUser | None = Depends(current_user),
):
    """Per-class XP / streak leaderboard. Admin / teacher / student can
    all read; non-members get 403. `period` is one of week / month /
    alltime (validated downstream by `_streaks.leaderboard`)."""
    from .. import web as _web
    user = _web._require_user(user)
    _web._org_or_404(org_id)
    _web._require_org_role(org_id, user.id, {"admin", "teacher", "student"})
    members = _web._orgs.list_members(org_id)
    scope = [
        m.user_id for m in members
        if m.user_id and m.class_id == class_id
    ]
    if not scope:
        return {"rows": []}
    try:
        rows = _web._streaks.leaderboard(
            scope_user_ids=scope, period=period, limit=limit,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"rows": rows, "period": period}
