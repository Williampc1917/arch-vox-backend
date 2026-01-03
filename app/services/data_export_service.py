"""
Data Export Service - GDPR Right to Data Portability (Article 20).

This service allows users to export all their personal data in a machine-readable
format (JSON), enabling them to transfer data to another service.

Features:
- Exports all user data in JSON format
- Excludes sensitive credentials (encrypted tokens)
- Includes metadata for transparency
- Never fails requests (resilient design)
- Audit logging of export requests

Usage:
    from app.services.data_export_service import data_export_service

    # Export all user data
    data = await data_export_service.export_user_data(user_id)
    # Returns: { "oauth": {...}, "vips": {...}, "metadata": {...} }

Design Principles:
1. Machine-readable format (JSON)
2. Include all personal data
3. Exclude encrypted secrets (security)
4. Include metadata (when created, last updated, etc.)
5. Graceful failure (don't block if one export fails)

IMPORTANT: When you add new user data tables, update export_user_data()
"""

from datetime import datetime
from uuid import UUID

from app.db.pool import db_pool
from app.infrastructure.observability.logging import get_logger

logger = get_logger(__name__)


class DataExportService:
    """
    Service for exporting user data (GDPR Right to Data Portability).

    Exports all user data in JSON format for data portability.
    """

    async def export_user_data(self, user_id: str | UUID) -> dict:
        """
        Export all user data in JSON format.

        This allows users to download their data and transfer to another service.

        Args:
            user_id: User ID

        Returns:
            dict: {
                "metadata": {...},
                "oauth_tokens": {...},
                "vip_list": {...},
                "contacts": {...},
                "email_metadata": {...},
                "events_metadata": {...},
                "user_settings": {...},
                "audit_summary": {...},
            }
        """
        user_id_str = str(user_id)

        logger.info("Starting data export for user", user_id=user_id_str)

        export_data = {
            "metadata": {
                "user_id": user_id_str,
                "export_timestamp": datetime.utcnow().isoformat(),
                "format_version": "1.0",
                "format": "JSON",
                "gdpr_article": "Article 20 - Right to Data Portability",
            },
        }

        # Export all tables
        try:
            oauth_data = await self._export_oauth_tokens(user_id_str)
            export_data["oauth_tokens"] = oauth_data
        except Exception as e:
            logger.error("Failed to export OAuth tokens", error=str(e))
            export_data["oauth_tokens"] = {"error": str(e)}

        try:
            vip_data = await self._export_vip_list(user_id_str)
            export_data["vip_list"] = vip_data
        except Exception as e:
            logger.error("Failed to export VIP list", error=str(e))
            export_data["vip_list"] = {"error": str(e)}

        try:
            contacts_data = await self._export_contacts(user_id_str)
            export_data["contacts"] = contacts_data
        except Exception as e:
            logger.error("Failed to export contacts", error=str(e))
            export_data["contacts"] = {"error": str(e)}

        try:
            email_data = await self._export_email_metadata(user_id_str)
            export_data["email_metadata"] = email_data
        except Exception as e:
            logger.error("Failed to export email metadata", error=str(e))
            export_data["email_metadata"] = {"error": str(e)}

        try:
            events_data = await self._export_events_metadata(user_id_str)
            export_data["events_metadata"] = events_data
        except Exception as e:
            logger.error("Failed to export events metadata", error=str(e))
            export_data["events_metadata"] = {"error": str(e)}

        try:
            settings_data = await self._export_user_settings(user_id_str)
            export_data["user_settings"] = settings_data
        except Exception as e:
            logger.error("Failed to export user settings", error=str(e))
            export_data["user_settings"] = {"error": str(e)}

        try:
            audit_summary = await self._export_audit_summary(user_id_str)
            export_data["audit_summary"] = audit_summary
        except Exception as e:
            logger.error("Failed to export audit summary", error=str(e))
            export_data["audit_summary"] = {"error": str(e)}

        logger.info("Data export completed", user_id=user_id_str)

        return export_data

    # =======================================================================
    # PRIVATE METHODS - OAuth Tokens
    # =======================================================================

    async def _export_oauth_tokens(self, user_id: str) -> dict:
        """Export OAuth tokens metadata (NOT the actual encrypted tokens)."""
        async with db_pool.connection() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    """
                    SELECT
                        provider,
                        scope,
                        expires_at,
                        updated_at,
                        last_used_at,
                        deleted_at
                    FROM oauth_tokens
                    WHERE user_id = %s
                    """,
                    (user_id,),
                )
                rows = await cursor.fetchall()

        tokens = []
        for row in rows:
            tokens.append({
                "provider": row[0],
                "scopes": row[1].split() if row[1] else [],
                "expires_at": row[2].isoformat() if row[2] else None,
                "updated_at": row[3].isoformat() if row[3] else None,
                "last_used_at": row[4].isoformat() if row[4] else None,
                "status": "deleted" if row[5] else "active",
                "deleted_at": row[5].isoformat() if row[5] else None,
            })

        return {
            "tokens": tokens,
            "total_count": len(tokens),
            "note": "For security, encrypted tokens are not included in exports.",
        }

    # =======================================================================
    # PRIVATE METHODS - VIP List
    # =======================================================================

    async def _export_vip_list(self, user_id: str) -> dict:
        """Export VIP list."""
        async with db_pool.connection() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    """
                    SELECT
                        v.rank,
                        c.contact_hash,
                        c.display_name,
                        v.created_at,
                        v.deleted_at
                    FROM vip_list v
                    JOIN contacts c ON v.contact_id = c.id
                    WHERE v.user_id = %s
                    ORDER BY v.rank
                    """,
                    (user_id,),
                )
                rows = await cursor.fetchall()

        vips = []
        for row in rows:
            vips.append({
                "rank": row[0],
                "contact_hash": row[1],
                "display_name": row[2],
                "selected_at": row[3].isoformat() if row[3] else None,
                "status": "deleted" if row[4] else "active",
                "deleted_at": row[4].isoformat() if row[4] else None,
            })

        return {
            "vips": vips,
            "total_count": len(vips),
            "note": "Contact hashes are pseudonymized for privacy (HMAC-SHA256).",
        }

    # =======================================================================
    # PRIVATE METHODS - Contacts
    # =======================================================================

    async def _export_contacts(self, user_id: str) -> dict:
        """Export contacts data."""
        async with db_pool.connection() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    """
                    SELECT
                        contact_hash,
                        display_name,
                        email_count_30d,
                        vip_score,
                        created_at,
                        deleted_at
                    FROM contacts
                    WHERE user_id = %s
                    ORDER BY vip_score DESC
                    LIMIT 1000
                    """,
                    (user_id,),
                )
                rows = await cursor.fetchall()

        contacts = []
        for row in rows:
            contacts.append({
                "contact_hash": row[0],
                "display_name": row[1],
                "email_count_30d": row[2],
                "vip_score": float(row[3]) if row[3] else 0.0,
                "created_at": row[4].isoformat() if row[4] else None,
                "status": "deleted" if row[5] else "active",
                "deleted_at": row[5].isoformat() if row[5] else None,
            })

        return {
            "contacts": contacts,
            "total_count": len(contacts),
            "note": "Limited to top 1000 contacts by VIP score. Contact hashes are pseudonymized.",
        }

    # =======================================================================
    # PRIVATE METHODS - Email Metadata
    # =======================================================================

    async def _export_email_metadata(self, user_id: str) -> dict:
        """Export email metadata (summary only, not full content)."""
        async with db_pool.connection() as conn:
            async with conn.cursor() as cursor:
                # Get summary stats
                await cursor.execute(
                    """
                    SELECT
                        COUNT(*) as total_emails,
                        COUNT(*) FILTER (WHERE direction = 'in') as inbound_count,
                        COUNT(*) FILTER (WHERE direction = 'out') as outbound_count,
                        MIN(timestamp) as first_email,
                        MAX(timestamp) as last_email
                    FROM email_metadata
                    WHERE user_id = %s
                    """,
                    (user_id,),
                )
                stats_row = await cursor.fetchone()

        return {
            "total_emails": stats_row[0] if stats_row else 0,
            "inbound_count": stats_row[1] if stats_row else 0,
            "outbound_count": stats_row[2] if stats_row else 0,
            "first_email": stats_row[3].isoformat() if stats_row and stats_row[3] else None,
            "last_email": stats_row[4].isoformat() if stats_row and stats_row[4] else None,
            "note": "Only metadata is exported (not email content). For privacy, individual emails are not included.",
        }

    # =======================================================================
    # PRIVATE METHODS - Events Metadata
    # =======================================================================

    async def _export_events_metadata(self, user_id: str) -> dict:
        """Export events metadata (summary only)."""
        async with db_pool.connection() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    """
                    SELECT
                        COUNT(*) as total_events,
                        COUNT(*) FILTER (WHERE user_is_organizer = true) as organized_count,
                        MIN(start_time) as first_event,
                        MAX(start_time) as last_event
                    FROM events_metadata
                    WHERE user_id = %s
                    """,
                    (user_id,),
                )
                stats_row = await cursor.fetchone()

        return {
            "total_events": stats_row[0] if stats_row else 0,
            "organized_count": stats_row[1] if stats_row else 0,
            "first_event": stats_row[2].isoformat() if stats_row and stats_row[2] else None,
            "last_event": stats_row[3].isoformat() if stats_row and stats_row[3] else None,
            "note": "Only metadata is exported (not event details). For privacy, individual events are not included.",
        }

    # =======================================================================
    # PRIVATE METHODS - User Settings
    # =======================================================================

    async def _export_user_settings(self, user_id: str) -> dict:
        """Export user settings."""
        async with db_pool.connection() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    """
                    SELECT
                        voice_preferences,
                        email_style_preferences,
                        email_style_skipped,
                        updated_at,
                        deleted_at
                    FROM user_settings
                    WHERE user_id = %s
                    """,
                    (user_id,),
                )
                row = await cursor.fetchone()

        if not row:
            return {
                "settings": None,
                "note": "No user settings found.",
            }

        return {
            "voice_preferences": row[0] or {},
            "email_style_preferences": row[1] or {},
            "email_style_skipped": row[2],
            "updated_at": row[3].isoformat() if row[3] else None,
            "status": "deleted" if row[4] else "active",
            "deleted_at": row[4].isoformat() if row[4] else None,
        }

    # =======================================================================
    # PRIVATE METHODS - Audit Log Summary
    # =======================================================================

    async def _export_audit_summary(self, user_id: str) -> dict:
        """Export audit log summary (transparency)."""
        async with db_pool.connection() as conn:
            async with conn.cursor() as cursor:
                # Get summary by action type
                await cursor.execute(
                    """
                    SELECT
                        action,
                        COUNT(*) as count,
                        MIN(created_at) as first_occurrence,
                        MAX(created_at) as last_occurrence
                    FROM audit_logs
                    WHERE user_id = %s
                    GROUP BY action
                    ORDER BY count DESC
                    """,
                    (user_id,),
                )
                action_summary = await cursor.fetchall()

                # Get total count
                await cursor.execute(
                    """
                    SELECT COUNT(*) FROM audit_logs WHERE user_id = %s
                    """,
                    (user_id,),
                )
                total_count = (await cursor.fetchone())[0]

        actions_by_type = []
        for row in action_summary:
            actions_by_type.append({
                "action": row[0],
                "count": row[1],
                "first_occurrence": row[2].isoformat() if row[2] else None,
                "last_occurrence": row[3].isoformat() if row[3] else None,
            })

        return {
            "total_audit_logs": total_count,
            "actions_by_type": actions_by_type,
            "note": "Audit logs provide transparency into actions taken on your data. They are retained for 1 year for compliance.",
        }


# Singleton instance
data_export_service = DataExportService()
