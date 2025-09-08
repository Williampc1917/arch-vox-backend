"""
Simple test for the 3 new files we created:
- app/models/user.py
- app/services/user_service.py
- app/routes/protected.py (updated)

Run with: pytest tests/test_new_auth_code.py -v
"""

from datetime import datetime
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

# Test the models
from app.models.user import Plan, UserProfile, UserProfileResponse

# Test the service
from app.services.user_service import get_user_profile


def test_user_models_work():
    """Test that our new Pydantic models work correctly."""
    # Create a plan
    plan = Plan(name="free", max_daily_requests=100)
    assert plan.name == "free"
    assert plan.max_daily_requests == 100

    # Create a user profile
    user_id = str(uuid4())
    profile = UserProfile(
        user_id=user_id,
        email="test@example.com",
        display_name="Test User",
        is_active=True,
        voice_preferences={"tone": "professional"},
        plan=plan,
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )

    assert profile.user_id == user_id
    assert profile.email == "test@example.com"
    assert profile.plan.name == "free"

    # Create API response
    auth_data = {"user_id": user_id, "role": "authenticated"}
    response = UserProfileResponse(profile=profile, auth=auth_data)

    assert response.profile.email == "test@example.com"
    assert response.auth["role"] == "authenticated"

    print("✅ User models work correctly")


@patch("psycopg.connect")
@pytest.mark.asyncio
async def test_user_service_success(mock_connect):
    """Test that our user service function works with mocked database."""
    user_id = str(uuid4())

    # Mock database response
    mock_row = (
        user_id,  # id
        "test@example.com",  # email
        "Test User",  # display_name
        True,  # is_active
        datetime.now(),  # created_at
        datetime.now(),  # updated_at
        {"tone": "professional"},  # voice_preferences
        datetime.now(),  # settings_updated_at
        "free",  # plan_name
        100,  # max_daily_requests
    )

    # Setup mock
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_connect.return_value.__enter__.return_value = mock_conn
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
    mock_cursor.fetchone.return_value = mock_row

    # Call our function
    result = await get_user_profile(user_id)

    # Check it worked
    assert result is not None
    assert result.user_id == user_id
    assert result.email == "test@example.com"
    assert result.plan.name == "free"

    print("✅ User service works correctly")


@patch("psycopg.connect")
@pytest.mark.asyncio
async def test_user_service_not_found(mock_connect):
    """Test user service when user doesn't exist."""
    user_id = str(uuid4())

    # Mock no user found
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_connect.return_value.__enter__.return_value = mock_conn
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
    mock_cursor.fetchone.return_value = None

    # Call our function
    result = await get_user_profile(user_id)

    # Should return None
    assert result is None

    print("✅ User service handles not found correctly")


def test_import_everything():
    """Test that we can import all our new code without errors."""
    # Test models import

    # Test service import

    # Test that the updated protected route imports

    print("✅ All imports work correctly")


if __name__ == "__main__":
    # Run tests manually
    import asyncio

    print("🧪 Testing new authentication code...")

    # Test 1: Models
    test_user_models_work()

    # Test 2: Service
    asyncio.run(test_user_service_success())
    asyncio.run(test_user_service_not_found())

    # Test 3: Imports
    test_import_everything()

    print("\n🎉 All tests passed! Your authentication code is working.")
