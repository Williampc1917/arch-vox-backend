"""
Generic background worker runner.

Reads the desired job name from CLI args or the WORKER_JOB environment
variable and delegates to the appropriate scheduler.
"""

import asyncio
import os
import sys
from collections.abc import Awaitable, Callable

from app.features.vip_onboarding.jobs.backfill_job import start_vip_backfill_scheduler
from app.features.vip_onboarding.jobs.identity_backfill_job import run_vip_identity_backfill
from app.infrastructure.observability.logging import get_logger
from app.jobs.oauth_cleanup_job import start_oauth_cleanup_scheduler
from app.jobs.token_refresh_job import start_token_refresh_scheduler

logger = get_logger(__name__)

JobCoroutine = Callable[[], Awaitable[None]]

JOB_REGISTRY: dict[str, JobCoroutine] = {
    "vip_backfill": start_vip_backfill_scheduler,
    "vip_identity_backfill": run_vip_identity_backfill,
    "token_refresh": start_token_refresh_scheduler,
    "oauth_cleanup": start_oauth_cleanup_scheduler,
}


def _resolve_job_name() -> str:
    """Pick the target job from CLI args or WORKER_JOB env variable."""
    if len(sys.argv) > 1:
        return sys.argv[1].strip().lower()
    return os.getenv("WORKER_JOB", "vip_backfill").strip().lower()


async def run_worker(job_name: str | None = None) -> None:
    """Run the requested background job."""
    name = (job_name or _resolve_job_name()).strip().lower()
    if name not in JOB_REGISTRY:
        raise ValueError(
            f"Unknown worker job '{name}'. "
            f"Available jobs: {', '.join(sorted(JOB_REGISTRY.keys()))}"
        )

    logger.info("Starting background worker", job=name)
    await JOB_REGISTRY[name]()


def main() -> None:
    """CLI entrypoint."""
    job_name = _resolve_job_name()
    asyncio.run(run_worker(job_name))


if __name__ == "__main__":
    main()
