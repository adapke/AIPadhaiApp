"""Auth endpoint tests: signup, login, token validation, edge cases."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from .conftest import auth_headers, random_email, signup


# ---------- Signup -----------------------------------------------------------

class TestSignup:
    def test_signup_success(self, client: TestClient):
        email = random_email()
        r = client.post("/auth/signup", data={
            "email": email,
            "password": "Secure99!",
            "terms_accepted": "true",
        })
        assert r.status_code == 200
        body = r.json()
        assert body["email"] == email
        assert body["token"] is not None
        assert body["subscription_tier"] == "M1"

    def test_signup_requires_terms_accepted(self, client: TestClient):
        r = client.post("/auth/signup", data={
            "email": random_email(),
            "password": "Secure99!",
            # terms_accepted omitted → defaults to False
        })
        assert r.status_code == 400
        assert "Terms" in r.json()["detail"]

    def test_signup_rejects_terms_false(self, client: TestClient):
        r = client.post("/auth/signup", data={
            "email": random_email(),
            "password": "Secure99!",
            "terms_accepted": "false",
        })
        assert r.status_code == 400

    def test_signup_duplicate_email(self, client: TestClient):
        email = random_email()
        client.post("/auth/signup", data={
            "email": email, "password": "Secure99!", "terms_accepted": "true",
        })
        r = client.post("/auth/signup", data={
            "email": email, "password": "Secure99!", "terms_accepted": "true",
        })
        assert r.status_code == 409

    def test_signup_invalid_email(self, client: TestClient):
        r = client.post("/auth/signup", data={
            "email": "notanemail",
            "password": "Secure99!",
            "terms_accepted": "true",
        })
        assert r.status_code == 400

    def test_signup_weak_password_no_digit(self, client: TestClient):
        r = client.post("/auth/signup", data={
            "email": random_email(),
            "password": "NoDigitHere!",
            "terms_accepted": "true",
        })
        assert r.status_code == 400

    def test_signup_weak_password_no_letter(self, client: TestClient):
        r = client.post("/auth/signup", data={
            "email": random_email(),
            "password": "12345678",
            "terms_accepted": "true",
        })
        assert r.status_code == 400

    def test_signup_weak_password_whitespace_only(self, client: TestClient):
        r = client.post("/auth/signup", data={
            "email": random_email(),
            "password": "        ",
            "terms_accepted": "true",
        })
        # 8 spaces: has length but no letter+digit and is all whitespace
        assert r.status_code in (400, 422)

    def test_signup_too_short_password(self, client: TestClient):
        r = client.post("/auth/signup", data={
            "email": random_email(),
            "password": "Ab1!",
            "terms_accepted": "true",
        })
        assert r.status_code in (400, 422)


# ---------- Login ------------------------------------------------------------

class TestLogin:
    def test_login_success(self, client: TestClient):
        email, _ = signup(client)
        r = client.post("/auth/login", data={
            "email": email, "password": "Test1234!",
        })
        assert r.status_code == 200
        assert r.json()["token"] is not None

    def test_login_wrong_password(self, client: TestClient):
        email, _ = signup(client)
        r = client.post("/auth/login", data={
            "email": email, "password": "WrongPass9!",
        })
        assert r.status_code == 401

    def test_login_unknown_email(self, client: TestClient):
        r = client.post("/auth/login", data={
            "email": "nobody@nowhere.invalid", "password": "Test1234!",
        })
        assert r.status_code == 401

    def test_login_case_insensitive_email(self, client: TestClient):
        email = random_email()
        client.post("/auth/signup", data={
            "email": email, "password": "Test1234!", "terms_accepted": "true",
        })
        r = client.post("/auth/login", data={
            "email": email.upper(), "password": "Test1234!",
        })
        # Should succeed regardless of case
        assert r.status_code == 200


# ---------- Authenticated endpoints ------------------------------------------

class TestAuthenticatedEndpoints:
    def test_get_profile_requires_auth(self, client: TestClient):
        # Signup sets a `pathshala_token` cookie and the session-scoped
        # TestClient persists cookies across tests; clear them so the
        # auth-required assertion isn't masked by a leftover session.
        client.cookies.clear()
        r = client.get("/api/me/profile")
        assert r.status_code == 401

    def test_get_profile_success(self, client: TestClient):
        _, token = signup(client)
        r = client.get("/api/me/profile", headers=auth_headers(token))
        assert r.status_code == 200
        body = r.json()
        assert "email" in body
        assert "subscription_tier" in body

    def test_data_export_requires_auth(self, client: TestClient):
        client.cookies.clear()
        r = client.get("/api/me/data/export")
        assert r.status_code == 401

    def test_data_export_returns_schema(self, client: TestClient):
        _, token = signup(client)
        r = client.get("/api/me/data/export", headers=auth_headers(token))
        assert r.status_code == 200
        body = r.json()
        assert "generated_at" in body
        assert "profile" in body
        assert "jobs" in body
        assert "schema_version" in body

    def test_delete_account_requires_auth(self, client: TestClient):
        client.cookies.clear()
        r = client.delete("/api/me/account")
        assert r.status_code == 401
