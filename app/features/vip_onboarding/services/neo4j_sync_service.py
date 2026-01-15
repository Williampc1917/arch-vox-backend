"""
Neo4j sync service for VIP onboarding.

Bootstraps Neo4j with basic VIP structure using Postgres as the source of truth.
"""

from dataclasses import dataclass

from app.db.helpers import fetch_all, fetch_one
from app.db.neo4j import get_neo4j_session
from app.features.vip_onboarding.repository.contact_identity_repository import (
    ContactIdentityRepository,
)
from app.features.vip_onboarding.repository.neo4j_repository import Neo4jRepository
from app.features.vip_onboarding.repository.neo4j_sync_state_repository import (
    Neo4jSyncStateRepository,
)
from app.infrastructure.observability.logging import get_logger
from app.services.infrastructure.encryption_service import EncryptionError, encrypt_data

logger = get_logger(__name__)


class Neo4jSyncServiceError(Exception):
    """Raised when Neo4j sync fails."""

    def __init__(self, message: str, *, recoverable: bool = True):
        super().__init__(message)
        self.recoverable = recoverable


@dataclass(slots=True)
class VipSyncContact:
    contact_hash: str
    rank: int
    manual_added: bool


class Neo4jSyncService:
    """Orchestrates Postgres -> Neo4j bootstrap sync for VIPs."""

    @classmethod
    async def sync_vips_to_neo4j(cls, user_id: str) -> dict:
        await Neo4jSyncStateRepository.mark_pending(user_id)

        try:
            user_row = await cls._fetch_user(user_id)
            if not user_row:
                raise Neo4jSyncServiceError("User not found", recoverable=False)

            vip_rows = await cls._fetch_vip_contacts(user_id)
            if not vip_rows:
                await Neo4jSyncStateRepository.mark_synced(user_id)
                return {"status": "synced", "vip_count": 0}

            contact_hashes = [vip.contact_hash for vip in vip_rows]
            identities = await ContactIdentityRepository.fetch_identities(user_id, contact_hashes)

            user_email = user_row.get("email")
            user_email_encrypted = (
                encrypt_data(user_email) if user_email else None
            )

            async with get_neo4j_session() as session:
                await Neo4jRepository.create_or_update_user(
                    user_id=user_id,
                    email_encrypted=user_email_encrypted,
                    subscription_status=user_row.get("subscription_status"),
                    session=session,
                )

                await Neo4jRepository.deactivate_all_vips(user_id, session=session)

                for vip in vip_rows:
                    identity = identities.get(vip.contact_hash, {})
                    email_encrypted = cls._normalize_bytes(identity.get("email_encrypted"))
                    display_name_encrypted = cls._normalize_bytes(
                        identity.get("display_name_encrypted")
                    )

                    await Neo4jRepository.create_or_update_person(
                        user_id=user_id,
                        contact_hash=vip.contact_hash,
                        email_encrypted=email_encrypted,
                        display_name_encrypted=display_name_encrypted,
                        session=session,
                    )

                    source = "manual" if vip.manual_added else "scored"
                    await Neo4jRepository.create_vip_relationship(
                        user_id=user_id,
                        contact_hash=vip.contact_hash,
                        rank=vip.rank,
                        source=source,
                        session=session,
                    )

            await Neo4jSyncStateRepository.mark_synced(user_id)
            return {"status": "synced", "vip_count": len(vip_rows)}

        except EncryptionError as exc:
            await Neo4jSyncStateRepository.mark_failed(user_id, str(exc))
            logger.error("Neo4j sync failed (encryption)", user_id=user_id, error=str(exc))
            raise Neo4jSyncServiceError(str(exc)) from exc
        except Neo4jSyncServiceError as exc:
            await Neo4jSyncStateRepository.mark_failed(user_id, str(exc))
            logger.error("Neo4j sync failed", user_id=user_id, error=str(exc))
            raise
        except Exception as exc:
            await Neo4jSyncStateRepository.mark_failed(user_id, str(exc))
            logger.error("Neo4j sync failed (unexpected)", user_id=user_id, error=str(exc))
            raise Neo4jSyncServiceError("Unexpected Neo4j sync failure") from exc

    @staticmethod
    async def _fetch_user(user_id: str) -> dict | None:
        query = """
            SELECT id, email, subscription_status
            FROM users
            WHERE id = %s
        """
        return await fetch_one(query, (user_id,))

    @staticmethod
    async def _fetch_vip_contacts(user_id: str) -> list[VipSyncContact]:
        query = """
            SELECT v.rank, c.contact_hash, c.manual_added
            FROM vip_list v
            JOIN contacts c ON c.id = v.contact_id
            WHERE v.user_id = %s
              AND v.deleted_at IS NULL
            ORDER BY v.rank ASC
        """
        rows = await fetch_all(query, (user_id,))
        return [
            VipSyncContact(
                contact_hash=row["contact_hash"],
                rank=row["rank"],
                manual_added=bool(row.get("manual_added")),
            )
            for row in rows
        ]

    @staticmethod
    def _normalize_bytes(value: bytes | memoryview | None) -> bytes | None:
        if value is None:
            return None
        if isinstance(value, memoryview):
            return value.tobytes()
        return value
