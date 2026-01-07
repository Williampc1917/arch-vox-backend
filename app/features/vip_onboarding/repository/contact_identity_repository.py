"""
Repository helpers for contact identity storage.

Stores encrypted email/display name for contact hashes used in VIP onboarding.
"""

from collections.abc import Iterable

from app.db.helpers import execute_query, fetch_all
from app.db.pool import get_db_connection
from app.features.vip_onboarding.domain import ContactIdentityRecord
from app.infrastructure.observability.logging import get_logger

logger = get_logger(__name__)


class ContactIdentityRepository:
    """Persistence helpers for contact identities."""

    @classmethod
    async def upsert_identities(cls, records: Iterable[ContactIdentityRecord]) -> None:
        payload = [
            (
                record.user_id,
                record.contact_hash,
                record.email_encrypted,
                record.display_name_encrypted,
            )
            for record in records
        ]

        if not payload:
            return

        query = """
            INSERT INTO contact_identities (
                user_id, contact_hash, email_encrypted, display_name_encrypted
            )
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (user_id, contact_hash)
            DO UPDATE SET
                email_encrypted = EXCLUDED.email_encrypted,
                display_name_encrypted = EXCLUDED.display_name_encrypted,
                updated_at = NOW()
        """

        async with await get_db_connection() as conn:
            await conn.executemany(query, payload)

        logger.info(
            "Contact identities upserted",
            record_count=len(payload),
        )

    @classmethod
    async def fetch_identities(cls, user_id: str, contact_hashes: list[str]) -> dict[str, dict]:
        if not contact_hashes:
            return {}

        query = """
            SELECT contact_hash, email_encrypted, display_name_encrypted
            FROM contact_identities
            WHERE user_id = %s
              AND contact_hash = ANY(%s)
        """

        rows = await fetch_all(query, (user_id, contact_hashes))
        return {
            row["contact_hash"]: {
                "email_encrypted": row.get("email_encrypted"),
                "display_name_encrypted": row.get("display_name_encrypted"),
            }
            for row in rows
        }

    @classmethod
    async def delete_identities_for_user(cls, user_id: str) -> int:
        query = """
            DELETE FROM contact_identities
            WHERE user_id = %s
        """
        return await execute_query(query, (user_id,))
