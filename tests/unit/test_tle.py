from datetime import UTC, datetime

from app.services.tle import parse_tle_payload


SAMPLE_TLE = """ISS (ZARYA)
1 25544U 98067A   24068.54791435  .00014473  00000+0  26506-3 0  9996
2 25544  51.6417 123.9854 0006150  72.1964  38.0304 15.49815300443527
NOAA 19
1 33591U 09005A   24068.43248857  .00000086  00000+0  72747-4 0  9997
2 33591  99.1887 127.5300 0014000  99.6556 260.6205 14.12415077778675
"""


def test_parse_tle_payload_parses_multiple_records() -> None:
    records = parse_tle_payload(SAMPLE_TLE, fetched_at=datetime(2026, 3, 18, tzinfo=UTC))
    assert len(records) == 2
    assert records[0].norad_id == 25544
    assert records[1].name == "NOAA 19"
