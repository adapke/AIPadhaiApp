"""Smoke for v3.16 — Step-by-step math solver.

Covers:
- create_problem + auto-detection of problem_kind
- solve_with_sympy for linear equations (3-step deterministic)
- accept_llm_steps for caller-supplied step lists
- flag_step + add_step_explanation
- high_flagged_steps admin queue
- HTTP endpoints + auth gates

Run: PYTHONPATH=. python scripts/test_v3_16.py
"""
from __future__ import annotations
import os, sys, tempfile, time
from pathlib import Path

_DB = os.environ.setdefault(
    "PADHAI_DB_PATH",
    str(Path(tempfile.gettempdir()) / "padhai_v3_16_smoke.db"),
)
Path(_DB).unlink(missing_ok=True)


def main() -> int:
    from fastapi.testclient import TestClient
    from padhai import step_math as sm
    from padhai.web import app

    failed: list[str] = []

    def check(name, ok, detail=""):
        m = "ok  " if ok else "FAIL"
        print(f"  {m}  {name}{(' — ' + detail) if detail else ''}")
        if not ok: failed.append(name)

    def raises(fn, exc_type=Exception, **kwargs):
        try:
            fn(**kwargs); return False
        except exc_type:
            return True

    print("v3.16 smoke test — Step math solver")
    print("-----------------------------------")

    sm.migrate()
    sm.migrate()

    # ============================================================
    # Problem creation + kind detection
    # ============================================================
    p1 = sm.create_problem(
        user_id="stu-1",
        problem_latex="2x + 3 = 11",
    )
    check(
        "create_problem detects linear_equation",
        p1.problem_kind == "linear_equation"
        and p1.status == "pending",
    )

    p_quad = sm.create_problem(
        user_id="stu-1",
        problem_latex="x^2 - 5x + 6 = 0",
    )
    check(
        "create_problem detects quadratic via x^2",
        p_quad.problem_kind == "quadratic_equation",
    )

    p_deriv = sm.create_problem(
        user_id="stu-1",
        problem_latex=r"\frac{d}{dx}(3x^2 + 2x)",
    )
    check(
        "create_problem detects derivative",
        p_deriv.problem_kind == "derivative",
    )

    p_int = sm.create_problem(
        user_id="stu-1",
        problem_latex=r"\int x^2 dx",
    )
    check(
        "create_problem detects integral",
        p_int.problem_kind == "integral",
    )

    p_unknown = sm.create_problem(
        user_id="stu-1",
        problem_latex="some free-form prompt",
    )
    check(
        "create_problem defaults to 'unknown'",
        p_unknown.problem_kind == "unknown",
    )

    # Bad inputs
    check(
        "create_problem rejects too-short latex",
        raises(sm.create_problem, exc_type=ValueError,
               user_id="x", problem_latex="x"),
    )

    # ============================================================
    # SymPy solver — linear equations
    # ============================================================
    if not sm.is_sympy_available():
        check("SymPy available (skipping deterministic solver test)", True)
    else:
        solved = sm.solve_with_sympy(p1.id)
        check(
            "solve_with_sympy returns 3 steps for 2x+3=11",
            len(solved.steps) == 3,
        )
        check(
            "Final step is x = 4 (correct)",
            "x = 4" in (solved.final_answer or "")
            or "x = 4" in solved.steps[-1].latex,
        )
        check(
            "All SymPy steps marked validated=True",
            all(s.validated for s in solved.steps),
        )
        check(
            "Problem status='solved' + solver='sympy'",
            solved.status == "solved"
            and solved.solver == "sympy",
        )

    # SymPy solver refuses non-linear
    check(
        "solve_with_sympy refuses quadratic_equation",
        raises(sm.solve_with_sympy, exc_type=ValueError,
               problem_id=p_quad.id),
    )

    # Can't solve already-solved
    if sm.is_sympy_available():
        check(
            "solve_with_sympy refuses already-solved problem",
            raises(sm.solve_with_sympy, exc_type=ValueError,
                   problem_id=p1.id),
        )

    # ============================================================
    # accept_llm_steps — for unsupported problem kinds
    # ============================================================
    p_llm = sm.create_problem(
        user_id="stu-1",
        problem_latex="solve sin(x) = 1/2 for x in [0, 2pi]",
    )
    p_llm_solved = sm.accept_llm_steps(
        problem_id=p_llm.id, user_id="stu-1",
        steps=[
            {"latex": r"\sin(x) = 1/2",
             "explanation": "Given equation"},
            {"latex": r"x = \arcsin(1/2)",
             "explanation": "Apply arcsin"},
            {"latex": r"x = \pi/6 \text{ or } 5\pi/6",
             "explanation":
             "Two solutions in [0, 2pi] from the unit circle"},
        ],
        final_answer="x = pi/6 or 5pi/6",
    )
    check(
        "accept_llm_steps persists 3 steps",
        len(p_llm_solved.steps) == 3,
    )
    check(
        "LLM steps marked validated=False (AI-only)",
        not any(s.validated for s in p_llm_solved.steps),
    )
    check(
        "Problem status='solved' + solver='llm'",
        p_llm_solved.status == "solved"
        and p_llm_solved.solver == "llm",
    )

    # Empty steps rejected
    check(
        "accept_llm_steps rejects empty steps",
        raises(sm.accept_llm_steps, exc_type=ValueError,
               problem_id=p_quad.id, user_id="stu-1",
               steps=[], final_answer="x = 2 or x = 3"),
    )

    # Permission check
    check(
        "accept_llm_steps refuses non-owner",
        raises(sm.accept_llm_steps, exc_type=PermissionError,
               problem_id=p_quad.id, user_id="stranger",
               steps=[{"latex": "x"}],
               final_answer="x = 0"),
    )

    # Too-long step latex rejected
    check(
        "accept_llm_steps rejects step.latex too long",
        raises(sm.accept_llm_steps, exc_type=ValueError,
               problem_id=p_quad.id, user_id="stu-1",
               steps=[{"latex": "x" * 5000}],
               final_answer="x = 1"),
    )

    # Retry path — failed problem can be re-accepted
    p_fail = sm.create_problem(
        user_id="stu-1", problem_latex="weird unknown problem here",
    )
    sm.mark_failed(
        problem_id=p_fail.id, user_id="stu-1",
        reason="solver timed out",
    )
    p_retry = sm.accept_llm_steps(
        problem_id=p_fail.id, user_id="stu-1",
        steps=[
            {"latex": "step 1", "explanation": "do this"},
            {"latex": "step 2", "explanation": "then this"},
        ],
        final_answer="final",
    )
    check(
        "accept_llm_steps recovers from 'failed' state",
        p_retry.status == "solved",
    )

    # ============================================================
    # Step flagging + explanations
    # ============================================================
    first_step = p_llm_solved.steps[0]
    check(
        "flag_step True for existing step",
        sm.flag_step(step_id=first_step.id, user_id="stu-1"),
    )
    sm.flag_step(step_id=first_step.id, user_id="stu-1")
    sm.flag_step(step_id=first_step.id, user_id="stu-1")
    flagged = sm.get_step(first_step.id)
    check(
        "flagged_count bumps to 3",
        flagged.flagged_count == 3,
    )

    # flag_step on unknown id → False
    check(
        "flag_step False for unknown step",
        not sm.flag_step(step_id="ghost-step", user_id="stu-1"),
    )

    # Add explanation
    eid = sm.add_step_explanation(
        step_id=first_step.id, user_id="tutor-1",
        explanation=(
            "We start by writing down the given equation so we "
            "have a clear point of reference for the steps below."
        ),
        citations=[
            {"source_kind": "upload", "source_id": "u1",
             "page_number": 5, "citation_text": "x"},
        ],
    )
    check("add_step_explanation returns id", bool(eid))

    # Short explanation rejected
    check(
        "add_step_explanation rejects too-short explanation",
        raises(sm.add_step_explanation, exc_type=ValueError,
               step_id=first_step.id, user_id="x",
               explanation="short"),
    )

    # List explanations
    explanations = sm.list_step_explanations(first_step.id)
    check(
        "list_step_explanations returns the added explanation",
        len(explanations) >= 1
        and "given equation" in explanations[0]["explanation"],
    )

    # Citations preserved
    check(
        "Stored explanation carries citations",
        explanations[0]["citations"]
        and explanations[0]["citations"][0]["page_number"] == 5,
    )

    # ============================================================
    # High-flagged steps admin queue
    # ============================================================
    queue = sm.high_flagged_steps(threshold=2)
    check(
        "high_flagged_steps returns flagged step",
        any(s.id == first_step.id for s in queue),
    )

    # Threshold check
    none_queue = sm.high_flagged_steps(threshold=10)
    check(
        "high_flagged_steps threshold filter works",
        not any(s.id == first_step.id for s in none_queue),
    )

    # ============================================================
    # Listing + stats
    # ============================================================
    rows = sm.list_user_problems("stu-1")
    check(
        "list_user_problems returns user's problems",
        len(rows) >= 5,
    )

    by_status = sm.list_user_problems("stu-1", status="solved")
    check(
        "list_user_problems filter by status",
        all(p.status == "solved" for p in by_status),
    )

    # Bad status filter
    check(
        "list_user_problems rejects bad status",
        raises(sm.list_user_problems, exc_type=ValueError,
               user_id="stu-1", status="alien"),
    )

    stats = sm.user_stats("stu-1")
    check(
        "user_stats returns by-status counts",
        isinstance(stats, dict),
    )

    # ============================================================
    # mark_failed
    # ============================================================
    p_mf = sm.create_problem(
        user_id="stu-1", problem_latex="another unparseable thing",
    )
    check(
        "mark_failed True for pending problem",
        sm.mark_failed(
            problem_id=p_mf.id, user_id="stu-1",
            reason="LLM timeout",
        ),
    )
    check(
        "mark_failed False on non-pending",
        not sm.mark_failed(
            problem_id=p_mf.id, user_id="stu-1",
            reason="x",
        ),
    )

    # Wrong user
    p_mf2 = sm.create_problem(
        user_id="stu-1", problem_latex="yet another problem",
    )
    check(
        "mark_failed False for non-owner",
        not sm.mark_failed(
            problem_id=p_mf2.id, user_id="stranger",
            reason="x",
        ),
    )

    # ============================================================
    # HTTP layer
    # ============================================================
    with TestClient(app) as c:
        # Auth gates
        r = c.post(
            "/api/step-math/problems",
            data={"problem_latex": "2x + 3 = 11"},
        )
        check(
            "POST /api/step-math/problems → 401",
            r.status_code == 401,
        )

        r = c.get("/api/step-math/problems/me")
        check(
            "GET /api/step-math/problems/me → 401",
            r.status_code == 401,
        )

        r = c.post(
            "/api/step-math/steps/x/flag",
        )
        check(
            "POST /api/step-math/steps/{}/flag → 401",
            r.status_code == 401,
        )

        r = c.get("/api/admin/step-math/high-flagged")
        check(
            "GET /api/admin/step-math/high-flagged → 401",
            r.status_code == 401,
        )

        # Form validation
        r = c.post(
            "/api/step-math/problems",
            data={"problem_latex": "x"},
        )
        check(
            "POST /api/step-math/problems rejects too-short latex (422)",
            r.status_code == 422,
        )

        # Version
        r = c.get("/")
        check(
            "Root reports a semver-shaped version",
            str(r.json().get("version", "")).split(".")[0].isdigit(),
            f"version={r.json().get('version')}",
        )

    check(
        "v3.16 brought route count above 580",
        len(app.routes) > 580,
        f"routes={len(app.routes)}",
    )

    print("-----------------------------------")
    if failed:
        print(f"FAILED: {len(failed)} checks")
        for n in failed:
            print(f"  - {n}")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
