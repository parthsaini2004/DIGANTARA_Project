from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from time import sleep

from alembic import command
from alembic.config import Config
from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import func, select, text

from app.config import get_settings
from app.database import SessionLocal, engine
from app.models import JobRun, Pass, Satellite, TLESnapshot
from app.services.ground_stations import seed_ground_stations
from app.services.pass_prediction import recompute_passes
from app.services.redis_state import get_last_tle_fetch_time, set_last_tle_fetch_time, set_status
from app.services.tle import fetch_active_tles, upsert_tles


class StartupCoordinator:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.scheduler = BackgroundScheduler(timezone="UTC")
        self.background_tasks: set[asyncio.Task] = set()

    def wait_for_dependencies(self, max_attempts: int = 30, delay_seconds: int = 2) -> None:
        last_error: Exception | None = None
        for _ in range(max_attempts):
            try:
                with engine.connect() as connection:
                    connection.execute(text("SELECT 1"))
                from app.services.redis_state import get_redis_client

                get_redis_client().ping()
                return
            except Exception as exc:  # pragma: no cover - exercised in integration environments
                last_error = exc
                sleep(delay_seconds)
        raise RuntimeError(f"Dependencies were not ready after {max_attempts} attempts: {last_error}")

    def run_migrations(self) -> None:
        alembic_cfg = Config("alembic.ini")
        alembic_cfg.set_main_option("sqlalchemy.url", self.settings.database_url)
        command.upgrade(alembic_cfg, "head")

    def restore_status_from_db(self) -> None:
        with SessionLocal() as session:
            latest_job = session.execute(select(JobRun).order_by(JobRun.id.desc())).scalar_one_or_none()
            if latest_job is not None:
                set_status(
                    {
                        "state": latest_job.status,
                        "started_at": latest_job.started_at,
                        "finished_at": latest_job.finished_at,
                        "progress_current": latest_job.progress_current,
                        "progress_total": latest_job.progress_total,
                    }
                )
            elif get_last_tle_fetch_time() is None:
                latest_fetch = session.scalar(select(func.max(Satellite.last_tle_fetch_time)))
                if latest_fetch is not None:
                    set_last_tle_fetch_time(latest_fetch.astimezone(UTC))

    def ensure_seed_and_tles(self) -> None:
        with SessionLocal() as session:
            seed_ground_stations(session)
            satellite_count = session.scalar(select(func.count()).select_from(Satellite)) or 0
            current_snapshot_count = (
                session.scalar(select(func.count()).select_from(TLESnapshot).where(TLESnapshot.is_current.is_(True))) or 0
            )
            latest_fetch = session.scalar(select(func.max(Satellite.last_tle_fetch_time)))
            fetch_is_stale = (
                latest_fetch is None
                or latest_fetch < datetime.now(UTC) - timedelta(hours=self.settings.tle_refresh_hours)
            )
            if satellite_count == 0 or current_snapshot_count == 0 or current_snapshot_count < satellite_count or fetch_is_stale:
                records = fetch_active_tles()
                upsert_tles(session, records)

    def should_trigger_initial_compute(self) -> bool:
        with SessionLocal() as session:
            latest_job = session.execute(
                select(JobRun).where(JobRun.job_type == "pass_compute").order_by(JobRun.id.desc())
            ).scalar_one_or_none()
            pass_count = session.scalar(select(func.count()).select_from(Pass)) or 0
            if latest_job is None:
                return True
            return latest_job.status != "completed" or pass_count == 0

    def refresh_tles_and_recompute(self) -> None:
        with SessionLocal() as session:
            records = fetch_active_tles()
            upsert_tles(session, records)
        recompute_passes(SessionLocal)

    def start_background_recompute(self) -> None:
        async def _runner() -> None:
            await asyncio.to_thread(recompute_passes, SessionLocal)

        task = asyncio.create_task(_runner())
        self.background_tasks.add(task)
        task.add_done_callback(self.background_tasks.discard)

    async def startup(self) -> None:
        if self.settings.skip_startup_tasks:
            return
        self.wait_for_dependencies()
        self.run_migrations()
        self.restore_status_from_db()
        self.ensure_seed_and_tles()
        if self.should_trigger_initial_compute():
            self.start_background_recompute()
        if not self.scheduler.running:
            self.scheduler.add_job(
                self.refresh_tles_and_recompute,
                "interval",
                hours=self.settings.tle_refresh_hours,
                id="tle_refresh",
                replace_existing=True,
                max_instances=1,
                coalesce=True,
            )
            self.scheduler.start()

    async def shutdown(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
        for task in list(self.background_tasks):
            task.cancel()
        if self.background_tasks:
            await asyncio.gather(*self.background_tasks, return_exceptions=True)
