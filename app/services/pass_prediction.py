from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from math import cos, radians, sin, sqrt

import numpy as np
from sgp4.api import Satrec, jday
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import GroundStation, JobRun, Pass, Satellite, TLESnapshot
from app.services import redis_state
from app.services.status import PASS_COMPUTE_JOB, complete_job, create_job_run, fail_job, update_job_progress

WGS84_A_KM = 6378.137
WGS84_F = 1 / 298.257223563
WGS84_E2 = WGS84_F * (2 - WGS84_F)


@dataclass(slots=True)
class StationGeometry:
    id: int
    station_code: str
    latitude_deg: float
    longitude_deg: float
    altitude_m: float


@dataclass(slots=True)
class SatelliteInput:
    satellite_id: int
    tle_snapshot_id: int
    norad_id: int
    name: str
    line1: str
    line2: str


@dataclass(slots=True)
class PassResult:
    station_id: int
    satellite_id: int
    tle_snapshot_id: int
    aos: datetime
    los: datetime
    tca: datetime
    max_elevation_deg: float
    duration_seconds: float
    aos_azimuth_deg: float | None
    los_azimuth_deg: float | None


def datetime_to_jd_fr(dt: datetime) -> tuple[float, float]:
    dt = dt.astimezone(UTC)
    fraction = dt.microsecond / 1_000_000
    return jday(dt.year, dt.month, dt.day, dt.hour, dt.minute, dt.second + fraction)


def build_time_grid(start: datetime, end: datetime, step_seconds: int) -> list[datetime]:
    times: list[datetime] = []
    current = start
    while current <= end:
        times.append(current)
        current += timedelta(seconds=step_seconds)
    return times


def gmst_radians(times: list[datetime]) -> np.ndarray:
    jd_pairs = [datetime_to_jd_fr(time_value) for time_value in times]
    jd = np.array([pair[0] + pair[1] for pair in jd_pairs])
    centuries = (jd - 2451545.0) / 36525.0
    gmst_deg = (
        280.46061837
        + 360.98564736629 * (jd - 2451545.0)
        + 0.000387933 * np.square(centuries)
        - np.power(centuries, 3) / 38710000.0
    )
    return np.radians(np.mod(gmst_deg, 360.0))


def geodetic_to_ecef_km(latitude_deg: float, longitude_deg: float, altitude_m: float) -> np.ndarray:
    lat = radians(latitude_deg)
    lon = radians(longitude_deg)
    alt_km = altitude_m / 1000.0
    prime_vertical = WGS84_A_KM / sqrt(1 - WGS84_E2 * sin(lat) * sin(lat))
    x = (prime_vertical + alt_km) * cos(lat) * cos(lon)
    y = (prime_vertical + alt_km) * cos(lat) * sin(lon)
    z = ((1 - WGS84_E2) * prime_vertical + alt_km) * sin(lat)
    return np.array([x, y, z])


def eci_to_ecef_km(eci_positions_km: np.ndarray, times: list[datetime]) -> np.ndarray:
    gmst = gmst_radians(times)
    cos_gmst = np.cos(gmst)
    sin_gmst = np.sin(gmst)
    x = eci_positions_km[:, 0] * cos_gmst + eci_positions_km[:, 1] * sin_gmst
    y = -eci_positions_km[:, 0] * sin_gmst + eci_positions_km[:, 1] * cos_gmst
    z = eci_positions_km[:, 2]
    return np.column_stack((x, y, z))


def elevation_and_azimuth(ecef_positions_km: np.ndarray, station: StationGeometry) -> tuple[np.ndarray, np.ndarray]:
    station_ecef = geodetic_to_ecef_km(station.latitude_deg, station.longitude_deg, station.altitude_m)
    delta = ecef_positions_km - station_ecef
    lat = radians(station.latitude_deg)
    lon = radians(station.longitude_deg)

    east = -sin(lon) * delta[:, 0] + cos(lon) * delta[:, 1]
    north = -sin(lat) * cos(lon) * delta[:, 0] - sin(lat) * sin(lon) * delta[:, 1] + cos(lat) * delta[:, 2]
    up = cos(lat) * cos(lon) * delta[:, 0] + cos(lat) * sin(lon) * delta[:, 1] + sin(lat) * delta[:, 2]

    elevation = np.degrees(np.arctan2(up, np.sqrt(np.square(east) + np.square(north))))
    azimuth = np.degrees(np.arctan2(east, north))
    return elevation, np.mod(azimuth + 360.0, 360.0)


