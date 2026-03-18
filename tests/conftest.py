import os


os.environ.setdefault("SKIP_STARTUP_TASKS", "true")
os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
