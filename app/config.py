from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Ground Pass Prediction Backend"
    database_url: str = Field(
        default="postgresql+psycopg2://postgres:postgres@localhost:5432/passes",
        alias="DATABASE_URL",
    )
    redis_url: str = Field(default="redis://localhost:6379/0", alias="REDIS_URL")
    tle_source_url: str = Field(
        default="https://celestrak.org/NORAD/elements/gp.php?GROUP=active&FORMAT=tle",
        alias="TLE_SOURCE_URL",
    )
    prediction_days: int = Field(default=7, alias="PREDICTION_DAYS")
    time_step_seconds: int = Field(default=60, alias="TIME_STEP_SECONDS")
    minimum_pass_seconds: int = Field(default=5, alias="MINIMUM_PASS_SECONDS")
    horizon_degrees: float = Field(default=0.0, alias="HORIZON_DEGREES")
    tle_refresh_hours: int = Field(default=24, alias="TLE_REFRESH_HOURS")
    worker_processes: int = Field(default=2, alias="WORKER_PROCESSES")
    satellite_chunk_size: int = Field(default=20, alias="SATELLITE_CHUNK_SIZE")
    request_timeout_seconds: int = Field(default=30, alias="REQUEST_TIMEOUT_SECONDS")
    max_satellites: int = Field(default=0, alias="MAX_SATELLITES")
    skip_startup_tasks: bool = Field(default=False, alias="SKIP_STARTUP_TASKS")
    station_seed_path: str = Field(default="app/seeds/ground_stations.json", alias="STATION_SEED_PATH")

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
