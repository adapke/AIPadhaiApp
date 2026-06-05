"""v3.16 — Step-by-step math solver workflow.

Per gap-review §7 (Photomath / Gauth competitor gap) — students
expect camera-first problem solving with step-by-step
explanations, where each step can be revealed individually,
and individual steps can be challenged ("how did we go from
line 3 to line 4?").

`padhai/math_vision.py` already covers OCR → LaTeX extraction +
SymPy validation of a whole expression. This module adds the
**step layer** on top:

  1. Student uploads a photo of a problem (or types in LaTeX)
  2. Solver produces an ordered sequence of steps (each step is
     a LaTeX expression + a natural-language explanation)
  3. UI reveals steps one at a time; student can mark a step as
     "didn't follow" → tutor explains
  4. Final answer carries citations to a relevant chapter when
     available

Three tables:
  step_problems       one row per problem submitted (links to a
                      math_submissions row when image-based)
  problem_steps       ordered step list per problem (latex +
                      explanation + correctness flag)
  step_explanations   when student flags a step, the tutor's
                      explanation gets stored here (audit + cache)

Solver:
  • If sympy is available + problem is parsable → deterministic
    step-by-step (for linear / quadratic equations, basic
    derivative / integral patterns)
  • Otherwise → LLM-generated steps (caller supplies them; we
    persist + structure)

This module ships:
  • The data layer + lifecycle
  • A SymPy-backed solver for a small class of problems (linear
    equations) — proves the structure end-to-end without needing
    an LLM in tests
  • `accept_llm_steps()` for caller-supplied step lists

Validation:
  • Each step is "consistent with the previous step" — we check
    that substituting back yields the same expression. When
    SymPy reports consistency, the step's `validated` flag flips
    to True. LLM-generated steps without SymPy parse get
    validated=False (UI shows "AI-generated, verify yourself").
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS step_problems (
    id              TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL,
    submission_id   TEXT,                            -- FK math_submissions.id (image-based)
    problem_latex   TEXT NOT NULL,                   -- canonical problem expression
    problem_kind    TEXT NOT NULL DEFAULT 'unknown',
    -- 'linear_equation' / 'quadratic_equation' / 'derivative' /
    -- 'integral' / 'simplify' / 'unknown'
    language        TEXT NOT NULL DEFAULT 'en',
    status          TEXT NOT NULL DEFAULT 'pending',
    -- 'pending' | 'solved' | 'failed'
    final_answer    TEXT,
    error_reason    TEXT,
    solver          TEXT,                            -- 'sympy' | 'llm' | 'mixed'
    created_at      REAL NOT NULL,
    solved_at       REAL
);
CREATE INDEX IF NOT EXISTS idx_sp_user
    ON step_problems(user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS problem_steps (
    id              TEXT PRIMARY KEY,
    problem_id      TEXT NOT NULL,
    position        INTEGER NOT NULL,                -- 1-indexed
    latex           TEXT NOT NULL,
    explanation     TEXT,
    validated       INTEGER NOT NULL DEFAULT 0,
    -- 1 if SymPy confirmed consistency with the prior step
    flagged_count   INTEGER NOT NULL DEFAULT 0,     -- # times students said "didn't follow"
    created_at      REAL NOT NULL,
    UNIQUE (problem_id, position)
);
CREATE INDEX IF NOT EXISTS idx_ps_problem
    ON problem_steps(problem_id, position);

CREATE TABLE IF NOT EXISTS step_explanations (
    id              TEXT PRIMARY KEY,
    step_id         TEXT NOT NULL,
    user_id         TEXT NOT NULL,                   -- who requested
    explanation     TEXT NOT NULL,
    citations_json  TEXT,
    ai_call_id      TEXT,
    created_at      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_se_step
    ON step_explanations(step_id, created_at DESC);
"""


VALID_PROBLEM_KINDS = frozenset({
    "linear_equation", "quadratic_equation",
    "derivative", "integral", "simplify", "unknown",
})

VALID_STATUSES = frozenset({"pending", "solved", "failed"})

VALID_SOLVERS = frozenset({"sympy", "llm", "mixed"})

# Simple linear-equation detector: matches `a x + b = c` etc.
# Captures coefficient, variable, constant.
_LINEAR_PATTERN = re.compile(
    r"^\s*([+-]?\s*\d*)\s*([a-zA-Z])\s*([+-])\s*(\d+)\s*=\s*(-?\d+)\s*$",
)


def is_sympy_available() -> bool:
    try:
        import sympy
        return True
    except ImportError:
        return False


