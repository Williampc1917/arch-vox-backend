"""
Postgres repository for neo4j_sync_state tracking.

Keeps minimal sync status for MVP (synced/failed/pending).
"""

from app.db.helpers import execute_query, fetch_one
from app.infrastructure.observability.logging import get_logger

logger = get_logger(__name__)


class Neo4jSyncStateRepository:
    """Persistence helpers for neo4j_sync_state."""

    @staticmethod
    async def get_sync_state(user_id: str) -> dict | None:
        query = """
            SELECT user_id, last_sync_at, sync_status, error_message, retry_count
            FROM neo4j_sync_state
            WHERE user_id = %s
        """
        return await fetch_one(query, (user_id,))

    @staticmethod
    async def mark_pending(user_id: str) -> None:
        query = """
            INSERT INTO neo4j_sync_state (user_id, sync_status, updated_at)
            VALUES (%s, 'pending', NOW())
            ON CONFLICT (user_id)
            DO UPDATE SET
                sync_status = 'pending',
                error_message = NULL,
                updated_at = NOW()
        """
        await execute_query(query, (user_id,))

    @staticmethod
    async def mark_synced(user_id: str) -> None:
        query = """
            INSERT INTO neo4j_sync_state (user_id, sync_status, last_sync_at, updated_at)
            VALUES (%s, 'synced', NOW(), NOW())
            ON CONFLICT (user_id)
            DO UPDATE SET
                sync_status = 'synced',
                last_sync_at = NOW(),
                error_message = NULL,
                updated_at = NOW()
        """
        await execute_query(query, (user_id,))

    @staticmethod
    async def mark_failed(user_id: str, error_message: str) -> None:
        truncated_error = (error_message or "")[:500]
        query = """
            INSERT INTO neo4j_sync_state (
                user_id, sync_status, error_message, retry_count, updated_at
            )
            VALUES (%s, 'failed', %s, 1, NOW())
            ON CONFLICT (user_id)
            DO UPDATE SET
                sync_status = 'failed',
                error_message = EXCLUDED.error_message,
                retry_count = neo4j_sync_state.retry_count + 1,
                updated_at = NOW()
        """
        await execute_query(query, (user_id, truncated_error))
        logger.warning("Neo4j sync marked failed", user_id=user_id, error=truncated_error)
