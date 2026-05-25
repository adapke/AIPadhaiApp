"""Health and public route smoke tests."""
from fastapi.testclient import TestClient


def test_healthz(client: TestClient):
    r = client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"


def test_landing_page(client: TestClient):
    r = client.get("/landing", follow_redirects=True)
    assert r.status_code == 200
    assert "PadhaiApp" in r.text


def test_terms_page(client: TestClient):
    r = client.get("/terms")
    assert r.status_code == 200
    assert "Terms" in r.text


def test_privacy_page(client: TestClient):
    r = client.get("/privacy")
    assert r.status_code == 200
    assert "Privacy" in r.text


def test_robots_txt(client: TestClient):
    r = client.get("/robots.txt")
    assert r.status_code == 200
    assert "User-agent" in r.text


def test_sitemap_xml(client: TestClient):
    r = client.get("/sitemap.xml")
    assert r.status_code == 200
    assert "<urlset" in r.text


def test_manifest_json(client: TestClient):
    r = client.get("/manifest.json")
    assert r.status_code == 200
    data = r.json()
    assert "name" in data


def test_api_fees_config(client: TestClient):
    r = client.get("/api/fees/config")
    assert r.status_code == 200
    assert "razorpay_configured" in r.json()
