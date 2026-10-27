from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from app.config import Settings
from app.main import create_app

KEY = "unit-test-secret-123456789"


@pytest.fixture
def client(tmp_path: Path):
    settings = Settings(db_path=tmp_path / "knowledge.sqlite3", api_key=KEY)
    with TestClient(create_app(settings)) as c:
        c.headers["X-API-Key"] = KEY
        yield c


@pytest.fixture
def upload(client):
    def put(text, filename="notes.txt", when="2026-09-18T10:00:00+05:30", **extra):
        data = {**extra}
        if when is not None:
            data["occurred_at"] = when
        response = client.post("/v1/sources", files={"file": (filename, text)}, data=data)
        assert response.status_code == 201, response.text
        return response.json()["source_id"]
    return put
