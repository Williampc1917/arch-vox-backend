"""
Domain subpackage for VIP onboarding feature.
"""

from .models import (
    CalendarEventRecord,
    ContactIdentityRecord,
    EmailMetadataRecord,
    VipBackfillJob,
    VipCandidate,
)

__all__ = [
    "CalendarEventRecord",
    "ContactIdentityRecord",
    "EmailMetadataRecord",
    "VipBackfillJob",
    "VipCandidate",
]
