"""
Legacy access point for VIP onboarding domain models.

The actual implementations live under app.features.vip_onboarding to
keep the feature organized, and this module simply re-exports them so
existing imports continue to work.
"""

from app.features.vip_onboarding.domain import (
    CalendarEventRecord,
    EmailMetadataRecord,
    VipBackfillJob,
    VipCandidate,
)

__all__ = ["CalendarEventRecord", "EmailMetadataRecord", "VipCandidate", "VipBackfillJob"]
