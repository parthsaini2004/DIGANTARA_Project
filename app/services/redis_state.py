import json
from datetime import UTC, datetime

import redis

from app.config import get_settings

STATUS_KEY = "init_pass_compute_status"
LAST_TLE_FETCH_KEY = "last_tle_fetch_time"
LAST_PASS_COMPUTE_KEY = "last_pass_compute_time"
LOCK_KEY = "pass_compute_lock"


def get_redis_client() -> redis.Redis:
    settings = get_settings()
    return redis.Redis.from_url(settings.redis_url, decode_responses=True)


def _json_default(value: object) -> str:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    raise TypeError(f"Unsupported value: {type(value)!r}")


def set_status(payload: dict) -> None:
    client = get_redis_client()
    client.set(STATUS_KEY, json.dumps(payload, default=_json_default))


def get_status() -> dict:
    client = get_redis_client()
    raw = client.get(STATUS_KEY)
    if not raw:
        return {"state": "not_started", "progress_current": 0, "progress_total": 0}
    return json.loads(raw)


def set_last_tle_fetch_time(value: datetime) -> None:
    client = get_redis_client()
    client.set(LAST_TLE_FETCH_KEY, value.astimezone(UTC).isoformat())


def get_last_tle_fetch_time() -> datetime | None:
    client = get_redis_client()
    raw = client.get(LAST_TLE_FETCH_KEY)
    if not raw:
        return None
    return datetime.fromisoformat(raw)


def set_last_pass_compute_time(value: datetime) -> None:
    client = get_redis_client()
    client.set(LAST_PASS_COMPUTE_KEY, value.astimezone(UTC).isoformat())


def acquire_lock(ttl_seconds: int = 3600) -> bool:
    client = get_redis_client()
    return bool(client.set(LOCK_KEY, "1", nx=True, ex=ttl_seconds))


def release_lock() -> None:
    client = get_redis_client()
    client.delete(LOCK_KEY)
