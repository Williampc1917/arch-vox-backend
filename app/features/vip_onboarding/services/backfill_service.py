"""
VIP backfill orchestration service.

Fetches Gmail and Calendar metadata (no content), hashes identifiers,
and persists everything through the repository so downstream features
can aggregate VIP suggestions and contact stats.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

from app.features.vip_onboarding.domain import (
    CalendarEventRecord,
    EmailMetadataRecord,
    VipBackfillJob,
)
from app.features.vip_onboarding.repository.vip_repository import VipRepository
from app.infrastructure.observability.logging import get_logger
from app.models.domain.oauth_domain import OAuthToken
from app.security.hashing import hash_email
from app.services.calendar.google_client import GoogleCalendarError, google_calendar_service
from app.services.core.token_service import get_oauth_tokens
from app.services.core.user_service import get_user_profile
from app.services.gmail.google_client import GoogleGmailError, google_gmail_service

logger = get_logger(__name__)


class VipBackfillError(Exception):
    """Raised when metadata collection fails."""


class VipBackfillService:
    """Entry point for VIP backfill operations."""

    EMAIL_LOOKBACK_DAYS = 30
    CALENDAR_LOOKBACK_DAYS = 30
    CALENDAR_LOOKAHEAD_DAYS = 2
    GMAIL_FETCH_LIMIT = 500
    CALENDAR_FETCH_LIMIT = 250
    DB_BATCH_SIZE = 200

    async def run(self, job: VipBackfillJob) -> None:
        """Collect email + calendar metadata for the given job."""
        logger.info("Starting VIP backfill", job_id=job.id, user_id=job.user_id)

        profile = await get_user_profile(job.user_id)
        if not profile or not profile.email:
            raise VipBackfillError("User profile missing or email unavailable")

        user_email = profile.email.strip().lower()
        tokens = await self._get_tokens(job.user_id)

        now = datetime.now(UTC)
        email_window_start = now - timedelta(days=self.EMAIL_LOOKBACK_DAYS)
        calendar_window_end = now + timedelta(days=self.CALENDAR_LOOKAHEAD_DAYS)

        await VipRepository.prune_recent_metadata(job.user_id, email_window_start)

        email_records = await self._collect_email_metadata(
            job, tokens, user_email, email_window_start
        )
        await self._persist_email_records(job.user_id, email_records)

        event_records = await self._collect_event_metadata(
            job, tokens, email_window_start, calendar_window_end
        )
        await self._persist_event_records(job.user_id, event_records)

        logger.info(
            "VIP backfill completed",
            job_id=job.id,
            user_id=job.user_id,
            email_records=len(email_records),
            event_records=len(event_records),
        )

    async def _get_tokens(self, user_id: str) -> OAuthToken:
        tokens = await get_oauth_tokens(user_id)
        if not tokens:
            raise VipBackfillError("OAuth tokens not found for user")

        if tokens.is_expired():
            raise VipBackfillError("OAuth token expired")

        if not tokens.has_gmail_access():
            raise VipBackfillError("OAuth token missing Gmail scopes")

        return tokens

    async def _collect_email_metadata(
        self,
        job: VipBackfillJob,
        tokens: OAuthToken,
        user_email: str,
        window_start: datetime,
    ) -> list[EmailMetadataRecord]:
        if not tokens.has_gmail_access():
            logger.warning("Skipping Gmail metadata - missing scopes", user_id=job.user_id)
            return []

        try:
            messages, _ = await google_gmail_service.list_messages(
                tokens.access_token,
                max_results=self.GMAIL_FETCH_LIMIT,
                query=f"newer_than:{self.EMAIL_LOOKBACK_DAYS}d",
            )
        except GoogleGmailError as exc:
            raise VipBackfillError(f"Gmail API error: {exc}") from exc

        records: list[EmailMetadataRecord] = []
        for message in messages:
            timestamp = message.get_received_datetime()
            if not timestamp:
                continue

            timestamp = self._ensure_utc(timestamp)
            if timestamp < window_start:
                continue

            from_email = (message.sender or {}).get("email")
            to_email = (message.recipient or {}).get("email")
            cc_hashes = self._hash_cc_addresses(message)
            headers = getattr(message, "headers", {})
            labels = set(message.label_ids or [])

            record = EmailMetadataRecord(
                user_id=job.user_id,
                message_id=message.id or "",
                thread_id=message.thread_id or "",
                from_contact_hash=hash_email(from_email),
                to_contact_hash=hash_email(to_email),
                internal_timestamp=timestamp,
                direction=self._determine_direction(from_email, user_email),
                cc_contact_hashes=cc_hashes,
                is_reply=self._is_reply(headers),
                has_attachment=bool(getattr(message, "has_attachments", lambda: False)()),
                is_starred="STARRED" in labels,
                is_important="IMPORTANT" in labels,
                is_promotional="CATEGORY_PROMOTIONS" in labels,
                is_social="CATEGORY_SOCIAL" in labels,
                subject_length=len(getattr(message, "subject", "") or ""),
                hour_of_day=timestamp.hour,
                day_of_week=self._day_of_week(timestamp),
            )
            records.append(record)

        logger.info(
            "Collected Gmail metadata",
            job_id=job.id,
            user_id=job.user_id,
            message_count=len(records),
        )
        return records

    async def _collect_event_metadata(
        self,
        job: VipBackfillJob,
        tokens: OAuthToken,
        window_start: datetime,
        window_end: datetime,
    ) -> list[CalendarEventRecord]:
        if not tokens.has_calendar_access():
            logger.info("Skipping calendar metadata - missing scopes", user_id=job.user_id)
            return []

        try:
            events = await google_calendar_service.list_events(
                tokens.access_token,
                time_min=window_start,
                time_max=window_end,
                max_results=self.CALENDAR_FETCH_LIMIT,
            )
        except GoogleCalendarError as exc:
            raise VipBackfillError(f"Calendar API error: {exc}") from exc

        records: list[CalendarEventRecord] = []
        for event in events:
            if not event.start_time or not event.end_time:
                continue

            start_time = self._ensure_utc(event.start_time)
            end_time = self._ensure_utc(event.end_time)

            attendee_emails = [
                (attendee.get("email") or "").strip().lower()
                for attendee in (event.attendees or [])
                if attendee.get("email")
            ]
            attendee_hashes = self._hash_unique_attendees(attendee_emails)
            organizer = event.raw_data.get("organizer", {}) if event.raw_data else {}
            recurrence_list = event.raw_data.get("recurrence") if event.raw_data else None
            user_response = self._get_user_response(event.attendees or [])

            record = CalendarEventRecord(
                user_id=job.user_id,
                event_id=event.id or "",
                start_time=start_time,
                end_time=end_time,
                attendee_hashes=attendee_hashes,
                duration_minutes=event.duration_minutes(),
                is_recurring=bool(
                    event.raw_data.get("recurringEventId") if event.raw_data else False
                ),
                recurrence_rule=(recurrence_list[0] if recurrence_list else None),
                organizer_hash=(
                    hash_email(organizer.get("email")) if organizer.get("email") else None
                ),
                user_is_organizer=bool(organizer.get("self")),
                user_response=user_response,
                is_one_on_one=self._is_one_on_one(attendee_emails),
                event_type=event.raw_data.get("eventType") if event.raw_data else None,
            )
            records.append(record)

        logger.info(
            "Collected calendar metadata",
            job_id=job.id,
            user_id=job.user_id,
            event_count=len(records),
        )
        return records

    async def _persist_email_records(
        self, user_id: str, records: Iterable[EmailMetadataRecord]
    ) -> None:
        batch: list[EmailMetadataRecord] = []
        for record in records:
            batch.append(record)
            if len(batch) >= self.DB_BATCH_SIZE:
                await VipRepository.record_email_metadata(user_id, batch)
                batch.clear()

        if batch:
            await VipRepository.record_email_metadata(user_id, batch)

    async def _persist_event_records(
        self, user_id: str, records: Iterable[CalendarEventRecord]
    ) -> None:
        batch: list[CalendarEventRecord] = []
        for record in records:
            batch.append(record)
            if len(batch) >= self.DB_BATCH_SIZE:
                await VipRepository.record_event_metadata(user_id, batch)
                batch.clear()

        if batch:
            await VipRepository.record_event_metadata(user_id, batch)

    def _determine_direction(self, from_email: str | None, user_email: str) -> str:
        if not from_email:
            return "in"

        normalized_from = from_email.strip().lower()
        if normalized_from and normalized_from == user_email:
            return "out"
        return "in"

    def _hash_unique_attendees(self, attendees: list[str]) -> list[str]:
        seen = set()
        hashes: list[str] = []
        for email in attendees:
            hashed = hash_email(email)
            if hashed not in seen:
                seen.add(hashed)
                hashes.append(hashed)
        return hashes

    def _ensure_utc(self, dt: datetime) -> datetime:
        if dt.tzinfo is None:
            return dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)

    def _hash_cc_addresses(self, message) -> list[str]:
        cc_list = getattr(message, "cc", []) or []
        emails = [
            (entry.get("email") or "").strip().lower() for entry in cc_list if entry.get("email")
        ]
        return self._hash_unique_attendees(emails)

    def _is_reply(self, headers: dict) -> bool:
        lower_headers = {k.lower(): v for k, v in headers.items()} if headers else {}
        return bool(lower_headers.get("in-reply-to") or lower_headers.get("references"))

    def _day_of_week(self, dt: datetime) -> int:
        # Python weekday: Monday=0. Convert to Sunday=0.
        weekday = dt.weekday()
        return (weekday + 1) % 7

    def _get_user_response(self, attendees: list[dict]) -> str | None:
        for attendee in attendees:
            if attendee.get("self"):
                return attendee.get("responseStatus")
        return None

    def _is_one_on_one(self, attendee_emails: list[str]) -> bool:
        unique = {email for email in attendee_emails if email}
        return len(unique) == 2


vip_backfill_service = VipBackfillService()