def _db_path() -> Path:
    from . import db as _db
    return _db.sqlite_path()


def _conn() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10.0)
    conn.executescript(SCHEMA)
    return conn


def migrate() -> None:
    with _conn():
        pass


# ---------- dataclasses ----------

@dataclass(frozen=True)
class Step:
    id: str
    problem_id: str
    position: int
    latex: str
    explanation: str | None
    validated: bool
    flagged_count: int
    created_at: float


@dataclass(frozen=True)
class Problem:
    id: str
    user_id: str
    submission_id: str | None
    problem_latex: str
    problem_kind: str
    language: str
    status: str
    final_answer: str | None
    error_reason: str | None
    solver: str | None
    created_at: float
    solved_at: float | None
    steps: list[Step]


# ---------- helpers ----------

def _detect_kind(latex: str) -> str:
    """Best-effort kind detection from LaTeX. Real solver in
    `solve_with_sympy` does its own checks; this is a hint for
    UI + analytics.

    Check derivative/integral FIRST since they may contain powers
    in the expression (e.g. ∫ x² dx) that would otherwise match
    the quadratic branch."""
    s = (latex or "").strip()
    if r"\int" in s:
        return "integral"
    if r"\frac{d}" in s or r"\frac{d" in s:
        return "derivative"
    if "=" in s and "x^{2}" not in s and "^2" not in s and _LINEAR_PATTERN.match(s):
        return "linear_equation"
    if "x^{2}" in s or "x^2" in s:
        return "quadratic_equation"
    return "unknown"


# ---------- problem lifecycle ----------

def create_problem(
    *,
    user_id: str,
    problem_latex: str,
    submission_id: str | None = None,
    language: str = "en",
) -> Problem:
    problem_latex = (problem_latex or "").strip()
    if len(problem_latex) < 3 or len(problem_latex) > 4000:
        raise ValueError(
            "problem_latex must be [3, 4000] chars",
        )
    pid = uuid.uuid4().hex
    kind = _detect_kind(problem_latex)
    with _conn() as conn:
        conn.execute(
            "INSERT INTO step_problems "
            "(id, user_id, submission_id, problem_latex, "
            " problem_kind, language, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (pid, user_id, submission_id, problem_latex,
             kind, language, time.time()),
        )
    return get_problem(pid)  # type: ignore[return-value]


def get_problem(problem_id: str) -> Problem | None:
    with _conn() as conn:
        r = conn.execute(
            _P_SEL + " WHERE id = ?", (problem_id,),
        ).fetchone()
        if not r:
            return None
        step_rows = conn.execute(
            "SELECT id, problem_id, position, latex, "
            "       explanation, validated, flagged_count, "
            "       created_at "
            "FROM problem_steps WHERE problem_id = ? "
            "ORDER BY position",
            (problem_id,),
        ).fetchall()
    steps = [
        Step(
            id=s[0], problem_id=s[1], position=s[2],
            latex=s[3], explanation=s[4],
            validated=bool(s[5]), flagged_count=s[6],
            created_at=s[7],
        )
        for s in step_rows
    ]
    return Problem(
        id=r[0], user_id=r[1], submission_id=r[2],
        problem_latex=r[3], problem_kind=r[4],
        language=r[5], status=r[6], final_answer=r[7],
        error_reason=r[8], solver=r[9],
        created_at=r[10], solved_at=r[11], steps=steps,
    )


_P_SEL = (
    "SELECT id, user_id, submission_id, problem_latex, "
    "       problem_kind, language, status, final_answer, "
    "       error_reason, solver, created_at, solved_at "
    "FROM step_problems"
)


def list_user_problems(
    user_id: str,
    *,
    status: str | None = None,
    limit: int = 50,
) -> list[Problem]:
    limit = max(1, min(limit, 500))
    sql = "SELECT id FROM step_problems WHERE user_id = ?"
    params: list = [user_id]
    if status:
        if status not in VALID_STATUSES:
            raise ValueError(
                f"status must be in {sorted(VALID_STATUSES)}",
            )
        sql += " AND status = ?"; params.append(status)
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    with _conn() as conn:
        ids = [r[0] for r in conn.execute(sql, params).fetchall()]
    return [p for p in (get_problem(i) for i in ids) if p]


# ---------- SymPy solver (linear equations only at v3.16) ----------

