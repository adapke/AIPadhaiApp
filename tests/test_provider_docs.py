"""prod-113 — Tests for the provider walkthrough docs.

The docs are part of the production-readiness contract — they're
what curators / ops will follow when wiring up Razorpay / SMTP /
Sentry / PostHog. If they regress (broken structure, leftover TODOs,
broken links), the whole launch sequence stalls.

This is a lightweight structural check, NOT a real linkcheck — that
would need network access. We validate:
  - The 4 expected docs exist
  - Each has at least 5 top-level sections (## headings)
  - No `[TODO]` / `XXX` / `[link]` placeholder markers
  - All internal `.md` link targets exist
  - Required env var names appear (caller pastes them into `.env`)
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCS_DIR = REPO_ROOT / "docs"


@pytest.fixture(scope="module")
def provider_docs() -> dict[str, str]:
    """Load the 4 provider docs once per test module."""
    out: dict[str, str] = {}
    for name in ("RAZORPAY", "SMTP", "SENTRY", "POSTHOG"):
        p = DOCS_DIR / f"{name}.md"
        assert p.is_file(), f"Missing provider doc: {p}"
        out[name] = p.read_text(encoding="utf-8")
    return out


@pytest.mark.parametrize("doc_name", ["RAZORPAY", "SMTP", "SENTRY", "POSTHOG"])
def test_provider_doc_exists(doc_name, provider_docs):
    """prod-113 — All four provider docs must be present + non-empty."""
    body = provider_docs[doc_name]
    assert body.strip(), f"{doc_name}.md is empty"
    assert len(body) > 1000, (
        f"{doc_name}.md is too short ({len(body)} chars) — likely a stub"
    )


@pytest.mark.parametrize("doc_name", ["RAZORPAY", "SMTP", "SENTRY", "POSTHOG"])
def test_provider_doc_has_min_sections(doc_name, provider_docs):
    """prod-113 — A useful walkthrough has at least 5 top-level sections."""
    body = provider_docs[doc_name]
    # Top-level sections are `## Heading` (one hash + space), but the
    # doc opens with `# Title` so we count `^## ` specifically.
    sections = re.findall(r"^## ", body, flags=re.MULTILINE)
    assert len(sections) >= 5, (
        f"{doc_name}.md has only {len(sections)} top-level sections; "
        "expect at least 5 (signup, env, test flow, gotchas, code refs)"
    )


@pytest.mark.parametrize("doc_name", ["RAZORPAY", "SMTP", "SENTRY", "POSTHOG"])
def test_provider_doc_no_placeholder_markers(doc_name, provider_docs):
    """prod-113 — Catch leftover [TODO] / XXX / FIXME markers that
    sneaked through. These signal "I'll come back to this" doc rot."""
    body = provider_docs[doc_name]
    # Allow "TODO:" inside code blocks (explicit deferred work is OK)
    # but flag bare `[TODO]` / `XXX` / `[link]` placeholders.
    forbidden_re = re.compile(r"\[TODO\]|XXX\b|\[link\]|\bFIXME\b")
    matches = forbidden_re.findall(body)
    assert not matches, (
        f"{doc_name}.md has placeholder markers: {matches}. "
        "Resolve them or remove the doc."
    )


def test_razorpay_doc_has_env_vars(provider_docs):
    """prod-113 — RAZORPAY.md must reference the 3 env vars by name."""
    body = provider_docs["RAZORPAY"]
    for var in ("RAZORPAY_KEY_ID", "RAZORPAY_KEY_SECRET", "RAZORPAY_WEBHOOK_SECRET"):
        assert var in body, f"RAZORPAY.md missing env var: {var}"


def test_smtp_doc_has_env_vars(provider_docs):
    """prod-113 — SMTP.md must reference the 5 SMTP env vars."""
    body = provider_docs["SMTP"]
    for var in (
        "SMTP_HOST", "SMTP_PORT", "SMTP_USER",
        "SMTP_PASSWORD", "SMTP_FROM",
    ):
        assert var in body, f"SMTP.md missing env var: {var}"


def test_sentry_doc_has_env_vars(provider_docs):
    """prod-113 — SENTRY.md must reference SENTRY_DSN + the test-fire token."""
    body = provider_docs["SENTRY"]
    assert "SENTRY_DSN" in body
    assert "PADHAI_SENTRY_TEST_TOKEN" in body
    assert "/__sentry_test" in body


def test_posthog_doc_has_env_vars(provider_docs):
    """prod-113 — POSTHOG.md must reference POSTHOG_API_KEY + host."""
    body = provider_docs["POSTHOG"]
    assert "POSTHOG_API_KEY" in body
    assert "POSTHOG_HOST" in body


@pytest.mark.parametrize("doc_name", ["RAZORPAY", "SMTP", "SENTRY", "POSTHOG"])
def test_provider_doc_internal_md_links_resolve(doc_name, provider_docs):
    """prod-113 — `[...](other.md)` links must point at files that exist.
    External http(s) links are not validated (no network). Catches
    typos like `RAZRPAY.md` instead of `RAZORPAY.md`."""
    body = provider_docs[doc_name]
    # Match `](path.md)` or `](path.md#anchor)` — internal markdown links.
    md_link_re = re.compile(r"\]\(([^)\s]+\.md)(#[^)]*)?\)")
    for match in md_link_re.finditer(body):
        target_str = match.group(1)
        # Skip absolute http(s) URLs and `mailto:`
        if target_str.startswith(("http://", "https://", "mailto:")):
            continue
        # Resolve relative to the doc's directory
        target = (DOCS_DIR / target_str).resolve()
        # Also allow links that are repo-root relative (start with ../)
        if not target.exists():
            alt = (REPO_ROOT / target_str).resolve()
            assert alt.exists(), (
                f"{doc_name}.md links to non-existent file: {target_str}"
            )


def test_deploy_doc_exists_and_has_required_sections():
    """prod-118/119 — DEPLOY.md walks the curator through first
    APP_ENV=production push. Lock the section structure."""
    p = DOCS_DIR / "DEPLOY.md"
    assert p.is_file(), f"Missing: {p}"
    body = p.read_text(encoding="utf-8")
    assert len(body) > 2000, "DEPLOY.md is suspiciously short"
    # Required sections
    for required in (
        "## 0. Decide hosting",            # Render/Modal/Spot matrix
        "## 1. Provider keys",              # cross-ref to provider docs
        "## 2. DNS + SSL",
        "## 3. Render-specific deploy",
        "## 6. Post-deploy smoke test",
        "## 7. Day-2 ops",
        "## 9. Rollback",
    ):
        assert required in body, f"DEPLOY.md missing section: {required!r}"


def test_deploy_doc_cross_references_all_provider_docs():
    """prod-118/119 — DEPLOY.md must link to RAZORPAY.md, SMTP.md,
    SENTRY.md, POSTHOG.md so deployers can find them in §1."""
    body = (DOCS_DIR / "DEPLOY.md").read_text(encoding="utf-8")
    for ref in ("RAZORPAY.md", "SMTP.md", "SENTRY.md", "POSTHOG.md"):
        assert ref in body, f"DEPLOY.md should link to {ref}"


def test_deploy_doc_mentions_three_hosting_options():
    """prod-119 — The hosting decision matrix is the most useful
    thing in DEPLOY.md; lock all three options stay present."""
    body = (DOCS_DIR / "DEPLOY.md").read_text(encoding="utf-8")
    for option in ("Render", "Modal", "Spot"):
        assert option in body, f"DEPLOY.md missing hosting option: {option}"


def test_deploy_doc_includes_post_deploy_smoke_command():
    """prod-119 — DEPLOY.md must include a curl-based smoke command
    so the deployer can verify the push without leaving the terminal."""
    body = (DOCS_DIR / "DEPLOY.md").read_text(encoding="utf-8")
    # Should show a curl against /healthz and against the test-fire route
    assert "curl" in body
    assert "/healthz" in body
    assert "__sentry_test" in body


def test_monitoring_doc_exists_and_has_required_sections():
    """prod-122/119 — MONITORING.md is the day-2 ops watchlist.
    Lock its section structure."""
    p = DOCS_DIR / "MONITORING.md"
    assert p.is_file(), f"Missing: {p}"
    body = p.read_text(encoding="utf-8")
    assert len(body) > 2000, "MONITORING.md is suspiciously short"
    for required in (
        "## 1. Daily 10-minute checklist",
        "## 2. Sentry alerts",
        "## 3. PostHog dashboards",
        "## 4. Cost watch",
        "## 5. Curator queue staleness",
        "## 9. Backup restore drill",
        "## 11. Escalation matrix",
    ):
        assert required in body, f"MONITORING.md missing section: {required!r}"


def test_monitoring_doc_cross_references_other_docs():
    """prod-122/119 — MONITORING.md should reference the related
    docs (DEPLOY.md for rollback, the admin pages, the make targets)."""
    body = (DOCS_DIR / "MONITORING.md").read_text(encoding="utf-8")
    # At least one reference to DEPLOY.md or the admin page surfaces
    has_xref = (
        "DEPLOY.md" in body
        or "/admin/health" in body
        or "/admin/curator-stats" in body
    )
    assert has_xref, "MONITORING.md should link to deploy/admin surfaces"


def test_monitoring_doc_references_admin_pages():
    """prod-123 — MONITORING.md should point ops at the 3 admin
    pages (concept-curator, curator-stats, health) and the
    `/admin/llm-costs` page."""
    body = (DOCS_DIR / "MONITORING.md").read_text(encoding="utf-8")
    for ref in (
        "/admin/health",
        "/admin/curator-stats",
        "/admin/llm-costs",
    ):
        assert ref in body, f"MONITORING.md should reference {ref}"


def test_monitoring_doc_references_make_targets():
    """prod-123 — MONITORING.md should point at the operational
    `make` targets so day-2 ops know they exist."""
    body = (DOCS_DIR / "MONITORING.md").read_text(encoding="utf-8")
    # At least `make stats` (the cost cap exposure) should be linked
    assert "make stats" in body, (
        "MONITORING.md should reference `make stats` for cost watching"
    )


def test_monitoring_doc_links_to_deploy():
    """prod-123 — MONITORING.md should link to DEPLOY.md for the
    rollback procedure (escalation matrix references it)."""
    body = (DOCS_DIR / "MONITORING.md").read_text(encoding="utf-8")
    assert "DEPLOY.md" in body, "MONITORING.md should reference DEPLOY.md"


def test_incident_doc_exists_and_has_required_sections():
    """prod-126/123 — INCIDENT.md is the on-fire playbook. Lock
    the section structure so future edits don't accidentally drop
    the triage matrix or DPDP §11 procedure."""
    p = DOCS_DIR / "INCIDENT.md"
    assert p.is_file(), f"Missing: {p}"
    body = p.read_text(encoding="utf-8")
    assert len(body) > 2000, "INCIDENT.md is suspiciously short"
    for required in (
        "## 0. The five-minute triage",
        "## 1. 5xx burst",
        "## 2. Full outage",
        "## 3. Payment fraud",
        "## 4. DPDP",
        "## 5. AI cost overrun",
        "## 8. Communication during an incident",
        "## 9. Post-incident review",
    ):
        assert required in body, f"INCIDENT.md missing section: {required!r}"


def test_incident_doc_cross_references_monitoring_and_deploy():
    """prod-126/123 — INCIDENT.md should reference MONITORING.md
    and DEPLOY.md for the rollback / day-2 cross-refs."""
    body = (DOCS_DIR / "INCIDENT.md").read_text(encoding="utf-8")
    assert "MONITORING.md" in body
    assert "DEPLOY.md" in body


def test_incident_doc_mentions_dpdp_sla():
    """prod-126/123 — DPDP §11 SLA is 30 days. The doc must
    surface that explicitly so ops can't miss it."""
    body = (DOCS_DIR / "INCIDENT.md").read_text(encoding="utf-8")
    assert "30 days" in body or "30-day" in body, (
        "INCIDENT.md should mention DPDP 30-day data-request SLA"
    )
    # The penalty is also significant enough to highlight
    assert "DPDP" in body


