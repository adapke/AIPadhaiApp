"""H1 — SAML 2.0 SSO.

Beyond Google/Microsoft OIDC (E7 in v0.11), some enterprise buyers
standardize on SAML 2.0 against their Okta / AzureAD / Ping / OneLogin
IdP. This module is the Service Provider side: metadata generation,
ACS endpoint logic, attribute mapping to our user model.

Design follows the H1 scoping in ROADMAP_V2.md: per-org SAML config
(an org admin uploads IdP metadata XML; we generate matching SP
metadata); attribute mapping configurable per org (different IdPs use
different claim names — `mail` vs `email`, `displayName` vs `cn`).

Library dependency: `python3-saml` (OneLogin's lib). Same pattern as
G2 — imported lazily so this module is importable without the lib
installed. When `python3-saml` is missing we degrade to a clear 503
on the SAML endpoints.

Schema is additive — one new table:

  org_saml_configs    one row per org with SAML enabled
                      stores IdP metadata + attribute map + state
"""

from __future__ import annotations

import base64
import json
import os
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS org_saml_configs (
    id              TEXT PRIMARY KEY,
    org_id          TEXT NOT NULL,
    idp_entity_id   TEXT NOT NULL,
    idp_sso_url     TEXT NOT NULL,
    idp_slo_url     TEXT,
    idp_certificate TEXT NOT NULL,            -- PEM, no BEGIN/END markers
    sp_entity_id    TEXT NOT NULL,
    attribute_map   TEXT,                      -- JSON: {our_field: idp_attr_name}
    enabled         INTEGER NOT NULL DEFAULT 1,
    created_at      REAL NOT NULL,
    updated_at      REAL NOT NULL,
    UNIQUE (org_id)
);
CREATE INDEX IF NOT EXISTS idx_saml_org ON org_saml_configs(org_id);
"""


# Default attribute mapping — works against Okta + AzureAD defaults.
# Org admin can override during config upload.
DEFAULT_ATTRIBUTE_MAP = {
    "email": "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress",
    "name": "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/name",
    "first_name": "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/givenname",
    "last_name": "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/surname",
}


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


def is_library_available() -> bool:
    """python3-saml availability gate. Used by endpoints to return
    503 cleanly when the lib isn't installed."""
    try:
        import onelogin.saml2.utils  # noqa: F401
        return True
    except ImportError:
        return False


@dataclass(frozen=True)
class SAMLConfig:
    id: str
    org_id: str
    idp_entity_id: str
    idp_sso_url: str
    idp_slo_url: str | None
    idp_certificate: str
    sp_entity_id: str
    attribute_map: dict
    enabled: bool
    created_at: float
    updated_at: float


def upsert(
    *,
    org_id: str,
    idp_entity_id: str,
    idp_sso_url: str,
    idp_certificate: str,
    sp_entity_id: str,
    idp_slo_url: str | None = None,
    attribute_map: dict | None = None,
    enabled: bool = True,
) -> SAMLConfig:
    """Insert or update SAML config for an org. PEM cert is stored
    without BEGIN/END markers (matches python3-saml's expected format).

    UNIQUE(org_id) means one config per org. To rotate certs, just
    call upsert again with the new value."""
    amap = attribute_map or DEFAULT_ATTRIBUTE_MAP
    now = time.time()
    cert = (idp_certificate or "").replace(
        "-----BEGIN CERTIFICATE-----", ""
    ).replace("-----END CERTIFICATE-----", "").strip()
    with _conn() as conn:
        existing = conn.execute(
            "SELECT id FROM org_saml_configs WHERE org_id = ?", (org_id,),
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE org_saml_configs SET "
                "idp_entity_id = ?, idp_sso_url = ?, idp_slo_url = ?, "
                "idp_certificate = ?, sp_entity_id = ?, "
                "attribute_map = ?, enabled = ?, updated_at = ? "
                "WHERE org_id = ?",
                (idp_entity_id, idp_sso_url, idp_slo_url, cert,
                 sp_entity_id, json.dumps(amap), 1 if enabled else 0,
                 now, org_id),
            )
        else:
            conn.execute(
                "INSERT INTO org_saml_configs "
                "(id, org_id, idp_entity_id, idp_sso_url, idp_slo_url, "
                " idp_certificate, sp_entity_id, attribute_map, enabled, "
                " created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (uuid.uuid4().hex, org_id, idp_entity_id, idp_sso_url,
                 idp_slo_url, cert, sp_entity_id, json.dumps(amap),
                 1 if enabled else 0, now, now),
            )
    return get_config(org_id)  # type: ignore[return-value]


def get_config(org_id: str) -> SAMLConfig | None:
    try:
        with _conn() as conn:
            r = conn.execute(
                "SELECT id, org_id, idp_entity_id, idp_sso_url, idp_slo_url, "
                "       idp_certificate, sp_entity_id, attribute_map, "
                "       enabled, created_at, updated_at "
                "FROM org_saml_configs WHERE org_id = ?",
                (org_id,),
            ).fetchone()
    except sqlite3.OperationalError:
        return None
    if not r:
        return None
    return SAMLConfig(
        id=r[0], org_id=r[1], idp_entity_id=r[2], idp_sso_url=r[3],
        idp_slo_url=r[4], idp_certificate=r[5], sp_entity_id=r[6],
        attribute_map=json.loads(r[7] or "{}"),
        enabled=bool(r[8]), created_at=r[9], updated_at=r[10],
    )


