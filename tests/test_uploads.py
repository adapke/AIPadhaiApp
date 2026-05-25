"""Upload endpoint tests: size limits, content-type validation."""
from __future__ import annotations

import io

from fastapi.testclient import TestClient

from .conftest import auth_headers, signup


_25MB = 25 * 1024 * 1024


class TestUpload:
    def test_upload_requires_auth(self, client: TestClient):
        r = client.post(
            "/api/uploads",
            files={"image": ("test.jpg", io.BytesIO(b"fake"), "image/jpeg")},
        )
        assert r.status_code == 401

    def test_upload_rejects_oversized_file(self, client: TestClient):
        _, token = signup(client)
        # 26 MB of zeros
        big = io.BytesIO(b"\x00" * (_25MB + 1024))
        r = client.post(
            "/api/uploads",
            files={"image": ("big.jpg", big, "image/jpeg")},
            headers=auth_headers(token),
        )
        assert r.status_code == 413

    def test_upload_rejects_bad_content_type(self, client: TestClient):
        _, token = signup(client)
        r = client.post(
            "/api/uploads",
            files={"image": ("evil.exe", io.BytesIO(b"MZ\x90\x00"), "application/octet-stream")},
            headers=auth_headers(token),
        )
        # Should reject non-image content type
        assert r.status_code in (400, 415)

    def test_upload_accepts_valid_image(self, client: TestClient):
        _, token = signup(client)
        # Minimal valid 1×1 white PNG
        png_1x1 = (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
            b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00"
            b"\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18"
            b"\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
        )
        r = client.post(
            "/api/uploads",
            files={"image": ("photo.png", io.BytesIO(png_1x1), "image/png")},
            headers=auth_headers(token),
        )
        # 200 OK or 201 Created; must not be a 4xx/5xx error
        assert r.status_code < 400, r.text
