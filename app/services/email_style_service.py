# app/services/email_style_service.py
"""
Email Style Service
Handles 3-profile email style management including validation and storage.
"""

import re
from typing import Any

from app.db.helpers import (
    get_email_style_preferences,
    store_email_style_preferences,
)
from app.infrastructure.observability.logging import get_logger
from app.services.email_style_rate_limiter import (
    RateLimitExceeded,
    check_email_extraction_limit,
    get_rate_limit_error_message,
    record_email_extraction_attempt,
)

logger = get_logger(__name__)


class EmailStyleError(Exception):
    """Base exception for email style service errors."""

    def __init__(self, message: str, user_id: str | None = None, recoverable: bool = True):
        super().__init__(message)
        self.user_id = user_id
        self.recoverable = recoverable


class InvalidEmailExamples(EmailStyleError):
    """Raised when provided email examples are invalid or insufficient."""

    pass


class EmailStyleService:
    """
    Service for managing 3-profile email styles.

    Handles validation, storage, and retrieval of email style preferences.
    """

    def __init__(self):
        logger.info("Email style service initialized for 3-profile system")

    async def validate_email_examples(self, labeled_emails: dict[str, str]) -> dict[str, Any]:
        """
        Validate labeled email examples for 3-profile extraction.

        Args:
            labeled_emails: {"professional": "...", "casual": "...", "friendly": "..."}

        Returns:
            dict: Validation results with issues found

        Raises:
            InvalidEmailExamples: If examples are not suitable for extraction
        """
        try:
            validation_result = {"valid": True, "issues": [], "warnings": []}

            # Check all 3 required labels exist
            required_labels = ["professional", "casual", "friendly"]
            for label in required_labels:
                if label not in labeled_emails:
                    validation_result["valid"] = False
                    validation_result["issues"].append(f"Missing {label} email")
                    continue

                email = labeled_emails[label]

                # Check if email is not empty
                if not email or not email.strip():
                    validation_result["valid"] = False
                    validation_result["issues"].append(f"{label.title()} email is empty")
                    continue

                # Check minimum length (should have subject + substantial body)
                if len(email.strip()) < 50:
                    validation_result["valid"] = False
                    validation_result["issues"].append(
                        f"{label.title()} email too short (minimum 50 characters)"
                    )

                # Check if it has both subject and body structure
                if not self._has_email_structure(email):
                    validation_result["warnings"].append(
                        f"{label.title()} email may be missing subject or proper structure"
                    )

                # Check for suspicious content
                if self._has_suspicious_content(email):
                    validation_result["valid"] = False
                    validation_result["issues"].append(
                        f"{label.title()} email contains suspicious or inappropriate content"
                    )

            # Final validation
            if not validation_result["valid"]:
                issues_text = "; ".join(validation_result["issues"])
                raise InvalidEmailExamples(f"Email examples validation failed: {issues_text}")

            logger.info(
                "Labeled email examples validated successfully",
                email_labels=list(labeled_emails.keys()),
                warnings_count=len(validation_result["warnings"]),
            )

            return validation_result

        except InvalidEmailExamples:
            raise
        except Exception as e:
            logger.error("Error validating email examples", error=str(e))
            raise EmailStyleError(f"Email validation failed: {e}") from e

    def _has_email_structure(self, email: str) -> bool:
        """Check if email has basic structure (subject line, body content)."""
        lines = email.strip().split("\n")

        # Should have multiple lines or clear subject/body separation
        if len(lines) < 2:
            # Single line emails are suspicious unless very long
            return len(email.strip()) > 100

        # Look for common email patterns
        has_greeting = any(
            greeting in email.lower() for greeting in ["hi ", "hey ", "hello ", "dear "]
        )

        has_closing = any(
            closing in email.lower()
            for closing in ["thanks", "best", "regards", "sincerely", "cheers"]
        )

        return has_greeting or has_closing

    def _has_suspicious_content(self, email: str) -> bool:
        """Check for suspicious or inappropriate content."""
        email_lower = email.lower()

        # Check for obviously inappropriate content
        suspicious_patterns = [
            r"\b(password|credit card|ssn|social security)\b",
            r"\b(hack|phishing|scam|fraud)\b",
            r"\b(urgent.*money|nigerian prince|lottery winner)\b",
        ]

        for pattern in suspicious_patterns:
            if re.search(pattern, email_lower):
                return True

        # Check for overly repetitive content
        words = email_lower.split()
        if len(words) > 10:
            unique_words = len(set(words))
            repetition_ratio = unique_words / len(words)
            if repetition_ratio < 0.3:  # More than 70% repeated words
                return True

        return False

    async def store_user_email_style(
        self,
        user_id: str,
        style_profiles: dict[str, Any],
        extraction_grades: dict[str, str] | None = None,
    ) -> bool:
        """
        Store user's 3 email style profiles in database.

        Args:
            user_id: UUID string of the user
            style_profiles: {"professional": {...}, "casual": {...}, "friendly": {...}}
            extraction_grades: {"professional": "A", "casual": "B", "friendly": "A"}

        Returns:
            bool: True if storage successful

        Raises:
            EmailStyleError: If storage fails
        """
        try:
            # Validate all 3 profiles exist
            required_types = ["professional", "casual", "friendly"]
            for style_type in required_types:
                if style_type not in style_profiles:
                    raise EmailStyleError(f"Missing {style_type} profile", user_id=user_id)

            # Prepare preferences structure
            preferences = {
                "styles": style_profiles,  # All 3 profiles
                "created_at": self._get_current_timestamp(),
                "version": "2.0",
            }

            # Add extraction metadata if grades provided
            if extraction_grades:
                preferences["extraction_metadata"] = {
                    "grades": extraction_grades,
                    "extraction_timestamp": self._get_current_timestamp(),
                }

            # Store in database
            success = await store_email_style_preferences(user_id, preferences)

            if not success:
                raise EmailStyleError("Database storage failed", user_id=user_id)

            logger.info(
                "3 email styles stored successfully",
                user_id=user_id,
                style_types=list(style_profiles.keys()),
                grades=extraction_grades,
            )

            return True

        except Exception as e:
            logger.error(
                "Error storing email styles",
                user_id=user_id,
                style_types=list(style_profiles.keys()) if style_profiles else [],
                error=str(e),
            )
            raise EmailStyleError(f"Failed to store email styles: {e}", user_id=user_id) from e

    async def get_user_email_style_preferences(self, user_id: str) -> dict[str, Any] | None:
        """
        Get user's current email style preferences (all 3 profiles).

        Args:
            user_id: UUID string of the user

        Returns:
            dict: Email style preferences with all 3 profiles or None if not found
        """
        try:
            preferences = await get_email_style_preferences(user_id)

            if preferences:
                logger.debug(
                    "Email style preferences retrieved",
                    user_id=user_id,
                    version=preferences.get("version"),
                    has_styles=bool(preferences.get("styles")),
                )
            else:
                logger.debug("No email style preferences found", user_id=user_id)

            return preferences

        except Exception as e:
            logger.error("Error getting email style preferences", user_id=user_id, error=str(e))
            raise EmailStyleError(
                f"Failed to get email style preferences: {e}", user_id=user_id
            ) from e

    async def get_email_style_options(self, user_id: str) -> dict[str, Any]:
        """
        Get 3-profile creation status for user.

        Args:
            user_id: UUID string of the user

        Returns:
            dict: Status of each style and overall completion
        """
        try:
            current_preferences = await self.get_user_email_style_preferences(user_id)

            # Check which styles exist
            styles_created = {
                "professional": False,
                "casual": False,
                "friendly": False,
            }

            if current_preferences and "styles" in current_preferences:
                styles = current_preferences["styles"]
                for style_type in ["professional", "casual", "friendly"]:
                    styles_created[style_type] = styles.get(style_type) is not None

            all_complete = all(styles_created.values())

            # Get rate limit status
            rate_limit_status = None
            try:
                from app.services.email_style_rate_limiter import get_email_extraction_status

                rate_limit_status = await get_email_extraction_status(user_id)
            except Exception as e:
                logger.warning("Could not get rate limit status", user_id=user_id, error=str(e))

            result = {
                "styles_created": styles_created,
                "all_styles_complete": all_complete,
                "can_advance": all_complete,
                "rate_limit_info": rate_limit_status,
                "current_preferences": current_preferences,
            }

            logger.info(
                "Email style options retrieved",
                user_id=user_id,
                all_complete=all_complete,
                styles_created=styles_created,
            )

            return result

        except Exception as e:
            logger.error("Error getting email style options", user_id=user_id, error=str(e))
            raise EmailStyleError(f"Failed to get email style options: {e}", user_id=user_id) from e

    async def create_custom_style_with_rate_limiting(
        self, user_id: str, labeled_emails: dict[str, str]
    ) -> dict[str, Any]:
        """
        Create 3 custom email styles with rate limiting and OpenAI integration.
        This is the main entry point for 3-profile creation.

        Args:
            user_id: UUID string of the user
            labeled_emails: {"professional": "...", "casual": "...", "friendly": "..."}

        Returns:
            dict: Custom style creation result with all 3 profiles

        Raises:
            RateLimitExceeded: If user has exceeded daily limit
            InvalidEmailExamples: If examples are invalid
            EmailStyleError: If creation fails
        """
        try:
            # Step 1: Validate labeled emails
            validation_result = await self.validate_email_examples(labeled_emails)

            # Step 2: Check rate limiting
            try:
                rate_limit_check = await check_email_extraction_limit(user_id)
                logger.info(
                    "Rate limit check passed for 3-profile creation",
                    user_id=user_id,
                    remaining=rate_limit_check.get("remaining"),
                    daily_limit=rate_limit_check.get("daily_limit"),
                )
            except RateLimitExceeded as e:
                # Generate user-friendly error message
                error_message = get_rate_limit_error_message(e.used, e.limit, e.reset_time)

                logger.warning(
                    "3-profile creation blocked by rate limit",
                    user_id=user_id,
                    used=e.used,
                    limit=e.limit,
                )

                return {
                    "success": False,
                    "error": "rate_limit_exceeded",
                    "message": error_message,
                    "rate_limit_info": {
                        "used": e.used,
                        "limit": e.limit,
                        "reset_time": e.reset_time.isoformat(),
                    },
                }

            # Step 3: Extract 3 styles using OpenAI
            try:
                from app.services.openai_service import extract_custom_email_style

                openai_result = await extract_custom_email_style(labeled_emails)

                extraction_success = True
                extraction_result = openai_result["style_profiles"]  # All 3 profiles
                extraction_grades = openai_result["extraction_grades"]  # Grades per profile
                extraction_error = None

                logger.info(
                    "OpenAI 3-profile extraction completed successfully",
                    user_id=user_id,
                    grades=extraction_grades,
                    profiles=list(extraction_result.keys()),
                )

            except Exception as openai_error:
                extraction_success = False
                extraction_result = {}
                extraction_grades = {"professional": "C", "casual": "C", "friendly": "C"}
                extraction_error = f"OpenAI extraction failed: {str(openai_error)}"

                logger.error(
                    "OpenAI 3-profile extraction failed",
                    user_id=user_id,
                    error=str(openai_error),
                    error_type=type(openai_error).__name__,
                )

            # Step 4: Always record the attempt (OpenAI charges even for failures)
            try:
                await record_email_extraction_attempt(
                    user_id,
                    success=extraction_success,
                    metadata={
                        "email_labels": list(labeled_emails.keys()),
                        "validation_warnings": len(validation_result.get("warnings", [])),
                        "extraction_error": extraction_error if not extraction_success else None,
                    },
                )
            except Exception as record_error:
                logger.error(
                    "Failed to record extraction attempt", user_id=user_id, error=str(record_error)
                )
                # Don't fail the whole operation if recording fails

            # Step 5: Handle extraction result
            if extraction_success:
                # Store all 3 profiles
                storage_success = await self.store_user_email_style(
                    user_id, extraction_result, extraction_grades
                )

                if storage_success:
                    return {
                        "success": True,
                        "style_profiles": extraction_result,  # All 3 profiles
                        "extraction_grades": extraction_grades,  # Grades per profile
                        "message": f"3 email styles created successfully! Grades: {extraction_grades}",
                    }
                else:
                    return {
                        "success": False,
                        "error": "storage_failed",
                        "message": "Style extraction succeeded but storage failed. Please try again.",
                    }
            else:
                return {
                    "success": False,
                    "error": "extraction_failed",
                    "message": f"Custom style extraction failed: {extraction_error}. You can try again with different email examples.",
                }

        except RateLimitExceeded:
            raise  # Re-raise rate limit exceptions
        except InvalidEmailExamples:
            raise  # Re-raise validation exceptions
        except Exception as e:
            logger.error(
                "Unexpected error creating 3-profile custom style",
                user_id=user_id,
                error=str(e),
                error_type=type(e).__name__,
            )
            raise EmailStyleError(
                f"3-profile style creation failed: {e}", user_id=user_id
            ) from e

    def _get_current_timestamp(self) -> str:
        """Get current UTC timestamp as ISO string."""
        from datetime import UTC, datetime

        return datetime.now(UTC).isoformat()

    async def health_check(self) -> dict[str, Any]:
        """
        Health check for email style service.

        Returns:
            dict: Health status
        """
        try:
            return {
                "healthy": True,
                "service": "email_style_service",
                "mode": "3-profile",
                "supported_styles": ["professional", "casual", "friendly"],
                "timestamp": self._get_current_timestamp(),
            }

        except Exception as e:
            logger.error("Email style service health check failed", error=str(e))
            return {"healthy": False, "service": "email_style_service", "error": str(e)}


