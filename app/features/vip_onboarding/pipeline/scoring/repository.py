"""
Repository helpers for VIP scoring and persistence.
"""

from collections.abc import Iterable, Sequence
from typing import TYPE_CHECKING

from app.db.helpers import execute_transaction, fetch_all
from app.features.vip_onboarding.pipeline.aggregation.repository import (
    ContactAggregationRepository,
)
from app.infrastructure.observability.logging import get_logger

if TYPE_CHECKING:  # pragma: no cover - avoids circular import at runtime
    from .service import ScoredContact


logger = get_logger(__name__)


class VipScoringRepository:
    """Thin wrappers for fetching contact aggregates and persisting VIP picks."""

    @staticmethod
    async def fetch_contacts(user_id: str, limit: int) -> list[dict]:
        return await ContactAggregationRepository.fetch_contacts(user_id, limit)

    @staticmethod
    async def update_contact_scores(user_id: str, contacts: Iterable["ScoredContact"]) -> None:
        """Batch update VIP scores for multiple contacts in a single transaction."""
        contacts_list = list(contacts)
        if not contacts_list:
            return

        # Build batch of UPDATE queries
        query = """
            UPDATE contacts
            SET vip_score = %s,
                confidence_score = %s,
                updated_at = NOW()
            WHERE user_id = %s
              AND contact_hash = %s
        """

        queries = [
            (
                query,
                (
                    contact.vip_score,
                    contact.confidence_score,
                    user_id,
                    contact.contact_hash,
                ),
            )
            for contact in contacts_list
        ]

        # Execute all updates in a single transaction
        await execute_transaction(queries)

        logger.debug(
            "Batch updated VIP scores",
            user_id=user_id,
            contact_count=len(contacts_list),
        )

    @staticmethod
    async def replace_vip_selection(user_id: str, contact_hashes: Sequence[str]) -> None:
        # First validate all contacts exist BEFORE starting transaction
        if contact_hashes:
            rows = await fetch_all(
                """
                SELECT contact_hash, id
                FROM contacts
                WHERE user_id = %s
                  AND contact_hash = ANY(%s)
                """,
                (user_id, list(contact_hashes)),
            )
            mapping = {row["contact_hash"]: row["id"] for row in rows}
            missing = [hash_ for hash_ in contact_hashes if hash_ not in mapping]
            if missing:
                logger.warning(
                    "VIP selection contains unknown contacts",
                    user_id=user_id,
                    missing_count=len(missing),
                    sample_missing=missing[:3],
                )
                raise ValueError(f"Contacts not found for user: {', '.join(missing)}")
        else:
            mapping = {}

        # Atomic transaction: DELETE old + INSERT new VIPs
        queries = [
            # Step 1: Clear existing VIP selections
            ("DELETE FROM vip_list WHERE user_id = %s", (user_id,)),
        ]

        # Step 2: Batch insert new VIP selections (if any)
        if contact_hashes:
            insert_query = """
                INSERT INTO vip_list (user_id, contact_id, rank)
                VALUES (%s, %s, %s)
            """
            for rank, contact_hash in enumerate(contact_hashes, start=1):
                queries.append((insert_query, (user_id, mapping[contact_hash], rank)))

        # Execute all queries atomically
        await execute_transaction(queries)

        logger.info(
            "VIP selection replaced atomically",
            user_id=user_id,
            vip_count=len(contact_hashes),
        )