def sp_metadata_xml(config: SAMLConfig, *, acs_url: str,
                    slo_url: str | None = None) -> str:
    """Generate SP metadata XML for the IdP to consume. Minimal valid
    SAML 2.0 metadata document — fields any IdP needs.

    Real prod SP metadata typically includes signing/encryption
    certificates; for v1.3 we operate in HTTP-POST binding without SP
    signing (most IdPs accept this for non-financial-grade apps)."""
    slo_block = ""
    if slo_url:
        slo_block = (
            f'\n  <md:SingleLogoutService '
            f'Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST" '
            f'Location="{slo_url}"/>'
        )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<md:EntityDescriptor xmlns:md="urn:oasis:names:tc:SAML:2.0:metadata"
                     entityID="{config.sp_entity_id}">
  <md:SPSSODescriptor AuthnRequestsSigned="false"
                      WantAssertionsSigned="true"
                      protocolSupportEnumeration="urn:oasis:names:tc:SAML:2.0:protocol">{slo_block}
    <md:AssertionConsumerService
      Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST"
      Location="{acs_url}" index="0" isDefault="true"/>
    <md:NameIDFormat>urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress</md:NameIDFormat>
  </md:SPSSODescriptor>
</md:EntityDescriptor>
"""


def parse_assertion(
    *,
    config: SAMLConfig,
    saml_response_b64: str,
    request_url: str,
) -> dict:
    """Validate the SAML assertion + extract attributes mapped to our
    user fields. Returns {email, name, name_id, attributes}.

    Real validation requires python3-saml (signature check, time
    skew, audience restriction). When the lib isn't available we
    raise — caller surfaces 503.

    For v1.3 the validation path uses python3-saml's
    `OneLogin_Saml2_Auth.process_response` shape; full settings dict
    construction lives at the call site in web.py."""
    if not is_library_available():
        raise RuntimeError(
            "python3-saml not installed — pip install python3-saml "
            "and redeploy to enable SAML"
        )
    from onelogin.saml2.auth import OneLogin_Saml2_Auth  # type: ignore

    settings = _build_onelogin_settings(config, request_url=request_url)
    # python3-saml expects request data via its dict shape; this is
    # the bridge that web.py wires up.
    request_data = {
        "https": "on",
        "http_host": _host_from_url(request_url),
        "server_port": _port_from_url(request_url),
        "script_name": _path_from_url(request_url),
        "get_data": {},
        "post_data": {"SAMLResponse": saml_response_b64},
    }
    auth = OneLogin_Saml2_Auth(request_data, settings)
    auth.process_response()
    errors = auth.get_errors()
    if errors:
        raise ValueError(f"SAML response invalid: {errors}")
    if not auth.is_authenticated():
        raise ValueError("SAML response not authenticated")
    attrs = auth.get_attributes()
    name_id = auth.get_nameid()
    # Map IdP-side attribute names to our internal user fields
    mapped = {}
    for our_field, idp_attr in config.attribute_map.items():
        vals = attrs.get(idp_attr) or []
        if vals:
            mapped[our_field] = vals[0]
    # Default email fallback to name_id (email-format NameID is the
    # SAML convention for SaaS apps)
    if "email" not in mapped and "@" in (name_id or ""):
        mapped["email"] = name_id
    return {
        "email": mapped.get("email"),
        "name": mapped.get("name"),
        "name_id": name_id,
        "attributes": attrs,
    }


def _build_onelogin_settings(config: SAMLConfig, *, request_url: str) -> dict:
    """python3-saml settings dict. Hard-codes HTTP-POST binding; no
    SP signing (matches sp_metadata_xml above)."""
    return {
        "strict": True,
        "debug": False,
        "sp": {
            "entityId": config.sp_entity_id,
            "assertionConsumerService": {
                "url": request_url,
                "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST",
            },
            "NameIDFormat": "urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress",
            "x509cert": "",
            "privateKey": "",
        },
        "idp": {
            "entityId": config.idp_entity_id,
            "singleSignOnService": {
                "url": config.idp_sso_url,
                "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect",
            },
            "singleLogoutService": (
                {
                    "url": config.idp_slo_url,
                    "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect",
                } if config.idp_slo_url else {}
            ),
            "x509cert": config.idp_certificate,
        },
    }


def _host_from_url(url: str) -> str:
    from urllib.parse import urlparse
    return urlparse(url).hostname or ""


def _port_from_url(url: str) -> str:
    from urllib.parse import urlparse
    p = urlparse(url)
    return str(p.port or (443 if p.scheme == "https" else 80))


def _path_from_url(url: str) -> str:
    from urllib.parse import urlparse
    return urlparse(url).path or "/"
