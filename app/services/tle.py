from dataclasses import dataclass
from datetime import UTC, datetime

import requests
from sgp4.api import Satrec
from sgp4.conveniences import sat_epoch_datetime
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Satellite, TLESnapshot
from app.services import redis_state


@dataclass(slots=True)
class TLERecord:
    name: str
    norad_id: int
    line1: str
    line2: str
    epoch: datetime
    fetched_at: datetime


class TLEFetchError(RuntimeError):
    pass


def parse_tle_payload(payload: str, fetched_at: datetime | None = None) -> list[TLERecord]:
    fetched_at = fetched_at or datetime.now(UTC)
    lines = [line.rstrip() for line in payload.splitlines() if line.strip()]
    if len(lines) < 3:
        raise TLEFetchError("CelesTrak response did not contain TLE triples")

    records: list[TLERecord] = []
    index = 0
    while index + 2 < len(lines):
        name, line1, line2 = lines[index], lines[index + 1], lines[index + 2]
        if not line1.startswith("1 ") or not line2.startswith("2 "):
            index += 1
            continue
        satrec = Satrec.twoline2rv(line1, line2)
        epoch = sat_epoch_datetime(satrec).astimezone(UTC)
        norad_id = int(line1[2:7])
        records.append(TLERecord(name=name.strip(), norad_id=norad_id, line1=line1, line2=line2, epoch=epoch, fetched_at=fetched_at))
        index += 3
    if not records:
        raise TLEFetchError("No valid TLE records parsed from payload")
    return records


def fetch_active_tles() -> list[TLERecord]:
    settings = get_settings()
    response = requests.get(settings.tle_source_url, timeout=settings.request_timeout_seconds)
    response.raise_for_status()
    return parse_tle_payload(response.text, fetched_at=datetime.now(UTC))


def upsert_tles(session: Session, records: list[TLERecord]) -> int:
    if not records:
        return 0

    now = datetime.now(UTC)
    norad_ids = [record.norad_id for record in records]
    satellites = session.execute(select(Satellite).where(Satellite.norad_id.in_(norad_ids))).scalars().all()
    satellites_by_norad = {sat.norad_id: sat for sat in satellites}

    for record in records:
        satellite = satellites_by_norad.get(record.norad_id)
        if satellite is None:
            satellite = Satellite(
                norad_id=record.norad_id,
                name=record.name,
                is_active=True,
                last_tle_fetch_time=record.fetched_at,
            )
            session.add(satellite)
            session.flush()
            satellites_by_norad[record.norad_id] = satellite
        else:
            satellite.name = record.name
            satellite.is_active = True
            satellite.last_tle_fetch_time = record.fetched_at

        session.execute(
            update(TLESnapshot)
            .where(TLESnapshot.satellite_id == satellite.id)
            .values(is_current=False)
        )

        existing_snapshot = session.execute(
            select(TLESnapshot).where(
                TLESnapshot.satellite_id == satellite.id,
                TLESnapshot.epoch == record.epoch,
            )
        ).scalar_one_or_none()

        if existing_snapshot is None:
            session.add(
                TLESnapshot(
                    satellite_id=satellite.id,
                    tle_line1=record.line1,
                    tle_line2=record.line2,
                    epoch=record.epoch,
                    fetched_at=record.fetched_at,
                    is_current=True,
                )
            )
        else:
            existing_snapshot.tle_line1 = record.line1
            existing_snapshot.tle_line2 = record.line2
            existing_snapshot.fetched_at = record.fetched_at
            existing_snapshot.is_current = True

    session.commit()
    redis_state.set_last_tle_fetch_time(now)
    return len(records)
