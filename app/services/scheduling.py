from app.models import Pass


def select_optimal_schedule(passes: list[Pass]) -> list[Pass]:
    ordered = sorted(passes, key=lambda item: (item.los, item.aos))
    selected: list[Pass] = []
    current_end = None
    for candidate in ordered:
        if current_end is None or candidate.aos >= current_end:
            selected.append(candidate)
            current_end = candidate.los
    return selected


def select_network_unique_coverage(passes_by_station: dict[str, list[Pass]]) -> list[Pass]:
    station_available_at = {station_key: None for station_key in passes_by_station}
    covered_satellites: set[int] = set()
    selected: list[Pass] = []

    candidates = sorted(
        (candidate for station_passes in passes_by_station.values() for candidate in station_passes),
        key=lambda item: (item.los, item.aos),
    )

    for candidate in candidates:
        station_key = str(candidate.station_id)
        available_at = station_available_at.get(station_key)
        if candidate.satellite_id in covered_satellites:
            continue
        if available_at is not None and candidate.aos < available_at:
            continue
        selected.append(candidate)
        covered_satellites.add(candidate.satellite_id)
        station_available_at[station_key] = candidate.los

    return selected
