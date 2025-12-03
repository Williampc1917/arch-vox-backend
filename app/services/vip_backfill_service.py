"""
Legacy access point for VIP backfill service.

The actual implementation lives under app.features.vip_onboarding so all
feature files stay co-located. This module re-exports the public classes
to keep existing imports stable.
"""

from app.features.vip_onboarding.services.backfill_service import (
    VipBackfillService,
    vip_backfill_service,
)

__all__ = ["VipBackfillService", "vip_backfill_service"]