def propagate_satellite(line1: str, line2: str, times: list[datetime]) -> np.ndarray:
    satrec = Satrec.twoline2rv(line1, line2)
    jd_fr = [datetime_to_jd_fr(time_value) for time_value in times]
    jd = np.array([pair[0] for pair in jd_fr])
    fr = np.array([pair[1] for pair in jd_fr])
    error_codes, positions, _ = satrec.sgp4_array(jd, fr)
    if np.any(error_codes != 0):
        raise RuntimeError(f"SGP4 propagation failed with codes {sorted(set(error_codes.tolist()))}")
    return positions


def refine_pass_edges(
    line1: str,
    line2: str,
    station: StationGeometry,
    start: datetime,
    end: datetime,
    horizon_degrees: float,
    minimum_pass_seconds: int,
) -> PassResult | None:
    total_seconds = max(1, int((end - start).total_seconds()))
    times = [start + timedelta(seconds=offset) for offset in range(total_seconds + 1)]
    positions = propagate_satellite(line1, line2, times)
    ecef_positions = eci_to_ecef_km(positions, times)
    elevations, azimuths = elevation_and_azimuth(ecef_positions, station)
    visible_indices = np.where(elevations > horizon_degrees)[0]
    if visible_indices.size == 0:
        return None

    aos_index = int(visible_indices[0])
    los_index = int(visible_indices[-1])
    aos = times[aos_index]
    los = times[los_index]
    duration_seconds = (los - aos).total_seconds()
    if duration_seconds < minimum_pass_seconds:
        return None

    visible_elevations = elevations[visible_indices]
    tca_index = int(visible_indices[int(np.argmax(visible_elevations))])
    return PassResult(
        station_id=station.id,
        satellite_id=-1,
        tle_snapshot_id=-1,
        aos=aos,
        los=los,
        tca=times[tca_index],
        max_elevation_deg=float(np.max(visible_elevations)),
        duration_seconds=float(duration_seconds),
        aos_azimuth_deg=float(azimuths[aos_index]),
        los_azimuth_deg=float(azimuths[los_index]),
    )


def detect_passes_for_station(
    satellite: SatelliteInput,
    station: StationGeometry,
    times: list[datetime],
    ecef_positions: np.ndarray,
    horizon_degrees: float,
    minimum_pass_seconds: int,
    coarse_step_seconds: int,
) -> list[PassResult]:
    elevations, _ = elevation_and_azimuth(ecef_positions, station)
    visible = elevations > horizon_degrees
    if not np.any(visible):
        return []

    rising = np.where((~visible[:-1]) & visible[1:])[0] + 1
    setting = np.where(visible[:-1] & (~visible[1:]))[0]

    if visible[0]:
        rising = np.insert(rising, 0, 0)
    if visible[-1]:
        setting = np.append(setting, len(visible) - 1)

    passes: list[PassResult] = []
    for rise_index, set_index in zip(rising, setting):
        coarse_start = times[max(rise_index - 1, 0)]
        coarse_end = min(times[min(set_index + 1, len(times) - 1)] + timedelta(seconds=coarse_step_seconds), times[-1])
        refined = refine_pass_edges(
            satellite.line1,
            satellite.line2,
            station,
            coarse_start,
            coarse_end,
            horizon_degrees,
            minimum_pass_seconds,
        )
        if refined is None:
            continue
        refined.satellite_id = satellite.satellite_id
        refined.tle_snapshot_id = satellite.tle_snapshot_id
        passes.append(refined)
    return passes


def compute_passes_for_chunk(
    chunk: list[SatelliteInput],
    stations: list[StationGeometry],
    window_start: datetime,
    window_end: datetime,
    step_seconds: int,
    horizon_degrees: float,
    minimum_pass_seconds: int,
) -> list[dict]:
    times = build_time_grid(window_start, window_end, step_seconds)
    results: list[dict] = []
    for satellite in chunk:
        positions = propagate_satellite(satellite.line1, satellite.line2, times)
        ecef_positions = eci_to_ecef_km(positions, times)
        for station in stations:
            for pass_result in detect_passes_for_station(
                satellite,
                station,
                times,
                ecef_positions,
                horizon_degrees,
                minimum_pass_seconds,
                step_seconds,
            ):
                results.append(
                    {
                        "station_id": pass_result.station_id,
                        "satellite_id": pass_result.satellite_id,
                        "tle_snapshot_id": pass_result.tle_snapshot_id,
                        "aos": pass_result.aos,
                        "los": pass_result.los,
                        "tca": pass_result.tca,
                        "max_elevation_deg": pass_result.max_elevation_deg,
                        "duration_seconds": pass_result.duration_seconds,
                        "aos_azimuth_deg": pass_result.aos_azimuth_deg,
                        "los_azimuth_deg": pass_result.los_azimuth_deg,
                    }
                )
    return results


