from datetime import UTC, datetime
from types import SimpleNamespace

from app.services.status import get_current_status


class DummySession:
    def __init__(self) -> None:
        self.values = [42, 314]

    def scalar(self, _stmt):
        return self.values.pop(0)


def test_status_progress_math(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.status.redis_state.get_status",
        lambda: {
            "state": "running",
            "progress_current": 21,
            "progress_total": 42,
            "started_at": datetime(2026, 3, 18, tzinfo=UTC).isoformat(),
            "finished_at": None,
        },
    )
    payload = get_current_status(DummySession())
    assert payload["state"] == "running"
    assert payload["total_satellites"] == 42
    assert payload["total_passes_computed"] == 314
    assert payload["computation_progress_percent"] == 50.0
