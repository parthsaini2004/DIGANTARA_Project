from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

from fastapi import Depends, FastAPI, HTTPException, Query, Response
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import GroundStation, Pass
from app.schemas import GroundStationOut, HealthResponse, NetworkSummaryResponse, PassOut, ScheduleResponse, StatusResponse
from app.services.orchestration import StartupCoordinator
from app.services.redis_state import get_last_tle_fetch_time, get_redis_client, get_status
from app.services.scheduling import select_network_unique_coverage, select_optimal_schedule
from app.services.status import get_current_status

settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    coordinator = StartupCoordinator()
    await coordinator.startup()
    try:
        yield
    finally:
        await coordinator.shutdown()


app = FastAPI(title=settings.app_name, lifespan=lifespan)


@app.get("/health", response_model=HealthResponse)
def health(db: Session = Depends(get_db)) -> HealthResponse:
    database_ok = True
    redis_ok = True

    try:
        db.execute(text("SELECT 1"))
    except Exception:
        database_ok = False

    try:
        get_redis_client().ping()
    except Exception:
        redis_ok = False

    payload = get_status()
    status = "ok" if database_ok and redis_ok else "degraded"
    return HealthResponse(
        status=status,
        database_ok=database_ok,
        redis_ok=redis_ok,
        last_tle_fetch_time=get_last_tle_fetch_time(),
        pass_computation_status=payload.get("state", "not_started"),
    )


@app.get("/status", response_model=StatusResponse)
def status(response: Response, db: Session = Depends(get_db)) -> StatusResponse:
    payload = get_current_status(db)
    if payload["state"] == "failed":
        response.status_code = 503
    elif payload["state"] != "completed":
        response.status_code = 202
    return StatusResponse(**payload)


@app.get("/stations", response_model=list[GroundStationOut])
def stations(db: Session = Depends(get_db)) -> list[GroundStationOut]:
    rows = db.scalars(select(GroundStation).order_by(GroundStation.station_code)).all()
    return [GroundStationOut.model_validate(row) for row in rows]


@app.get("/passes", response_model=list[PassOut])
def passes(
    start: datetime = Query(...),
    end: datetime = Query(...),
    station_id: int | None = Query(default=None),
    station_code: str | None = Query(default=None),
    satellite_id: int | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> list[PassOut]:
    if station_id is None and station_code is None:
        raise HTTPException(status_code=400, detail="Provide either station_id or station_code")

    if station_code is not None:
        station = db.execute(select(GroundStation).where(GroundStation.station_code == station_code)).scalar_one_or_none()
        if station is None:
            raise HTTPException(status_code=404, detail="Station not found")
        station_id = station.id

    stmt = (
        select(Pass)
        .where(Pass.station_id == station_id, Pass.los >= start, Pass.aos <= end)
        .order_by(Pass.aos)
        .limit(limit)
        .offset(offset)
    )
    if satellite_id is not None:
        stmt = stmt.where(Pass.satellite_id == satellite_id)

    rows = db.scalars(stmt).all()
    return [PassOut.model_validate(row) for row in rows]


@app.get("/schedule/{station_code}", response_model=ScheduleResponse)
def schedule(
    station_code: str,
    start: datetime = Query(...),
    end: datetime = Query(...),
    db: Session = Depends(get_db),
) -> ScheduleResponse:
    station = db.execute(select(GroundStation).where(GroundStation.station_code == station_code)).scalar_one_or_none()
    if station is None:
        raise HTTPException(status_code=404, detail="Station not found")

    candidates = db.scalars(
        select(Pass)
        .where(Pass.station_id == station.id, Pass.los >= start, Pass.aos <= end)
        .order_by(Pass.aos)
    ).all()
    selected = select_optimal_schedule(candidates)
    return ScheduleResponse(
        station_code=station.station_code,
        start=start,
        end=end,
        selected_passes=[PassOut.model_validate(row) for row in selected],
        total_candidates=len(candidates),
        total_selected=len(selected),
    )


@app.get("/network/summary", response_model=NetworkSummaryResponse)
def network_summary(
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
    db: Session = Depends(get_db),
) -> NetworkSummaryResponse:
    start = start or datetime.now(UTC)
    end = end or (start + timedelta(days=1))
    stations = db.scalars(select(GroundStation).order_by(GroundStation.station_code)).all()
    pass_count_rows = db.execute(
        select(GroundStation.station_code, func.count(Pass.id))
        .join(Pass, Pass.station_id == GroundStation.id)
        .where(Pass.los >= start, Pass.aos <= end)
        .group_by(GroundStation.station_code)
    ).all()
    total_passes = db.scalar(select(func.count(Pass.id)).where(Pass.los >= start, Pass.aos <= end)) or 0
    unique_satellites = db.scalar(
        select(func.count(func.distinct(Pass.satellite_id))).where(Pass.los >= start, Pass.aos <= end)
    ) or 0

    passes_per_station: dict[str, int] = {}
    scheduled_per_station: dict[str, int] = {}
    grouped_for_network: dict[str, list[Pass]] = {}
    pass_count_lookup = {station_code: count for station_code, count in pass_count_rows}
    for station in stations:
        station_rows = db.scalars(
            select(Pass)
            .where(Pass.station_id == station.id, Pass.los >= start, Pass.aos <= end)
            .order_by(Pass.los, Pass.aos)
        ).all()
        selected = select_optimal_schedule(station_rows)
        grouped_for_network[str(station.id)] = selected
        passes_per_station[station.station_code] = int(pass_count_lookup.get(station.station_code, 0))
        scheduled_per_station[station.station_code] = len(selected)

    network_selected = select_network_unique_coverage(grouped_for_network)
    return NetworkSummaryResponse(
        start=start,
        end=end,
        total_satellites_with_passes=unique_satellites,
        total_passes=total_passes,
        unique_satellites_visible_network_wide=unique_satellites,
        scheduled_total_passes_network_wide=len(network_selected),
        scheduled_unique_satellites_network_wide=len({row.satellite_id for row in network_selected}),
        passes_per_station=passes_per_station,
        scheduled_passes_per_station=scheduled_per_station,
    )