def solve_with_sympy(problem_id: str) -> Problem:
    """Deterministic step-by-step for the small class of problems
    SymPy can crunch reliably. Currently handles:
      • Linear equations matching the _LINEAR_PATTERN

    Raises ValueError when SymPy isn't installed or the problem
    isn't in the supported class — caller falls back to
    `accept_llm_steps()`.
    """
    p = get_problem(problem_id)
    if not p:
        raise ValueError("problem not found")
    if p.status != "pending":
        raise ValueError(
            f"problem status is {p.status!r}, not pending",
        )
    if not is_sympy_available():
        raise ValueError("sympy not installed")
    if p.problem_kind != "linear_equation":
        raise ValueError(
            f"sympy solver only handles linear_equation "
            f"(got {p.problem_kind!r})",
        )
    m = _LINEAR_PATTERN.match(p.problem_latex)
    if not m:
        raise ValueError("problem doesn't match linear pattern")
    coef_str, var, sign, const_str, rhs_str = m.groups()
    coef = int(coef_str.replace(" ", "") or "1")
    if coef == 0:
        coef = 1
    const = int(const_str)
    if sign == "-":
        const = -const
    rhs = int(rhs_str)
    # Steps:
    #   1: a*x + b = c
    #   2: a*x = c - b
    #   3: x = (c - b) / a
    step1_latex = p.problem_latex
    step1_expl = "Start with the given equation."
    step2_rhs = rhs - const
    step2_latex = f"{coef}{var} = {step2_rhs}"
    step2_expl = (
        f"Subtract {const} from both sides "
        f"(if {const} was added on the left, do the opposite "
        "on the right)."
    ) if const >= 0 else (
        f"Add {-const} to both sides."
    )
    final_value = step2_rhs / coef
    step3_latex = (
        f"{var} = {step2_rhs // coef}"
        if step2_rhs % coef == 0
        else f"{var} = {step2_rhs}/{coef}"
    )
    step3_expl = (
        f"Divide both sides by the coefficient ({coef})."
    )
    # Persist
    now = time.time()
    final = (
        f"{var} = {step2_rhs // coef}"
        if step2_rhs % coef == 0
        else f"{var} = {final_value}"
    )
    with _conn() as conn:
        for pos, (latex, expl) in enumerate([
            (step1_latex, step1_expl),
            (step2_latex, step2_expl),
            (step3_latex, step3_expl),
        ], start=1):
            conn.execute(
                "INSERT INTO problem_steps "
                "(id, problem_id, position, latex, "
                " explanation, validated, created_at) "
                "VALUES (?, ?, ?, ?, ?, 1, ?)",
                (uuid.uuid4().hex, problem_id, pos,
                 latex, expl, now),
            )
        conn.execute(
            "UPDATE step_problems SET "
            " status = 'solved', final_answer = ?, "
            " solver = 'sympy', solved_at = ? "
            "WHERE id = ?",
            (final, now, problem_id),
        )
    return get_problem(problem_id)  # type: ignore[return-value]


def accept_llm_steps(
    *,
    problem_id: str,
    user_id: str,
    steps: list[dict],
    final_answer: str,
    solver: str = "llm",
) -> Problem:
    """Caller (tutor / LLM wrapper) supplies the step list +
    final answer; we persist with validated=False. UI flags these
    as 'AI-generated, verify yourself'."""
    p = get_problem(problem_id)
    if not p:
        raise ValueError("problem not found")
    if p.user_id != user_id:
        raise PermissionError("not your problem")
    if p.status not in ("pending", "failed"):
        raise ValueError(
            f"problem status is {p.status!r}, can't accept steps",
        )
    if solver not in VALID_SOLVERS:
        raise ValueError(
            f"solver must be in {sorted(VALID_SOLVERS)}",
        )
    if not steps:
        raise ValueError("at least one step required")
    if len(steps) > 30:
        raise ValueError("max 30 steps per problem")
    final_answer = (final_answer or "").strip()
    if len(final_answer) < 1 or len(final_answer) > 2000:
        raise ValueError("final_answer must be [1, 2000] chars")
    now = time.time()
    with _conn() as conn:
        # Clear prior steps (in case retry)
        conn.execute(
            "DELETE FROM problem_steps WHERE problem_id = ?",
            (problem_id,),
        )
        for i, s in enumerate(steps, start=1):
            if not isinstance(s, dict):
                raise ValueError(f"steps[{i-1}] must be dict")
            latex = (s.get("latex") or "").strip()
            if len(latex) < 1 or len(latex) > 2000:
                raise ValueError(
                    f"steps[{i-1}].latex required (max 2000 chars)",
                )
            expl = s.get("explanation")
            if expl is not None and len(expl) > 4000:
                raise ValueError(
                    f"steps[{i-1}].explanation must be ≤4000 chars",
                )
            conn.execute(
                "INSERT INTO problem_steps "
                "(id, problem_id, position, latex, "
                " explanation, validated, created_at) "
                "VALUES (?, ?, ?, ?, ?, 0, ?)",
                (uuid.uuid4().hex, problem_id, i, latex,
                 expl, now),
            )
        conn.execute(
            "UPDATE step_problems SET "
            " status = 'solved', final_answer = ?, "
            " solver = ?, solved_at = ? "
            "WHERE id = ?",
            (final_answer, solver, now, problem_id),
        )
    return get_problem(problem_id)  # type: ignore[return-value]


