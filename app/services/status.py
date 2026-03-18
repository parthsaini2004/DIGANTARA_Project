from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import JobRun, Pass, Satellite
from app.services import redis_state


PASS_COMPUTE_JOB = "pass_compute"


def create_job_run(session: Session, job_type: str, total: int) -> JobRun:
    job = JobRun(
        job_type=job_type,
        status="running",
        started_at=datetime.now(UTC),
        finished_at=None,
        progress_current=0,
        progress_total=total,
        error_message=None,
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def update_job_progress(session: Session, job: JobRun, current: int, total: int) -> None:
    job.progress_current = current
    job.progress_total = total
    session.add(job)
    session.commit()
    redis_state.set_status(
        {
            "state": job.status,
            "started_at": job.started_at,
            "finished_at": job.finished_at,
            "progress_current": current,
            "progress_total": total,
        }
    )


def complete_job(session: Session, job: JobRun) -> None:
    job.status = "completed"
    job.finished_at = datetime.now(UTC)
    job.progress_current = job.progress_total
    session.add(job)
    session.commit()
    redis_state.set_status(
        {
            "state": job.status,
            "started_at": job.started_at,
            "finished_at": job.finished_at,
            "progress_current": job.progress_current,
            "progress_total": job.progress_total,
        }
    )
    redis_state.set_last_pass_compute_time(job.finished_at)


def fail_job(session: Session, job: JobRun, error_message: str) -> None:
    job.status = "failed"
    job.finished_at = datetime.now(UTC)
    job.error_message = error_message
    session.add(job)
    session.commit()
    redis_state.set_status(
        {
            "state": job.status,
            "started_at": job.started_at,
            "finished_at": job.finished_at,
            "progress_current": job.progress_current,
            "progress_total": job.progress_total,
            "error_message": error_message,
        }
    )


def get_current_status(session: Session) -> dict:
    redis_status = redis_state.get_status()
    total_satellites = session.scalar(select(func.count()).select_from(Satellite)) or 0
    total_passes = session.scalar(select(func.count()).select_from(Pass)) or 0
    progress_total = int(redis_status.get("progress_total") or 0)
    progress_current = int(redis_status.get("progress_current") or 0)
    progress_percent = 0.0 if progress_total == 0 else round((progress_current / progress_total) * 100.0, 2)
    started_at = redis_status.get("started_at")
    finished_at = redis_status.get("finished_at")
    if isinstance(started_at, str):
        started_at = datetime.fromisoformat(started_at)
    if isinstance(finished_at, str):
        finished_at = datetime.fromisoformat(finished_at)
    return {
        "state": redis_status.get("state", "not_started"),
        "total_satellites": total_satellites,
        "total_passes_computed": total_passes,
        "computation_progress_percent": progress_percent,
        "started_at": started_at.astimezone(UTC) if isinstance(started_at, datetime) else None,
        "finished_at": finished_at.astimezone(UTC) if isinstance(finished_at, datetime) else None,
    }
