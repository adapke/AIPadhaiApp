"""v3.1 — AI accuracy benchmark.

Per gap-review §4.3 — route smokes (test_v*.py) verify business
logic + 401 auth gates. They prove **nothing** about answer
correctness. A unicorn-level learning product needs a measurable,
expert-reviewed accuracy benchmark.

Four tables:
  bench_datasets     curated test sets — e.g. 'ncert_class_10_math_v1'
  bench_items        the questions + expert-reviewed expected outputs
  bench_runs         one row per benchmark execution
  bench_results      per-item scoring within a run

Three task kinds:
  answer_correctness — Q → A, scored by expected-answer match
  citation_correctness — Q → (A, [citations]), scored on whether
                          cited pages actually contain the answer
  quiz_key_correctness — Q + options → correct option, scored by
                          exact key match

This module ships the storage + scoring framework. The judges
(LLM-as-judge / rubric / exact-match) plug in via `Judge` ABC.
Default exact-match + rouge-l + LLM-judge implementations included.

Test-set curation is *content work*, not engineering — expert
reviewers compile datasets via the admin API, store expected
answers as JSON. We run benchmarks on schedule + on every model
swap; the trust dashboard surfaces the rate.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import statistics
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS bench_datasets (
    id              TEXT PRIMARY KEY,
    code            TEXT NOT NULL,                 -- 'ncert_class_10_math_v1'
    title           TEXT NOT NULL,
    domain          TEXT NOT NULL,                 -- 'school' / 'upsc' / 'jee' / 'hindi_tutor' / ...
    task_kind       TEXT NOT NULL,                 -- 'answer_correctness' / 'citation_correctness' / 'quiz_key_correctness'
    description     TEXT,
    item_count      INTEGER NOT NULL DEFAULT 0,
    reviewed_by     TEXT,                          -- comma-sep reviewer names / ids
    version         INTEGER NOT NULL DEFAULT 1,
    status          TEXT NOT NULL DEFAULT 'draft',
    -- 'draft' | 'published' | 'archived'
    created_at      REAL NOT NULL,
    published_at    REAL,
    UNIQUE (code, version)
);
CREATE INDEX IF NOT EXISTS idx_bds_domain
    ON bench_datasets(domain, status);

CREATE TABLE IF NOT EXISTS bench_items (
    id              TEXT PRIMARY KEY,
    dataset_id      TEXT NOT NULL,
    prompt          TEXT NOT NULL,
    expected_json   TEXT NOT NULL,                 -- {"answer": "...", "citations": [...]}
    rubric          TEXT,                          -- free-form grading notes for LLM-judge
    difficulty      TEXT,                          -- 'easy' | 'medium' | 'hard'
    tags_json       TEXT,                          -- topic_code, language, source_year, ...
    weight          REAL NOT NULL DEFAULT 1.0,
    created_at      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_bitems_dataset
    ON bench_items(dataset_id);

CREATE TABLE IF NOT EXISTS bench_runs (
    id              TEXT PRIMARY KEY,
    dataset_id      TEXT NOT NULL,
    judge           TEXT NOT NULL,                  -- 'exact_match' | 'rouge_l' | 'llm_judge' | 'citation_check'
    target          TEXT NOT NULL,                  -- 'tutor' / 'lesson' / 'quiz' / model name
    model_version   TEXT,
    item_count      INTEGER NOT NULL,
    pass_count      INTEGER NOT NULL DEFAULT 0,
    fail_count      INTEGER NOT NULL DEFAULT 0,
    skipped_count   INTEGER NOT NULL DEFAULT 0,
    mean_score      REAL,                           -- 0..1
    p50_score       REAL,
    p90_score       REAL,
    started_at      REAL NOT NULL,
    finished_at     REAL,
    notes           TEXT
);
CREATE INDEX IF NOT EXISTS idx_bruns_target
    ON bench_runs(target, started_at DESC);

CREATE TABLE IF NOT EXISTS bench_results (
    id              TEXT PRIMARY KEY,
    run_id          TEXT NOT NULL,
    item_id         TEXT NOT NULL,
    actual_json     TEXT NOT NULL,                 -- {"answer": "...", "citations": [...]}
    score           REAL,                           -- 0..1; NULL if skipped
    passed          INTEGER NOT NULL DEFAULT 0,
    reason          TEXT,                           -- diagnostic when fail / skip
    judged_at       REAL NOT NULL,
    UNIQUE (run_id, item_id)
);
CREATE INDEX IF NOT EXISTS idx_bres_run
    ON bench_results(run_id, score);
"""


