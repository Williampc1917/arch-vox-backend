from datetime import UTC, datetime, timedelta

from app.features.vip_onboarding.pipeline.aggregation.repository import EmailMetadataRow
from app.features.vip_onboarding.pipeline.aggregation.service import ContactAggregationService


def test_recency_buckets_from_email_timestamps():
    service = ContactAggregationService()
    now = datetime.now(UTC)

    emails = [
        EmailMetadataRow(
            message_id="m1",
            thread_id="t1",
            direction="in",
            from_contact_hash="contact-1",
            to_contact_hash="user",
            timestamp=now - timedelta(days=2),
            has_attachment=False,
            is_starred=False,
            is_important=False,
            is_reply=False,
            hour_of_day=10,
            day_of_week=2,
            cc_contact_hashes=[],
        ),
        EmailMetadataRow(
            message_id="m2",
            thread_id="t2",
            direction="out",
            from_contact_hash="user",
            to_contact_hash="contact-1",
            timestamp=now - timedelta(days=10),
            has_attachment=False,
            is_starred=False,
            is_important=False,
            is_reply=False,
            hour_of_day=12,
            day_of_week=3,
            cc_contact_hashes=[],
        ),
        EmailMetadataRow(
            message_id="m3",
            thread_id="t3",
            direction="in",
            from_contact_hash="contact-1",
            to_contact_hash="user",
            timestamp=now - timedelta(days=45),
            has_attachment=False,
            is_starred=False,
            is_important=False,
            is_reply=False,
            hour_of_day=9,
            day_of_week=1,
            cc_contact_hashes=[],
        ),
    ]

    aggregates = service._build_contact_aggregates("user", "user", emails, [])
    aggregate = aggregates["contact-1"]

    assert aggregate.email_count_7d == 1
    assert aggregate.email_count_8_30d == 1
    assert aggregate.email_count_31_90d == 1
