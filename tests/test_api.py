import httpx
from fastapi.testclient import TestClient

from faangscout.api.app import app


def test_index_serves_html():
    client = TestClient(app)
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "FaangScout" in r.text


def test_list_companies():
    client = TestClient(app)
    r = client.get("/api/companies")
    assert r.status_code == 200
    data = r.json()
    assert len(data["companies"]) > 0
    assert all("name" in c and "resolved" in c for c in data["companies"])


def test_search_unresolved_company():
    client = TestClient(app)
    r = client.post("/api/search", json={"companies": ["totally-unknown-co"], "role": "backend"})
    assert r.status_code == 200
    data = r.json()
    assert data["jobs"] == []
    assert data["unresolved"] == ["totally-unknown-co"]


def test_search_validates_empty_companies():
    client = TestClient(app)
    r = client.post("/api/search", json={"companies": []})
    assert r.status_code == 422


def test_search_with_mocked_provider(monkeypatch):
    def handler(request):
        return httpx.Response(
            200,
            json={
                "jobs": [
                    {
                        "id": 1,
                        "title": "Backend Engineer",
                        "absolute_url": "https://x/1",
                        "updated_at": "2026-06-01T00:00:00Z",
                        "location": {"name": "Remote"},
                        "departments": [],
                        "content": "",
                    }
                ]
            },
        )

    real_client = httpx.Client

    def fake_client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr("faangscout.scout.httpx.Client", fake_client)
    monkeypatch.setattr("faangscout.providers.base.httpx.Client", fake_client)

    client = TestClient(app)
    r = client.post("/api/search", json={"companies": ["Acme:greenhouse:board=acme"], "hours": 87600})
    assert r.status_code == 200
    data = r.json()
    assert len(data["jobs"]) == 1
    assert data["jobs"][0]["company"] == "Acme"