VALID_TASK_KINDS = frozenset({
    "answer_correctness", "citation_correctness",
    "quiz_key_correctness",
})

VALID_DATASET_STATUSES = frozenset({"draft", "published", "archived"})

VALID_JUDGES = frozenset({
    "exact_match", "rouge_l", "llm_judge", "citation_check",
})

VALID_DIFFICULTIES = frozenset({"easy", "medium", "hard"})

# Score below which a result counts as `passed = 0`. Used by the
# trust dashboard's pass-rate metric.
PASS_THRESHOLD = 0.70


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
class Dataset:
    id: str
    code: str
    title: str
    domain: str
    task_kind: str
    description: str | None
    item_count: int
    reviewed_by: str | None
    version: int
    status: str
    created_at: float
    published_at: float | None


@dataclass(frozen=True)
class Item:
    id: str
    dataset_id: str
    prompt: str
    expected: dict
    rubric: str | None
    difficulty: str | None
    tags: dict
    weight: float
    created_at: float


@dataclass(frozen=True)
class Run:
    id: str
    dataset_id: str
    judge: str
    target: str
    model_version: str | None
    item_count: int
    pass_count: int
    fail_count: int
    skipped_count: int
    mean_score: float | None
    p50_score: float | None
    p90_score: float | None
    started_at: float
    finished_at: float | None
    notes: str | None


@dataclass(frozen=True)
class Result:
    id: str
    run_id: str
    item_id: str
    actual: dict
    score: float | None
    passed: bool
    reason: str | None
    judged_at: float


# ---------- dataset CRUD ----------

def create_dataset(
    *,
    code: str,
    title: str,
    domain: str,
    task_kind: str,
    description: str | None = None,
    version: int = 1,
    reviewed_by: str | None = None,
) -> Dataset:
    code = (code or "").strip()
    if not re.match(r"^[a-z0-9_]{3,80}$", code):
        raise ValueError(
            "code must be 3-80 lowercase letters/digits/_ chars",
        )
    if task_kind not in VALID_TASK_KINDS:
        raise ValueError(
            f"task_kind must be in {sorted(VALID_TASK_KINDS)}",
        )
    if version < 1:
        raise ValueError("version must be ≥1")
    title = (title or "").strip()
    if len(title) < 4 or len(title) > 200:
        raise ValueError("title must be [4, 200] chars")
    domain = (domain or "").strip()
    if not domain or len(domain) > 60:
        raise ValueError("domain required (max 60 chars)")
    did = uuid.uuid4().hex
    try:
        with _conn() as conn:
            conn.execute(
                "INSERT INTO bench_datasets "
                "(id, code, title, domain, task_kind, description, "
                " version, reviewed_by, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (did, code, title, domain, task_kind, description,
                 version, reviewed_by, time.time()),
            )
    except sqlite3.IntegrityError as e:
        raise ValueError(
            f"dataset {code!r} v{version} already exists",
        ) from e
    return get_dataset(did)  # type: ignore[return-value]


def get_dataset(dataset_id: str) -> Dataset | None:
    with _conn() as conn:
        r = conn.execute(
            _DATASET_SEL + " WHERE id = ?", (dataset_id,),
        ).fetchone()
    return _row_to_dataset(r) if r else None


_DATASET_SEL = (
    "SELECT id, code, title, domain, task_kind, description, "
    "       item_count, reviewed_by, version, status, "
    "       created_at, published_at "
    "FROM bench_datasets"
)


def _row_to_dataset(r) -> Dataset:
    return Dataset(
        id=r[0], code=r[1], title=r[2], domain=r[3],
        task_kind=r[4], description=r[5], item_count=r[6],
        reviewed_by=r[7], version=r[8], status=r[9],
        created_at=r[10], published_at=r[11],
    )


