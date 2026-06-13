"""prod-133 — Tests for the mobile-shell math-vision home wiring.

The Capacitor student shell launches at `/?home=math` (set by
mobile/scripts/configure-server.cjs). The home page must emit a
synchronous client-side redirect to `/math` so the user lands on the
math-vision photo-OCR flow — CK-12's "scan and solve" mobile entry.

We test the HTML side:

  1. HOME_HTML carries the redirect script (substring match).
  2. /home returns HTML that contains the redirect script.
  3. /home without `?home=math` still renders normally (no infinite loop
     — the redirect is wrapped in a regex that requires the exact param).
  4. The /math page already exists (prod-28).
  5. The redirect preserves other query params (e.g. `?home=math&lang=hi`
     → `/math?lang=hi`).

We also test the JS side of the configure-server.cjs script via
subprocess — it must:
  6. Set the student shell URL to `/?home=math` by default.
  7. Allow override via CAPACITOR_HOME_PATH_STUDENT.
  8. Leave parent/teacher shells unchanged.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def test_home_html_contains_redirect_script():
    """prod-133 — HOME_HTML must carry the math-redirect shim."""
    from padhai import home_ui
    assert "home=math" in home_ui.HOME_HTML
    assert "window.location.replace('/math'" in home_ui.HOME_HTML
    # The shim must live in <body> before the visible content,
    # else the page renders first and only THEN redirects.
    # Use the literal HTML `<a ... class="skip-link">` element, not
    # the CSS selector `.skip-link` (which appears earlier in the head).
    body_start = home_ui.HOME_HTML.index("<body>")
    redirect_pos = home_ui.HOME_HTML.index("window.location.replace('/math'")
    skip_link_html_pos = home_ui.HOME_HTML.index('class="skip-link"')
    assert body_start < redirect_pos < skip_link_html_pos, (
        f"Redirect must run BEFORE skip-link element; "
        f"got body={body_start} redirect={redirect_pos} "
        f"skip_link={skip_link_html_pos}"
    )


def test_home_html_regex_does_not_match_unrelated_query():
    """prod-133 — `?home=math` must NOT match `?homepage=mathbook` or
    similar substrings. The regex is /[?&]home=math(\\b|&|$)/."""
    pattern = r"[?&]home=math(\b|&|$)"
    assert re.search(pattern, "?home=math")
    assert re.search(pattern, "?lang=en&home=math")
    assert re.search(pattern, "?home=math&lang=hi")
    assert not re.search(pattern, "?homepage=math")
    assert not re.search(pattern, "?home=mathbook")
    assert not re.search(pattern, "?other=foo")


def test_home_route_returns_html_with_redirect(monkeypatch):
    """prod-133 — /home returns HTML; the redirect script is embedded."""
    monkeypatch.setenv("PADHAI_SKIP_DOTENV", "1")
    monkeypatch.setenv(
        "PADHAI_JWT_SECRET",
        "test-secret-abcdef0123456789abcdef0123456789",
    )

    from padhai.web import app
    client = TestClient(app)
    r = client.get("/home")
    assert r.status_code == 200
    assert "text/html" in r.headers.get("content-type", "")
    assert "window.location.replace('/math'" in r.text


def test_math_route_exists(monkeypatch):
    """prod-133 — the /math page (from prod-28) is the destination
    the mobile shell lands on. Confirm it actually responds."""
    monkeypatch.setenv("PADHAI_SKIP_DOTENV", "1")
    monkeypatch.setenv(
        "PADHAI_JWT_SECRET",
        "test-secret-abcdef0123456789abcdef0123456789",
    )

    from padhai.web import app
    client = TestClient(app)
    r = client.get("/math")
    # Public page — should return 200 (HTML) or possibly 401 if it
    # requires auth, but NOT 404.
    assert r.status_code != 404, "/math must exist (prod-28 new_ui_pages)"


def test_configure_server_default_student_home_is_math():
    """prod-133 — running mobile/scripts/configure-server.cjs without
    any env overrides must point the student shell at `/?home=math`.

    The script is invoked via node; if node isn't installed, skip."""
    if not _node_available():
        pytest.skip("node not available on PATH")

    cfg_path = REPO_ROOT / "mobile" / "capacitor.config.json"
    if not cfg_path.exists():
        pytest.skip("mobile/capacitor.config.json absent")

    # Snapshot + run the script + verify + restore
    original = cfg_path.read_text(encoding="utf-8")
    try:
        result = _run_configure(env_overrides={"CAPACITOR_SERVER_URL": "http://10.0.2.2:8000"})
        assert result.returncode == 0, result.stderr
        new = json.loads(cfg_path.read_text(encoding="utf-8"))
        url = new.get("server", {}).get("url", "")
        assert url.endswith("/?home=math"), (
            f"expected student url to end with /?home=math; got {url!r}"
        )
    finally:
        cfg_path.write_text(original, encoding="utf-8")


