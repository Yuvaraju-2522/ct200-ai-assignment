from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"

def test_reject_non_pdf():
    response = client.post("/ingest", files={"file": ("notes.txt", b"not a pdf", "text/plain")})
    assert response.status_code == 400

def test_versions_empty_or_list():
    response = client.get("/versions")
    assert response.status_code == 200
    assert isinstance(response.json(), list)
