"""prod-131 / prod-132 — Tests for the teacher AI tools router.

Locks the contract for the two CK-12-inspired endpoints:

  POST /api/admin/teacher-tools/ai-resistant-assignment
  POST /api/admin/teacher-tools/adjust-reading-level

Both are admin-gated by the prod-9 router-level dep injection (paths
under `/api/admin/*`). We test:

  1. Stub-level (no Claude call): the module's `stub()` function
     produces a contract-shaped dict that downstream UIs can render.
  2. HTTP-level: anonymous gets 401/403; happy-path POST with
     monkeypatched Claude returns 200 + expected fields.
  3. Validation: missing topic → 400; empty text → 400;
     target_grade out of range → 400.

NO real Claude calls — all interactions go through monkeypatch.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import ClassVar

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


# ---------- Stub-level tests (no Claude) ----------


def test_stub_returns_contract_shape():
    """prod-131 — `stub()` is the no-Claude path. Contract must match
    what the HTTP endpoint returns so UIs render identically."""
    from padhai import ai_resistant_assignments as ar
    out = ar.stub(topic="Newton's first law")
    assert out.title
    assert out.instructions_md
    assert isinstance(out.questions, list)
    assert len(out.questions) >= 1
    for q in out.questions:
        assert "id" in q
        assert "marks" in q
        assert "prompt" in q
        assert "anti_cheat_pattern" in q


def test_stub_respects_count_parameter():
    """prod-131 — count param controls how many questions stub() returns."""
    from padhai import ai_resistant_assignments as ar
    out = ar.stub(topic="Photosynthesis", count=7)
    assert len(out.questions) == 7


def test_stub_marks_default_to_int():
    """prod-131 — every question must carry an integer `marks` field
    so the rubric arithmetic doesn't crash on float divisions."""
    from padhai import ai_resistant_assignments as ar
    out = ar.stub(topic="Algebra", count=4)
    for q in out.questions:
        assert isinstance(q["marks"], int)


def test_strip_to_json_handles_code_fences():
    """prod-131 — Claude sometimes wraps JSON in ```json ... ```.
    The defensive stripper must peel both backtick variants."""
    from padhai.ai_resistant_assignments import _strip_to_json
    raw_a = '```json\n{"a": 1}\n```'
    raw_b = '```\n{"b": 2}\n```'
    raw_c = '{"c": 3}'
    assert _strip_to_json(raw_a) == '{"a": 1}'
    assert _strip_to_json(raw_b) == '{"b": 2}'
    assert _strip_to_json(raw_c) == '{"c": 3}'


def test_generate_handles_malformed_json(monkeypatch):
    """prod-131 — When Claude returns garbage, raise ValueError
    cleanly instead of crashing the request handler."""
    from padhai import ai_resistant_assignments as ar
    from padhai import llm_call

    class FakeBlock:
        type = "text"
        text = "this is not JSON"

    class FakeResponse:
        content: ClassVar[list] = [FakeBlock()]

    class FakeResult:
        response = FakeResponse()
        call_id = "test"
        cost_inr_paise = 0
        model = "fake"

    def _fake_call(**_kwargs):
        return FakeResult()

    monkeypatch.setattr(llm_call, "call_claude", _fake_call)

    with pytest.raises(ValueError, match=r"malformed JSON|JSON"):
        ar.generate(topic="test", user_id="u1")


def test_generate_validates_questions_array(monkeypatch):
    """prod-131 — Claude must return a non-empty questions array;
    otherwise raise ValueError so the caller can show a fallback."""
    from padhai import ai_resistant_assignments as ar
    from padhai import llm_call

    class FakeBlock:
        type = "text"
        text = '{"title": "x", "instructions_md": "y"}'  # no questions

    class FakeResponse:
        content: ClassVar[list] = [FakeBlock()]

    class FakeResult:
        response = FakeResponse()
        call_id = "test"
        cost_inr_paise = 0
        model = "fake"

    def _fake_call_q(**_kwargs):
        return FakeResult()

    monkeypatch.setattr(llm_call, "call_claude", _fake_call_q)

    with pytest.raises(ValueError, match="questions"):
        ar.generate(topic="test", user_id="u1")


# ---------- HTTP-level tests ----------


def test_ai_resistant_endpoint_requires_admin():
    """prod-131 — endpoint is under /api/admin/* so the prod-9
    router-level admin dep injection applies. Anonymous → 401."""
    from padhai.web import app
    client = TestClient(app)
    r = client.post(
        "/api/admin/teacher-tools/ai-resistant-assignment",
        json={"topic": "test"},
    )
    assert r.status_code in (401, 403), (
        f"expected 401/403; got {r.status_code}"
    )


def test_reading_level_endpoint_requires_admin():
    """prod-132 — same admin-gate applies."""
    from padhai.web import app
    client = TestClient(app)
    r = client.post(
        "/api/admin/teacher-tools/adjust-reading-level",
        json={"text": "hi", "target_grade": 5},
    )
    assert r.status_code in (401, 403)


