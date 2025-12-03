"""
Legacy access point for VIP repository helpers.

The actual repository lives under app.features.vip_onboarding to keep
the feature code organized; this module simply re-exports it.
"""

from app.features.vip_onboarding.repository import VipRepository

__all__ = ["VipRepository"]
