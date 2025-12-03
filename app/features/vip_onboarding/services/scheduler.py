"""
VIP backfill scheduler helpers.

This module will expose helpers to enqueue VIP backfill jobs whenever a
user connects Gmail/Calendar, ensuring work is idempotent and resilient.
"""


async def enqueue_vip_backfill_job(user_id: str, force: bool = False) -> None:
    """
    Placeholder enqueue helper.

    Args:
        user_id: ID for which we want to trigger the backfill.
        force: Whether to bypass idempotency once real logic lands.
    """

    # Implementation will push work to Redis / database once available.
    return None

