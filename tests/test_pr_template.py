"""prod-114 — Tests for the PR template + Honest-gaps CI workflow.

The CI workflow itself runs on GitHub; we can't run it locally. What
we CAN test:
  - The template file exists + contains the expected sections
  - The workflow YAML is valid
  - The regex the workflow uses parses correctly for a few sample
    PR bodies (good / missing-section / empty-section)
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PR_TEMPLATE = REPO_ROOT / ".github" / "PULL_REQUEST_TEMPLATE.md"
PR_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "pr-honest-gaps.yml"


def test_pr_template_exists():
    """prod-110/114 — The template must exist + be non-empty."""
    assert PR_TEMPLATE.is_file(), f"Missing: {PR_TEMPLATE}"
    body = PR_TEMPLATE.read_text(encoding="utf-8")
    assert len(body) > 200, "PR template is suspiciously short"


def test_pr_template_has_required_sections():
    """prod-110/114 — The template must include all 4 sections the
    workflow + reviewers expect."""
    body = PR_TEMPLATE.read_text(encoding="utf-8")
    for section in ("## Summary", "## Test plan", "## Honest gaps"):
        assert section in body, (
            f"PR template missing required section: {section!r}"
        )


def test_pr_template_includes_make_verify_checkbox():
    """prod-110/114 — The Test plan section should include `make verify`
    as a checkbox so contributors don't forget."""
    body = PR_TEMPLATE.read_text(encoding="utf-8")
    assert "make verify" in body, (
        "PR template should reference `make verify` in the test plan"
    )


def test_pr_workflow_yaml_parses():
    """prod-114 — The CI workflow YAML must be valid."""
    import yaml
    assert PR_WORKFLOW.is_file(), f"Missing: {PR_WORKFLOW}"
    body = PR_WORKFLOW.read_text(encoding="utf-8")
    try:
        data = yaml.safe_load(body)
    except yaml.YAMLError as e:
        raise AssertionError(f"YAML parse failed: {e}") from e
    # Sanity: required top-level keys.
    # PyYAML parses bare `on:` as Python `True` (boolean) — accept both.
    assert "name" in data
    on_key = "on" if "on" in data else (True if True in data else None)
    assert on_key is not None, "workflow missing `on:` trigger"
    assert "jobs" in data
    assert "honest-gaps" in data["jobs"]


def test_pr_workflow_triggers_on_pull_request():
    """prod-114 — Workflow must fire on PR open/edit/reopen/sync."""
    body = PR_WORKFLOW.read_text(encoding="utf-8")
    assert "pull_request" in body
    for trigger in ("opened", "edited", "reopened", "synchronize"):
        assert trigger in body, f"workflow missing trigger: {trigger}"


def test_workflow_regex_matches_good_pr_body():
    """prod-114 — The regex the workflow uses must accept a valid PR body."""
    good_body = """
## Summary

Adds a new endpoint.

## Test plan

- [x] make verify green

## Honest gaps

Did not add a Cypress spec; the unit test covers the same surface.
"""
    heading_re = re.compile(r"^## Honest gaps\s*$", re.MULTILINE)
    assert heading_re.search(good_body), (
        "good PR body should match the workflow heading regex"
    )
    section_re = re.compile(
        r"## Honest gaps\s*\n([\s\S]*?)(?=^## |^---|\Z)",
        re.MULTILINE,
    )
    match = section_re.search(good_body)
    assert match is not None
    content = re.sub(r"<!--[\s\S]*?-->", "", match.group(1)).strip()
    assert len(content) >= 5, "section content should be non-empty"


def test_workflow_regex_rejects_missing_section():
    """prod-114 — A PR body without the section should not match."""
    bad_body = """
## Summary

Adds a thing.

## Test plan

- [x] make verify green
"""
    heading_re = re.compile(r"^## Honest gaps\s*$", re.MULTILINE)
    assert heading_re.search(bad_body) is None, (
        "missing-section PR body should NOT match"
    )


def test_workflow_regex_rejects_empty_section():
    """prod-114 — A PR body with the heading but no content (only the
    template's HTML comments) should be flagged as empty."""
    empty_body = """
## Summary

Hi.

## Honest gaps

<!--
What is NOT in this PR that arguably should be?
-->

## Screenshots
"""
    heading_re = re.compile(r"^## Honest gaps\s*$", re.MULTILINE)
    assert heading_re.search(empty_body), "should find heading"
    section_re = re.compile(
        r"## Honest gaps\s*\n([\s\S]*?)(?=^## |^---|\Z)",
        re.MULTILINE,
    )
    match = section_re.search(empty_body)
    assert match is not None
    content = re.sub(r"<!--[\s\S]*?-->", "", match.group(1))
    content = re.sub(r"^\s*$", "", content, flags=re.MULTILINE).strip()
    assert len(content) < 5, (
        "empty section (only HTML comments) should fail the content check"
    )


def test_workflow_exempts_bot_actors():
    """prod-114 — Dependabot / Renovate / GitHub Actions PRs are exempt
    so we don't fail their auto-bumps. Verify the exemption list is
    present in the workflow."""
    body = PR_WORKFLOW.read_text(encoding="utf-8")
    for actor in ("dependabot", "renovate"):
        assert actor in body, (
            f"workflow should exempt {actor!r} from the honest-gaps check"
        )
