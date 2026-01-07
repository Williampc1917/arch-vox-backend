"""
VIP onboarding routes.

Expose endpoints for checking VIP backfill/aggregation status and
retrieving candidate VIPs for the onboarding flow.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, EmailStr, Field

from app.auth.verify import auth_dependency
from app.config import settings
from app.features.vip_onboarding.pipeline.aggregation import contact_aggregation_service
from app.features.vip_onboarding.pipeline.aggregation.repository import (
    ContactAggregationRepository,
)
from app.features.vip_onboarding.pipeline.scoring import scoring_service
from app.features.vip_onboarding.domain import ContactIdentityRecord
from app.features.vip_onboarding.repository.contact_identity_repository import (
    ContactIdentityRepository,
)
from app.infrastructure.observability.logging import get_logger
from app.middleware.rate_limit_dependencies import rate_limit_user
from app.security.hashing import hash_email
from app.services.core.onboarding_service import get_onboarding_status
from app.services.infrastructure.encryption_service import encrypt_data
from app.utils.audit_helpers import audit_data_modification, audit_pii_access

router = APIRouter(prefix="/onboarding/vips", tags=["onboarding-vips"])
logger = get_logger(__name__)


class VipSelectionRequest(BaseModel):
    contacts: list[str] = Field(..., min_length=1, max_length=20)


class VipManualContactRequest(BaseModel):
    email: EmailStr
    display_name: str | None = Field(default=None, max_length=120)


@router.get("/status")
async def get_vip_onboarding_status(claims: dict = Depends(auth_dependency)) -> dict:
    """Return the current VIP backfill + aggregation status for the user."""

    user_id = claims.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token: missing user ID",
        )
    if not settings.VIP_BACKFILL_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="VIP backfill is currently disabled.",
        )
    if not settings.VIP_IDENTITY_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Manual contact add is disabled until VIP identity storage is enabled.",
        )

    profile = await get_onboarding_status(user_id)
    if not profile:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    # Check if contacts are ready
    backfill_complete = await contact_aggregation_service.has_contacts(user_id)

    # Get latest VIP backfill job details
    from app.features.vip_onboarding.repository.vip_repository import VipRepository

    latest_job = await VipRepository.load_latest_job_for_user(user_id)

    # Determine if user can retry based on job status and attempts
    MAX_RETRY_ATTEMPTS = 3
    can_retry = False
    job_status_value = None
    error_message = None

    if latest_job:
        job_status_value = latest_job.status
        error_message = latest_job.error_message

        # Allow retry if job failed and hasn't exceeded max attempts
        if latest_job.status == "failed" and latest_job.attempts < MAX_RETRY_ATTEMPTS:
            can_retry = True

    return {
        "backfill_ready": backfill_complete,
        "job_status": job_status_value,
        "error_message": error_message,
        "can_retry": can_retry,
        "vip_onboarding_skipped": getattr(profile, "vip_onboarding_skipped", False),
        "vip_acquisition_status": getattr(profile, "vip_acquisition_status", "active"),
        "vip_last_attempt_at": getattr(profile, "vip_last_attempt_at", None),
    }


@router.post("/retry-backfill", status_code=202)
async def retry_vip_backfill(claims: dict = Depends(auth_dependency)) -> dict:
    """
    Manually trigger a VIP backfill retry for users with failed jobs.

    Returns 202 Accepted if retry was enqueued successfully.
    Returns 400 Bad Request if retry is not allowed (job not failed or max attempts exceeded).
    """
    user_id = claims.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token: missing user ID",
        )

    # Load the latest job to check if retry is allowed
    from app.features.vip_onboarding.repository.vip_repository import VipRepository

    latest_job = await VipRepository.load_latest_job_for_user(user_id)

    MAX_RETRY_ATTEMPTS = 3

    # Validate retry eligibility
    if not latest_job:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No VIP backfill job found for this user.",
        )

    if latest_job.status != "failed":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot retry job with status '{latest_job.status}'. Only failed jobs can be retried.",
        )

    if latest_job.attempts >= MAX_RETRY_ATTEMPTS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Maximum retry attempts ({MAX_RETRY_ATTEMPTS}) exceeded. Please contact support.",
        )

    # Enqueue a new job with force=True to bypass deduplication
    from app.features.vip_onboarding.services.scheduler import (
        VipSchedulerError,
        enqueue_vip_backfill_job,
    )

    try:
        new_job = await enqueue_vip_backfill_job(
            user_id=user_id, trigger_reason="manual_retry", force=True
        )

        if not new_job:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to enqueue retry job.",
            )

        logger.info(
            "VIP backfill retry enqueued",
            user_id=user_id,
            new_job_id=new_job.id,
            previous_job_id=latest_job.id,
            previous_attempts=latest_job.attempts,
        )

        return {
            "message": "VIP backfill retry enqueued successfully.",
            "job_id": new_job.id,
            "attempt": new_job.attempts,
        }

    except VipSchedulerError as exc:
        logger.error(
            "VIP backfill retry failed",
            user_id=user_id,
            error=str(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to enqueue VIP backfill retry. Please try again later.",
        ) from exc


@router.get("/")
async def list_vip_candidates(
    request: Request,
    claims: dict = Depends(auth_dependency),
    limit: int = Query(50, ge=1, le=100),
    _rate: None = Depends(
        lambda r, c=Depends(auth_dependency): rate_limit_user(
            r, c, user_limit=settings.get_rate_limits()["vip_endpoints"]
        )
    ),
) -> dict:
    """
    Return aggregated contacts to be used as VIP candidates.

    This endpoint accesses PII (display names) and is:
    - Audit logged for Gmail API compliance
    - Rate limited to prevent data scraping (60 req/min in production)
    """

    user_id = claims.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token: missing user ID",
        )

    candidates = await scoring_service.score_contacts_for_user(user_id, limit)

    # Handle 0 contacts gracefully - return empty list instead of 404
    if not candidates:
        logger.warning(
            "No VIP candidates found for user",
            user_id=user_id,
            reason="no_contacts_or_data_not_ready",
        )
        return {"vips": []}

    # ✅ AUDIT LOG: Track PII access (display names, emails)
    await audit_pii_access(
        request=request,
        user_id=user_id,
        action="vip_candidates_viewed",
        resource_type="vip_contacts",
        resource_count=len(candidates),
        pii_fields=["email", "display_name", "contact_hash"],
        metadata={"requested_limit": limit, "returned_count": len(candidates)},
    )

    serialized = [
        {
            "contact_hash": c.contact_hash,
            "email": c.email,
            "display_name": c.display_name,
            "vip_score": round(c.vip_score, 4),
            "metrics": c.raw_metrics,
        }
        for c in candidates
    ]

    return {"vips": serialized}


@router.post("/selection", status_code=204)
async def save_vip_selection(
    req: Request,
    vip_request: VipSelectionRequest,
    claims: dict = Depends(auth_dependency),
    _rate: None = Depends(
        lambda r, c=Depends(auth_dependency): rate_limit_user(
            r, c, user_limit=settings.get_rate_limits()["write_endpoints"]
        )
    ),
):
    """
    Save user's VIP selection.

    This endpoint modifies user data and is:
    - Audit logged for compliance
    - Rate limited to prevent spam writes (30 req/min in production)
    """
    user_id = claims.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token: missing user ID",
        )

    # Validate selection count (1-20 VIPs)
    if len(vip_request.contacts) < 1 or len(vip_request.contacts) > 20:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Must select between 1 and 20 VIPs. You selected {len(vip_request.contacts)}.",
        )

    try:
        await scoring_service.save_vip_selection(user_id, vip_request.contacts)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    # ✅ AUDIT LOG: Track VIP selection saved
    await audit_data_modification(
        request=req,
        user_id=user_id,
        action="vip_selection_saved",
        resource_type="vip_selections",
        changes={"vip_count": len(vip_request.contacts), "selected_at": "now"},
    )

    # Complete onboarding after VIP selection is saved
    from app.services.core.onboarding_service import OnboardingServiceError, complete_onboarding

    try:
        profile = await complete_onboarding(user_id)
        if profile:
            logger.info(
                "Onboarding completed after VIP selection",
                user_id=user_id,
                vip_count=len(vip_request.contacts),
                step_transition="vip_selection → completed",
            )
        else:
            logger.warning(
                "VIP selection saved but onboarding completion failed",
                user_id=user_id,
                vip_count=len(vip_request.contacts),
            )
    except OnboardingServiceError as e:
        logger.warning(
            "Failed to complete onboarding after VIP selection",
            user_id=user_id,
            error=str(e),
            recoverable=e.recoverable,
        )
        # Don't fail the request - VIP selection was saved successfully
    except Exception as e:
        logger.error(
            "Unexpected error completing onboarding after VIP selection",
            user_id=user_id,
            error=str(e),
        )
        # Don't fail the request - VIP selection was saved successfully

    return None


@router.post("/skip", status_code=204)
async def skip_vip_onboarding(
    req: Request,
    claims: dict = Depends(auth_dependency),
    _rate: None = Depends(
        lambda r, c=Depends(auth_dependency): rate_limit_user(
            r, c, user_limit=settings.get_rate_limits()["write_endpoints"]
        )
    ),
):
    """
    Skip VIP selection and complete onboarding.
    """
    user_id = claims.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token: missing user ID",
        )

    from app.services.core.onboarding_service import (
        OnboardingServiceError,
        skip_vip_onboarding as skip_vip_onboarding_step,
    )

    try:
        await skip_vip_onboarding_step(user_id)
    except OnboardingServiceError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    await audit_data_modification(
        request=req,
        user_id=user_id,
        action="vip_onboarding_skipped",
        resource_type="vip_onboarding",
        changes={"skipped_at": "now"},
    )

    return None


@router.post("/manual", status_code=201)
async def add_manual_contact(
    req: Request,
    payload: VipManualContactRequest,
    claims: dict = Depends(auth_dependency),
    _rate: None = Depends(
        lambda r, c=Depends(auth_dependency): rate_limit_user(
            r, c, user_limit=settings.get_rate_limits()["write_endpoints"]
        )
    ),
) -> dict:
    """
    Manually add a contact for VIP selection.

    This endpoint stores encrypted identity data and ensures a contact row exists
    for selection validation.
    """
    user_id = claims.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token: missing user ID",
        )

    email = payload.email.strip().lower()
    display_name = payload.display_name.strip() if payload.display_name else None
    contact_hash = hash_email(email)

    await ContactAggregationRepository.ensure_contact_exists(user_id, contact_hash)

    await ContactIdentityRepository.upsert_identities(
        [
            ContactIdentityRecord(
                user_id=user_id,
                contact_hash=contact_hash,
                email_encrypted=encrypt_data(email),
                display_name_encrypted=encrypt_data(display_name) if display_name else None,
            )
        ]
    )

    await audit_data_modification(
        request=req,
        user_id=user_id,
        action="vip_manual_contact_added",
        resource_type="vip_contact_identity",
        changes={"contact_hash": contact_hash},
    )

    await audit_pii_access(
        request=req,
        user_id=user_id,
        action="vip_manual_contact_added",
        resource_type="vip_contact_identity",
        resource_id=contact_hash,
        resource_count=1,
        pii_fields=["email", "display_name"],
    )

    return {"contact_hash": contact_hash, "email": email, "display_name": display_name}
