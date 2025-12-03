"""
VIP onboarding feature package.

This vertical slice keeps every layer related to the VIP onboarding flow
co-located (domain models, repositories, services, jobs, API routers, and
tests) so contributors can navigate the feature without hunting through
global folders.
"""

# Re-export the primary building blocks for easy access.
from .api.router import router as vip_router  # noqa: F401
from .services.backfill_service import VipBackfillService, vip_backfill_service  # noqa: F401
from .services.scheduler import enqueue_vip_backfill_job  # noqa: F401
from .jobs.backfill_job import start_vip_backfill_scheduler  # noqa: F401
from .domain.models import VipCandidate, EmailMetadataRecord, CalendarEventRecord  # noqa: F401
