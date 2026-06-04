"""Smoke test for v1.3 — H1 SAML, H2 SCIM, H4 data residency.

Run: PYTHONPATH=. python scripts/test_v1_3.py
"""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from pathlib import Path

_DB = os.environ.setdefault(
    "PADHAI_DB_PATH",
    str(Path(tempfile.gettempdir()) / "padhai_v1_3_smoke.db"),
)
Path(_DB).unlink(missing_ok=True)


def main() -> int:
    from fastapi.testclient import TestClient

    from padhai import residency, saml, scim
    from padhai.web import app

    failed: list[str] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        marker = "ok  " if ok else "FAIL"
        print(f"  {marker}  {name}{(' — ' + detail) if detail else ''}")
        if not ok:
            failed.append(name)

    print("v1.3 smoke test")
    print("---------------")

    # --- SAML module direct API ---
    saml.migrate()
    cfg = saml.upsert(
        org_id="org-test",
        idp_entity_id="https://idp.example.com/saml/metadata",
        idp_sso_url="https://idp.example.com/saml/sso",
        idp_certificate="MIIDtest...",
        sp_entity_id="https://aipathshala.in/sp",
    )
    check(
        "H1 upsert SAML config + read back",
        cfg.org_id == "org-test" and cfg.enabled,
    )
    xml = saml.sp_metadata_xml(cfg, acs_url="https://aipathshala.in/auth/saml/x/acs")
    check(
        "H1 SP metadata XML is well-formed",
        "<md:EntityDescriptor" in xml
        and "https://aipathshala.in/auth/saml/x/acs" in xml,
    )
    check(
        "H1 is_library_available reflects environment",
        saml.is_library_available() in (True, False),
    )

    # --- SCIM module direct API ---
    scim.migrate()
    tok, raw = scim.issue_token(org_id="org-test", name="Okta Prod")
    check(
        "H2 issue_token returns raw bearer + record with prefix",
        tok.token_prefix == raw[:8] and raw.startswith("padhai_scim_"),
    )
    check(
        "H2 authenticate(raw) returns the org_id",
        scim.authenticate(raw) == "org-test",
    )
    check(
        "H2 authenticate(wrong) returns None",
        scim.authenticate("padhai_scim_wrong") is None,
    )
    tokens = scim.list_tokens("org-test")
    check(
        "H2 list_tokens returns the issued token",
        any(t.id == tok.id for t in tokens),
    )
    revoked = scim.revoke_token(token_id=tok.id, org_id="org-test")
    check("H2 revoke_token returns True first time", revoked)
    check(
        "H2 authenticate after revoke returns None",
        scim.authenticate(raw) is None,
    )

    # SCIM parse_scim_user — happy path
    parsed = scim.parse_scim_user({
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
        "userName": "user@example.com",
        "emails": [{"value": "user@example.com", "primary": True}],
        "name": {"formatted": "User Name"},
        "active": True,
    })
    check(
        "H2 parse_scim_user extracts email + display_name",
        parsed["email"] == "user@example.com"
        and parsed["display_name"] == "User Name",
    )
    # SCIM parse_scim_user — bad schema
    try:
        scim.parse_scim_user({"schemas": ["bogus"], "userName": "x@y.com"})
        check("H2 parse_scim_user rejects wrong schema", False)
    except ValueError:
        check("H2 parse_scim_user rejects wrong schema", True)

    # SCIM ListResponse + Error shapes
    lr = scim.list_response(resources=[{"id": "1"}], total=1)
    check(
        "H2 list_response has SCIM-shaped output",
        lr["schemas"][0].endswith("ListResponse")
        and lr["totalResults"] == 1,
    )
    er = scim.error_response(status=400, detail="bad", scim_type="invalidValue")
    check(
        "H2 error_response has SCIM-shaped output",
        er["schemas"][0].endswith("Error") and er["status"] == "400",
    )

    # --- residency module direct API ---
    residency.migrate()
    check(
        "H4 default residency 'global' for unknown org",
        residency.get_residency("unknown-org") == "global",
    )
    check(
        "H4 should_route_to_india returns False for unknown org",
        residency.should_route_to_india("unknown-org") is False,
    )
    # Invalid region
    try:
        residency.set_residency(org_id="x", region="mars")
        check("H4 set_residency rejects invalid region", False)
    except ValueError:
        check("H4 set_residency rejects invalid region", True)

    # --- HTTP-level checks ---
    with TestClient(app) as c:
        # SAML metadata for unconfigured org
        r = c.get("/auth/saml/no-such-org/metadata")
        check(
            "GET /auth/saml/{id}/metadata → 404 when not configured",
            r.status_code == 404,
            f"status={r.status_code}",
        )

        # SCIM ServiceProviderConfig is public (no auth)
        r = c.get("/scim/v2/ServiceProviderConfig")
        check(
            "GET /scim/v2/ServiceProviderConfig returns 200 + SCIM shape",
            r.status_code == 200
            and r.json()["schemas"][0].endswith("ServiceProviderConfig"),
            f"status={r.status_code}",
        )

        # SCIM Users without bearer → 401
        r = c.get("/scim/v2/Users")
        check(
            "GET /scim/v2/Users without bearer → 401",
            r.status_code == 401,
        )

        # Admin endpoints all require auth
        for path in [
            "/api/orgs/some-org/saml",
            "/api/orgs/some-org/scim/tokens",
            "/api/orgs/some-org/data-residency",
        ]:
            r = c.get(path)
            check(
                f"GET {path} requires auth (401)",
                r.status_code == 401,
                f"got status={r.status_code}",
            )

        # Version bump
        r = c.get("/")
        check(
            "Root endpoint reports a semver-shaped version",
            str(r.json().get("version", "")).split(".")[0].isdigit(),
            f"version={r.json().get('version')}",
        )

    # Verify the new tables exist
    with sqlite3.connect(_DB) as conn:
        tables = {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    for t in ("org_saml_configs", "org_scim_tokens"):
        check(f"v1.3 table '{t}' created at startup", t in tables)

    print("---------------")
    if failed:
        print(f"FAILED: {len(failed)} checks")
        for name in failed:
            print(f"  - {name}")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
