from datetime import UTC, datetime, timedelta

from app.services.pass_prediction import StationGeometry, build_time_grid, geodetic_to_ecef_km, propagate_satellite


LINE1 = "1 25544U 98067A   24068.54791435  .00014473  00000+0  26506-3 0  9996"
LINE2 = "2 25544  51.6417 123.9854 0006150  72.1964  38.0304 15.49815300443527"


def test_build_time_grid_uses_inclusive_end() -> None:
    start = datetime(2026, 3, 18, tzinfo=UTC)
    end = start + timedelta(minutes=2)
    times = build_time_grid(start, end, 60)
    assert len(times) == 3


def test_geodetic_to_ecef_returns_xyz() -> None:
    vector = geodetic_to_ecef_km(12.9716, 77.5946, 920)
    assert len(vector) == 3
    assert vector[0] != 0


def test_propagate_satellite_returns_positions() -> None:
    times = [datetime(2026, 3, 18, tzinfo=UTC), datetime(2026, 3, 18, 0, 1, tzinfo=UTC)]
    positions = propagate_satellite(LINE1, LINE2, times)
    assert positions.shape == (2, 3)
