"""
Repository helpers for contact aggregation.

Responsible for reading raw metadata (email/events) and writing the final
per-contact statistics into the contacts table.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from app.db.helpers import execute_query, fetch_all
from app.infrastructure.observability.logging import get_logger

logger = get_logger(__name__)


@dataclass(slots=True)
class EmailMetadataRow:
    message_id: str
    thread_id: str
    direction: str
    from_contact_hash: str | None
    to_contact_hash: str | None
    timestamp: datetime
    has_attachment: bool
    is_starred: bool
    is_important: bool
    is_reply: bool
    hour_of_day: int | None
    day_of_week: int | None
    cc_contact_hashes: list[str]


@dataclass(slots=True)
class EventMetadataRow:
    event_id: str
    start_time: datetime
    end_time: datetime
    attendee_contact_hashes: list[str]
    duration_minutes: int
    is_recurring: bool
    organizer_hash: str | None
    user_is_organizer: bool
    user_response: str
    is_one_on_one: bool
    event_type: str


@dataclass(slots=True)
class ContactAggregate:
    user_id: str
    contact_hash: str
    email: str | None
    display_name: str | None
    email_count_30d: int
    inbound_count_30d: int
    outbound_count_30d: int
    direct_email_count: int
    cc_email_count: int
    thread_count_30d: int
    avg_thread_depth: float
    attachment_email_count: int
    starred_email_count: int
    important_email_count: int
    reply_rate_30d: float
    median_response_hours: float | None
    off_hours_ratio: float
    threads_they_started: int
    threads_you_started: int
    meeting_count_30d: int
    total_meeting_minutes: int
    recurring_meeting_count: int
    meetings_you_organized: int
    meetings_they_organized: int
    weighted_meeting_score: float
    meeting_recurrence_score: float
    first_contact_at: datetime | None
    last_contact_at: datetime | None
    consistency_score: float
    initiation_score: float


class ContactAggregationRepository:
    """Raw SQL helpers for contact aggregation."""

    @classmethod
    async def fetch_email_metadata(
        cls, user_id: str, window_start: datetime
    ) -> list[EmailMetadataRow]:
        query = """
            SELECT
                message_id,
                thread_id,
                direction,
                NULLIF(from_contact_hash, '') AS from_contact_hash,
                NULLIF(to_contact_hash, '') AS to_contact_hash,
                timestamp,
                has_attachment,
                is_starred,
                is_important,
                is_reply,
                hour_of_day,
                day_of_week,
                COALESCE(cc_contact_hashes, '{}'::text[]) AS cc_contact_hashes
            FROM email_metadata
            WHERE user_id = %s
              AND timestamp >= %s
              AND is_promotional = false
              AND is_social = false
            ORDER BY timestamp ASC
        """

        rows = await fetch_all(query, (user_id, window_start))
        emails: list[EmailMetadataRow] = []
        for row in rows:
            emails.append(
                EmailMetadataRow(
                    message_id=row["message_id"],
                    thread_id=row["thread_id"],
                    direction=row["direction"],
                    from_contact_hash=row.get("from_contact_hash"),
                    to_contact_hash=row.get("to_contact_hash"),
                    timestamp=row["timestamp"],
                    has_attachment=row["has_attachment"],
                    is_starred=row["is_starred"],
                    is_important=row["is_important"],
                    is_reply=row["is_reply"],
                    hour_of_day=row.get("hour_of_day"),
                    day_of_week=row.get("day_of_week"),
                    cc_contact_hashes=row.get("cc_contact_hashes") or [],
                )
            )
        return emails

    @classmethod
    async def fetch_event_metadata(
        cls, user_id: str, window_start: datetime, window_end: datetime
    ) -> list[EventMetadataRow]:
        query = """
            SELECT
                event_id,
                start_time,
                end_time,
                COALESCE(attendee_contact_hashes, '{}'::text[]) AS attendee_contact_hashes,
                COALESCE(duration_minutes, 0) AS duration_minutes,
                is_recurring,
                NULLIF(organizer_hash, '') AS organizer_hash,
                user_is_organizer,
                user_response,
                is_one_on_one,
                event_type
            FROM events_metadata
            WHERE user_id = %s
              AND start_time >= %s
              AND start_time <= %s
              AND user_response = 'accepted'
              AND event_type = 'default'
        """

        rows = await fetch_all(query, (user_id, window_start, window_end))
        events: list[EventMetadataRow] = []
        for row in rows:
            events.append(
                EventMetadataRow(
                    event_id=row["event_id"],
                    start_time=row["start_time"],
                    end_time=row["end_time"],
                    attendee_contact_hashes=row.get("attendee_contact_hashes") or [],
                    duration_minutes=row.get("duration_minutes") or 0,
                    is_recurring=row["is_recurring"],
                    organizer_hash=row.get("organizer_hash"),
                    user_is_organizer=row["user_is_organizer"],
                    user_response=row["user_response"],
                    is_one_on_one=row["is_one_on_one"],
                    event_type=row["event_type"],
                )
            )
        return events

    @classmethod
    async def upsert_contacts(cls, aggregates: Iterable[ContactAggregate]) -> None:
        query = """
            INSERT INTO contacts (
                user_id, contact_hash, email, display_name, email_count_30d,
                inbound_count_30d, outbound_count_30d, direct_email_count, cc_email_count,
                thread_count_30d, avg_thread_depth, attachment_email_count,
                starred_email_count, important_email_count, reply_rate_30d,
                median_response_hours, off_hours_ratio, threads_they_started,
                threads_you_started, meeting_count_30d, total_meeting_minutes,
                recurring_meeting_count, meetings_you_organized, meetings_they_organized,
                weighted_meeting_score, meeting_recurrence_score,
                first_contact_at, last_contact_at, consistency_score, initiation_score,
                updated_at
            )
            VALUES (
                %(user_id)s, %(contact_hash)s, %(email)s, %(display_name)s, %(email_count_30d)s,
                %(inbound_count_30d)s, %(outbound_count_30d)s, %(direct_email_count)s, %(cc_email_count)s,
                %(thread_count_30d)s, %(avg_thread_depth)s, %(attachment_email_count)s,
                %(starred_email_count)s, %(important_email_count)s, %(reply_rate_30d)s,
                %(median_response_hours)s, %(off_hours_ratio)s, %(threads_they_started)s,
                %(threads_you_started)s, %(meeting_count_30d)s, %(total_meeting_minutes)s,
                %(recurring_meeting_count)s, %(meetings_you_organized)s, %(meetings_they_organized)s,
                %(weighted_meeting_score)s, %(meeting_recurrence_score)s,
                %(first_contact_at)s, %(last_contact_at)s, %(consistency_score)s,
                %(initiation_score)s, NOW()
            )
            ON CONFLICT (user_id, contact_hash)
            DO UPDATE SET
                email = EXCLUDED.email,
                display_name = EXCLUDED.display_name,
                email_count_30d = EXCLUDED.email_count_30d,
                inbound_count_30d = EXCLUDED.inbound_count_30d,
                outbound_count_30d = EXCLUDED.outbound_count_30d,
                direct_email_count = EXCLUDED.direct_email_count,
                cc_email_count = EXCLUDED.cc_email_count,
                thread_count_30d = EXCLUDED.thread_count_30d,
                avg_thread_depth = EXCLUDED.avg_thread_depth,
                attachment_email_count = EXCLUDED.attachment_email_count,
                starred_email_count = EXCLUDED.starred_email_count,
                important_email_count = EXCLUDED.important_email_count,
                reply_rate_30d = EXCLUDED.reply_rate_30d,
                median_response_hours = EXCLUDED.median_response_hours,
                off_hours_ratio = EXCLUDED.off_hours_ratio,
                threads_they_started = EXCLUDED.threads_they_started,
                threads_you_started = EXCLUDED.threads_you_started,
                meeting_count_30d = EXCLUDED.meeting_count_30d,
                total_meeting_minutes = EXCLUDED.total_meeting_minutes,
                recurring_meeting_count = EXCLUDED.recurring_meeting_count,
                meetings_you_organized = EXCLUDED.meetings_you_organized,
                meetings_they_organized = EXCLUDED.meetings_they_organized,
                weighted_meeting_score = EXCLUDED.weighted_meeting_score,
                meeting_recurrence_score = EXCLUDED.meeting_recurrence_score,
                first_contact_at = EXCLUDED.first_contact_at,
                last_contact_at = EXCLUDED.last_contact_at,
                consistency_score = EXCLUDED.consistency_score,
                initiation_score = EXCLUDED.initiation_score,
                updated_at = NOW()
        """

        for aggregate in aggregates:
            await execute_query(query, aggregate.__dict__)

    @classmethod
    async def fetch_contacts(cls, user_id: str, limit: int = 50) -> list[dict]:
        query = """
            SELECT
                id,
                contact_hash,
                email,
                display_name,
                first_contact_at,
                last_contact_at,
                email_count_30d,
                inbound_count_30d,
                outbound_count_30d,
                direct_email_count,
                cc_email_count,
                thread_count_30d,
                avg_thread_depth,
                attachment_email_count,
                starred_email_count,
                important_email_count,
                reply_rate_30d,
                median_response_hours,
                off_hours_ratio,
                threads_they_started,
                threads_you_started,
                meeting_count_30d,
                total_meeting_minutes,
                recurring_meeting_count,
                meetings_you_organized,
                meetings_they_organized,
                weighted_meeting_score,
                meeting_recurrence_score,
                consistency_score,
                initiation_score,
                vip_score,
                confidence_score
            FROM contacts
            WHERE user_id = %s
            ORDER BY
                COALESCE(vip_score, 0) DESC,
                weighted_meeting_score DESC,
                email_count_30d DESC
            LIMIT %s
        """

        return await fetch_all(query, (user_id, limit))

    @classmethod
    async def count_contacts(cls, user_id: str) -> int:
        query = """
            SELECT COUNT(*) AS total
            FROM contacts
            WHERE user_id = %s
        """

        rows = await fetch_all(query, (user_id,))
        if not rows:
            return 0
        return rows[0]["total"]
