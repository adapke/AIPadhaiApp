"""prod-8 — Endpoint tier map regression.

Locks the distribution from the prod-8 audit. If a future PR adds,
removes, or reclassifies an endpoint, the counts drift and this
test fails — surfacing the change for explicit review.

The honest baseline at prod-8: **0 TIER_GATED endpoints** despite
the codebase having a `_require_tier()` helper. Every "paid"
feature is free for any signed-in user. This test makes that
visible — so adding a `_require_tier()` call somewhere on purpose
will appropriately fail this test and force a docs/pricing review.

To update the baseline (intentional change):
  1. python scripts/audit_endpoint_tiers.py --write
  2. Update EXPECTED_COUNTS / EXPECTED_TOTAL below
  3. Mention the count delta in the PR description
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


# Baseline captured at prod-8 (the audit-write run).
EXPECTED_TOTAL = 726
EXPECTED_COUNTS = {
    "ADMIN_ONLY":    5,
    "ANONYMOUS_OK":  499,
    "AUTH_REQUIRED": 16,
    "PUBLIC":        205,
    "UNKNOWN":       1,
    # NOTE: TIER_GATED intentionally absent (count == 0). When the
    # first endpoint adds `_require_tier(user, "Mx")`, this test
    # fails with a clear diff, and TIER_GATED gets added here.
}

# Drift tolerance — counts must match EXACTLY. A "small" change is
# still a change worth noticing. Update the baseline together with
# the PR that changes route registration.
ALLOWED_DRIFT = 0


def _load_map() -> dict:
    """Source of truth: the committed JSON file. We don't re-run the
    audit script in CI — the JSON is the contract."""
    path = REPO_ROOT / "data" / "endpoint_tier_map.json"
    assert path.is_file(), (
        f"endpoint tier map missing at {path}. "
        "Run `python scripts/audit_endpoint_tiers.py --write`."
    )
    return json.loads(path.read_text(encoding="utf-8"))


def test_total_endpoint_count_locked():
    """Catches silent route loss (router unregistered, slice deletion)
    AND silent route bloat (uncatalogued new endpoint)."""
    data = _load_map()
    delta = abs(data["total"] - EXPECTED_TOTAL)
    assert delta <= ALLOWED_DRIFT, (
        f"endpoint total drifted: expected {EXPECTED_TOTAL}, "
        f"got {data['total']}. If intentional, re-run "
        "`python scripts/audit_endpoint_tiers.py --write` and "
        "update EXPECTED_TOTAL in this test."
    )


def test_tier_class_distribution_locked():
    """Catches a misclassification — e.g. a paid feature accidentally
    falling into ANONYMOUS_OK because someone removed the gate."""
    data = _load_map()
    actual = data["counts"]
    # Add TIER_GATED key as 0 so the comparison is symmetric.
    actual_normalised = dict(actual)
    actual_normalised.setdefault("TIER_GATED", 0)
    expected_normalised = dict(EXPECTED_COUNTS)
    expected_normalised.setdefault("TIER_GATED", 0)
    assert actual_normalised == expected_normalised, (
        f"tier-class distribution drifted.\n"
        f"  expected: {expected_normalised}\n"
        f"  actual:   {actual_normalised}\n"
        "If intentional, re-run "
        "`python scripts/audit_endpoint_tiers.py --write` and "
        "update EXPECTED_COUNTS in this test."
    )


def test_no_tier_gated_endpoints_yet():
    """Surface the honest gap — at prod-8 NO endpoint gates by tier.
    This test exists to be deliberately deleted/inverted when the
    first paid feature lands. Don't quietly assume it's still true."""
    data = _load_map()
    tier_gated = [
        r for r in data["routes"] if r["tier_class"] == "TIER_GATED"
    ]
    assert tier_gated == [], (
        f"TIER_GATED endpoints found: "
        f"{[r['path'] for r in tier_gated]}. "
        "This test was written when there were zero such endpoints. "
        "Delete it (and the comment) once tier-gating starts shipping."
    )


def test_admin_only_endpoints_match_known_admin_paths():
    """Sanity: admin-only endpoints should look like admin endpoints
    (paths containing /admin or /llm — not random feature paths).
    Catches a misclassification where require_admin_role got dropped
    into a non-admin handler."""
    data = _load_map()
    admin_routes = [
        r for r in data["routes"] if r["tier_class"] == "ADMIN_ONLY"
    ]
    for r in admin_routes:
        p = r["path"]
        assert (
            "/admin" in p or "/llm/" in p or "/billing/" in p
            or "/orgs/" in p or "/exam-mode/" in p
        ), f"unexpected admin route: {p}"
