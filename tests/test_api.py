from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)
def test_health():
    assert client.get("/health").json() == {"status": "ok"}
def test_all_mock_resources():
    for resource in ["dashboard","timeline","routines","nutrition","weight","workouts","money","tasks","focus","reports","notifications","profile","settings"]:
        assert client.get(f"/{resource}").status_code == 200
