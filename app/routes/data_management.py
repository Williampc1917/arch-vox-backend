"""
Data Management API Router - GDPR Compliance Endpoints.

Provides endpoints for:
- Data deletion (Right to Erasure - GDPR Article 17)
- Data export (Right to Data Portability - GDPR Article 20)
- Deletion cancellation (recover data during grace period)

All endpoints require authentication and operate on the authenticated user's data only.
"""

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.auth.verify import auth_dependency
from app.config import settings
from app.middleware.rate_limit_dependencies import rate_limit_user
from app.services.data_management.deletion_service import data_deletion_service
from app.services.data_management.export_service import data_export_service
from app.utils.audit_helpers import audit_security_event

router = APIRouter(prefix="/data", tags=["Data Management (GDPR)"])


@router.delete(
    "/delete-my-data",
    status_code=200,
    summary="Delete my data (GDPR Right to Erasure)",
    description="""
    Delete all your personal data from the system.

    **GDPR Article 17 - Right to Erasure**

    This endpoint:
    - Soft deletes your data with 30-day grace period
    - Allows recovery during grace period (see /cancel-deletion)
    - Revokes OAuth tokens with Google (prevents future use)
    - Logs deletion request in audit trail

    After 30 days, data is permanently deleted (cannot be recovered).

    **What gets deleted:**
    - OAuth credentials (Gmail access)
    - VIP contact selections
    - User preferences
    - Email processing cache

    **What does NOT get deleted:**
    - Audit logs (kept for 1 year for compliance)
    """,
)
async def delete_my_data(
    request: Request,
    claims: dict = Depends(auth_dependency),
    _rate: None = Depends(
        lambda r, c=Depends(auth_dependency): rate_limit_user(
            r, c, user_limit=settings.get_rate_limits()["write_endpoints"]
        )
    ),
):
    """
    Delete user's data (soft delete with grace period).

    Rate limit: 30 req/min (production), 300 req/min (dev)
    """
    user_id = claims.get("sub")

    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User ID not found in claims",
        )

    # Check if GDPR deletion is enabled
    retention_config = settings.get_data_retention_config()
    if not retention_config["data_deletion_enabled"]:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Data deletion is currently disabled",
        )

    # Soft delete user data
    result = await data_deletion_service.soft_delete_user_data(user_id)

    # Audit log the deletion request
    await audit_security_event(
        request=request,
        event_type="data_deletion_requested",
        severity="high",
        description="User requested deletion of all personal data (GDPR Right to Erasure)",
        user_id=user_id,
        metadata={
            "deleted_items": result["deleted_items"],
            "total_items": result["total_items"],
            "grace_period_days": result["grace_period_days"],
            "grace_period_until": result["grace_period_until"],
            "revoked_oauth": result["revoked_oauth"],
        },
    )

    return {
        "success": True,
        "message": "Your data has been scheduled for deletion",
        "deleted_at": result["deleted_at"],
        "grace_period_until": result["grace_period_until"],
        "grace_period_days": result["grace_period_days"],
        "deleted_items": result["deleted_items"],
        "total_items": result["total_items"],
        "can_recover_until": result["grace_period_until"],
        "recovery_endpoint": "/data/cancel-deletion",
        "note": "You can cancel this deletion and recover your data within the grace period by calling /data/cancel-deletion",
    }


@router.post(
    "/cancel-deletion",
    status_code=200,
    summary="Cancel data deletion (recover data)",
    description="""
    Cancel your data deletion request and recover your data.

    **Only works during grace period (30 days after deletion request)**

    This will:
    - Restore all soft-deleted data
    - Make your data accessible again
    - Log recovery in audit trail

    After grace period expires, data is permanently deleted and cannot be recovered.
    """,
)
async def cancel_deletion(
    request: Request,
    claims: dict = Depends(auth_dependency),
    _rate: None = Depends(
        lambda r, c=Depends(auth_dependency): rate_limit_user(
            r, c, user_limit=settings.get_rate_limits()["write_endpoints"]
        )
    ),
):
    """
    Cancel deletion and recover user's data.

    Rate limit: 30 req/min (production), 300 req/min (dev)
    """
    user_id = claims.get("sub")

    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User ID not found in claims",
        )

    # Cancel deletion (recover data)
    result = await data_deletion_service.cancel_deletion(user_id)

    if result["total_items"] == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No pending deletion found (data may have already been permanently deleted)",
        )

    # Audit log the recovery
    await audit_security_event(
        request=request,
        event_type="data_deletion_cancelled",
        severity="medium",
        description="User cancelled data deletion and recovered data",
        user_id=user_id,
        metadata={
            "recovered_items": result["recovered_items"],
            "total_items": result["total_items"],
        },
    )

    return {
        "success": True,
        "message": "Your data has been recovered",
        "recovered_items": result["recovered_items"],
        "total_items": result["total_items"],
        "note": "Your data is now accessible again",
    }


