"""initial schema

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-03-18 00:00:00
"""

from alembic import op
import sqlalchemy as sa

revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")

    op.create_table(
        "ground_stations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("station_code", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("latitude_deg", sa.Float(), nullable=False),
        sa.Column("longitude_deg", sa.Float(), nullable=False),
        sa.Column("altitude_m", sa.Float(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("station_code", name="uq_ground_stations_station_code"),
    )

    op.create_table(
        "satellites",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("norad_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_tle_fetch_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("norad_id", name="uq_satellites_norad_id"),
    )
    op.create_index("ix_satellites_norad_id", "satellites", ["norad_id"], unique=False)

    op.create_table(
        "tle_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("satellite_id", sa.Integer(), sa.ForeignKey("satellites.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tle_line1", sa.Text(), nullable=False),
        sa.Column("tle_line2", sa.Text(), nullable=False),
        sa.Column("epoch", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.UniqueConstraint("satellite_id", "epoch", name="uq_tle_snapshot_satellite_epoch"),
    )
    op.create_index("ix_tle_snapshots_satellite_id", "tle_snapshots", ["satellite_id"], unique=False)

    op.create_table(
        "passes",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("station_id", sa.Integer(), sa.ForeignKey("ground_stations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("satellite_id", sa.Integer(), sa.ForeignKey("satellites.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tle_snapshot_id", sa.Integer(), sa.ForeignKey("tle_snapshots.id", ondelete="CASCADE"), nullable=False),
        sa.Column("aos", sa.DateTime(timezone=True), primary_key=True, nullable=False),
        sa.Column("los", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tca", sa.DateTime(timezone=True), nullable=False),
        sa.Column("max_elevation_deg", sa.Float(), nullable=False),
        sa.Column("duration_seconds", sa.Float(), nullable=False),
        sa.Column("aos_azimuth_deg", sa.Float(), nullable=True),
        sa.Column("los_azimuth_deg", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("station_id", "satellite_id", "tle_snapshot_id", "aos", name="uq_pass_identity"),
    )
    op.create_index("ix_passes_aos", "passes", ["aos"], unique=False)
    op.create_index("ix_passes_station_aos", "passes", ["station_id", "aos"], unique=False)
    op.create_index("ix_passes_satellite_aos", "passes", ["satellite_id", "aos"], unique=False)
    op.create_index("ix_passes_station_aos_los", "passes", ["station_id", "aos", "los"], unique=False)
    op.execute("SELECT create_hypertable('passes', 'aos', if_not_exists => TRUE)")

    op.create_table(
        "job_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("job_type", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("progress_current", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("progress_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("metadata_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_job_runs_job_type", "job_runs", ["job_type"], unique=False)
    op.create_index("ix_job_runs_status", "job_runs", ["status"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_job_runs_status", table_name="job_runs")
    op.drop_index("ix_job_runs_job_type", table_name="job_runs")
    op.drop_table("job_runs")

    op.drop_index("ix_passes_station_aos_los", table_name="passes")
    op.drop_index("ix_passes_satellite_aos", table_name="passes")
    op.drop_index("ix_passes_station_aos", table_name="passes")
    op.drop_index("ix_passes_aos", table_name="passes")
    op.drop_table("passes")

    op.drop_index("ix_tle_snapshots_satellite_id", table_name="tle_snapshots")
    op.drop_table("tle_snapshots")

    op.drop_index("ix_satellites_norad_id", table_name="satellites")
    op.drop_table("satellites")

    op.drop_table("ground_stations")
