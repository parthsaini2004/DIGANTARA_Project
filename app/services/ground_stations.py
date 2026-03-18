import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import GroundStation


def load_ground_stations_from_seed() -> list[dict]:
    settings = get_settings()
    seed_path = Path(settings.station_seed_path)
    with seed_path.open("r", encoding="utf-8") as file_obj:
        return json.load(file_obj)


def seed_ground_stations(session: Session) -> int:
    stations = load_ground_stations_from_seed()
    existing_codes = set(session.scalars(select(GroundStation.station_code)).all())
    new_rows = []
    for station in stations:
        if station["station_code"] in existing_codes:
            continue
        new_rows.append(GroundStation(**station))

    if new_rows:
        session.add_all(new_rows)
        session.commit()
    return len(new_rows)
