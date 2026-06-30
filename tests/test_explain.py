"""prod-194 — AI answer-explanation generator + backfill regression.

Pins: generate_explanation(client=fake) returns the model's text with no
real Claude call, the empty-question guard, and the question_bank
backfill helpers (set_explanation / list_without_explanation / coverage).
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


class _Resp:
    """Minimal stand-in for an Anthropic response (note padded text)."""
    class _Block:
        type = "text"
        text = "  Subtract 7 then divide by 3, so x = 5.  "

    class _Usage:
        input_tokens = 40
        output_tokens = 15
        cache_read_input_tokens = 0

    content = [_Block()]  # noqa: RUF012 — read-only test fixture
    usage = _Usage()


class _FakeClient:
    def __init__(self):
        self.calls: list[dict] = []
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return _Resp()


def test_generate_explanation_returns_model_text(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-not-real")
    from padhai import explain
    client = _FakeClient()
    out = explain.generate_explanation(
        question_text="If 3x + 7 = 22, what is x?",
        options=["3", "5", "7", "15"],
        correct_answer="B",
        subject="sat_math",
        client=client,
    )
    assert out == "Subtract 7 then divide by 3, so x = 5."  # stripped
    assert len(client.calls) == 1
    # The question + correct answer are forwarded in the user message.
    msg = client.calls[0]["messages"][0]["content"]
    assert "3x + 7 = 22" in msg
    assert "Correct answer: B" in msg


def test_generate_explanation_empty_question_raises():
    from padhai import explain
    with pytest.raises(ValueError):
        explain.generate_explanation(question_text="   ")


@pytest.fixture()
def temp_db(tmp_path, monkeypatch):
    db = tmp_path / "explain_test.db"
    monkeypatch.setenv("PADHAI_DB_PATH", str(db))
    monkeypatch.setenv("PADHAI_SKIP_DOTENV", "1")
    from padhai import db as _db
    from padhai import question_bank as _qb
    importlib.reload(_db)
    importlib.reload(_qb)
    yield db


def test_backfill_helpers_roundtrip(temp_db):  # noqa: ARG001 (fixture side-effect)
    from padhai import question_bank as qb
    q = qb.upsert(
        board="cbse", grade=10, subject="mathematics",
        question_text="What is 2 + 2?", options=["3", "4", "5", "6"],
        correct_answer="B", difficulty="easy",
    )
    # Freshly inserted -> no explanation -> appears in the backfill work list.
    assert any(m.id == q.id for m in qb.list_without_explanation(limit=50))
    assert qb.explanation_coverage_stats()["explained"] == 0

    assert qb.set_explanation(q.id, "2 + 2 = 4 by basic addition.") is True
    # Now explained -> gone from the list, reflected in coverage + readback.
    assert all(m.id != q.id for m in qb.list_without_explanation(limit=50))
    stats = qb.explanation_coverage_stats()
    assert stats["explained"] == 1
    assert stats["missing"] == 0
    assert qb.get_by_id(q.id).explanation == "2 + 2 = 4 by basic addition."
    # Curated explanations are never re-listed for backfill (the guard
    # that protects the SAT seed from being overwritten).
    assert qb.list_without_explanation(limit=50, board="cbse") == []
