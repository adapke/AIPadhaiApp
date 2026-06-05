"""L3 — Handwritten math recognition.

Student writes math on a tablet or paper. We scan the image, extract
LaTeX via Claude vision, then validate the work step-by-step.

Two-stage pipeline:
  1. `extract(image_url, expected_language='en')` → LaTeX + confidence
  2. `validate_steps([latex_step_1, latex_step_2, ...])` → per-step
     pass/fail with the first-wrong-step pointed out

Validation uses sympy when available; falls back to syntactic equality
when sympy missing (still useful — catches typos but not algebraic
equivalence).

Schema:
  math_submissions    one row per scan; image_url, extracted_latex,
                      validation_json, status, ai_call_id (L6 backref)

Cost: vision tokens are expensive — every extraction goes through
Q2 with caching (the system prompt is invariant across submissions).
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

from . import models as _models

SCHEMA = """
CREATE TABLE IF NOT EXISTS math_submissions (
    id              TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL,
    image_url       TEXT NOT NULL,
    expected_language TEXT NOT NULL DEFAULT 'en',
    extracted_latex TEXT,                       -- the model's best read
    confidence      REAL,                        -- 0..1
    steps_json      TEXT,                        -- ["step1", "step2", ...]
    validation_json TEXT,                        -- {first_wrong_step, per_step:[...]}
    status          TEXT NOT NULL DEFAULT 'pending',
    -- pending | extracted | validated | rejected | errored
    ai_call_id      TEXT,
    error           TEXT,
    created_at      REAL NOT NULL,
    extracted_at    REAL,
    validated_at    REAL
);
CREATE INDEX IF NOT EXISTS idx_math_user_time
    ON math_submissions(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_math_status
    ON math_submissions(status, created_at DESC);
"""

# Minimum confidence below which we mark the submission 'rejected'
# and ask the student to re-upload. Below this the LaTeX is likely
# wrong and step validation would give misleading feedback.
MIN_CONFIDENCE = 0.55


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


def is_vision_available() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def is_sympy_available() -> bool:
    try:
        import sympy
        return True
    except ImportError:
        return False


# ---------- dataclass ----------

@dataclass(frozen=True)
class MathSubmission:
    id: str
    user_id: str
    image_url: str
    expected_language: str
    extracted_latex: str | None
    confidence: float | None
    steps: list[str] | None
    validation: dict | None
    status: str
    ai_call_id: str | None
    error: str | None
    created_at: float
    extracted_at: float | None
    validated_at: float | None


_SEL = (
    "id, user_id, image_url, expected_language, extracted_latex, "
    "confidence, steps_json, validation_json, status, ai_call_id, "
    "error, created_at, extracted_at, validated_at"
)


def _row_to_sub(r) -> MathSubmission:
    return MathSubmission(
        id=r[0], user_id=r[1], image_url=r[2], expected_language=r[3],
        extracted_latex=r[4], confidence=r[5],
        steps=json.loads(r[6]) if r[6] else None,
        validation=json.loads(r[7]) if r[7] else None,
        status=r[8], ai_call_id=r[9], error=r[10],
        created_at=r[11], extracted_at=r[12], validated_at=r[13],
    )


# ---------- submission ----------

def submit(
    *,
    user_id: str,
    image_url: str,
    expected_language: str = "en",
) -> MathSubmission:
    if not image_url or len(image_url) > 2048:
        raise ValueError("image_url required, max 2048 chars")
    if expected_language not in ("en", "hi", "ta", "te", "kn", "ml", "mr", "bn", "gu", "pa"):
        # Unknown languages are OK at the model level; we just don't
        # carry any custom prompt for them.
        expected_language = "en"
    sid = uuid.uuid4().hex
    with _conn() as conn:
        conn.execute(
            "INSERT INTO math_submissions "
            "(id, user_id, image_url, expected_language, status, created_at) "
            "VALUES (?, ?, ?, ?, 'pending', ?)",
            (sid, user_id, image_url, expected_language, time.time()),
        )
    return get(sid)  # type: ignore[return-value]


def get(sid: str) -> MathSubmission | None:
    with _conn() as conn:
        r = conn.execute(
            f"SELECT {_SEL} FROM math_submissions WHERE id = ?", (sid,),
        ).fetchone()
    return _row_to_sub(r) if r else None


def list_for_user(user_id: str, *, limit: int = 30) -> list[MathSubmission]:
    limit = max(1, min(limit, 500))
    with _conn() as conn:
        rows = conn.execute(
            f"SELECT {_SEL} FROM math_submissions WHERE user_id = ? "
            "ORDER BY created_at DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
    return [_row_to_sub(r) for r in rows]


# ---------- vision extraction ----------

_VISION_SYSTEM = """You are a math notation reader. The user uploads
a photo of handwritten math. Your job: extract each line as a clean
LaTeX expression, then return them as a JSON object.

Rules:
- Each visually-distinct LINE of work becomes one element in `steps`.
- Output strict JSON only (no markdown):

{
  "steps": ["x^2 + 2x + 1 = 0", "(x + 1)^2 = 0", "x = -1"],
  "confidence": 0.92
}

- If the image is unreadable / not math, return
  {"steps": [], "confidence": 0.0}.
- Confidence is your subjective certainty in the extraction.
"""


def extract(
    *,
    submission_id: str,
) -> MathSubmission:
    """Run Claude vision over the submission's image and persist
    `extracted_latex`, `steps`, `confidence`, `status`.

    Falls back to a 'no_provider' status when ANTHROPIC_API_KEY
    unset — the schema row still resolves so the frontend dev path
    works."""
    sub = get(submission_id)
    if not sub:
        raise ValueError(f"submission {submission_id!r} not found")
    if sub.status not in ("pending",):
        return sub  # idempotent — re-extract requires a new submission

    if not is_vision_available():
        _persist_extract_failure(
            submission_id, error="no_provider", status="errored",
        )
        return get(submission_id)  # type: ignore[return-value]

    try:
        from anthropic import Anthropic
    except ImportError:
        _persist_extract_failure(
            submission_id, error="anthropic-sdk-missing",
            status="errored",
        )
        return get(submission_id)  # type: ignore[return-value]

    from . import llm_cache, llm_obs
    model = os.environ.get(
        "PADHAI_MATH_VISION_MODEL", _models.OPUS_MODEL,
    )
    user_text = (
        f"Read this handwritten math. Expected language: "
        f"{sub.expected_language}. Return JSON per the schema."
    )
    # Claude vision: image goes as a `content` block. We can't use the
    # plain `with_caching()` helper because of the image block, but we
    # can mark the system block as cached manually.
    system_block = (
        [{"type": "text", "text": _VISION_SYSTEM,
          "cache_control": {"type": "ephemeral"}}]
        if llm_cache.is_caching_enabled() else _VISION_SYSTEM
    )

    started = time.time()
    try:
        client = Anthropic()
        resp = client.messages.create(
            model=model, max_tokens=1024, system=system_block,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image",
                     "source": {"type": "url", "url": sub.image_url}},
                    {"type": "text", "text": user_text},
                ],
            }],
        )
    except Exception as e:
        _persist_extract_failure(
            submission_id, error=str(e)[:500], status="errored",
        )
        return get(submission_id)  # type: ignore[return-value]

    latency_ms = int((time.time() - started) * 1000)
    body = "".join(b.text for b in resp.content if b.type == "text")
    parsed = _parse_vision_json(body)
    tokens_in = getattr(resp.usage, "input_tokens", 0) or 0
    tokens_out = getattr(resp.usage, "output_tokens", 0) or 0
    cached = bool(getattr(resp.usage, "cache_read_input_tokens", 0))
    call_id = llm_obs.record_call(
        module="math_vision", prompt_version="v1",
        model=model, tokens_in=tokens_in, tokens_out=tokens_out,
        latency_ms=latency_ms, user_id=sub.user_id, cached=cached,
    )

    steps = parsed.get("steps") or []
    confidence = float(parsed.get("confidence") or 0.0)
    extracted_latex = "\n".join(steps) if steps else None
    status = "extracted" if confidence >= MIN_CONFIDENCE else "rejected"
    with _conn() as conn:
        conn.execute(
            "UPDATE math_submissions SET "
            " extracted_latex = ?, confidence = ?, steps_json = ?, "
            " status = ?, ai_call_id = ?, extracted_at = ? "
            "WHERE id = ?",
            (extracted_latex, confidence, json.dumps(steps),
             status, call_id, time.time(), submission_id),
        )
    return get(submission_id)  # type: ignore[return-value]


def _persist_extract_failure(submission_id: str, *,
                             error: str, status: str) -> None:
    with _conn() as conn:
        conn.execute(
            "UPDATE math_submissions SET "
            " status = ?, error = ?, extracted_at = ? "
            "WHERE id = ?",
            (status, error, time.time(), submission_id),
        )


def _parse_vision_json(body: str) -> dict:
    body = body.strip()
    candidates = [body]
    if "```" in body:
        for fence in body.split("```"):
            s = fence.strip()
            if s.startswith("{") or s.startswith("json\n{"):
                candidates.append(s[5:] if s.startswith("json\n") else s)
    for c in candidates:
        try:
            parsed = json.loads(c)
            if isinstance(parsed, dict):
                return parsed
        except (ValueError, TypeError):
            continue
    return {"steps": [], "confidence": 0.0}


# ---------- step validation ----------

@dataclass(frozen=True)
class ValidationResult:
    submission_id: str
    overall: str                     # 'correct' | 'wrong' | 'unknown'
    first_wrong_step: int | None     # 1-indexed; None when all correct
    per_step: list[dict]             # [{idx, expr_latex, equals_prev, sympy_ok}]
    method: str                      # 'sympy' | 'syntactic' | 'unavailable'


def validate(submission_id: str) -> ValidationResult:
    """Walk the extracted steps. For each adjacent pair (step_i,
    step_{i+1}), check if they're algebraically equal (sympy) or at
    least syntactically equal (fallback). The first failing pair is
    where the student went wrong.

    Idempotent — re-running overwrites the persisted validation_json.
    """
    sub = get(submission_id)
    if not sub:
        raise ValueError(f"submission {submission_id!r} not found")
    if not sub.steps or len(sub.steps) < 2:
        result = ValidationResult(
            submission_id=submission_id,
            overall="unknown",
            first_wrong_step=None,
            per_step=[
                {"idx": i + 1, "expr_latex": s,
                 "equals_prev": None, "sympy_ok": None}
                for i, s in enumerate(sub.steps or [])
            ],
            method="unavailable",
        )
        _persist_validation(submission_id, result)
        return result

    method = "sympy" if is_sympy_available() else "syntactic"
    per_step = []
    first_wrong = None

    if method == "sympy":
        import sympy
        prev_expr = None
        for i, step in enumerate(sub.steps):
            parsed_ok = False
            equals_prev = None
            sympy_ok = None
            try:
                expr = sympy.sympify(_latex_to_sympy_input(step))
                parsed_ok = True
                if prev_expr is not None:
                    # Treat both sides of equation as Eq for difference
                    try:
                        eq = sympy.simplify(expr - prev_expr)
                        equals_prev = (eq == 0)
                    except Exception:
                        equals_prev = None
                    sympy_ok = parsed_ok
                prev_expr = expr
            except Exception:
                parsed_ok = False
            per_step.append({
                "idx": i + 1, "expr_latex": step,
                "equals_prev": equals_prev,
                "sympy_ok": parsed_ok,
            })
            if equals_prev is False and first_wrong is None:
                first_wrong = i + 1
    else:
        # Syntactic fallback — adjacent steps just compared as strings
        # after whitespace normalization. Useful for catching typos /
        # missing terms.
        for i, step in enumerate(sub.steps):
            equals_prev = None
            if i > 0:
                a = _norm_ws(sub.steps[i - 1])
                b = _norm_ws(step)
                equals_prev = a == b
            per_step.append({
                "idx": i + 1, "expr_latex": step,
                "equals_prev": equals_prev,
                "sympy_ok": None,
            })

    if first_wrong is not None:
        overall = "wrong"
    elif any(s["equals_prev"] is False for s in per_step):
        overall = "wrong"
        first_wrong = next(
            (s["idx"] for s in per_step if s["equals_prev"] is False),
            None,
        )
    elif any(s["equals_prev"] for s in per_step):
        overall = "correct"
    else:
        overall = "unknown"

    result = ValidationResult(
        submission_id=submission_id,
        overall=overall,
        first_wrong_step=first_wrong,
        per_step=per_step,
        method=method,
    )
    _persist_validation(submission_id, result)
    return result


def _persist_validation(submission_id: str, result: ValidationResult) -> None:
    payload = {
        "overall": result.overall,
        "first_wrong_step": result.first_wrong_step,
        "per_step": result.per_step,
        "method": result.method,
    }
    with _conn() as conn:
        conn.execute(
            "UPDATE math_submissions SET "
            " validation_json = ?, status = 'validated', "
            " validated_at = ? "
            "WHERE id = ?",
            (json.dumps(payload), time.time(), submission_id),
        )


# ---------- LaTeX → sympy input helpers ----------

# Map common LaTeX-isms to sympy-friendly forms. Not exhaustive.
_LATEX_FIXES = [
    (r"\\cdot", "*"),
    (r"\\times", "*"),
    (r"\\div", "/"),
    (r"\\frac\{([^}]+)\}\{([^}]+)\}", r"(\1)/(\2)"),
    (r"\\sqrt\{([^}]+)\}", r"sqrt(\1)"),
    (r"\\pi", "pi"),
    (r"\\\\", " "),
    (r"\^\{([^}]+)\}", r"**(\1)"),
    (r"\^(\d)", r"**\1"),
    (r"\\left|\\right", ""),
    (r"\\,|\\;|\\:|\\!", " "),
    (r"[{}]", ""),
]


def _latex_to_sympy_input(latex: str) -> str:
    """Best-effort conversion. Handles a typical K-12 / coaching
    subset; sympy.sympify takes care of the rest. Anything not in
    the catalog falls through and sympify either parses it or
    raises — we treat raise as parse failure."""
    s = latex
    for pat, repl in _LATEX_FIXES:
        s = re.sub(pat, repl, s)
    # Strip leading/trailing 'equation' bits like `= 0` so we get a
    # standalone expression. (For full equations we'd split on `=` and
    # compare both sides — v1.3.x.)
    if "=" in s:
        s = s.split("=", 1)[1].strip() or s
    return s.strip()


def _norm_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()