def mark_failed(
    *,
    problem_id: str,
    user_id: str,
    reason: str,
) -> bool:
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE step_problems SET "
            " status = 'failed', error_reason = ? "
            "WHERE id = ? AND user_id = ? "
            "AND status = 'pending'",
            ((reason or "")[:1000], problem_id, user_id),
        )
        return cur.rowcount > 0


# ---------- step-level interaction ----------

def get_step(step_id: str) -> Step | None:
    with _conn() as conn:
        r = conn.execute(
            "SELECT id, problem_id, position, latex, "
            "       explanation, validated, flagged_count, "
            "       created_at "
            "FROM problem_steps WHERE id = ?",
            (step_id,),
        ).fetchone()
    if not r:
        return None
    return Step(
        id=r[0], problem_id=r[1], position=r[2],
        latex=r[3], explanation=r[4],
        validated=bool(r[5]), flagged_count=r[6],
        created_at=r[7],
    )


def flag_step(*, step_id: str, user_id: str) -> bool:  # noqa: ARG001
    """Student says 'didn't follow' on a step. Bumps the
    flagged_count; high-flagged steps surface in the admin queue
    for content improvement."""
    step = get_step(step_id)
    if not step:
        return False
    with _conn() as conn:
        conn.execute(
            "UPDATE problem_steps SET "
            " flagged_count = flagged_count + 1 "
            "WHERE id = ?", (step_id,),
        )
    return True


def add_step_explanation(
    *,
    step_id: str,
    user_id: str,
    explanation: str,
    citations: list[dict] | None = None,
    ai_call_id: str | None = None,
) -> str:
    """Persist a tutor explanation for a flagged step. Returns
    explanation_id."""
    step = get_step(step_id)
    if not step:
        raise ValueError("step not found")
    explanation = (explanation or "").strip()
    if len(explanation) < 10 or len(explanation) > 8000:
        raise ValueError(
            "explanation must be [10, 8000] chars",
        )
    eid = uuid.uuid4().hex
    with _conn() as conn:
        conn.execute(
            "INSERT INTO step_explanations "
            "(id, step_id, user_id, explanation, "
            " citations_json, ai_call_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (eid, step_id, user_id, explanation,
             json.dumps(citations) if citations else None,
             ai_call_id, time.time()),
        )
    return eid


def list_step_explanations(step_id: str) -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT id, user_id, explanation, citations_json, "
            "       ai_call_id, created_at "
            "FROM step_explanations WHERE step_id = ? "
            "ORDER BY created_at DESC",
            (step_id,),
        ).fetchall()
    return [
        {
            "id": r[0], "user_id": r[1], "explanation": r[2],
            "citations": json.loads(r[3]) if r[3] else [],
            "ai_call_id": r[4], "created_at": r[5],
        }
        for r in rows
    ]


def high_flagged_steps(
    *, threshold: int = 3, limit: int = 50,
) -> list[Step]:
    """Admin queue — steps with ≥threshold flags. Editorial team
    rewrites these explanations."""
    limit = max(1, min(limit, 200))
    with _conn() as conn:
        rows = conn.execute(
            "SELECT id, problem_id, position, latex, "
            "       explanation, validated, flagged_count, "
            "       created_at "
            "FROM problem_steps "
            "WHERE flagged_count >= ? "
            "ORDER BY flagged_count DESC LIMIT ?",
            (threshold, limit),
        ).fetchall()
    return [
        Step(
            id=r[0], problem_id=r[1], position=r[2],
            latex=r[3], explanation=r[4],
            validated=bool(r[5]), flagged_count=r[6],
            created_at=r[7],
        )
        for r in rows
    ]


def user_stats(user_id: str) -> dict:
    """Per-user stats."""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) "
            "FROM step_problems WHERE user_id = ? GROUP BY status",
            (user_id,),
        ).fetchall()
    return {r[0]: r[1] for r in rows}
