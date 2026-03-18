from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class GroundStation(Base):
    __tablename__ = "ground_stations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    station_code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    latitude_deg: Mapped[float] = mapped_column(Float, nullable=False)
    longitude_deg: Mapped[float] = mapped_column(Float, nullable=False)
    altitude_m: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class Satellite(Base):
    __tablename__ = "satellites"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    norad_id: Mapped[int] = mapped_column(Integer, unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_tle_fetch_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    tle_snapshots: Mapped[list["TLESnapshot"]] = relationship(back_populates="satellite")


class TLESnapshot(Base):
    __tablename__ = "tle_snapshots"
    __table_args__ = (UniqueConstraint("satellite_id", "epoch", name="uq_tle_snapshot_satellite_epoch"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    satellite_id: Mapped[int] = mapped_column(ForeignKey("satellites.id", ondelete="CASCADE"), nullable=False, index=True)
    tle_line1: Mapped[str] = mapped_column(Text, nullable=False)
    tle_line2: Mapped[str] = mapped_column(Text, nullable=False)
    epoch: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    satellite: Mapped[Satellite] = relationship(back_populates="tle_snapshots")


class Pass(Base):
    __tablename__ = "passes"
    __table_args__ = (
        UniqueConstraint("station_id", "satellite_id", "tle_snapshot_id", "aos", name="uq_pass_identity"),
        Index("ix_passes_station_aos", "station_id", "aos"),
        Index("ix_passes_satellite_aos", "satellite_id", "aos"),
        Index("ix_passes_station_aos_los", "station_id", "aos", "los"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    station_id: Mapped[int] = mapped_column(ForeignKey("ground_stations.id", ondelete="CASCADE"), nullable=False)
    satellite_id: Mapped[int] = mapped_column(ForeignKey("satellites.id", ondelete="CASCADE"), nullable=False)
    tle_snapshot_id: Mapped[int] = mapped_column(ForeignKey("tle_snapshots.id", ondelete="CASCADE"), nullable=False)
    aos: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True, nullable=False, index=True)
    los: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    tca: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    max_elevation_deg: Mapped[float] = mapped_column(Float, nullable=False)
    duration_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    aos_azimuth_deg: Mapped[float | None] = mapped_column(Float, nullable=True)
    los_azimuth_deg: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class JobRun(Base):
    __tablename__ = "job_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    progress_current: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    progress_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
