from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class GroundStationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    station_code: str
    name: str
    latitude_deg: float
    longitude_deg: float
    altitude_m: float


class PassOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    station_id: int
    satellite_id: int
    tle_snapshot_id: int
    aos: datetime
    los: datetime
    tca: datetime
    max_elevation_deg: float
    duration_seconds: float
    aos_azimuth_deg: float | None = None
    los_azimuth_deg: float | None = None


class ScheduleResponse(BaseModel):
    station_code: str
    start: datetime
    end: datetime
    selected_passes: list[PassOut]
    total_candidates: int
    total_selected: int


class HealthResponse(BaseModel):
    status: str
    database_ok: bool
    redis_ok: bool
    last_tle_fetch_time: datetime | None
    pass_computation_status: str


class StatusResponse(BaseModel):
    state: str
    total_satellites: int
    total_passes_computed: int
    computation_progress_percent: float = Field(ge=0.0, le=100.0)
    started_at: datetime | None
    finished_at: datetime | None


class NetworkSummaryResponse(BaseModel):
    start: datetime
    end: datetime
    total_satellites_with_passes: int
    total_passes: int
    unique_satellites_visible_network_wide: int
    scheduled_total_passes_network_wide: int
    scheduled_unique_satellites_network_wide: int
    passes_per_station: dict[str, int]
    scheduled_passes_per_station: dict[str, int]
