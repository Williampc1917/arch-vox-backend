"""
Domain models for VIP onboarding feature.

These lightweight dataclasses describe the shapes of the metadata the
background jobs will produce. They intentionally avoid business logic so
they can be reused by repositories, services, and API layers.
"""

from dataclasses import dataclass
from datetime import datetime


@dataclass(slots=True)
class EmailMetadataRecord:
    """Represents a hashed email interaction that feeds the VIP ranking."""

    user_id: str
    message_id: str
    thread_id: str | None
    direction: str  # "in" or "out"
    from_contact_hash: str
    to_contact_hash: str
    internal_timestamp: datetime


@dataclass(slots=True)
class CalendarEventRecord:
    """Represents a calendar event attendee list used for VIP scoring."""

    user_id: str
    event_id: str
    start_time: datetime
    end_time: datetime
    attendee_hashes: list[str]


@dataclass(slots=True)
class VipCandidate:
    """Aggregated VIP candidate produced after processing metadata."""

    user_id: str
    contact_hash: str
    score: float
    rank: int
    sources: list[str]


@dataclass(slots=True)
class VipBackfillJob:
    """Represents a user_vip_backfill_jobs row."""

    id: str
    user_id: str
    status: str
    attempts: int
    trigger_reason: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    error_message: str | None