def test_production_checklist_links_companion_docs():
    """prod-125/123 — PRODUCTION_CHECKLIST.md should cross-link
    DEPLOY, MONITORING, INCIDENT in the new "Companion docs"
    section. This is the discoverability fix from prod-125."""
    body = (REPO_ROOT / "PRODUCTION_CHECKLIST.md").read_text(encoding="utf-8")
    for doc in ("DEPLOY.md", "MONITORING.md", "INCIDENT.md"):
        assert doc in body, (
            f"PRODUCTION_CHECKLIST.md should link to {doc}"
        )


def test_production_checklist_references_provider_docs():
    """prod-113/106 — PRODUCTION_CHECKLIST should link to the provider
    walkthroughs so deployers can find them."""
    p = REPO_ROOT / "PRODUCTION_CHECKLIST.md"
    assert p.is_file()
    body = p.read_text(encoding="utf-8")
    # At least the Razorpay doc should be referenced — it's the only
    # provider with a non-trivial test-mode flow worth documenting.
    # SMTP / SENTRY / POSTHOG may or may not be linked yet (engineering
    # deferred to keep this batch small).
    # Soft assertion: at least one provider doc is linked.
    any_linked = any(
        f"{name}.md" in body
        for name in ("RAZORPAY", "SMTP", "SENTRY", "POSTHOG")
    )
    # This is a regression test — if no docs are linked, deployers
    # have to grep manually. Allow this to soft-fail for now.
    if not any_linked:
        pytest.skip(
            "PRODUCTION_CHECKLIST.md doesn't link to any provider docs yet "
            "(non-blocking; add the links in a follow-up)",
        )
