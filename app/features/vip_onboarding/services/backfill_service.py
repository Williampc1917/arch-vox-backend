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
    ContactIdentityRecord,
    EmailMetadataRecord,
    VipBackfillJob,
)
from app.features.vip_onboarding.repository.contact_identity_repository import (
    ContactIdentityRepository,
)
from app.config import settings
from app.features.vip_onboarding.repository.vip_repository import VipRepository
from app.infrastructure.audit import audit_logger
from app.infrastructure.observability.logging import get_logger
from app.models.domain.oauth_domain import OAuthToken
from app.security.hashing import hash_email
from app.services.calendar.google_client import GoogleCalendarError, google_calendar_service
from app.services.core.token_service import get_oauth_tokens
from app.services.core.user_service import get_user_profile
from app.services.gmail.google_client import GoogleGmailError, google_gmail_service
from app.services.infrastructure.encryption_service import encrypt_data

logger = get_logger(__name__)


class VipBackfillError(Exception):
    """Raised when metadata collection fails."""


class VipBackfillService:
    """Entry point for VIP backfill operations."""

    EMAIL_LOOKBACK_DAYS = 30
    EXTENDED_EMAIL_LOOKBACK_DAYS = 90
    MIN_CONTACT_THRESHOLD = 15
    CALENDAR_LOOKBACK_DAYS = 30
    EXTENDED_CALENDAR_LOOKBACK_DAYS = 90
    CALENDAR_LOOKAHEAD_DAYS = 2
    GMAIL_FETCH_LIMIT = 500
    CALENDAR_FETCH_LIMIT = 250
    DB_BATCH_SIZE = 200
    GMAIL_UNIQUE_CONTACT_CAP = 300
    GMAIL_MAX_PAGES = 10
    CALENDAR_MAX_EVENTS = 1000
    GMAIL_METADATA_HEADERS = [
        "List-Unsubscribe",
        "List-Id",
        "Precedence",
        "Auto-Submitted",
    ]

    async def run(self, job: VipBackfillJob) -> None:
        """Collect email + calendar metadata for the given job."""
        logger.info("Starting VIP backfill", job_id=job.id, user_id=job.user_id)

        profile = await get_user_profile(job.user_id)
        if not profile or not profile.email:
            raise VipBackfillError("User profile missing or email unavailable")

        user_email = profile.email.strip().lower()
        tokens = await self._get_tokens(job.user_id)

        now = datetime.now(UTC)
        lookback_days = self.EMAIL_LOOKBACK_DAYS
        calendar_lookback_days = self.CALENDAR_LOOKBACK_DAYS
        calendar_window_end = now + timedelta(days=self.CALENDAR_LOOKAHEAD_DAYS)

        email_window_start = now - timedelta(days=lookback_days)

        email_records, email_identities, unique_contact_count = await self._collect_email_metadata(
            job,
            tokens,
            user_email,
            email_window_start,
            lookback_days,
            enable_prefilter=settings.VIP_PREFILTER_ENABLED,
            collect_identities=settings.VIP_IDENTITY_ENABLED,
        )

        if (
            settings.VIP_LOOKBACK_EXPANSION_ENABLED
            and unique_contact_count < self.MIN_CONTACT_THRESHOLD
        ):
            lookback_days = self.EXTENDED_EMAIL_LOOKBACK_DAYS
            calendar_lookback_days = self.EXTENDED_CALENDAR_LOOKBACK_DAYS
            email_window_start = now - timedelta(days=lookback_days)

            email_records, email_identities, unique_contact_count = (
                await self._collect_email_metadata(
                    job,
                    tokens,
                    user_email,
                    email_window_start,
                    lookback_days,
                    enable_prefilter=settings.VIP_PREFILTER_ENABLED,
                    collect_identities=settings.VIP_IDENTITY_ENABLED,
                )
            )

        await VipRepository.replace_email_metadata(job.user_id, email_window_start, email_records)

        event_window_start = now - timedelta(days=calendar_lookback_days)
        event_records, event_identities = await self._collect_event_metadata(
            job,
            tokens,
            user_email,
            event_window_start,
            calendar_window_end,
            collect_identities=settings.VIP_IDENTITY_ENABLED,
        )
        await VipRepository.replace_event_metadata(job.user_id, event_window_start, event_records)
        if settings.VIP_IDENTITY_ENABLED:
            await self._persist_contact_identities(
                job.user_id, self._merge_identities(email_identities, event_identities)
            )

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

        if not tokens.has_calendar_access():
            raise VipBackfillError("OAuth token missing Calendar scopes")

        return tokens

    async def _collect_email_metadata(
        self,
        job: VipBackfillJob,
        tokens: OAuthToken,
        user_email: str,
        window_start: datetime,
        lookback_days: int,
        enable_prefilter: bool,
        collect_identities: bool,
    ) -> tuple[list[EmailMetadataRecord], dict[str, tuple[str, str | None]], int]:
        if not tokens.has_gmail_access():
            logger.warning("Skipping Gmail metadata - missing scopes", user_id=job.user_id)
            return [], {}, 0

        records: list[EmailMetadataRecord] = []
        identities: dict[str, tuple[str, str | None]] = {}
        unique_contacts: set[str] = set()
        page_token: str | None = None
        pages_seen = 0

        while pages_seen < self.GMAIL_MAX_PAGES:
            pages_seen += 1
            try:
                query_parts = [f"newer_than:{lookback_days}d"]
                if enable_prefilter:
                    query_parts.append("-category:promotions -category:social -category:forums")
                    query_parts.append('-from:(noreply OR "no-reply" OR "do-not-reply")')

                messages, _, next_page_token = await google_gmail_service.list_messages(
                    tokens.access_token,
                    max_results=self.GMAIL_FETCH_LIMIT,
                    query=" ".join(query_parts),
                    page_token=page_token,
                    message_format="metadata",
                    metadata_headers=self.GMAIL_METADATA_HEADERS,
                )
            except GoogleGmailError as exc:
                raise VipBackfillError(f"Gmail API error: {exc}") from exc

            if not messages:
                break

            for message in messages:
                timestamp = message.get_received_datetime()
                if not timestamp:
                    continue

                timestamp = self._ensure_utc(timestamp)
                if timestamp < window_start:
                    continue

                from_email = (message.sender or {}).get("email")
                from_name = (message.sender or {}).get("name") or None
                to_emails = [
                    (entry.get("email") or "").strip().lower()
                    for entry in (getattr(message, "recipients", []) or [])
                    if entry.get("email")
                ]
                to_entries = getattr(message, "recipients", []) or []
                to_names = [
                    (entry.get("name") or "").strip() or None
                    for entry in to_entries
                    if entry.get("email")
                ]
                primary_to = to_emails[0] if to_emails else None

                cc_list = getattr(message, "cc", []) or []
                cc_emails = [
                    (entry.get("email") or "").strip().lower()
                    for entry in cc_list
                    if entry.get("email")
                ]
                cc_names = [
                    (entry.get("name") or "").strip() or None
                    for entry in cc_list
                    if entry.get("email")
                ]
                extra_to = [email for email in to_emails[1:] if email]
                combined_cc = [
                    email
                    for email in (cc_emails + extra_to)
                    if email and email != primary_to and email != user_email
                ]
                cc_hashes = self._hash_unique_attendees(combined_cc)

                headers = getattr(message, "headers", {}) or {}
                labels = set(message.label_ids or [])

                if enable_prefilter:
                    if (
                        "CATEGORY_PROMOTIONS" in labels
                        or "CATEGORY_SOCIAL" in labels
                        or "CATEGORY_FORUMS" in labels
                    ):
                        continue

                    if self._is_automated_sender(headers):
                        continue

                direction = self._determine_direction(from_email, user_email)
                normalized_from = (from_email or "").strip().lower()
                normalized_to = (primary_to or "").strip().lower() if primary_to else ""
                if direction == "in" and not normalized_from:
                    continue
                if direction == "out" and not normalized_to:
                    continue

                record = EmailMetadataRecord(
                    user_id=job.user_id,
                    message_id=message.id or "",
                    thread_id=message.thread_id or "",
                    from_contact_hash=hash_email(normalized_from),
                    to_contact_hash=hash_email(normalized_to),
                    internal_timestamp=timestamp,
                    direction=direction,
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

                if collect_identities:
                    self._maybe_add_identity(identities, from_email, from_name, user_email)
                    for email, name in zip(to_emails, to_names, strict=False):
                        self._maybe_add_identity(identities, email, name, user_email)
                    for email, name in zip(cc_emails, cc_names, strict=False):
                        self._maybe_add_identity(identities, email, name, user_email)

                for email in [from_email, *to_emails, *cc_emails]:
                    normalized = (email or "").strip().lower()
                    if not normalized or normalized == user_email:
                        continue
                    unique_contacts.add(hash_email(normalized))

                if len(unique_contacts) >= self.GMAIL_UNIQUE_CONTACT_CAP:
                    break

            if len(unique_contacts) >= self.GMAIL_UNIQUE_CONTACT_CAP:
                break
            if not next_page_token:
                break
            page_token = next_page_token

        logger.info(
            "Collected Gmail metadata",
            job_id=job.id,
            user_id=job.user_id,
            message_count=len(records),
        )
        return records, identities, len(unique_contacts)

    async def _collect_event_metadata(
        self,
        job: VipBackfillJob,
        tokens: OAuthToken,
        user_email: str,
        window_start: datetime,
        window_end: datetime,
        collect_identities: bool,
    ) -> tuple[list[CalendarEventRecord], dict[str, tuple[str, str | None]]]:
        if not tokens.has_calendar_access():
            logger.info("Skipping calendar metadata - missing scopes", user_id=job.user_id)
            return [], {}

        records: list[CalendarEventRecord] = []
        identities: dict[str, tuple[str, str | None]] = {}
        page_token: str | None = None

        while True:
            try:
                events, next_page_token = await google_calendar_service.list_events(
                    tokens.access_token,
                    time_min=window_start,
                    time_max=window_end,
                    max_results=self.CALENDAR_FETCH_LIMIT,
                    page_token=page_token,
                )
            except GoogleCalendarError as exc:
                raise VipBackfillError(f"Calendar API error: {exc}") from exc

            if not events:
                break

            for event in events:
                if not event.start_time or not event.end_time:
                    continue

                start_time = self._ensure_utc(event.start_time)
                end_time = self._ensure_utc(event.end_time)

                attendee_emails = []
                attendee_names = []
                for attendee in event.attendees or []:
                    email = (attendee.get("email") or "").strip().lower()
                    if not email:
                        continue
                    attendee_emails.append(email)
                    attendee_names.append((attendee.get("displayName") or "").strip() or None)
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

                if collect_identities:
                    for email, name in zip(attendee_emails, attendee_names, strict=False):
                        self._maybe_add_identity(identities, email, name, user_email=user_email)

                if len(records) >= self.CALENDAR_MAX_EVENTS:
                    break

            if len(records) >= self.CALENDAR_MAX_EVENTS:
                break
            if not next_page_token:
                break
            page_token = next_page_token

        logger.info(
            "Collected calendar metadata",
            job_id=job.id,
            user_id=job.user_id,
            event_count=len(records),
        )
        return records, identities

    async def _persist_contact_identities(
        self,
        user_id: str,
        identities: dict[str, tuple[str, str | None]],
    ) -> None:
        records: list[ContactIdentityRecord] = []
        for contact_hash, (email, name) in identities.items():
            if not email:
                continue
            records.append(
                ContactIdentityRecord(
                    user_id=user_id,
                    contact_hash=contact_hash,
                    email_encrypted=encrypt_data(email),
                    display_name_encrypted=encrypt_data(name) if name else None,
                )
            )

        if not records:
            return

        await ContactIdentityRepository.upsert_identities(records)
        await audit_logger.log(
            user_id=user_id,
            action="vip_contact_identities_backfilled",
            resource_type="vip_contact_identity",
            resource_count=len(records),
            pii_fields=["email", "display_name"],
            metadata={"source": "vip_backfill"},
        )

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

    def _is_automated_sender(self, headers: dict) -> bool:
        if not headers:
            return False

        lower_headers = {k.lower(): v for k, v in headers.items()}
        if lower_headers.get("list-unsubscribe") or lower_headers.get("list-id"):
            return True

        precedence = (lower_headers.get("precedence") or "").lower()
        if "bulk" in precedence or "list" in precedence:
            return True

        auto_submitted = (lower_headers.get("auto-submitted") or "").lower()
        if auto_submitted and auto_submitted != "no":
            return True

        return False

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

    def _maybe_add_identity(
        self,
        identities: dict[str, tuple[str, str | None]],
        email: str | None,
        name: str | None,
        user_email: str | None,
    ) -> None:
        normalized = (email or "").strip().lower()
        if not normalized:
            return
        if user_email and normalized == user_email:
            return

        contact_hash = hash_email(normalized)
        existing = identities.get(contact_hash)
        if existing:
            _, existing_name = existing
            if existing_name or not name:
                return

        identities[contact_hash] = (normalized, name)

    def _merge_identities(
        self,
        base: dict[str, tuple[str, str | None]],
        extra: dict[str, tuple[str, str | None]],
    ) -> dict[str, tuple[str, str | None]]:
        merged = dict(base)
        for contact_hash, (email, name) in extra.items():
            if contact_hash not in merged:
                merged[contact_hash] = (email, name)
                continue
            existing_email, existing_name = merged[contact_hash]
            if existing_name or not name:
                merged[contact_hash] = (existing_email, existing_name)
            else:
                merged[contact_hash] = (email, name)
        return merged


vip_backfill_service = VipBackfillService()