def list_datasets(
    *,
    domain: str | None = None,
    status: str | None = None,
) -> list[Dataset]:
    sql = _DATASET_SEL
    where: list[str] = []
    params: list = []
    if domain:
        where.append("domain = ?"); params.append(domain)
    if status:
        if status not in VALID_DATASET_STATUSES:
            raise ValueError(
                f"status must be in {sorted(VALID_DATASET_STATUSES)}",
            )
        where.append("status = ?"); params.append(status)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY created_at DESC"
    with _conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_dataset(r) for r in rows]


def add_item(
    *,
    dataset_id: str,
    prompt: str,
    expected: dict,
    rubric: str | None = None,
    difficulty: str | None = None,
    tags: dict | None = None,
    weight: float = 1.0,
) -> Item:
    ds = get_dataset(dataset_id)
    if not ds:
        raise ValueError("dataset not found")
    if ds.status != "draft":
        raise ValueError(
            "items can only be added to draft datasets",
        )
    prompt = (prompt or "").strip()
    if not prompt or len(prompt) > 8000:
        raise ValueError("prompt required (max 8000 chars)")
    if not isinstance(expected, dict):
        raise ValueError("expected must be a dict")
    if difficulty is not None and difficulty not in VALID_DIFFICULTIES:
        raise ValueError(
            f"difficulty must be in {sorted(VALID_DIFFICULTIES)}",
        )
    if weight <= 0 or weight > 10:
        raise ValueError("weight must be in (0, 10]")
    iid = uuid.uuid4().hex
    now = time.time()
    with _conn() as conn:
        conn.execute(
            "INSERT INTO bench_items "
            "(id, dataset_id, prompt, expected_json, rubric, "
            " difficulty, tags_json, weight, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (iid, dataset_id, prompt, json.dumps(expected),
             rubric, difficulty,
             json.dumps(tags) if tags else None,
             weight, now),
        )
        conn.execute(
            "UPDATE bench_datasets SET item_count = item_count + 1 "
            "WHERE id = ?", (dataset_id,),
        )
    return Item(
        id=iid, dataset_id=dataset_id, prompt=prompt,
        expected=expected, rubric=rubric,
        difficulty=difficulty, tags=tags or {},
        weight=weight, created_at=now,
    )


def list_items(dataset_id: str) -> list[Item]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT id, dataset_id, prompt, expected_json, "
            "       rubric, difficulty, tags_json, weight, "
            "       created_at "
            "FROM bench_items WHERE dataset_id = ? "
            "ORDER BY created_at",
            (dataset_id,),
        ).fetchall()
    return [
        Item(
            id=r[0], dataset_id=r[1], prompt=r[2],
            expected=json.loads(r[3]),
            rubric=r[4], difficulty=r[5],
            tags=json.loads(r[6]) if r[6] else {},
            weight=r[7], created_at=r[8],
        )
        for r in rows
    ]


def publish_dataset(dataset_id: str) -> bool:
    """Lock the dataset against further item edits — published
    datasets are reproducible benchmarks. Requires ≥10 items
    (smaller sets aren't statistically useful)."""
    ds = get_dataset(dataset_id)
    if not ds:
        raise ValueError("dataset not found")
    if ds.status != "draft":
        return False
    if ds.item_count < 10:
        raise ValueError(
            "dataset must have ≥10 items before publishing",
        )
    with _conn() as conn:
        conn.execute(
            "UPDATE bench_datasets SET "
            " status = 'published', published_at = ? "
            "WHERE id = ?",
            (time.time(), dataset_id),
        )
    return True


def archive_dataset(dataset_id: str) -> bool:
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE bench_datasets SET status = 'archived' "
            "WHERE id = ? AND status != 'archived'",
            (dataset_id,),
        )
        return cur.rowcount > 0


# ---------- judges ----------

def _exact_match(
    expected: dict, actual: dict, *, prompt: str | None = None,  # noqa: ARG001
) -> tuple[float, str | None]:
    e = (expected.get("answer") or "").strip().lower()
    a = (actual.get("answer") or "").strip().lower()
    if not e:
        return 0.0, "expected.answer missing"
    if e == a:
        return 1.0, None
    return 0.0, f"expected {e!r} != actual {a!r}"


