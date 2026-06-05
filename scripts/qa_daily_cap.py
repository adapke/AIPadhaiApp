"""QA — daily-cap helper enforces per-user budget across surfaces."""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ["PADHAI_DB_PATH"] = str(ROOT / "qa_cap.db")
qa_db = Path(os.environ["PADHAI_DB_PATH"])
if qa_db.exists():
    qa_db.unlink()
os.environ.pop("ANTHROPIC_API_KEY", None)

from padhai import (
    citations,
    doubt_clearing,
    essay_grader,
    llm_obs,
    mock_interview,
)

UID_FREE = f"qa-free-{uuid.uuid4().hex[:8]}"
UID_PREMIUM = f"qa-premium-{uuid.uuid4().hex[:8]}"
UID_ENTERPRISE = f"qa-ent-{uuid.uuid4().hex[:8]}"


def test_cap_table():
    assert llm_obs.daily_cap_paise("M1") == 0
    assert llm_obs.daily_cap_paise("M2") == 2000
    assert llm_obs.daily_cap_paise("M3") == 10000
    assert llm_obs.daily_cap_paise("M4") is None
    assert llm_obs.daily_cap_paise("M4b") is None
    assert llm_obs.daily_cap_paise(None) == 0
    assert llm_obs.daily_cap_paise("unknown") == 0
    print("  [OK] cap table — M1=0, M2=2000p, M3=10000p, M4*=None, default=0")


def test_check_anonymous():
    # No user_id -> never raises (anon paths handled via rate_limit)
    llm_obs.check_daily_cap(user_id=None, subscription_tier="M1")
    print("  [OK] anonymous bypasses cap check")


def test_check_free_tier_refuses():
    try:
        llm_obs.check_daily_cap(user_id=UID_FREE, subscription_tier="M1")
        raise AssertionError("expected BudgetExceeded for M1 user")
    except llm_obs.BudgetExceeded as e:
        assert e.reason == "premium_feature", e.reason
        print("  [OK] M1 user -> premium_feature (cap=0p, spent=0p)")


def test_check_premium_under_budget():
    # Spend INR5 (500p) on essay grading — well under M2's INR20 cap
    llm_obs.record_call(
        module="test", prompt_version="qa",
        model="claude-haiku-4-5-20251001",
        tokens_in=100, tokens_out=50, latency_ms=200,
        user_id=UID_PREMIUM, cost_inr_paise=500,
    )
    llm_obs.check_daily_cap(user_id=UID_PREMIUM, subscription_tier="M2")
    print("  [OK] M2 user @ INR5/20 budget — under cap, no raise")


def test_check_premium_over_budget():
    # Push spend over the M2 cap (INR20 = 2000p). Add another 1600p -> 2100p total.
    llm_obs.record_call(
        module="test", prompt_version="qa",
        model="claude-sonnet-4-6",
        tokens_in=4000, tokens_out=2000, latency_ms=2500,
        user_id=UID_PREMIUM, cost_inr_paise=1600,
    )
    try:
        llm_obs.check_daily_cap(user_id=UID_PREMIUM, subscription_tier="M2")
        raise AssertionError("expected BudgetExceeded for over-budget M2")
    except llm_obs.BudgetExceeded as e:
        assert e.reason == "over_budget", e.reason
        assert e.spent_today_paise >= e.cap_paise
        print(
            f"  [OK] M2 user over cap — reason={e.reason} "
            f"spent={e.spent_today_paise}p cap={e.cap_paise}p"
        )


def test_enterprise_uncapped():
    # Even with massive spend, M4 doesn't raise
    llm_obs.record_call(
        module="test", prompt_version="qa",
        model="claude-opus-4-7",
        tokens_in=100000, tokens_out=20000, latency_ms=5000,
        user_id=UID_ENTERPRISE, cost_inr_paise=200000,  # INR2000 spent
    )
    llm_obs.check_daily_cap(user_id=UID_ENTERPRISE, subscription_tier="M4b")
    print("  [OK] M4 enterprise — INR2000 spent, no cap, no raise")


def test_essay_grader_falls_back_under_budget():
    rubric = essay_grader.upsert_rubric(
        exam="cbse_class10_eng", paper="essay", topic="cap_qa",
        criteria=[{"name": "Thesis", "weight": 10,
                   "description": "Clear position"}],
        max_marks=10, created_by="qa",
    )
    sub = essay_grader.submit(
        user_id=UID_PREMIUM,  # over budget at this point
        rubric_id=rubric.id,
        text="A short paragraph about something. " * 4,
    )
    result = essay_grader.grade(sub.id, user_tier="M2")
    assert result.method.startswith("budget_over_budget"), result.method
    print(f"  [OK] essay_grader.grade refused -> method={result.method}")
    # Provenance row should also reflect the over_budget reason
    rows = citations.list_user_answers(user_id=UID_PREMIUM, surface="essay")
    assert any(r.fallback_reason and "budget" in r.fallback_reason for r in rows), \
        f"expected a budget-reason essay row, got: {[r.fallback_reason for r in rows]}"
    print("  [OK] essay_grader provenance row carries fallback_reason=budget_*")


def test_mock_interview_falls_back_under_budget():
    interview, _ = mock_interview.start(user_id=UID_PREMIUM, track="upsc_personality")
    res = mock_interview.submit_answer(
        interview_id=interview.id, turn_index=0,
        answer_text="I want to serve the people of India through public administration.",
        user_tier="M2",
    )
    method = res.feedback.get("method")
    assert method.startswith("budget_"), f"expected budget_*, got {method}"
    print(f"  [OK] mock_interview refused -> method={method}")


def test_doubt_falls_back_under_budget():
    d = doubt_clearing.submit(
        user_id=UID_PREMIUM,
        question_text="Solve for x: 2x + 3 = 11",
    )
    answered = doubt_clearing.answer_via_ai_vision(
        doubt_id=d.id, user_tier="M2",
    )
    rows = citations.list_user_answers(user_id=UID_PREMIUM, surface="doubt")
    last = rows[0] if rows else None
    assert last and last.fallback_reason and "budget" in last.fallback_reason, \
        f"expected budget fallback on doubt, got: {last and last.fallback_reason}"
    print(
        f"  [OK] doubt refused -> status={answered.status} "
        f"reason={last.fallback_reason}"
    )


def main():
    print("QA: daily-cap helper")
    print("-" * 60)
    try:
        test_cap_table()
        test_check_anonymous()
        test_check_free_tier_refuses()
        test_check_premium_under_budget()
        test_check_premium_over_budget()
        test_enterprise_uncapped()
        test_essay_grader_falls_back_under_budget()
        test_mock_interview_falls_back_under_budget()
        test_doubt_falls_back_under_budget()
    except AssertionError as e:
        print(f"  [FAIL] {e}", file=sys.stderr)
        return 1
    print("-" * 60)
    print("ALL daily-cap checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