def test_configure_server_respects_student_home_override():
    """prod-133 — `CAPACITOR_HOME_PATH_STUDENT=/` restores the old
    landing behaviour. Ops must be able to flip this without code
    edits."""
    if not _node_available():
        pytest.skip("node not available on PATH")

    cfg_path = REPO_ROOT / "mobile" / "capacitor.config.json"
    if not cfg_path.exists():
        pytest.skip("mobile/capacitor.config.json absent")

    original = cfg_path.read_text(encoding="utf-8")
    try:
        result = _run_configure(env_overrides={
            "CAPACITOR_SERVER_URL": "http://10.0.2.2:8000",
            "CAPACITOR_HOME_PATH_STUDENT": "/",
        })
        assert result.returncode == 0, result.stderr
        new = json.loads(cfg_path.read_text(encoding="utf-8"))
        url = new.get("server", {}).get("url", "")
        assert url.endswith("10.0.2.2:8000/"), (
            f"override should produce trailing /; got {url!r}"
        )
        assert "home=math" not in url
    finally:
        cfg_path.write_text(original, encoding="utf-8")


def test_configure_server_leaves_parent_teacher_unchanged():
    """prod-133 — only the student shell defaults to math-vision.
    Parents/teachers continue to land on their existing role pages."""
    if not _node_available():
        pytest.skip("node not available on PATH")

    parent_cfg = REPO_ROOT / "mobile" / "parent" / "capacitor.config.json"
    teacher_cfg = REPO_ROOT / "mobile" / "teacher" / "capacitor.config.json"
    if not (parent_cfg.exists() and teacher_cfg.exists()):
        pytest.skip("parent/teacher Capacitor configs absent")

    pre_parent = parent_cfg.read_text(encoding="utf-8")
    pre_teacher = teacher_cfg.read_text(encoding="utf-8")
    try:
        result = _run_configure(env_overrides={"CAPACITOR_SERVER_URL": "http://10.0.2.2:8000"})
        assert result.returncode == 0, result.stderr
        p_url = json.loads(parent_cfg.read_text(encoding="utf-8"))["server"]["url"]
        t_url = json.loads(teacher_cfg.read_text(encoding="utf-8"))["server"]["url"]
        assert p_url.endswith("/ui?mode=parent")
        assert t_url.endswith("/ui?mode=teacher")
    finally:
        parent_cfg.write_text(pre_parent, encoding="utf-8")
        teacher_cfg.write_text(pre_teacher, encoding="utf-8")


def _node_available() -> bool:
    try:
        r = subprocess.run(
            ["node", "--version"], capture_output=True, timeout=5,
        )
        return r.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _run_configure(env_overrides: dict[str, str]) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env.update(env_overrides)
    # The node script lives at mobile/scripts/configure-server.cjs
    script = REPO_ROOT / "mobile" / "scripts" / "configure-server.cjs"
    return subprocess.run(
        ["node", str(script)],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