# Singleton instance for application use
email_style_service = EmailStyleService()


# Convenience functions for easy import
async def validate_custom_email_examples(labeled_emails: dict[str, str]) -> dict[str, Any]:
    """Validate labeled email examples for 3-profile extraction."""
    return await email_style_service.validate_email_examples(labeled_emails)


async def store_email_style_selection(
    user_id: str, style_profiles: dict[str, Any], extraction_grades: dict[str, str] | None = None
) -> bool:
    """Store user's 3 email style profiles."""
    return await email_style_service.store_user_email_style(
        user_id, style_profiles, extraction_grades
    )


async def get_user_email_style(user_id: str) -> dict[str, Any] | None:
    """Get user's current email style preferences (all 3 profiles)."""
    return await email_style_service.get_user_email_style_preferences(user_id)


async def get_email_style_selection_options(user_id: str) -> dict[str, Any]:
    """Get 3-profile creation status for user."""
    return await email_style_service.get_email_style_options(user_id)


async def create_custom_email_style(
    user_id: str, labeled_emails: dict[str, str]
) -> dict[str, Any]:
    """Create 3 custom email styles with rate limiting."""
    return await email_style_service.create_custom_style_with_rate_limiting(
        user_id, labeled_emails
    )


async def email_style_service_health() -> dict[str, Any]:
    """Check email style service health."""
    return await email_style_service.health_check()