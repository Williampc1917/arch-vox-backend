from datetime import UTC, datetime, timedelta

from app.features.vip_onboarding.pipeline.scoring.service import AggregatedContact, ScoringService


def _build_contact(**overrides):
    now = datetime.now(UTC)
    contact = AggregatedContact(
        user_id="user",
        contact_hash="hash",
        email=None,
        display_name=None,
        last_contact_at=now,
        email_count_30d=6,
        email_count_7d=2,
        email_count_8_30d=4,
        email_count_31_90d=0,
        inbound_count_30d=3,
        outbound_count_30d=3,
        direct_email_count=3,
        cc_email_count=0,
        thread_count_30d=2,
        avg_thread_depth=2.0,
        attachment_email_count=0,
        starred_email_count=0,
        important_email_count=0,
        reply_rate_30d=0.5,
        median_response_hours=2.0,
        off_hours_ratio=0.1,
        threads_they_started=1,
        threads_you_started=1,
        meeting_count_30d=0,
        weighted_meeting_score=0.0,
        meeting_recurrence_score=0.0,
        total_meeting_minutes=0,
        recurring_meeting_count=0,
        meetings_you_organized=0,
        meetings_they_organized=0,
        first_contact_at=now - timedelta(days=40),
        consistency_score=0.5,
        initiation_score=0.5,
        email_domain=None,
        is_shared_inbox=False,
        manual_added=False,
    )
    for key, value in overrides.items():
        setattr(contact, key, value)
    return contact


def test_cc_weighting_boosts_score():
    service = ScoringService()
    base = _build_contact(cc_email_count=0)
    boosted = _build_contact(cc_email_count=10)

    base_score = service._score_contact(base, None, False, False)
    boosted_score = service._score_contact(boosted, None, False, False)

    assert base_score is not None
    assert boosted_score is not None
    assert boosted_score.vip_score > base_score.vip_score


def test_manual_contact_bypasses_interaction_exclusion():
    service = ScoringService()
    manual_contact = _build_contact(
        email_count_30d=0,
        email_count_7d=0,
        email_count_8_30d=0,
        email_count_31_90d=0,
        inbound_count_30d=0,
        outbound_count_30d=0,
        reply_rate_30d=0.0,
        meeting_count_30d=0,
        last_contact_at=None,
        manual_added=True,
    )
    blocked_contact = _build_contact(
        email_count_30d=0,
        email_count_7d=0,
        email_count_8_30d=0,
        email_count_31_90d=0,
        inbound_count_30d=0,
        outbound_count_30d=0,
        reply_rate_30d=0.0,
        meeting_count_30d=0,
        last_contact_at=None,
        manual_added=False,
    )

    manual_score = service._score_contact(manual_contact, None, False, False)
    blocked_score = service._score_contact(blocked_contact, None, False, False)

    assert manual_score is not None
    assert blocked_score is None
