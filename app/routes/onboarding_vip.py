"""
Legacy router shim for VIP onboarding endpoints.

The feature-specific router lives under app.features.vip_onboarding.
"""

from app.features.vip_onboarding.api import router

__all__ = ["router"]