def _serialize_stations(stations: list[GroundStation]) -> list[StationGeometry]:
    return [
        StationGeometry(
            id=station.id,
            station_code=station.station_code,
            latitude_deg=station.latitude_deg,
            longitude_deg=station.longitude_deg,
            altitude_m=station.altitude_m,
        )
        for station in stations
    ]


def _serialize_satellites(rows: list[tuple[Satellite, TLESnapshot]]) -> list[SatelliteInput]:
    return [
        SatelliteInput(
            satellite_id=satellite.id,
            tle_snapshot_id=snapshot.id,
            norad_id=satellite.norad_id,
            name=satellite.name,
            line1=snapshot.tle_line1,
            line2=snapshot.tle_line2,
        )
        for satellite, snapshot in rows
    ]


def _chunk_items(items: list[SatelliteInput], chunk_size: int) -> list[list[SatelliteInput]]:
    return [items[index : index + chunk_size] for index in range(0, len(items), chunk_size)]


def fetch_current_satellite_inputs(session: Session) -> list[SatelliteInput]:
    stmt = (
        select(Satellite, TLESnapshot)
        .join(TLESnapshot, TLESnapshot.satellite_id == Satellite.id)
        .where(TLESnapshot.is_current.is_(True), Satellite.is_active.is_(True))
        .order_by(Satellite.norad_id)
    )
    rows = session.execute(stmt).all()
    inputs = _serialize_satellites(rows)
    settings = get_settings()
    return inputs[: settings.max_satellites] if settings.max_satellites > 0 else inputs


def recompute_passes(session_factory) -> None:
    settings = get_settings()
    if not redis_state.acquire_lock(ttl_seconds=max(3600, settings.prediction_days * 24 * 3600)):
        return

    try:
        with session_factory() as session:
            stations = session.scalars(select(GroundStation).order_by(GroundStation.id)).all()
            satellites = fetch_current_satellite_inputs(session)
            if not satellites or not stations:
                redis_state.set_status({"state": "not_started", "progress_current": 0, "progress_total": len(satellites)})
                return

            window_start = datetime.now(UTC)
            window_end = window_start + timedelta(days=settings.prediction_days)
            job = create_job_run(session, PASS_COMPUTE_JOB, len(satellites))
            redis_state.set_status(
                {
                    "state": "running",
                    "started_at": job.started_at,
                    "finished_at": None,
                    "progress_current": 0,
                    "progress_total": len(satellites),
                }
            )
            station_payload = _serialize_stations(stations)
            chunks = _chunk_items(satellites, settings.satellite_chunk_size)

        completed = 0
        with ProcessPoolExecutor(max_workers=settings.worker_processes) as executor:
            future_map = {
                executor.submit(
                    compute_passes_for_chunk,
                    chunk,
                    station_payload,
                    window_start,
                    window_end,
                    settings.time_step_seconds,
                    settings.horizon_degrees,
                    settings.minimum_pass_seconds,
                ): chunk
                for chunk in chunks
            }

            for future in as_completed(future_map):
                chunk = future_map[future]
                rows = future.result()
                satellite_ids = [item.satellite_id for item in chunk]
                with session_factory() as write_session:
                    write_session.execute(delete(Pass).where(Pass.satellite_id.in_(satellite_ids), Pass.aos >= window_start))
                    if rows:
                        write_session.bulk_insert_mappings(Pass, rows)
                    write_session.commit()
                    job = write_session.execute(
                        select(JobRun).where(JobRun.job_type == PASS_COMPUTE_JOB).order_by(JobRun.id.desc())
                    ).scalar_one()
                    completed += len(chunk)
                    update_job_progress(write_session, job, completed, len(satellites))

        with session_factory() as session:
            job = session.execute(select(JobRun).where(JobRun.job_type == PASS_COMPUTE_JOB).order_by(JobRun.id.desc())).scalar_one()
            complete_job(session, job)
    except Exception as exc:
        with session_factory() as session:
            job = session.execute(select(JobRun).where(JobRun.job_type == PASS_COMPUTE_JOB).order_by(JobRun.id.desc())).scalar_one_or_none()
            if job is not None:
                fail_job(session, job, str(exc))
        raise
    finally:
        redis_state.release_lock()
