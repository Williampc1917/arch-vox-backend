"""
Audit logging infrastructure for PII access tracking.

This module provides centralized audit logging for Gmail API compliance
and GDPR requirements.
"""

from app.infrastructure.audit.audit_logger import AuditLogger, audit_logger

__all__ = ["AuditLogger", "audit_logger"]
