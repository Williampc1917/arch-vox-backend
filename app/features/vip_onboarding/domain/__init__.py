"""
Domain subpackage for VIP onboarding feature.
"""

from .models import (
    CalendarEventRecord,
    EmailMetadataRecord,
    VipBackfillJob,
    VipCandidate,
)

__all__ = [
    "CalendarEventRecord",
    "EmailMetadataRecord",
    "VipBackfillJob",
    "VipCandidate",
]
