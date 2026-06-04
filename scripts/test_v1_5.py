"""Smoke test for v1.5 — I4 streaks, J1 math, J2 diagrams.

Run: PYTHONPATH=. python scripts/test_v1_5.py
"""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from pathlib import Path

_DB = os.environ.setdefault(
    "PADHAI_DB_PATH",
    str(Path(tempfile.gettempdir()) / "padhai_v1_5_smoke.db"),
)
Path(_DB).unlink(missing_ok=True)


def main() -> int:
    from fastapi.testclient import TestClient

    from padhai import diagram_generator, math_render, streaks
    from padhai.web import app

    failed: list[str] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        marker = "ok  " if ok else "FAIL"
        print(f"  {marker}  {name}{(' — ' + detail) if detail else ''}")
        if not ok:
            failed.append(name)

    print("v1.5 smoke test")
    print("---------------")

    # --- I4 streaks ---
    streaks.migrate()
    s = streaks.get_streak("user-x")
    check("I4 unknown user → zero streak", s.current_streak == 0 and s.level == 1)
    s = streaks.award(user_id="user-x", kind="lesson_done")
    check(
        "I4 award lesson_done → current_streak=1 + xp=10",
        s.current_streak == 1 and s.xp_total == 10,
        f"streak={s.current_streak} xp={s.xp_total}",
    )
    s = streaks.award(user_id="user-x", kind="quiz_perfect")
    check(
        "I4 second award same day → streak unchanged + xp accumulates",
        s.current_streak == 1 and s.xp_total == 35,
        f"streak={s.current_streak} xp={s.xp_total}",
    )
    check("I4 level still 1 at 35 xp", s.level == 1)
    s = streaks.award(user_id="user-x", kind="streak_30", xp_amount=200)
    check(
        "I4 high xp pushes level up",
        s.level >= 2,
        f"level={s.level} xp={s.xp_total}",
    )

    # Leaderboard
    streaks.award(user_id="user-a", kind="lesson_done")
    streaks.award(user_id="user-b", kind="quiz_perfect")
    streaks.award(user_id="user-b", kind="lesson_done")
    rows = streaks.leaderboard(
        scope_user_ids=["user-a", "user-b"], period="alltime",
    )
    check(
        "I4 leaderboard sorted by xp desc",
        len(rows) == 2 and rows[0]["user_id"] == "user-b",
        str(rows),
    )

    # Calendar heatmap
    heat = streaks.calendar_heatmap(user_id="user-x", days=30)
    check(
        "I4 calendar_heatmap returns at least one day with XP",
        sum(heat.values()) > 0,
        str(heat),
    )

    # --- J1 math rendering ---
    spoken = math_render.latex_to_spoken(
        r"\frac{-b \pm \sqrt{b^2 - 4ac}}{2a}",
    )
    check(
        "J1 latex_to_spoken handles quadratic formula",
        "over" in spoken and "square root" in spoken
        and "plus or minus" in spoken,
        spoken,
    )
    html = math_render.render_to_html(r"x^2 + y^2 = r^2")
    check(
        "J1 render_to_html (no katex env) falls back to text",
        'class="math-fallback"' in html and "squared" in html.lower(),
        html[:80],
    )
    check(
        "J1 is_katex_available reflects env (false in this sandbox)",
        not math_render.is_katex_available(),
    )

    # --- J2 diagrams ---
    cycle_svg = diagram_generator.render_cycle(
        ["Evaporation", "Condensation", "Precipitation", "Collection"],
    )
    check(
        "J2 render_cycle produces SVG",
        cycle_svg.startswith("<svg") and "Evaporation" in cycle_svg,
    )

    bar_svg = diagram_generator.render_bar_chart(
        [("Mon", 4), ("Tue", 7), ("Wed", 3), ("Thu", 9)],
    )
    check(
        "J2 render_bar_chart produces SVG with bars",
        bar_svg.startswith("<svg") and "<rect" in bar_svg,
    )

    fc_svg = diagram_generator.render_food_chain(["Sun", "Grass", "Cow", "Human"])
    check(
        "J2 render_food_chain produces SVG with boxes",
        fc_svg.startswith("<svg") and "Grass" in fc_svg,
    )

    # Master entry — falls back when MERMAID_BIN unset
    d = diagram_generator.GeneratedDiagram(
        spec_lang="mermaid",
        spec="graph TD\nA --> B",
        alt_text="placeholder diagram",
    )
    out = diagram_generator.render(d)
    check(
        "J2 render() falls back gracefully when mermaid missing",
        out.startswith("<svg") and "placeholder" in out,
    )

    # --- HTTP-level ---
    with TestClient(app) as c:
        r = c.get("/api/me/streak")
        check(
            "GET /api/me/streak requires auth (401)",
            r.status_code == 401,
        )

        r = c.post("/api/render/math",
                   data={"latex": "x^2", "inline": "false"})
        check(
            "POST /api/render/math returns html + alt_text",
            r.status_code == 200
            and "html" in r.json() and "alt_text" in r.json(),
            f"status={r.status_code}",
        )

        r = c.post(
            "/api/render/diagram",
            data={
                "spec_lang": "svg_spec",
                "spec": '{"type": "cycle", "items": ["A", "B", "C"]}',
                "alt_text": "test cycle",
                "diagram_type": "cycle",
                "width": "400", "height": "300",
            },
        )
        check(
            "POST /api/render/diagram returns SVG",
            r.status_code == 200 and r.text.startswith("<svg"),
            f"status={r.status_code}",
        )

        r = c.post(
            "/api/render/diagram",
            data={"spec_lang": "svg_spec", "spec": "{}",
                  "alt_text": "x", "diagram_type": "bogus_type",
                  "width": "300", "height": "200"},
        )
        check(
            "POST /api/render/diagram rejects unknown type",
            r.status_code == 400,
        )

        # Version
        r = c.get("/")
        check(
            "Root endpoint reports a semver-shaped version",
            str(r.json().get("version", "")).split(".")[0].isdigit(),
            f"version={r.json().get('version')}",
        )

    # Tables present
    with sqlite3.connect(_DB) as conn:
        tables = {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    for t in ("user_streaks", "xp_events"):
        check(f"v1.5 table '{t}' created at startup", t in tables)

    print("---------------")
    if failed:
        print(f"FAILED: {len(failed)} checks")
        for n in failed:
            print(f"  - {n}")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
