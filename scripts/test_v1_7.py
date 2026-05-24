"""Smoke for v1.7 — J6 question bank, I5/I6 mobile config.
Run: PYTHONPATH=. python scripts/test_v1_7.py
"""
from __future__ import annotations
import json, os, sys, tempfile
from pathlib import Path

_DB = os.environ.setdefault(
    "PADHAI_DB_PATH",
    str(Path(tempfile.gettempdir()) / "padhai_v1_7_smoke.db"),
)
Path(_DB).unlink(missing_ok=True)


def main() -> int:
    from fastapi.testclient import TestClient
    from padhai import question_bank
    from padhai.web import app

    failed: list[str] = []

    def check(name, ok, detail=""):
        m = "ok  " if ok else "FAIL"
        print(f"  {m}  {name}{(' — ' + detail) if detail else ''}")
        if not ok: failed.append(name)

    print("v1.7 smoke test")
    print("---------------")

    # J6 question bank
    question_bank.migrate()
    q1 = question_bank.upsert(
        board="cbse", grade=10, subject="mathematics",
        question_text="Solve: x^2 + 5x + 6 = 0",
        chapter="Quadratic Equations", year=2024, paper="main",
        options=["x = -2, -3", "x = 2, 3", "x = -1, -6", "x = 1, 6"],
        correct_answer="x = -2, -3", marks=2, difficulty="medium",
        topic_tags=["algebra", "quadratic"],
        source="CBSE 2024 Math paper",
    )
    check("J6 upsert returns Question dataclass", q1.id and q1.board == "cbse")

    # Idempotent re-upsert on natural key
    q2 = question_bank.upsert(
        board="cbse", grade=10, subject="mathematics",
        question_text="Solve: x^2 + 5x + 6 = 0",
        chapter="Quadratic Equations (Updated)",
        year=2024, paper="main", marks=3,
    )
    check(
        "J6 re-upsert same natural key updates non-key fields",
        q2.marks == 3 and q2.chapter == "Quadratic Equations (Updated)",
    )

    # Insert more for search tests
    question_bank.upsert(
        board="icse", grade=9, subject="science",
        question_text="What is photosynthesis?",
        chapter="Plants", year=2023, paper="main",
        difficulty="easy", marks=3,
    )
    question_bank.upsert(
        board="cbse", grade=10, subject="science",
        question_text="State Newton's first law.",
        chapter="Laws of Motion", year=2024,
        difficulty="easy", marks=1,
    )

    rows = question_bank.search(board="cbse", grade=10)
    check(
        "J6 search filters by board+grade",
        len(rows) == 2,
        f"count={len(rows)}",
    )

    rows = question_bank.search(subject="science")
    check(
        "J6 search filters by subject",
        len(rows) == 2,
    )

    rows = question_bank.search(text_query="photosynthesis")
    check(
        "J6 search text_query works (LIKE)",
        any("photosynthesis" in r.question_text.lower() for r in rows),
    )

    rows = question_bank.search(difficulty="easy")
    check(
        "J6 search filters by difficulty",
        all(r.difficulty == "easy" for r in rows),
    )

    # Bad difficulty rejected on upsert
    try:
        question_bank.upsert(
            board="cbse", grade=10, subject="x",
            question_text="y", difficulty="impossible",
        )
        check("J6 upsert rejects unknown difficulty", False)
    except ValueError:
        check("J6 upsert rejects unknown difficulty", True)

    # Stats
    s = question_bank.stats()
    check(
        "J6 stats returns total + breakdowns",
        s["total"] >= 3 and "cbse" in s["by_board"]
        and "icse" in s["by_board"],
        str(s)[:120],
    )

    # I5 + I6 mobile configs
    parent_cfg = Path("mobile/parent/capacitor.config.json")
    teacher_cfg = Path("mobile/teacher/capacitor.config.json")
    check("I5 parent capacitor.config.json exists", parent_cfg.exists())
    check("I6 teacher capacitor.config.json exists", teacher_cfg.exists())
    if parent_cfg.exists():
        cfg = json.loads(parent_cfg.read_text())
        check(
            "I5 parent appId distinct from main",
            cfg["appId"] == "in.aipathshala.parent",
            cfg["appId"],
        )
        check(
            "I5 parent server URL routes to ?mode=parent",
            "?mode=parent" in cfg["server"]["url"],
        )
    if teacher_cfg.exists():
        cfg = json.loads(teacher_cfg.read_text())
        check(
            "I6 teacher appId distinct from main",
            cfg["appId"] == "in.aipathshala.teacher",
            cfg["appId"],
        )
        check(
            "I6 teacher server URL routes to ?mode=teacher",
            "?mode=teacher" in cfg["server"]["url"],
        )

    # HTTP layer
    with TestClient(app) as c:
        r = c.get("/api/question-bank/search?board=cbse&grade=10")
        check(
            "GET /api/question-bank/search returns 200 with rows",
            r.status_code == 200 and len(r.json()["rows"]) >= 1,
        )
        r = c.get("/api/question-bank/stats")
        check(
            "GET /api/question-bank/stats returns total",
            r.status_code == 200 and r.json()["total"] >= 3,
        )
        r = c.get("/api/question-bank/non-existent-id")
        check(
            "GET /api/question-bank/{bad} returns 404",
            r.status_code == 404,
        )
        r = c.get("/")
        check(
            "Root reports a semver-shaped version",
            str(r.json().get("version", "")).split(".")[0].isdigit(),)

    print("---------------")
    if failed:
        print(f"FAILED: {len(failed)} checks")
        for n in failed: print(f"  - {n}")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
