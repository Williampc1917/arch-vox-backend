"""
Legacy access point for VIP backfill scheduler helpers.

The feature-specific implementation lives under app.features.vip_onboarding.
"""

from app.features.vip_onboarding.services.scheduler import enqueue_vip_backfill_job

__all__ = ["enqueue_vip_backfill_job"]
