"""
VIP scoring service - ranks aggregated contacts and persists selections.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Iterable

from .repository import VipScoringRepository
from app.infrastructure.observability.logging import get_logger

logger = get_logger(__name__)


@dataclass(slots=True)
class AggregatedContact:
    user_id: str
    contact_hash: str
    display_name: str | None
    last_contact_at: datetime | None
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
    weighted_meeting_score: float
    meeting_recurrence_score: float
    total_meeting_minutes: int
    recurring_meeting_count: int
    meetings_you_organized: int
    meetings_they_organized: int
    first_contact_at: datetime | None
    consistency_score: float
    initiation_score: float

    @property
    def last_activity(self) -> datetime | None:
        return self.last_contact_at or self.first_contact_at
11113

@dataclass(slots=True)
class ScoredContact:
    contact_hash: str
    display_name: str | None
    vip_score: float
    confidence_score: float
    component_scores: dict[str, float]
    raw_metrics: dict[str, Any]


class ScoringService:
    DEFAULT_LIMIT = 50
    MAX_SELECTION = 20

    async def score_contacts_for_user(self, user_id: str, limit: int = DEFAULT_LIMIT, force_rescore: bool = False) -> list[ScoredContact]:
        """
        Score and rank VIP contacts for a user.

        Args:
            user_id: User ID
            limit: Max contacts to return
            force_rescore: If True, always re-score. If False, use cached scores if available.

        Returns:
            List of scored contacts, sorted by VIP score descending
        """
        # Check if we have cached scores (from previous scoring)
        if not force_rescore:
            cached = await self._get_cached_scores(user_id, limit)
            if cached:
                logger.info(
                    "Returning cached VIP scores",
                    user_id=user_id,
                    requested_limit=limit,
                    returned=len(cached),
                    cached=True,
                )
                return cached

        # No cache or force rescore - compute fresh scores
        rows = await VipScoringRepository.fetch_contacts(user_id, limit * 2)  # fetch extra for filtering
        contacts = [self._row_to_contact(user_id, row) for row in rows]

        scored = [score for score in (self._score_contact(c) for c in contacts) if score is not None]
        scored.sort(key=lambda c: c.vip_score, reverse=True)
        top = scored[:limit]

        if top:
            await VipScoringRepository.update_contact_scores(user_id, top)

        logger.info(
            "VIP contacts scored",
            user_id=user_id,
            requested_limit=limit,
            returned=len(top),
            cached=False,
        )
        return top

    async def _get_cached_scores(self, user_id: str, limit: int) -> list[ScoredContact] | None:
        """
        Return cached VIP scores if they exist and are non-null.

        Returns None if no cached scores exist (vip_score is NULL for all contacts).
        """
        rows = await VipScoringRepository.fetch_contacts(user_id, limit)

        if not rows:
            return None

        # Check if any contacts have been scored (vip_score != NULL)
        scored_rows = [row for row in rows if row.get("vip_score") is not None]

        if not scored_rows:
            # No scores exist yet, need to compute
            return None

        # Convert rows to ScoredContact objects
        cached_contacts = []
        for row in scored_rows:
            # Reconstruct component scores from raw metrics (we don't store them)
            # This is a simplified version - just return the aggregate score
            contact = AggregatedContact(
                user_id=user_id,
                contact_hash=row["contact_hash"],
                display_name=row.get("display_name"),
                last_contact_at=row.get("last_contact_at"),
                email_count_30d=row.get("email_count_30d", 0),
                inbound_count_30d=row.get("inbound_count_30d", 0),
                outbound_count_30d=row.get("outbound_count_30d", 0),
                direct_email_count=row.get("direct_email_count", 0),
                cc_email_count=row.get("cc_email_count", 0),
                thread_count_30d=row.get("thread_count_30d", 0),
                avg_thread_depth=row.get("avg_thread_depth", 0.0) or 0.0,
                attachment_email_count=row.get("attachment_email_count", 0),
                starred_email_count=row.get("starred_email_count", 0),
                important_email_count=row.get("important_email_count", 0),
                reply_rate_30d=row.get("reply_rate_30d", 0.0) or 0.0,
                median_response_hours=row.get("median_response_hours"),
                off_hours_ratio=row.get("off_hours_ratio", 0.0) or 0.0,
                threads_they_started=row.get("threads_they_started", 0),
                threads_you_started=row.get("threads_you_started", 0),
                meeting_count_30d=row.get("meeting_count_30d", 0),
                weighted_meeting_score=row.get("weighted_meeting_score", 0.0) or 0.0,
                meeting_recurrence_score=row.get("meeting_recurrence_score", 0.0) or 0.0,
                total_meeting_minutes=row.get("total_meeting_minutes", 0),
                recurring_meeting_count=row.get("recurring_meeting_count", 0),
                meetings_you_organized=row.get("meetings_you_organized", 0),
                meetings_they_organized=row.get("meetings_they_organized", 0),
                first_contact_at=row.get("first_contact_at"),
                consistency_score=row.get("consistency_score", 0.5) or 0.5,
                initiation_score=row.get("initiation_score", 0.5) or 0.5,
            )

            raw_metrics = {
                "email_count_30d": contact.email_count_30d,
                "inbound_count_30d": contact.inbound_count_30d,
                "outbound_count_30d": contact.outbound_count_30d,
                "direct_email_count": contact.direct_email_count,
                "cc_email_count": contact.cc_email_count,
                "meeting_count_30d": contact.meeting_count_30d,
                "total_meeting_minutes": contact.total_meeting_minutes,
                "reply_rate_30d": contact.reply_rate_30d,
                "starred_email_count": contact.starred_email_count,
                "important_email_count": contact.important_email_count,
                "weighted_meeting_score": contact.weighted_meeting_score,
                "threads_they_started": contact.threads_they_started,
                "threads_you_started": contact.threads_you_started,
                "consistency_score": contact.consistency_score,
                "initiation_score": contact.initiation_score,
                "off_hours_ratio": contact.off_hours_ratio,
                "median_response_hours": contact.median_response_hours,
                "last_contact_at": contact.last_contact_at.isoformat() if contact.last_contact_at else None,
                "first_contact_at": contact.first_contact_at.isoformat() if contact.first_contact_at else None,
            }

            # Use cached vip_score and confidence_score from database
            cached_contacts.append(
                ScoredContact(
                    contact_hash=row["contact_hash"],
                    display_name=row.get("display_name"),
                    vip_score=row["vip_score"],
                    confidence_score=row.get("confidence_score", 0.5),
                    component_scores={},  # We don't cache component scores
                    raw_metrics=raw_metrics,
                )
            )

        return cached_contacts[:limit] if cached_contacts else None

    async def save_vip_selection(self, user_id: str, contact_hashes: Iterable[str]) -> None:
        hashes = list(dict.fromkeys(contact_hashes))
        if not hashes:
            raise ValueError("At least one contact must be selected")
        if len(hashes) > self.MAX_SELECTION:
            raise ValueError(f"At most {self.MAX_SELECTION} contacts can be selected")

        await VipScoringRepository.replace_vip_selection(user_id, hashes)
        logger.info("VIP selection saved", user_id=user_id, count=len(hashes))

    def _row_to_contact(self, user_id: str, row: dict) -> AggregatedContact:
        return AggregatedContact(
            user_id=user_id,
            contact_hash=row["contact_hash"],
            display_name=row.get("display_name"),
            first_contact_at=row.get("first_contact_at"),
            last_contact_at=row.get("last_contact_at"),
            email_count_30d=row.get("email_count_30d", 0),
            inbound_count_30d=row.get("inbound_count_30d", 0),
            outbound_count_30d=row.get("outbound_count_30d", 0),
            direct_email_count=row.get("direct_email_count", 0),
            cc_email_count=row.get("cc_email_count", 0),
            thread_count_30d=row.get("thread_count_30d", 0),
            avg_thread_depth=row.get("avg_thread_depth", 0.0) or 0.0,
            attachment_email_count=row.get("attachment_email_count", 0),
            starred_email_count=row.get("starred_email_count", 0),
            important_email_count=row.get("important_email_count", 0),
            reply_rate_30d=row.get("reply_rate_30d", 0.0) or 0.0,
            median_response_hours=row.get("median_response_hours"),
            off_hours_ratio=row.get("off_hours_ratio", 0.0) or 0.0,
            threads_they_started=row.get("threads_they_started", 0),
            threads_you_started=row.get("threads_you_started", 0),
            meeting_count_30d=row.get("meeting_count_30d", 0),
            weighted_meeting_score=row.get("weighted_meeting_score", 0.0) or 0.0,
            meeting_recurrence_score=row.get("meeting_recurrence_score", 0.0) or 0.0,
            total_meeting_minutes=row.get("total_meeting_minutes", 0),
            recurring_meeting_count=row.get("recurring_meeting_count", 0),
            meetings_you_organized=row.get("meetings_you_organized", 0),
            meetings_they_organized=row.get("meetings_they_organized", 0),
            first_contact_at=row.get("first_contact_at"),
            consistency_score=row.get("consistency_score", 0.5) or 0.5,
            initiation_score=row.get("initiation_score", 0.5) or 0.5,
        )

    def _score_contact(self, contact: AggregatedContact) -> ScoredContact | None:
        should_exclude, exclude_reason = self._should_exclude(contact)
        if should_exclude:
            logger.debug(
                "Skipping VIP scoring - exclusion rule hit",
                user_id=contact.user_id,
                contact_hash=contact.contact_hash,
                reason=exclude_reason,
            )
            return None

        scores = {
            "recency": self._recency(contact),
            "frequency": self._frequency(contact),
            "meeting": self._meeting(contact),
            "engagement": self._engagement(contact),
            "initiation": self._initiation(contact),
            "response_time": self._response_time(contact),
        }

        passes_gate, gate_reason = self._passes_gate(contact, scores)
        if not passes_gate:
            logger.debug(
                "Skipping VIP scoring - gate failed",
                user_id=contact.user_id,
                contact_hash=contact.contact_hash,
                reason=gate_reason,
            )
            return None

        base_score = self._base_score(scores)
        score = self._apply_signal_bonuses(contact, scores, base_score)
        score = self._apply_edge_cases(contact, scores, score)
        score = self._apply_penalties(contact, scores, score)
        confidence = self._confidence(contact, scores)

        raw_metrics = {
            "email_count_30d": contact.email_count_30d,
            "inbound_count_30d": contact.inbound_count_30d,
            "outbound_count_30d": contact.outbound_count_30d,
            "direct_email_count": contact.direct_email_count,
            "cc_email_count": contact.cc_email_count,
            "meeting_count_30d": contact.meeting_count_30d,
            "total_meeting_minutes": contact.total_meeting_minutes,
            "reply_rate_30d": contact.reply_rate_30d,
            "starred_email_count": contact.starred_email_count,
            "important_email_count": contact.important_email_count,
            "weighted_meeting_score": contact.weighted_meeting_score,
            "threads_they_started": contact.threads_they_started,
            "threads_you_started": contact.threads_you_started,
            "consistency_score": contact.consistency_score,
            "initiation_score": contact.initiation_score,
            "off_hours_ratio": contact.off_hours_ratio,
            "median_response_hours": contact.median_response_hours,
            "last_contact_at": contact.last_contact_at.isoformat() if contact.last_contact_at else None,
            "first_contact_at": contact.first_contact_at.isoformat() if contact.first_contact_at else None,
        }

        return ScoredContact(
            contact_hash=contact.contact_hash,
            display_name=contact.display_name,
            vip_score=score,
            confidence_score=confidence,
            component_scores=scores,
            raw_metrics=raw_metrics,
        )

    def _should_exclude(self, contact: AggregatedContact) -> tuple[bool, str | None]:
        interactions = contact.email_count_30d + contact.meeting_count_30d
        if interactions < 2:
            return True, "insufficient_interactions"

        if (
            contact.inbound_count_30d >= 5
            and contact.outbound_count_30d == 0
            and contact.meeting_count_30d == 0
        ):
            return True, "inbound_only_distribution"

        if (
            contact.inbound_count_30d >= 10
            and (contact.outbound_count_30d / max(contact.inbound_count_30d, 1)) < 0.1
            and contact.reply_rate_30d < 0.1
        ):
            return True, "newsletter_pattern"

        if (
            contact.inbound_count_30d >= 5
            and contact.reply_rate_30d < 0.1
            and contact.avg_thread_depth < 1.3
        ):
            return True, "broadcast_sender"

        return False, None

    def _passes_gate(self, contact: AggregatedContact, scores: dict[str, float]) -> tuple[bool, str | None]:
        has_email_engagement = scores["engagement"] >= 0.1
        has_meeting_presence = contact.meeting_count_30d >= 1
        if has_email_engagement or has_meeting_presence:
            return True, None
        return False, "insufficient_engagement"

    def _recency(self, contact: AggregatedContact, activity_level: str = "medium") -> float:
        last_activity = contact.last_activity
        if not last_activity:
            return 0.0
        now = datetime.now(UTC)
        days_since = max((now - last_activity).total_seconds() / 86400, 0)
        half_life = {"high": 4, "medium": 7, "low": 12}.get(activity_level, 7)
        if days_since >= 30:
            return 0.05
        decay_const = 0.693 / half_life
        return max(0.05, math.exp(-decay_const * days_since))

    def _frequency(self, contact: AggregatedContact) -> float:
        base = min(contact.email_count_30d / 30.0, 1.0)
        consistency_factor = 0.7 + (0.3 * (contact.consistency_score or 0.0))
        return min(base * consistency_factor, 1.0)

    def _meeting(self, contact: AggregatedContact) -> float:
        base = min(contact.weighted_meeting_score / 4.0, 1.0)
        if contact.meeting_count_30d > 0:
            base = max(base, 0.25)
        recurrence_bonus = 1.0 + (0.2 * (contact.meeting_recurrence_score or 0.0))
        return min(base * recurrence_bonus, 1.0)

    def _engagement(self, contact: AggregatedContact) -> float:
        inbound = contact.inbound_count_30d
        outbound = contact.outbound_count_30d

        if inbound > 0 and outbound > 0:
            base = (contact.reply_rate_30d * 0.7) + 0.3
            ratio = min(inbound, outbound) / max(inbound, outbound)
            base *= 0.95 + (0.1 * ratio)
        elif inbound > 0 and outbound == 0:
            base = 0.1
        elif inbound == 0 and outbound > 0:
            base = 0.35
        else:
            base = 0.25

        if contact.avg_thread_depth >= 5:
            base *= 1.12
        elif contact.avg_thread_depth >= 3:
            base *= 1.06

        if contact.reply_rate_30d >= 0.8:
            base *= 1.0 + ((contact.reply_rate_30d - 0.8) * 0.25)

        return min(base, 1.0)

    def _initiation(self, contact: AggregatedContact) -> float:
        total_threads = contact.threads_they_started + contact.threads_you_started
        if total_threads == 0:
            return 0.5
        return contact.initiation_score or 0.5

    def _response_time(self, contact: AggregatedContact) -> float:
        hours = contact.median_response_hours
        if hours is None:
            return 0.5
        if hours <= 0.5:
            return 1.0
        if hours <= 1:
            return 0.95
        if hours <= 2:
            return 0.85
        if hours <= 4:
            return 0.75
        if hours <= 8:
            return 0.65
        if hours <= 12:
            return 0.55
        if hours <= 24:
            return 0.45
        if hours <= 48:
            return 0.3
        if hours <= 72:
            return 0.2
        return 0.1

    def _base_score(self, scores: dict[str, float]) -> float:
        weights = {
            "engagement": 0.25,
            "meeting": 0.25,
            "recency": 0.18,
            "frequency": 0.14,
            "initiation": 0.10,
            "response_time": 0.08,
        }
        return sum(weights[key] * scores.get(key, 0.0) for key in weights)

    def _apply_signal_bonuses(self, contact: AggregatedContact, scores: dict[str, float], base_score: float) -> float:
        multiplier = 1.0
        if contact.meeting_count_30d >= 2 and contact.email_count_30d >= 5:
            multiplier *= 1.08
        if contact.starred_email_count >= 3:
            multiplier *= 1.10
        elif contact.starred_email_count >= 1:
            multiplier *= 1.05
        if contact.consistency_score >= 0.7 and contact.email_count_30d >= 15:
            multiplier *= 1.06
        if contact.meetings_they_organized >= 2:
            multiplier *= 1.05
        if contact.first_contact_at:
            days_since_first = (datetime.now(UTC) - contact.first_contact_at).days
            if days_since_first >= 180 and scores["recency"] >= 0.5:
                multiplier *= 1.04
        return base_score * multiplier

    def _apply_edge_cases(self, contact: AggregatedContact, scores: dict[str, float], current: float) -> float:
        score = current
        if scores["frequency"] < 0.2 and scores["response_time"] >= 0.75 and scores["meeting"] >= 0.25:
            score *= 1.2
        if contact.first_contact_at:
            days_since_first = (datetime.now(UTC) - contact.first_contact_at).days
            if (
                days_since_first <= 14
                and contact.email_count_30d >= 8
                and contact.consistency_score >= 0.5
                and scores["recency"] >= 0.8
            ):
                score *= 1.15
            elif (
                days_since_first <= 7
                and contact.email_count_30d >= 5
                and contact.consistency_score >= 0.4
            ):
                score *= 1.10
        if 1 <= contact.email_count_30d <= 5 and contact.starred_email_count >= 1:
            score *= 1.12
        return score

    def _apply_penalties(self, contact: AggregatedContact, scores: dict[str, float], current: float) -> float:
        multiplier = 1.0
        if contact.meeting_count_30d >= 3 and contact.email_count_30d <= 3:
            multiplier *= 0.55
        if contact.email_count_30d > 0:
            ratio = contact.meeting_count_30d / contact.email_count_30d
            if ratio > 2.0 and contact.meeting_count_30d >= 2:
                multiplier *= 0.6
        if (
            contact.email_count_30d >= 25
            and contact.avg_thread_depth <= 1.5
            and contact.off_hours_ratio >= 0.5
        ):
            multiplier *= 0.8
        if (
            contact.outbound_count_30d >= 5
            and contact.inbound_count_30d <= 1
            and contact.meeting_count_30d == 0
        ):
            multiplier *= 0.5
        if scores["frequency"] >= 0.5 and scores["engagement"] < 0.15:
            multiplier *= 0.7
        return current * multiplier

    def _confidence(self, contact: AggregatedContact, scores: dict[str, float]) -> float:
        total = contact.email_count_30d + contact.meeting_count_30d
        if total >= 25:
            confidence = 1.0
        elif total >= 15:
            confidence = 0.9
        elif total >= 10:
            confidence = 0.8
        elif total >= 5:
            confidence = 0.65
        elif total >= 3:
            confidence = 0.5
        else:
            confidence = 0.3

        if contact.inbound_count_30d > 0 and contact.outbound_count_30d > 0:
            confidence = min(confidence + 0.08, 1.0)
        if contact.meeting_count_30d >= 1:
            confidence = min(confidence + 0.08, 1.0)
        if contact.starred_email_count >= 1:
            confidence = min(confidence + 0.05, 1.0)
        if contact.first_contact_at:
            days_since_first = (datetime.now(UTC) - contact.first_contact_at).days
            if days_since_first <= 7:
                confidence *= 0.85
        return confidence


scoring_service = ScoringService()
