"""
Legacy entry point for the VIP backfill worker.

Delegates to the implementation under app.features.vip_onboarding so the
deployment scripts can keep referencing this module.
"""

from app.features.vip_onboarding.jobs.backfill_job import start_vip_backfill_scheduler

__all__ = ["start_vip_backfill_scheduler"]

if __name__ == "__main__":
    import asyncio

    asyncio.run(start_vip_backfill_scheduler())

