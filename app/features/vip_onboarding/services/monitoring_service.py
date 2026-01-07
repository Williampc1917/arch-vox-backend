"""
VIP monitoring helpers.

Provides lightweight metrics for dashboards and compliance monitoring.
"""

from datetime import datetime, timedelta

from app.db.pool import db_pool
from app.infrastructure.observability.logging import get_logger

logger = get_logger(__name__)


class VipMonitoringService:
    """Compile VIP onboarding metrics for dashboards."""

    async def get_metrics(self, window_days: int = 7) -> dict:
        cutoff = datetime.utcnow() - timedelta(days=window_days)

        async with db_pool.connection() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    """
                    SELECT
                        COUNT(*) AS completed_jobs,
                        AVG(EXTRACT(EPOCH FROM (completed_at - started_at))) AS avg_seconds,
                        PERCENTILE_CONT(0.5) WITHIN GROUP (
                            ORDER BY EXTRACT(EPOCH FROM (completed_at - started_at))
                        ) AS p50_seconds,
                        PERCENTILE_CONT(0.95) WITHIN GROUP (
                            ORDER BY EXTRACT(EPOCH FROM (completed_at - started_at))
                        ) AS p95_seconds
                    FROM user_vip_backfill_jobs
                    WHERE status = 'completed'
                      AND started_at IS NOT NULL
                      AND completed_at IS NOT NULL
                      AND completed_at >= %s
                    """,
                    (cutoff,),
                )
                backfill_row = await cursor.fetchone()

                await cursor.execute(
                    """
                    SELECT
                        COUNT(*) AS total_contacts,
                        COUNT(DISTINCT user_id) AS users_with_contacts,
                        AVG(contact_count) AS avg_contacts_per_user
                    FROM (
                        SELECT user_id, COUNT(*) AS contact_count
                        FROM contacts
                        GROUP BY user_id
                    ) AS counts
                    """
                )
                contacts_row = await cursor.fetchone()

                await cursor.execute(
                    """
                    SELECT COUNT(DISTINCT user_id) AS users_with_vips
                    FROM vip_list
                    WHERE deleted_at IS NULL
                    """
                )
                vip_row = await cursor.fetchone()

                await cursor.execute(
                    """
                    SELECT COUNT(*) AS audit_events_24h
                    FROM audit_logs
                    WHERE created_at >= NOW() - INTERVAL '24 hours'
                    """
                )
                audit_row = await cursor.fetchone()

        users_with_contacts = contacts_row.get("users_with_contacts") or 0
        users_with_vips = vip_row.get("users_with_vips") or 0
        vip_selection_rate = (
            (users_with_vips / users_with_contacts) if users_with_contacts else 0.0
        )

        metrics = {
            "timestamp": datetime.utcnow().isoformat(),
            "window_days": window_days,
            "backfill_latency": {
                "completed_jobs": backfill_row.get("completed_jobs") or 0,
                "avg_seconds": float(backfill_row.get("avg_seconds") or 0.0),
                "p50_seconds": float(backfill_row.get("p50_seconds") or 0.0),
                "p95_seconds": float(backfill_row.get("p95_seconds") or 0.0),
            },
            "candidate_volume": {
                "total_contacts": contacts_row.get("total_contacts") or 0,
                "users_with_contacts": users_with_contacts,
                "avg_contacts_per_user": float(contacts_row.get("avg_contacts_per_user") or 0.0),
            },
            "vip_selection_rate": {
                "users_with_vips": users_with_vips,
                "users_with_contacts": users_with_contacts,
                "rate": round(vip_selection_rate, 4),
            },
            "audit_volume": {
                "events_last_24h": audit_row.get("audit_events_24h") or 0
            },
        }

        logger.info(
            "VIP monitoring metrics compiled",
            window_days=window_days,
            completed_jobs=metrics["backfill_latency"]["completed_jobs"],
            users_with_contacts=users_with_contacts,
            users_with_vips=users_with_vips,
        )

        return metrics


vip_monitoring_service = VipMonitoringService()
