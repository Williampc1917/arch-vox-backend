"""
Persistence layer for VIP onboarding feature.

Provides a single place for job lifecycle updates and metadata inserts so
the scheduler/worker logic can stay slim and focus on orchestration.
"""

from datetime import UTC, datetime
from typing import Iterable

from app.db.helpers import (
    DatabaseError,
    execute_query,
    execute_transaction,
    fetch_one,
    fetch_val,
)
from app.db.pool import get_db_connection
from app.features.vip_onboarding.domain import (
    CalendarEventRecord,
    EmailMetadataRecord,
    VipBackfillJob,
)
from app.infrastructure.observability.logging import get_logger

logger = get_logger(__name__)


class VipRepositoryError(DatabaseError):
    """More specific exception for repository failures."""


class VipRepository:
    """Persistence helpers backing the VIP backfill job."""

    JOB_SELECT_COLUMNS = """
        id, user_id, status, attempts, trigger_reason,
        created_at, started_at, completed_at, error_message
    """

    @classmethod
    def _row_to_job(cls, row: dict | None) -> VipBackfillJob | None:
        if not row:
            return None

        return VipBackfillJob(
            id=str(row["id"]),
            user_id=str(row["user_id"]),
            status=row["status"],
            attempts=row["attempts"],
            trigger_reason=row.get("trigger_reason"),
            created_at=row["created_at"],
            started_at=row.get("started_at"),
            completed_at=row.get("completed_at"),
            error_message=row.get("error_message"),
        )

    @classmethod
    async def create_job(cls, user_id: str, trigger_reason: str) -> VipBackfillJob:
        """
        Insert a new job row (status=pending) and return the record.
        """

        attempts_query = """
            SELECT COALESCE(MAX(attempts), 0)
            FROM user_vip_backfill_jobs
            WHERE user_id = %s
        """

        current_attempts = await fetch_val(attempts_query, (user_id,))
        next_attempt = (current_attempts or 0) + 1

        insert_query = f"""
            INSERT INTO user_vip_backfill_jobs (
                user_id, status, attempts, trigger_reason
            )
            VALUES (%s, 'pending', %s, %s)
            RETURNING {cls.JOB_SELECT_COLUMNS}
        """

        row = await fetch_one(insert_query, (user_id, next_attempt, trigger_reason))
        if not row:
            raise VipRepositoryError("Failed to create VIP backfill job", operation="create_job")

        logger.info(
            "VIP backfill job created", user_id=user_id, trigger_reason=trigger_reason, attempt=next_attempt
        )
        return cls._row_to_job(row)

    @classmethod
    async def load_job(cls, job_id: str) -> VipBackfillJob | None:
        """Return job row if it exists."""

        query = f"SELECT {cls.JOB_SELECT_COLUMNS} FROM user_vip_backfill_jobs WHERE id = %s"
        row = await fetch_one(query, (job_id,))
        return cls._row_to_job(row)

    @classmethod
    async def mark_job_running(cls, job_id: str) -> None:
        """Mark the job as running and timestamp the start."""

        query = """
            UPDATE user_vip_backfill_jobs
            SET status = 'running',
                started_at = NOW()
            WHERE id = %s
        """

        await execute_query(query, (job_id,))
        logger.info("VIP backfill job running", job_id=job_id)

    @classmethod
    async def mark_job_completed(cls, job_id: str) -> None:
        """Mark the job as completed."""

        query = """
            UPDATE user_vip_backfill_jobs
            SET status = 'completed',
                completed_at = NOW(),
                error_message = NULL
            WHERE id = %s
        """

        await execute_query(query, (job_id,))
        logger.info("VIP backfill job completed", job_id=job_id)

    @classmethod
    async def mark_job_failed(cls, job_id: str, error_message: str) -> None:
        """Mark the job as failed and store the error."""

        truncated_error = (error_message or "")[:500]
        query = """
            UPDATE user_vip_backfill_jobs
            SET status = 'failed',
                completed_at = NOW(),
                error_message = %s
            WHERE id = %s
        """

        await execute_query(query, (truncated_error, job_id))
        logger.warning("VIP backfill job failed", job_id=job_id, error=truncated_error)

    @classmethod
    async def record_email_metadata(
        cls, user_id: str, records: Iterable[EmailMetadataRecord]
    ) -> None:
        """Bulk insert email metadata rows."""

        payload = [
            (
                user_id,
                record.message_id,
                record.thread_id or "",
                record.from_contact_hash,
                record.to_contact_hash,
                record.internal_timestamp,
                record.direction,
            )
            for record in records
        ]

        if not payload:
            return

        insert_query = """
            INSERT INTO email_metadata (
                user_id, message_id, thread_id,
                from_contact_hash, to_contact_hash,
                timestamp, direction
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (user_id, message_id) DO NOTHING
        """

        async with await get_db_connection() as conn:
            await conn.executemany(insert_query, payload)

        logger.info(
            "Email metadata records stored",
            user_id=user_id,
            batch_size=len(payload),
        )

    @classmethod
    async def record_event_metadata(
        cls, user_id: str, records: Iterable[CalendarEventRecord]
    ) -> None:
        """Bulk insert calendar event metadata rows."""

        payload = [
            (
                user_id,
                record.event_id,
                record.start_time,
                record.end_time,
                record.attendee_hashes,
            )
            for record in records
        ]

        if not payload:
            return

        insert_query = """
            INSERT INTO events_metadata (
                user_id, event_id, start_time, end_time, attendee_contact_hashes
            )
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (user_id, event_id) DO NOTHING
        """

        async with await get_db_connection() as conn:
            await conn.executemany(insert_query, payload)

        logger.info(
            "Event metadata records stored",
            user_id=user_id,
            batch_size=len(payload),
        )

    @classmethod
    async def prune_recent_metadata(cls, user_id: str, window_start: datetime) -> None:
        """
        Remove metadata in the provided window before inserting a fresh slice.
        """

        if window_start.tzinfo is None:
            window_start = window_start.replace(tzinfo=UTC)

        queries = [
            (
                "DELETE FROM email_metadata WHERE user_id = %s AND timestamp >= %s",
                (user_id, window_start),
            ),
            (
                "DELETE FROM events_metadata WHERE user_id = %s AND start_time >= %s",
                (user_id, window_start),
            ),
        ]

        await execute_transaction(queries)
        logger.info("Pruned VIP metadata window", user_id=user_id, window_start=window_start.isoformat())
