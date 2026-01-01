"""
VIP scoring package.

Provides services that rank aggregated contacts and persist the user's
final VIP selections.
"""

from .service import ScoringService, scoring_service

__all__ = ["ScoringService", "scoring_service"]