def _rouge_l_lite(
    expected: dict, actual: dict, *, prompt: str | None = None,  # noqa: ARG001
) -> tuple[float, str | None]:
    """Lightweight ROUGE-L approximation — longest common
    subsequence ratio on tokenised words. Good enough for short
    answers; production-grade ROUGE pulls a separate dep."""
    def tokens(s: str) -> list[str]:
        return re.findall(r"[a-z0-9]+", (s or "").lower())
    e = tokens(expected.get("answer") or "")
    a = tokens(actual.get("answer") or "")
    if not e or not a:
        return 0.0, "empty expected or actual"
    # LCS via DP
    n, m = len(e), len(a)
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            if e[i - 1] == a[j - 1]:
                dp[i][j] = dp[i - 1][j - 1] + 1
            else:
                dp[i][j] = max(dp[i - 1][j], dp[i][j - 1])
    lcs = dp[n][m]
    p = lcs / m
    r = lcs / n
    f1 = 2 * p * r / (p + r) if (p + r) else 0.0
    return round(f1, 4), None


def _quiz_key_check(
    expected: dict, actual: dict, *, prompt: str | None = None,  # noqa: ARG001
) -> tuple[float, str | None]:
    e = expected.get("correct_option")
    a = actual.get("correct_option")
    if e is None or a is None:
        return 0.0, "correct_option missing"
    return (1.0, None) if e == a else (0.0, f"key {e!r} != {a!r}")


def _citation_check(
    expected: dict, actual: dict, *, prompt: str | None = None,  # noqa: ARG001
) -> tuple[float, str | None]:
    """Score = fraction of expected citations that the actual
    answer also covered. Each citation matches on (source_id,
    page_number) tuple."""
    exp = expected.get("citations") or []
    act = actual.get("citations") or []
    if not exp:
        return 0.0, "expected.citations missing or empty"

    def key(c):
        return (c.get("source_id"), c.get("page_number"))
    exp_keys = {key(c) for c in exp}
    act_keys = {key(c) for c in act}
    hit = len(exp_keys & act_keys)
    score = hit / len(exp_keys)
    if score == 1.0:
        return 1.0, None
    return round(score, 4), (
        f"covered {hit}/{len(exp_keys)} expected citations"
    )


# LLM-judge — calls Claude Haiku to grade whether `actual.answer` is
# acceptable given `expected.answer` and the original prompt. Used when
# exact_match / rouge_l are too brittle (paraphrases, synonyms, valid
# alternative wordings). Returns 1.0 for CORRECT, 0.5 for PARTIAL, 0.0
# for WRONG. Costs ~₹0.001 per call at Haiku rates.
_LLM_JUDGE_SYSTEM = (
    "You are grading a student's short answer against an expected "
    "reference answer. Output exactly one of these three tokens and "
    "nothing else:\n"
    "  CORRECT   — the student answer matches the expected meaning "
    "(synonyms, paraphrases, different wording all count)\n"
    "  PARTIAL   — on-topic but missing key information OR contains "
    "a notable error\n"
    "  WRONG     — incorrect, off-topic, empty, or nonsense\n"
    "Do not explain. Do not output any other text."
)

_LLM_JUDGE_VERDICTS = {
    "CORRECT": 1.0,
    "PARTIAL": 0.5,
    "WRONG":   0.0,
}