def test_ai_resistant_endpoint_validates_topic(monkeypatch):
    """prod-131 — missing/empty topic → 400 (not 502 or 500).
    Use dev-fallback admin mode so the router gate passes."""
    monkeypatch.setenv("PADHAI_SKIP_DOTENV", "1")
    monkeypatch.setenv(
        "PADHAI_JWT_SECRET",
        "test-secret-abcdef0123456789abcdef0123456789",
    )
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("PADHAI_SUPERUSER_EMAILS", raising=False)

    import importlib

    from padhai import auth as _auth
    from padhai import web as _web
    importlib.reload(_auth)
    importlib.reload(_web)

    client = TestClient(_web.app)
    import uuid
    email = f"teacher+{uuid.uuid4().hex[:8]}@example.com"
    sres = client.post(
        "/auth/signup",
        data={"email": email, "password": "Pass@12345", "terms_accepted": "true"},
    )
    if sres.status_code == 503:
        pytest.skip("auth not configured")
    tok = sres.json()["token"]

    r = client.post(
        "/api/admin/teacher-tools/ai-resistant-assignment",
        json={},  # missing topic
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 400


def test_reading_level_endpoint_validates_target_grade(monkeypatch):
    """prod-132 — target_grade out of [1..12] → 400."""
    monkeypatch.setenv("PADHAI_SKIP_DOTENV", "1")
    monkeypatch.setenv(
        "PADHAI_JWT_SECRET",
        "test-secret-abcdef0123456789abcdef0123456789",
    )
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("PADHAI_SUPERUSER_EMAILS", raising=False)

    import importlib

    from padhai import auth as _auth
    from padhai import web as _web
    importlib.reload(_auth)
    importlib.reload(_web)

    client = TestClient(_web.app)
    import uuid
    email = f"rl+{uuid.uuid4().hex[:8]}@example.com"
    sres = client.post(
        "/auth/signup",
        data={"email": email, "password": "Pass@12345", "terms_accepted": "true"},
    )
    if sres.status_code == 503:
        pytest.skip("auth not configured")
    tok = sres.json()["token"]

    # grade=99 is out of range
    r = client.post(
        "/api/admin/teacher-tools/adjust-reading-level",
        json={"text": "hello world", "target_grade": 99},
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 400

    # missing target_grade
    r = client.post(
        "/api/admin/teacher-tools/adjust-reading-level",
        json={"text": "hello world"},
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 400


def test_reading_level_endpoint_rejects_oversize_text(monkeypatch):
    """prod-132 — text > 8000 chars → 413 (avoid burning Claude tokens
    on accidental whole-document submissions)."""
    monkeypatch.setenv("PADHAI_SKIP_DOTENV", "1")
    monkeypatch.setenv(
        "PADHAI_JWT_SECRET",
        "test-secret-abcdef0123456789abcdef0123456789",
    )
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("PADHAI_SUPERUSER_EMAILS", raising=False)

    import importlib

    from padhai import auth as _auth
    from padhai import web as _web
    importlib.reload(_auth)
    importlib.reload(_web)

    client = TestClient(_web.app)
    import uuid
    email = f"oversize+{uuid.uuid4().hex[:8]}@example.com"
    sres = client.post(
        "/auth/signup",
        data={"email": email, "password": "Pass@12345", "terms_accepted": "true"},
    )
    if sres.status_code == 503:
        pytest.skip("auth not configured")
    tok = sres.json()["token"]

    r = client.post(
        "/api/admin/teacher-tools/adjust-reading-level",
        json={"text": "a" * 8001, "target_grade": 5},
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 413


def test_ai_resistant_endpoint_happy_path(monkeypatch):
    """prod-131 — full HTTP smoke with monkeypatched generator.
    Returns 200 + contract fields."""
    monkeypatch.setenv("PADHAI_SKIP_DOTENV", "1")
    monkeypatch.setenv(
        "PADHAI_JWT_SECRET",
        "test-secret-abcdef0123456789abcdef0123456789",
    )
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("PADHAI_SUPERUSER_EMAILS", raising=False)

    import importlib

    from padhai import auth as _auth
    from padhai import web as _web
    importlib.reload(_auth)
    importlib.reload(_web)

    # Replace the real generator with the stub
    from padhai import ai_resistant_assignments as ar
    monkeypatch.setattr(
        ar, "generate",
        lambda **kw: ar.stub(topic=kw.get("topic", "test"), count=kw.get("count", 3)),
    )

    client = TestClient(_web.app)
    import uuid
    email = f"hp+{uuid.uuid4().hex[:8]}@example.com"
    sres = client.post(
        "/auth/signup",
        data={"email": email, "password": "Pass@12345", "terms_accepted": "true"},
    )
    if sres.status_code == 503:
        pytest.skip("auth not configured")
    tok = sres.json()["token"]

    r = client.post(
        "/api/admin/teacher-tools/ai-resistant-assignment",
        json={
            "topic": "Newton's first law",
            "grade": 9,
            "subject": "physics",
            "board": "CBSE",
            "language": "en",
            "count": 4,
            "total_marks": 20,
            "difficulty": "medium",
        },
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert r.status_code == 200, r.text
    d = r.json()
    for key in (
        "title", "instructions_md", "questions", "rubric_md",
        "anti_cheat_techniques", "estimated_time_min",
        "grade", "subject", "language", "board",
    ):
        assert key in d, f"missing key: {key}"
    assert len(d["questions"]) == 4
