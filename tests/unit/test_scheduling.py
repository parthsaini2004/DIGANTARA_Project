from datetime import UTC, datetime
from types import SimpleNamespace

from app.services.scheduling import select_optimal_schedule


def test_greedy_schedule_selects_non_overlapping_passes() -> None:
    passes = [
        SimpleNamespace(aos=datetime(2026, 3, 18, 0, 0, tzinfo=UTC), los=datetime(2026, 3, 18, 0, 10, tzinfo=UTC)),
        SimpleNamespace(aos=datetime(2026, 3, 18, 0, 5, tzinfo=UTC), los=datetime(2026, 3, 18, 0, 15, tzinfo=UTC)),
        SimpleNamespace(aos=datetime(2026, 3, 18, 0, 15, tzinfo=UTC), los=datetime(2026, 3, 18, 0, 20, tzinfo=UTC)),
    ]
    selected = select_optimal_schedule(passes)
    assert len(selected) == 2
    assert selected[0].los <= selected[1].aos