def _llm_judge_client():
    """Return an Anthropic client + model id, or raise on missing
    API key. Lazy-imported so structural mode never pulls anthropic."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError(
            "llm_judge requires ANTHROPIC_API_KEY in environment",
        )
    from anthropic import Anthropic

    from . import models as _models
    client = Anthropic()
    model = os.environ.get("PADHAI_BENCH_JUDGE_MODEL", _models.HAIKU_MODEL)
    return client, model


def _llm_judge(
    expected: dict, actual: dict, *, prompt: str | None = None,
) -> tuple[float, str | None]:
    """Grade actual against expected via a Claude call. Open-coded
    here (not via llm_call.call_claude) so the bench has zero
    cross-dependency on cost-tracking / cap-checking — judges are
    a system-internal QA loop, not a user-facing surface."""
    exp = (expected.get("answer") or "").strip()
    act = (actual.get("answer") or "").strip()
    if not exp:
        return 0.0, "expected.answer missing"
    if not act:
        return 0.0, "actual.answer empty"

    client, model = _llm_judge_client()
    user_text = (
        f"Question: {prompt or '(not provided)'}\n"
        f"Expected: {exp}\n"
        f"Student:  {act}\n"
        f"Grade:"
    )
    resp = client.messages.create(
        model=model,
        max_tokens=10,
        system=_LLM_JUDGE_SYSTEM,
        messages=[{"role": "user", "content": user_text}],
    )
    text = "".join(b.text for b in resp.content if b.type == "text").strip().upper()
    # Tolerate trailing punctuation / extra whitespace
    token = re.split(r"[^A-Z]", text, maxsplit=1)[0]
    if token in _LLM_JUDGE_VERDICTS:
        return _LLM_JUDGE_VERDICTS[token], (
            None if token == "CORRECT" else f"judge: {token}"
        )
    return 0.0, f"unparseable verdict: {text!r}"


_JUDGES = {
    "exact_match": _exact_match,
    "rouge_l": _rouge_l_lite,
    "quiz_key": _quiz_key_check,
    "citation_check": _citation_check,
    "llm_judge": _llm_judge,
}


# ---------- run a benchmark ----------

def run_benchmark(
    *,
    dataset_id: str,
    target: str,
    judge: str,
    runner,
    model_version: str | None = None,
    notes: str | None = None,
) -> Run:
    """Execute a published dataset against `runner(prompt) -> dict`.

    Returns the Run row with aggregate stats; per-item results live
    in bench_results.

    `judge` selects the scoring strategy. `runner` is the system
    under test — typically a closure over the tutor/lesson API that
    returns `{"answer": str, "citations": [...]}`.
    """
    ds = get_dataset(dataset_id)
    if not ds:
        raise ValueError("dataset not found")
    if ds.status != "published":
        raise ValueError(
            "only published datasets can be benchmarked",
        )
    # `quiz_key` judge is selected via `judge='exact_match'` for
    # task_kind='quiz_key_correctness'; surface a sane mapping
    if judge not in VALID_JUDGES:
        raise ValueError(f"judge must be in {sorted(VALID_JUDGES)}")
    items = list_items(dataset_id)
    if not items:
        raise ValueError("dataset has no items")
    target = (target or "").strip()
    if not target or len(target) > 100:
        raise ValueError("target required (max 100 chars)")

    judge_fn = _JUDGES.get(judge)
    if judge == "exact_match" and ds.task_kind == "quiz_key_correctness":
        judge_fn = _JUDGES["quiz_key"]
    if not judge_fn:
        raise ValueError(f"no judge implementation for {judge!r}")

    rid = uuid.uuid4().hex
    started = time.time()
    with _conn() as conn:
        conn.execute(
            "INSERT INTO bench_runs "
            "(id, dataset_id, judge, target, model_version, "
            " item_count, started_at, notes) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (rid, dataset_id, judge, target, model_version,
             len(items), started, notes),
        )

    scores: list[float] = []
    pass_count = 0
    fail_count = 0
    skipped = 0
    for item in items:
        try:
            actual = runner(item.prompt)
            if not isinstance(actual, dict):
                raise ValueError(
                    "runner must return dict, got "
                    f"{type(actual).__name__}",
                )
        except Exception as e:
            skipped += 1
            with _conn() as conn:
                conn.execute(
                    "INSERT INTO bench_results "
                    "(id, run_id, item_id, actual_json, "
                    " score, passed, reason, judged_at) "
                    "VALUES (?, ?, ?, ?, NULL, 0, ?, ?)",
                    (uuid.uuid4().hex, rid, item.id,
                     json.dumps({"error": str(e)}),
                     f"runner_error: {e}", time.time()),
                )
            continue
        score, reason = judge_fn(item.expected, actual, prompt=item.prompt)
        weighted = score * item.weight
        scores.append(weighted)
        passed = score >= PASS_THRESHOLD
        if passed:
            pass_count += 1
        else:
            fail_count += 1
        with _conn() as conn:
            conn.execute(
                "INSERT INTO bench_results "
                "(id, run_id, item_id, actual_json, "
                " score, passed, reason, judged_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (uuid.uuid4().hex, rid, item.id,
                 json.dumps(actual), weighted,
                 1 if passed else 0, reason, time.time()),
            )

    mean = round(statistics.mean(scores), 4) if scores else None
    p50 = (
        round(statistics.median(scores), 4) if scores else None
    )
    p90 = _percentile(scores, 0.90) if scores else None
    finished = time.time()
    with _conn() as conn:
        conn.execute(
            "UPDATE bench_runs SET "
            " pass_count = ?, fail_count = ?, skipped_count = ?, "
            " mean_score = ?, p50_score = ?, p90_score = ?, "
            " finished_at = ? "
            "WHERE id = ?",
            (pass_count, fail_count, skipped, mean, p50, p90,
             finished, rid),
        )
    return get_run(rid)  # type: ignore[return-value]


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * p
    f = int(k)
    if f == k:
        return round(s[int(k)], 4)
    return round(s[f] + (s[f + 1] - s[f]) * (k - f), 4)


def get_run(run_id: str) -> Run | None:
    with _conn() as conn:
        r = conn.execute(
            "SELECT id, dataset_id, judge, target, model_version, "
            "       item_count, pass_count, fail_count, "
            "       skipped_count, mean_score, p50_score, p90_score, "
            "       started_at, finished_at, notes "
            "FROM bench_runs WHERE id = ?", (run_id,),
        ).fetchone()
    if not r:
        return None
    return Run(
        id=r[0], dataset_id=r[1], judge=r[2], target=r[3],
        model_version=r[4], item_count=r[5],
        pass_count=r[6], fail_count=r[7], skipped_count=r[8],
        mean_score=r[9], p50_score=r[10], p90_score=r[11],
        started_at=r[12], finished_at=r[13], notes=r[14],
    )


def list_runs(
    *,
    target: str | None = None,
    dataset_id: str | None = None,
    limit: int = 20,
) -> list[Run]:
    limit = max(1, min(limit, 200))
    sql = (
        "SELECT id, dataset_id, judge, target, model_version, "
        "       item_count, pass_count, fail_count, "
        "       skipped_count, mean_score, p50_score, p90_score, "
        "       started_at, finished_at, notes "
        "FROM bench_runs WHERE 1=1"
    )
    params: list = []
    if target:
        sql += " AND target = ?"; params.append(target)
    if dataset_id:
        sql += " AND dataset_id = ?"; params.append(dataset_id)
    sql += " ORDER BY started_at DESC LIMIT ?"
    params.append(limit)
    with _conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [
        Run(
            id=r[0], dataset_id=r[1], judge=r[2], target=r[3],
            model_version=r[4], item_count=r[5],
            pass_count=r[6], fail_count=r[7], skipped_count=r[8],
            mean_score=r[9], p50_score=r[10], p90_score=r[11],
            started_at=r[12], finished_at=r[13], notes=r[14],
        )
        for r in rows
    ]


def trust_dashboard(*, since: float | None = None) -> dict:
    """Headline trust metric. Used by the v3.1 trust dashboard.
    Bottom-line: across the most recent published-dataset runs per
    target, what's the pass rate + mean score? This is what the
    review §4.3 + §24 calls 'measured + improved + publicly
    credible'."""
    cutoff = since or 0.0
    with _conn() as conn:
        rows = conn.execute(
            "SELECT target, "
            "       COUNT(*), "
            "       COALESCE(SUM(pass_count), 0), "
            "       COALESCE(SUM(fail_count), 0), "
            "       COALESCE(AVG(mean_score), 0) "
            "FROM bench_runs "
            "WHERE finished_at IS NOT NULL "
            "  AND started_at >= ? "
            "GROUP BY target",
            (cutoff,),
        ).fetchall()
    out = []
    for r in rows:
        total = (r[2] or 0) + (r[3] or 0)
        out.append({
            "target": r[0],
            "runs": r[1],
            "pass_count": int(r[2] or 0),
            "fail_count": int(r[3] or 0),
            "pass_rate": round((r[2] or 0) / total, 4) if total else 0.0,
            "mean_score": round(r[4] or 0, 4),
        })
    return {"targets": out, "pass_threshold": PASS_THRESHOLD}
