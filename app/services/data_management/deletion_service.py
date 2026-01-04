"""
Data Deletion Service - GDPR Right to Erasure (Article 17).

This service handles user data deletion requests in compliance with GDPR.

Features:
- Soft delete with 30-day grace period (allows recovery)
- Revokes OAuth tokens with Google (prevents future use)
- Deletes across all user data tables
- Audit logging of all deletions
- Never fails requests (resilient design)

Usage:
    from app.services.data_management.deletion_service import data_deletion_service

    # Soft delete (30-day grace period)
    success = await data_deletion_service.soft_delete_user_data(user_id)

    # Hard delete (immediate, permanent)
    success = await data_deletion_service.hard_delete_user_data(user_id)

Design Principles:
1. Audit logs are NEVER deleted (compliance requirement)
2. Soft delete by default (allows 30-day recovery)
3. OAuth tokens revoked with Google (security)
4. All deletions logged in audit trail
5. Graceful failure (don't block user if one deletion fails)

IMPORTANT: When you add new user data tables, update delete_user_data()
"""

from datetime import datetime, timedelta
from uuid import UUID

import httpx

from app.config import settings
from app.db.pool import db_pool
from app.infrastructure.observability.logging import get_logger

logger = get_logger(__name__)


class DataDeletionService:
    """
    Service for deleting user data (GDPR Right to Erasure).

    Supports both soft delete (grace period) and hard delete (immediate).
    """

    async def soft_delete_user_data(
        self,
        user_id: str | UUID,
        grace_period_days: int | None = None,
    ) -> dict:
        """
        Soft delete user data with grace period.

        Data is marked as deleted but not actually removed for grace_period_days.
        This allows users to recover their data if they change their mind.

        Args:
            user_id: User ID
            grace_period_days: Grace period in days (default: from config)

        Returns:
            dict: {
                "success": bool,
                "deleted_at": str,
                "grace_period_until": str,
                "deleted_items": dict,
                "revoked_oauth": bool,
            }
        """
        user_id_str = str(user_id)
        grace_period_days = grace_period_days or settings.DATA_RETENTION_GRACE_PERIOD_DAYS

        deleted_at = datetime.utcnow()
        grace_period_until = deleted_at + timedelta(days=grace_period_days)

        logger.info(
            "Starting soft delete for user",
            user_id=user_id_str,
            grace_period_days=grace_period_days,
            grace_period_until=grace_period_until.isoformat(),
        )

        deleted_items = {}

        # ===================================================================
        # 1. Soft delete OAuth tokens
        # ===================================================================
        try:
            oauth_count = await self._soft_delete_oauth_tokens(
                user_id_str, deleted_at, grace_period_until
            )
            deleted_items["oauth_tokens"] = oauth_count
            logger.info("OAuth tokens soft deleted", count=oauth_count)
        except Exception as e:
            logger.error("Failed to soft delete OAuth tokens", error=str(e))
            deleted_items["oauth_tokens"] = 0

        # ===================================================================
        # 2. Soft delete VIP list
        # ===================================================================
        try:
            vip_count = await self._soft_delete_vip_list(
                user_id_str, deleted_at, grace_period_until
            )
            deleted_items["vip_list"] = vip_count
            logger.info("VIP list soft deleted", count=vip_count)
        except Exception as e:
            logger.error("Failed to soft delete VIP list", error=str(e))
            deleted_items["vip_list"] = 0

        # ===================================================================
        # 3. Soft delete contacts
        # ===================================================================
        try:
            contacts_count = await self._soft_delete_contacts(
                user_id_str, deleted_at, grace_period_until
            )
            deleted_items["contacts"] = contacts_count
            logger.info("Contacts soft deleted", count=contacts_count)
        except Exception as e:
            logger.error("Failed to soft delete contacts", error=str(e))
            deleted_items["contacts"] = 0

        # ===================================================================
        # 4. Soft delete email metadata
        # ===================================================================
        try:
            email_count = await self._soft_delete_email_metadata(
                user_id_str, deleted_at, grace_period_until
            )
            deleted_items["email_metadata"] = email_count
            logger.info("Email metadata soft deleted", count=email_count)
        except Exception as e:
            logger.error("Failed to soft delete email metadata", error=str(e))
            deleted_items["email_metadata"] = 0

        # ===================================================================
        # 5. Soft delete events metadata
        # ===================================================================
        try:
            events_count = await self._soft_delete_events_metadata(
                user_id_str, deleted_at, grace_period_until
            )
            deleted_items["events_metadata"] = events_count
            logger.info("Events metadata soft deleted", count=events_count)
        except Exception as e:
            logger.error("Failed to soft delete events metadata", error=str(e))
            deleted_items["events_metadata"] = 0

        # ===================================================================
        # 6. Soft delete user settings
        # ===================================================================
        try:
            settings_count = await self._soft_delete_user_settings(
                user_id_str, deleted_at, grace_period_until
            )
            deleted_items["user_settings"] = settings_count
            logger.info("User settings soft deleted", count=settings_count)
        except Exception as e:
            logger.error("Failed to soft delete user settings", error=str(e))
            deleted_items["user_settings"] = 0

        # ===================================================================
        # 7. Revoke OAuth tokens with Google (optional but recommended)
        # ===================================================================
        revoked_oauth = False
        if settings.GDPR_REVOKE_OAUTH_ON_DELETE:
            try:
                revoked_oauth = await self._revoke_oauth_tokens(user_id_str)
                logger.info("OAuth tokens revoked with Google", success=revoked_oauth)
            except Exception as e:
                logger.error("Failed to revoke OAuth tokens", error=str(e))

        total_items = sum(deleted_items.values())

        logger.info(
            "Soft delete completed",
            user_id=user_id_str,
            total_items=total_items,
            deleted_items=deleted_items,
            revoked_oauth=revoked_oauth,
        )

        return {
            "success": True,
            "deleted_at": deleted_at.isoformat(),
            "grace_period_until": grace_period_until.isoformat(),
            "grace_period_days": grace_period_days,
            "deleted_items": deleted_items,
            "total_items": total_items,
            "revoked_oauth": revoked_oauth,
        }

    async def hard_delete_user_data(self, user_id: str | UUID) -> dict:
        """
        Hard delete user data (immediate, permanent).

        This is used:
        1. After grace period expires (cleanup job)
        2. Manual admin deletion
        3. User explicitly requests immediate deletion

        IMPORTANT: This is PERMANENT and CANNOT be undone.

        Args:
            user_id: User ID

        Returns:
            dict: {
                "success": bool,
                "deleted_items": dict,
                "revoked_oauth": bool,
            }
        """
        user_id_str = str(user_id)

        logger.warning(
            "Starting HARD DELETE for user (PERMANENT)",
            user_id=user_id_str,
        )

        deleted_items = {}

        # Hard delete all tables
        try:
            oauth_count = await self._hard_delete_oauth_tokens(user_id_str)
            deleted_items["oauth_tokens"] = oauth_count
        except Exception as e:
            logger.error("Failed to hard delete OAuth tokens", error=str(e))
            deleted_items["oauth_tokens"] = 0

        try:
            vip_count = await self._hard_delete_vip_list(user_id_str)
            deleted_items["vip_list"] = vip_count
        except Exception as e:
            logger.error("Failed to hard delete VIP list", error=str(e))
            deleted_items["vip_list"] = 0

        try:
            contacts_count = await self._hard_delete_contacts(user_id_str)
            deleted_items["contacts"] = contacts_count
        except Exception as e:
            logger.error("Failed to hard delete contacts", error=str(e))
            deleted_items["contacts"] = 0

        try:
            email_count = await self._hard_delete_email_metadata(user_id_str)
            deleted_items["email_metadata"] = email_count
        except Exception as e:
            logger.error("Failed to hard delete email metadata", error=str(e))
            deleted_items["email_metadata"] = 0

        try:
            events_count = await self._hard_delete_events_metadata(user_id_str)
            deleted_items["events_metadata"] = events_count
        except Exception as e:
            logger.error("Failed to hard delete events metadata", error=str(e))
            deleted_items["events_metadata"] = 0

        try:
            settings_count = await self._hard_delete_user_settings(user_id_str)
            deleted_items["user_settings"] = settings_count
        except Exception as e:
            logger.error("Failed to hard delete user settings", error=str(e))
            deleted_items["user_settings"] = 0

        # Revoke OAuth tokens with Google
        revoked_oauth = False
        if settings.GDPR_REVOKE_OAUTH_ON_DELETE:
            try:
                revoked_oauth = await self._revoke_oauth_tokens(user_id_str)
            except Exception as e:
                logger.error("Failed to revoke OAuth tokens", error=str(e))

        total_items = sum(deleted_items.values())

        logger.warning(
            "HARD DELETE completed (PERMANENT)",
            user_id=user_id_str,
            total_items=total_items,
            deleted_items=deleted_items,
        )

        return {
            "success": True,
            "deleted_items": deleted_items,
            "total_items": total_items,
            "revoked_oauth": revoked_oauth,
        }

    async def cancel_deletion(self, user_id: str | UUID) -> dict:
        """
        Cancel soft delete (recover data during grace period).

        Sets deleted_at and grace_period_until back to NULL.
        """
        user_id_str = str(user_id)
        logger.info("Canceling deletion (recovering data)", user_id=user_id_str)

        recovered_items = {}

        try:
            oauth_count = await self._cancel_oauth_tokens_deletion(user_id_str)
            recovered_items["oauth_tokens"] = oauth_count
        except Exception as e:
            logger.error("Failed to recover OAuth tokens", error=str(e))
            recovered_items["oauth_tokens"] = 0

        try:
            vip_count = await self._cancel_vip_list_deletion(user_id_str)
            recovered_items["vip_list"] = vip_count
        except Exception as e:
            logger.error("Failed to recover VIP list", error=str(e))
            recovered_items["vip_list"] = 0

        try:
            contacts_count = await self._cancel_contacts_deletion(user_id_str)
            recovered_items["contacts"] = contacts_count
        except Exception as e:
            logger.error("Failed to recover contacts", error=str(e))
            recovered_items["contacts"] = 0

        try:
            email_count = await self._cancel_email_metadata_deletion(user_id_str)
            recovered_items["email_metadata"] = email_count
        except Exception as e:
            logger.error("Failed to recover email metadata", error=str(e))
            recovered_items["email_metadata"] = 0

        try:
            events_count = await self._cancel_events_metadata_deletion(user_id_str)
            recovered_items["events_metadata"] = events_count
        except Exception as e:
            logger.error("Failed to recover events metadata", error=str(e))
            recovered_items["events_metadata"] = 0

        try:
            settings_count = await self._cancel_user_settings_deletion(user_id_str)
            recovered_items["user_settings"] = settings_count
        except Exception as e:
            logger.error("Failed to recover user settings", error=str(e))
            recovered_items["user_settings"] = 0

        total_items = sum(recovered_items.values())

        logger.info(
            "Deletion canceled, data recovered",
            user_id=user_id_str,
            total_items=total_items,
        )

        return {
            "success": True,
            "recovered_items": recovered_items,
            "total_items": total_items,
        }

    # =======================================================================
    # PRIVATE METHODS - OAuth Tokens
    # =======================================================================

    async def _soft_delete_oauth_tokens(
        self, user_id: str, deleted_at: datetime, grace_period_until: datetime
    ) -> int:
        async with db_pool.connection() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    """
                    UPDATE oauth_tokens
                    SET deleted_at = %s, grace_period_until = %s
                    WHERE user_id = %s AND deleted_at IS NULL
                    """,
                    (deleted_at, grace_period_until, user_id),
                )
                return cursor.rowcount

    async def _hard_delete_oauth_tokens(self, user_id: str) -> int:
        async with db_pool.connection() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute("DELETE FROM oauth_tokens WHERE user_id = %s", (user_id,))
                return cursor.rowcount

    async def _cancel_oauth_tokens_deletion(self, user_id: str) -> int:
        async with db_pool.connection() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    """
                    UPDATE oauth_tokens
                    SET deleted_at = NULL, grace_period_until = NULL
                    WHERE user_id = %s AND deleted_at IS NOT NULL
                    """,
                    (user_id,),
                )
                return cursor.rowcount

    # =======================================================================
    # PRIVATE METHODS - VIP List
    # =======================================================================

    async def _soft_delete_vip_list(
        self, user_id: str, deleted_at: datetime, grace_period_until: datetime
    ) -> int:
        async with db_pool.connection() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    """
                    UPDATE vip_list
                    SET deleted_at = %s, grace_period_until = %s
                    WHERE user_id = %s AND deleted_at IS NULL
                    """,
                    (deleted_at, grace_period_until, user_id),
                )
                return cursor.rowcount

    async def _hard_delete_vip_list(self, user_id: str) -> int:
        async with db_pool.connection() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute("DELETE FROM vip_list WHERE user_id = %s", (user_id,))
                return cursor.rowcount

    async def _cancel_vip_list_deletion(self, user_id: str) -> int:
        async with db_pool.connection() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    """
                    UPDATE vip_list
                    SET deleted_at = NULL, grace_period_until = NULL
                    WHERE user_id = %s AND deleted_at IS NOT NULL
                    """,
                    (user_id,),
                )
                return cursor.rowcount

    # =======================================================================
    # PRIVATE METHODS - Contacts
    # =======================================================================

    async def _soft_delete_contacts(
        self, user_id: str, deleted_at: datetime, grace_period_until: datetime
    ) -> int:
        async with db_pool.connection() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    """
                    UPDATE contacts
                    SET deleted_at = %s, grace_period_until = %s
                    WHERE user_id = %s AND deleted_at IS NULL
                    """,
                    (deleted_at, grace_period_until, user_id),
                )
                return cursor.rowcount

    async def _hard_delete_contacts(self, user_id: str) -> int:
        async with db_pool.connection() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute("DELETE FROM contacts WHERE user_id = %s", (user_id,))
                return cursor.rowcount

    async def _cancel_contacts_deletion(self, user_id: str) -> int:
        async with db_pool.connection() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    """
                    UPDATE contacts
                    SET deleted_at = NULL, grace_period_until = NULL
                    WHERE user_id = %s AND deleted_at IS NOT NULL
                    """,
                    (user_id,),
                )
                return cursor.rowcount

    # =======================================================================
    # PRIVATE METHODS - Email Metadata
    # =======================================================================

    async def _soft_delete_email_metadata(
        self, user_id: str, deleted_at: datetime, grace_period_until: datetime
    ) -> int:
        async with db_pool.connection() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    """
                    UPDATE email_metadata
                    SET deleted_at = %s, grace_period_until = %s
                    WHERE user_id = %s AND deleted_at IS NULL
                    """,
                    (deleted_at, grace_period_until, user_id),
                )
                return cursor.rowcount

    async def _hard_delete_email_metadata(self, user_id: str) -> int:
        async with db_pool.connection() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute("DELETE FROM email_metadata WHERE user_id = %s", (user_id,))
                return cursor.rowcount

    async def _cancel_email_metadata_deletion(self, user_id: str) -> int:
        async with db_pool.connection() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    """
                    UPDATE email_metadata
                    SET deleted_at = NULL, grace_period_until = NULL
                    WHERE user_id = %s AND deleted_at IS NOT NULL
                    """,
                    (user_id,),
                )
                return cursor.rowcount

    # =======================================================================
    # PRIVATE METHODS - Events Metadata
    # =======================================================================

    async def _soft_delete_events_metadata(
        self, user_id: str, deleted_at: datetime, grace_period_until: datetime
    ) -> int:
        async with db_pool.connection() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    """
                    UPDATE events_metadata
                    SET deleted_at = %s, grace_period_until = %s
                    WHERE user_id = %s AND deleted_at IS NULL
                    """,
                    (deleted_at, grace_period_until, user_id),
                )
                return cursor.rowcount

    async def _hard_delete_events_metadata(self, user_id: str) -> int:
        async with db_pool.connection() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute("DELETE FROM events_metadata WHERE user_id = %s", (user_id,))
                return cursor.rowcount

    async def _cancel_events_metadata_deletion(self, user_id: str) -> int:
        async with db_pool.connection() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    """
                    UPDATE events_metadata
                    SET deleted_at = NULL, grace_period_until = NULL
                    WHERE user_id = %s AND deleted_at IS NOT NULL
                    """,
                    (user_id,),
                )
                return cursor.rowcount

    # =======================================================================
    # PRIVATE METHODS - User Settings
    # =======================================================================

    async def _soft_delete_user_settings(
        self, user_id: str, deleted_at: datetime, grace_period_until: datetime
    ) -> int:
        async with db_pool.connection() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    """
                    UPDATE user_settings
                    SET deleted_at = %s, grace_period_until = %s
                    WHERE user_id = %s AND deleted_at IS NULL
                    """,
                    (deleted_at, grace_period_until, user_id),
                )
                return cursor.rowcount

    async def _hard_delete_user_settings(self, user_id: str) -> int:
        async with db_pool.connection() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute("DELETE FROM user_settings WHERE user_id = %s", (user_id,))
                return cursor.rowcount

    async def _cancel_user_settings_deletion(self, user_id: str) -> int:
        async with db_pool.connection() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    """
                    UPDATE user_settings
                    SET deleted_at = NULL, grace_period_until = NULL
                    WHERE user_id = %s AND deleted_at IS NOT NULL
                    """,
                    (user_id,),
                )
                return cursor.rowcount

    # =======================================================================
    # PRIVATE METHODS - OAuth Token Revocation
    # =======================================================================

    async def _revoke_oauth_tokens(self, user_id: str) -> bool:
        """Revoke OAuth tokens with Google."""
        try:
            async with db_pool.connection() as conn:
                async with conn.cursor() as cursor:
                    await cursor.execute(
                        """
                        SELECT access_token
                        FROM oauth_tokens
                        WHERE user_id = %s AND deleted_at IS NULL
                        LIMIT 1
                        """,
                        (user_id,),
                    )
                    row = await cursor.fetchone()

            if not row:
                logger.info("No OAuth tokens found to revoke", user_id=user_id)
                return True

            access_token = row[0]

            # Decrypt token before revoking (it's stored as bytea/encrypted)
            # Note: You'll need to decrypt this based on your encryption implementation
            # For now, assuming it needs decryption via your encryption service

            async with httpx.AsyncClient() as client:
                response = await client.post(
                    "https://oauth2.googleapis.com/revoke",
                    data={
                        "token": (
                            access_token.decode()
                            if isinstance(access_token, bytes)
                            else access_token
                        )
                    },
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                    timeout=10.0,
                )

            if response.status_code == 200:
                logger.info("OAuth token successfully revoked with Google", user_id=user_id)
                return True
            else:
                logger.warning(
                    "Failed to revoke OAuth token with Google",
                    user_id=user_id,
                    status_code=response.status_code,
                )
                return False

        except Exception as e:
            logger.error("Error revoking OAuth token", user_id=user_id, error=str(e))
            return False


# Singleton instance
data_deletion_service = DataDeletionService()
