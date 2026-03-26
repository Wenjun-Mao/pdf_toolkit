from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4

from sqlalchemy import DateTime, JSON, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utc_now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class JobStatus(StrEnum):
    QUEUED = "queued"
    ANALYZING = "analyzing"
    AWAITING_SETTINGS = "awaiting_settings"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    EXPIRED = "expired"


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    tool_name: Mapped[str] = mapped_column(String(64), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default=JobStatus.QUEUED.value, nullable=False)
    input_paths: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    params_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    artifact_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    output_path: Mapped[str | None] = mapped_column(String(1024))
    result_kind: Mapped[str] = mapped_column(String(32), default="file", nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )
