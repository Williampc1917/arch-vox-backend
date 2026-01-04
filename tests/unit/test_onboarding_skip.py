from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from app.models.domain.user_domain import Plan, UserProfile
from app.services.core.onboarding_service import (
    OnboardingServiceError,
    skip_email_style_step,
)


def _build_profile(
    *,
    onboarding_step: str,
    onboarding_completed: bool,
    gmail_connected: bool = True,
    email_style_skipped: bool = False,
) -> UserProfile:
    now = datetime.now(UTC)
    plan = Plan(name="pro", max_daily_requests=100)
    return UserProfile(
        user_id="user-123",
        email="user@example.com",
        display_name="User",
        is_active=True,
        timezone="UTC",
        onboarding_completed=onboarding_completed,
        gmail_connected=gmail_connected,
        onboarding_step=onboarding_step,
        email_style_skipped=email_style_skipped,
        voice_preferences={"tone": "neutral"},
        plan=plan,
        created_at=now,
        updated_at=now,
    )


@pytest.mark.asyncio
async def test_skip_email_style_step_success(monkeypatch):
    """User in email_style step can skip and reach completed without styles."""
    initial_profile = _build_profile(onboarding_step="email_style", onboarding_completed=False)
    completed_profile = _build_profile(
        onboarding_step="completed",
        onboarding_completed=True,
        email_style_skipped=True,
    )

    get_profile_mock = AsyncMock(side_effect=[initial_profile, completed_profile])
    persist_skip_mock = AsyncMock(return_value=1)
    calendar_permissions_mock = AsyncMock(return_value=True)
    gmail_valid_mock = AsyncMock(return_value=True)
    skip_flag_mock = AsyncMock(return_value=True)

    monkeypatch.setattr(
        "app.services.core.onboarding_service.get_user_profile",
        get_profile_mock,
    )
    monkeypatch.setattr(
        "app.services.core.onboarding_service._check_calendar_permissions",
        calendar_permissions_mock,
    )
    monkeypatch.setattr(
        "app.services.core.onboarding_service._validate_gmail_connection",
        gmail_valid_mock,
    )
    monkeypatch.setattr(
        "app.services.core.onboarding_service.set_email_style_skipped",
        skip_flag_mock,
    )
    monkeypatch.setattr(
        "app.services.core.onboarding_service._persist_email_style_skip",
        persist_skip_mock,
    )

    result = await skip_email_style_step("user-123")

    assert result == completed_profile
    persist_skip_mock.assert_awaited_once_with("user-123", True)
    calendar_permissions_mock.assert_awaited_once_with("user-123")
    gmail_valid_mock.assert_awaited_once_with("user-123")
    skip_flag_mock.assert_awaited_once_with("user-123", True)


@pytest.mark.asyncio
async def test_skip_email_style_step_invalid_step(monkeypatch):
    """Skipping should fail if user is not in the email_style step."""
    profile = _build_profile(onboarding_step="gmail", onboarding_completed=False)

    monkeypatch.setattr(
        "app.services.core.onboarding_service.get_user_profile",
        AsyncMock(return_value=profile),
    )
    monkeypatch.setattr(
        "app.services.core.onboarding_service.set_email_style_skipped",
        AsyncMock(return_value=False),
    )

    with pytest.raises(OnboardingServiceError):
        await skip_email_style_step("user-123")
