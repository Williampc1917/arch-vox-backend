"""
Aggregation package for VIP onboarding.

Contains services and repositories that transform raw metadata into
per-contact statistics stored in the contacts table.
"""

from .service import ContactAggregationService, contact_aggregation_service

__all__ = ["ContactAggregationService", "contact_aggregation_service"]