@router.get(
    "/export-my-data",
    status_code=200,
    summary="Export my data (GDPR Right to Data Portability)",
    description="""
    Export all your personal data in JSON format.

    **GDPR Article 20 - Right to Data Portability**

    This endpoint:
    - Exports all your personal data
    - Returns JSON format (machine-readable)
    - Excludes encrypted secrets (security)
    - Includes metadata for transparency
    - Logs export request in audit trail

    **What gets exported:**
    - OAuth metadata (scopes, expiry, etc.)
    - VIP contact selections
    - Audit log summary (transparency)
    - User preferences

    **What does NOT get exported:**
    - Encrypted OAuth tokens (security)
    - Other users' data
    """,
)
async def export_my_data(
    request: Request,
    claims: dict = Depends(auth_dependency),
    _rate: None = Depends(
        lambda r, c=Depends(auth_dependency): rate_limit_user(
            r, c, user_limit=settings.get_rate_limits()["read_endpoints"]
        )
    ),
):
    """
    Export user's data in JSON format.

    Rate limit: 100 req/min (production), 1000 req/min (dev)
    """
    user_id = claims.get("sub")

    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User ID not found in claims",
        )

    # Check if GDPR export is enabled
    retention_config = settings.get_data_retention_config()
    if not retention_config["data_export_enabled"]:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Data export is currently disabled",
        )

    # Export user data
    export_data = await data_export_service.export_user_data(user_id)

    # Audit log the export request
    await audit_security_event(
        request=request,
        event_type="data_export_requested",
        severity="medium",
        description="User requested export of all personal data (GDPR Right to Data Portability)",
        user_id=user_id,
        metadata={
            "format": "JSON",
            "format_version": export_data["metadata"]["format_version"],
        },
    )

    return export_data


@router.get(
    "/deletion-status",
    status_code=200,
    summary="Check deletion status",
    description="""
    Check if you have a pending deletion request and when grace period ends.

    Returns:
    - Whether deletion is pending
    - When deletion was requested
    - When grace period ends
    - What can be recovered
    """,
)
async def deletion_status(
    request: Request,
    claims: dict = Depends(auth_dependency),
    _rate: None = Depends(
        lambda r, c=Depends(auth_dependency): rate_limit_user(
            r, c, user_limit=settings.get_rate_limits()["read_endpoints"]
        )
    ),
):
    """
    Check user's deletion status.

    Rate limit: 100 req/min (production), 1000 req/min (dev)
    """
    user_id = claims.get("sub")

    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User ID not found in claims",
        )

    # Check if any data is soft deleted (check all user data tables)
    from app.db.pool import db_pool

    async with db_pool.connection() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(
                """
                SELECT
                    deleted_at,
                    grace_period_until
                FROM oauth_tokens
                WHERE user_id = %s AND deleted_at IS NOT NULL
                LIMIT 1
                """,
                (user_id,),
            )
            row = await cursor.fetchone()

    if not row:
        return {
            "deletion_pending": False,
            "message": "No pending deletion request",
        }

    deleted_at, grace_period_until = row

    return {
        "deletion_pending": True,
        "deleted_at": deleted_at.isoformat(),
        "grace_period_until": grace_period_until.isoformat(),
        "can_recover": grace_period_until > deleted_at,
        "recovery_endpoint": "/data/cancel-deletion",
        "message": "You have a pending deletion request. You can cancel it and recover your data using /data/cancel-deletion",
    }
