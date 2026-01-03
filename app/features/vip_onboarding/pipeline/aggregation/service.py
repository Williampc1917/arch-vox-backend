"""
Contact aggregation service.

Transforms raw metadata (email + calendar) into per-contact statistics
stored in the contacts table. Follows the "Contact Aggregation Guide".
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, DefaultDict

from .repository import (
    ContactAggregate,
    ContactAggregationRepository,
    EmailMetadataRow,
    EventMetadataRow,
)
from app.infrastructure.observability.logging import get_logger
from app.security.hashing import hash_email
from app.services.user_service import get_user_profile

logger = get_logger(__name__)


@dataclass
class _ThreadMessage:
    timestamp: datetime
    direction: str  # "in" or "out"


@dataclass
class _ContactWorkingSet:
    contact_hash: str
    email_count: int = 0
    inbound_count: int = 0
    outbound_count: int = 0
    cc_count: int = 0
    attachment_count: int = 0
    starred_count: int = 0
    important_count: int = 0
    thread_ids: set[str] = field(default_factory=set)
    thread_messages: DefaultDict[str, list[_ThreadMessage]] = field(
        default_factory=lambda: defaultdict(list)
    )
    timestamps: list[datetime] = field(default_factory=list)
    off_hours_count: int = 0
    meeting_count: int = 0
    total_meeting_minutes: int = 0
    recurring_meeting_count: int = 0
    meetings_you_organized: int = 0
    meetings_they_organized: int = 0
    weighted_meeting_score: float = 0.0
    meeting_times: list[datetime] = field(default_factory=list)
    last_meeting_at: datetime | None = None
    first_meeting_at: datetime | None = None


class ContactAggregationService:
    EMAIL_LOOKBACK_DAYS = 30
    MEETING_LOOKBACK_DAYS = 30

    async def aggregate_contacts_for_user(self, user_id: str) -> int:
        profile = await get_user_profile(user_id)
        if not profile or not profile.email:
            logger.warning("Skipping contact aggregation - user profile missing email", user_id=user_id)
            return 0

        user_email_hash = hash_email(profile.email)
        now = datetime.now(UTC)
        email_window_start = now - timedelta(days=self.EMAIL_LOOKBACK_DAYS)
        meeting_window_start = now - timedelta(days=self.MEETING_LOOKBACK_DAYS)

        emails = await ContactAggregationRepository.fetch_email_metadata(user_id, email_window_start)
        events = await ContactAggregationRepository.fetch_event_metadata(
            user_id, meeting_window_start, now
        )

        aggregates = self._build_contact_aggregates(user_id, user_email_hash, emails, events)

        if not aggregates:
            logger.info("No contacts to aggregate", user_id=user_id)
            return 0

        await ContactAggregationRepository.upsert_contacts(aggregates.values())
        logger.info("Contacts aggregated", user_id=user_id, contact_count=len(aggregates))
        return len(aggregates)

    async def get_aggregated_contacts(self, user_id: str, limit: int = 50) -> list[dict]:
        return await ContactAggregationRepository.fetch_contacts(user_id, limit)

    async def has_contacts(self, user_id: str) -> bool:
        total = await ContactAggregationRepository.count_contacts(user_id)
        return total > 0

    def _build_contact_aggregates(
        self,
        user_id: str,
        user_email_hash: str,
        emails: list[EmailMetadataRow],
        events: list[EventMetadataRow],
    ) -> dict[str, ContactAggregate]:
        contacts: dict[str, _ContactWorkingSet] = {}

        def ensure_contact(contact_hash: str) -> _ContactWorkingSet:
            if contact_hash not in contacts:
                contacts[contact_hash] = _ContactWorkingSet(contact_hash=contact_hash)
            return contacts[contact_hash]

        # Process direct emails
        for email in emails:
            primary_hash = email.from_contact_hash if email.direction == "in" else email.to_contact_hash
            if not primary_hash:
                continue
            contact = ensure_contact(primary_hash)
            contact.email_count += 1
            contact.timestamps.append(email.timestamp)
            contact.thread_ids.add(email.thread_id)
            contact.thread_messages[email.thread_id].append(
                _ThreadMessage(timestamp=email.timestamp, direction=email.direction)
            )
            if email.direction == "in":
                contact.inbound_count += 1
            else:
                contact.outbound_count += 1
            if email.has_attachment:
                contact.attachment_count += 1
            if email.is_starred:
                contact.starred_count += 1
            if email.is_important:
                contact.important_count += 1
            if email.hour_of_day is not None and email.day_of_week is not None:
                if (
                    email.hour_of_day < 8
                    or email.hour_of_day >= 19
                    or email.day_of_week in (0, 6)
                ):
                    contact.off_hours_count += 1

            # Count CC involvement for other contacts
            for cc_hash in email.cc_contact_hashes or []:
                if not cc_hash or cc_hash == primary_hash:
                    continue
                cc_contact = ensure_contact(cc_hash)
                cc_contact.cc_count += 1

        # Process meetings
        for event in events:
            attendee_hashes = [
                hash_value for hash_value in event.attendee_contact_hashes or [] if hash_value
            ]
            attendee_set = {hash_value for hash_value in attendee_hashes if hash_value != user_email_hash}
            if not attendee_set:
                continue
            attendee_count = len(attendee_set)
            duration = max(0, event.duration_minutes or 0)
            weight = self._calculate_meeting_weight(attendee_count, duration, event.is_recurring)

            for contact_hash in attendee_set:
                contact = ensure_contact(contact_hash)
                contact.meeting_count += 1
                contact.total_meeting_minutes += duration
                if event.is_recurring:
                    contact.recurring_meeting_count += 1
                if event.user_is_organizer:
                    contact.meetings_you_organized += 1
                if event.organizer_hash and event.organizer_hash == contact_hash:
                    contact.meetings_they_organized += 1
                contact.weighted_meeting_score += weight
                contact.meeting_times.append(event.start_time)

                if not contact.first_meeting_at or event.start_time < contact.first_meeting_at:
                    contact.first_meeting_at = event.start_time
                if not contact.last_meeting_at or event.start_time > contact.last_meeting_at:
                    contact.last_meeting_at = event.start_time

        aggregates: dict[str, ContactAggregate] = {}
        reply_window = timedelta(hours=48)

        for contact_hash, working in contacts.items():
            thread_count = len(working.thread_ids)
            avg_thread_depth = (
                working.email_count / thread_count if thread_count > 0 else 0.0
            )
            first_contact_at = (
                min(working.timestamps) if working.timestamps else working.first_meeting_at
            )
            last_contact_at = (
                max(working.timestamps) if working.timestamps else working.last_meeting_at
            )
            if working.last_meeting_at and (
                not last_contact_at or working.last_meeting_at > last_contact_at
            ):
                last_contact_at = working.last_meeting_at
            if working.first_meeting_at and (
                not first_contact_at or working.first_meeting_at < first_contact_at
            ):
                first_contact_at = working.first_meeting_at

            threads_they_started = 0
            threads_you_started = 0
            total_inbound = working.inbound_count
            replies = 0
            response_hours: list[float] = []

            for thread_id, messages in working.thread_messages.items():
                ordered = sorted(messages, key=lambda m: m.timestamp)
                if not ordered:
                    continue
                first_direction = ordered[0].direction
                if first_direction == "in":
                    threads_they_started += 1
                else:
                    threads_you_started += 1

                for idx, current in enumerate(ordered):
                    if current.direction != "in":
                        continue
                    reply_ts = None
                    for future in ordered[idx + 1 :]:
                        if future.direction == "out":
                            if future.timestamp - current.timestamp <= reply_window:
                                reply_ts = future.timestamp
                                break
                            if future.timestamp - current.timestamp > reply_window:
                                break
                    if reply_ts:
                        replies += 1
                        delta_hours = (reply_ts - current.timestamp).total_seconds() / 3600
                        response_hours.append(delta_hours)

            reply_rate = replies / total_inbound if total_inbound > 0 else 0.0
            median_response = (
                statistics.median(response_hours) if response_hours else None
            )
            off_hours_ratio = (
                working.off_hours_count / working.email_count
                if working.email_count > 0
                else 0.0
            )
            consistency_score = self._compute_consistency_score(working.timestamps)
            initiation_score = self._compute_initiation_score(
                threads_they_started, threads_you_started
            )
            meeting_recurrence_score = self._compute_meeting_recurrence_score(
                working.meeting_times
            )

            aggregate = ContactAggregate(
                user_id=user_id,
                contact_hash=contact_hash,
                email=None,  # TODO: Populate from backfill service contact mapping
                display_name=None,  # TODO: Populate from backfill service contact mapping
                email_count_30d=working.email_count,
                inbound_count_30d=working.inbound_count,
                outbound_count_30d=working.outbound_count,
                direct_email_count=working.outbound_count,
                cc_email_count=working.cc_count,
                thread_count_30d=thread_count,
                avg_thread_depth=avg_thread_depth,
                attachment_email_count=working.attachment_count,
                starred_email_count=working.starred_count,
                important_email_count=working.important_count,
                reply_rate_30d=reply_rate,
                median_response_hours=median_response,
                off_hours_ratio=off_hours_ratio,
                threads_they_started=threads_they_started,
                threads_you_started=threads_you_started,
                meeting_count_30d=working.meeting_count,
                total_meeting_minutes=working.total_meeting_minutes,
                recurring_meeting_count=working.recurring_meeting_count,
                meetings_you_organized=working.meetings_you_organized,
                meetings_they_organized=working.meetings_they_organized,
                weighted_meeting_score=working.weighted_meeting_score,
                meeting_recurrence_score=meeting_recurrence_score,
                first_contact_at=first_contact_at,
                last_contact_at=last_contact_at,
                consistency_score=consistency_score,
                initiation_score=initiation_score,
            )
            aggregates[contact_hash] = aggregate

        return aggregates

    def _calculate_meeting_weight(
        self, attendee_count: int, duration_minutes: int, is_recurring: bool
    ) -> float:
        if attendee_count <= 2:
            base = 1.0
        elif attendee_count <= 5:
            base = 0.7
        elif attendee_count <= 10:
            base = 0.4
        elif attendee_count <= 20:
            base = 0.2
        else:
            base = 0.05

        if duration_minutes < 30:
            duration_modifier = 0.5
        elif duration_minutes < 60:
            duration_modifier = 1.0
        elif duration_minutes < 120:
            duration_modifier = 1.25
        else:
            duration_modifier = 1.5

        recurring_bonus = 1.1 if is_recurring else 1.0
        return base * duration_modifier * recurring_bonus

    def _compute_consistency_score(self, timestamps: list[datetime]) -> float:
        if len(timestamps) < 4:
            return 0.5

        ordered = sorted(timestamps)
        intervals = [
            (ordered[i + 1] - ordered[i]).total_seconds() / 86400 for i in range(len(ordered) - 1)
        ]
        intervals = [interval for interval in intervals if interval > 0]
        if not intervals:
            return 1.0

        mean_interval = statistics.mean(intervals)
        if mean_interval <= 0:
            return 1.0

        std_dev = statistics.pstdev(intervals) if len(intervals) > 1 else 0.0
        coefficient = std_dev / mean_interval if mean_interval else 0.0
        score = 1.0 - (coefficient / 3.0)
        return max(0.0, min(1.0, score))

    def _compute_initiation_score(self, threads_they_started: int, threads_you_started: int) -> float:
        total = threads_they_started + threads_you_started
        if total == 0:
            return 0.5

        their_rate = threads_they_started / total
        if their_rate >= 0.5:
            score = 0.8 + 0.2 * (their_rate - 0.5) * 2
        else:
            score = 0.5 + 0.3 * their_rate * 2
        return max(0.5, min(1.0, score))

    def _compute_meeting_recurrence_score(self, meeting_times: list[datetime]) -> float:
        if len(meeting_times) < 3:
            return 0.0

        ordered = sorted(meeting_times)
        intervals = [
            (ordered[i + 1] - ordered[i]).days for i in range(len(ordered) - 1)
        ]
        if not intervals:
            return 0.0

        weekly = sum(1 for interval in intervals if 5 <= interval <= 9)
        biweekly = sum(1 for interval in intervals if 11 <= interval <= 17)
        total = len(intervals)

        weekly_ratio = weekly / total
        biweekly_ratio = biweekly / total
        combined_ratio = (weekly + biweekly) / total

        if weekly_ratio >= 0.6:
            return 1.0
        if biweekly_ratio >= 0.6:
            return 0.8
        if combined_ratio >= 0.4:
            return 0.5
        return 0.2


contact_aggregation_service = ContactAggregationService()
