from datetime import UTC, datetime
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.database import get_db
from app.main import app


def test_status_endpoint_returns_202_when_backfill_running(monkeypatch) -> None:
    async def _startup(self) -> None:
        return None

    async def _shutdown(self) -> None:
        return None

    monkeypatch.setattr("app.services.orchestration.StartupCoordinator.startup", _startup)
    monkeypatch.setattr("app.services.orchestration.StartupCoordinator.shutdown", _shutdown)
    monkeypatch.setattr("app.main.get_current_status", lambda _db: {
        "state": "running",
        "total_satellites": 10,
        "total_passes_computed": 5,
        "computation_progress_percent": 50.0,
        "started_at": datetime(2026, 3, 18, tzinfo=UTC),
        "finished_at": None,
    })
    with TestClient(app) as client:
        response = client.get("/status")
    assert response.status_code == 202


def test_health_endpoint_reports_component_state(monkeypatch) -> None:
    async def _startup(self) -> None:
        return None

    async def _shutdown(self) -> None:
        return None

    class DummyDB:
        def execute(self, _query):
            return 1

    monkeypatch.setattr("app.services.orchestration.StartupCoordinator.startup", _startup)
    monkeypatch.setattr("app.services.orchestration.StartupCoordinator.shutdown", _shutdown)
    monkeypatch.setattr("app.main.get_redis_client", lambda: SimpleNamespace(ping=lambda: True))
    monkeypatch.setattr("app.main.get_status", lambda: {"state": "running"})
    monkeypatch.setattr("app.main.get_last_tle_fetch_time", lambda: datetime(2026, 3, 18, tzinfo=UTC))
    app.dependency_overrides.clear()
    app.dependency_overrides[get_db] = lambda: DummyDB()
    with TestClient(app) as client:
        response = client.get("/health")
    app.dependency_overrides.clear()
    assert response.status_code == 200
    assert response.json()["pass_computation_status"] == "running"
