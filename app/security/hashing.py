"""
Deterministic HMAC-SHA256 helpers for Gmail metadata pseudonymization.

All hashing occurs server-side, never exposing raw identifiers beyond
transient memory, aligning with Google Restricted Scopes guidance.
"""

from __future__ import annotations

import hashlib
import hmac
from typing import Iterable

from app.config import settings

SECRET_MIN_LENGTH = 16  # keep configurable but catch obvious misconfiguration

__all__ = [
    "compute_hmac",
    "hash_email",
    "hash_thread_id",
    "hash_message_id",
    "hash_contact",
    "hash_contacts",
    "hash_label",
]


class HashingError(RuntimeError):
    """Raised when hashing prerequisites are not satisfied."""


def _secret_bytes() -> bytes:
    secret = getattr(settings, "HASHING_SECRET", None)
    if not secret:
        raise HashingError("HASHING_SECRET is not configured")
    if len(secret) < SECRET_MIN_LENGTH:
        raise HashingError("HASHING_SECRET is too short; please rotate it")
    return secret.encode("utf-8")


def compute_hmac(value: str, *, namespace: str) -> str:
    """
    Compute a namespaced hex HMAC-SHA256 digest.

    Args:
        value: Raw string value to hash (will be normalized by caller).
        namespace: Logical namespace/salt to avoid cross-field collisions.
    """
    payload = value or ""
    scoped = f"{namespace}:{payload}"
    digest = hmac.new(_secret_bytes(), scoped.encode("utf-8"), hashlib.sha256)
    return digest.hexdigest()


def _normalize_email(email: str | None) -> str:
    return (email or "").strip().lower()


def hash_email(email: str | None) -> str:
    """Deterministically hash a single email address."""
    return compute_hmac(_normalize_email(email), namespace="email")


def hash_contacts(emails: Iterable[str | None]) -> list[str]:
    """
    Hash a collection of addresses while preserving input order.
    """
    return [hash_email(address) for address in emails]


def hash_contact(email: str | None) -> str:
    """Alias to hash_email for readability."""
    return hash_email(email)


def hash_thread_id(thread_id: str | None) -> str:
    """Deterministically hash a Gmail thread id."""
    return compute_hmac(thread_id or "", namespace="thread")


def hash_message_id(message_id: str | None) -> str:
    """Deterministically hash a Gmail message id."""
    return compute_hmac(message_id or "", namespace="message")


def hash_label(label_id: str | None) -> str:
    """Hash a Gmail label identifier should we store label metadata."""
    return compute_hmac(label_id or "", namespace="label")
