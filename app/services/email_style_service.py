# app/services/email_style_service.py
"""
Email Style Service
Handles email style management including predefined profiles, validation, and storage.
"""

import re
from typing import Any, Literal

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
    Service for managing email styles including predefined and custom styles.

    Handles validation, storage, and retrieval of email style preferences.
    """

    def __init__(self):
        logger.info("Email style service initialized")

    def get_predefined_style_profile(
        self, style_type: Literal["casual", "professional"]
    ) -> dict[str, Any]:
        """
        Get predefined style profile from documentation.

        Args:
            style_type: Either "casual" or "professional"

        Returns:
            dict: Complete style profile JSON

        Raises:
            EmailStyleError: If invalid style type provided
        """
        try:
            if style_type == "casual":
                return self._get_casual_style_profile()
            elif style_type == "professional":
                return self._get_professional_style_profile()
            else:
                raise EmailStyleError(f"Invalid predefined style type: {style_type}")

        except Exception as e:
            logger.error(
                "Error getting predefined style profile", style_type=style_type, error=str(e)
            )
            raise EmailStyleError(f"Failed to get predefined style: {e}") from e

    def _get_casual_style_profile(self) -> dict[str, Any]:
        """Get casual style profile from documentation."""
        return {
            "greeting": {"style": "Hey [name]!", "warmth": "friendly"},
            "closing": {"styles": ["Thanks!", "Talk soon!", "Cheers!"], "includes_name": False},
            "subject_style": {
                "reply_behavior": "uses_re_prefix",
                "new_email_style": "direct",
                "tone": "casual",
                "length": "short",
                "uses_action_words": False,
                "capitalization": "sentence_case",
            },
            "tone": {"formality": 1, "directness": 5, "enthusiasm": 4, "politeness": 3},
            "writing_style": {
                "sentence_length": "short",
                "paragraph_style": "single_line",
                "punctuation": "exclamation_heavy",
                "capitalization": "casual",
            },
            "vocabulary": {
                "complexity": "simple",
                "common_phrases": ["sounds good", "no worries", "let me know", "thanks a bunch"],
                "filler_words": ["just", "totally", "really"],
                "transition_words": ["so", "but", "anyway"],
            },
            "personal_touches": {
                "uses_emojis": True,
                "shares_context": True,
                "asks_questions": True,
                "uses_humor": True,
            },
        }

    def _get_professional_style_profile(self) -> dict[str, Any]:
        """Get professional style profile from documentation."""
        return {
            "greeting": {"style": "Dear [name],", "warmth": "professional"},
            "closing": {
                "styles": ["Best regards,", "Sincerely,", "Thank you,"],
                "includes_name": True,
            },
            "subject_style": {
                "reply_behavior": "uses_re_prefix",
                "new_email_style": "professional",
                "tone": "professional",
                "length": "medium",
                "uses_action_words": True,
                "capitalization": "title_case",
            },
            "tone": {"formality": 5, "directness": 4, "enthusiasm": 2, "politeness": 5},
            "writing_style": {
                "sentence_length": "long",
                "paragraph_style": "long_paragraphs",
                "punctuation": "standard",
                "capitalization": "standard",
            },
            "vocabulary": {
                "complexity": "professional",
                "common_phrases": [
                    "I hope this email finds you well",
                    "please let me know",
                    "I look forward to",
                    "thank you for your time",
                ],
                "filler_words": [],
                "transition_words": ["however", "additionally", "furthermore", "therefore"],
            },
            "personal_touches": {
                "uses_emojis": False,
                "shares_context": False,
                "asks_questions": False,
                "uses_humor": False,
            },
        }

    async def validate_email_examples(self, email_examples: list[str]) -> dict[str, Any]:
        """
        Validate email examples for custom style extraction.

        Args:
            email_examples: List of 3 email examples (subject + body)

        Returns:
            dict: Validation results with issues found

        Raises:
            InvalidEmailExamples: If examples are not suitable for extraction
        """
        try:
            validation_result = {"valid": True, "issues": [], "warnings": []}

            # Check count
            if len(email_examples) != 3:
                validation_result["valid"] = False
                validation_result["issues"].append(
                    f"Expected 3 email examples, got {len(email_examples)}"
                )

            for i, email in enumerate(email_examples, 1):
                # Check if email is not empty
                if not email or not email.strip():
                    validation_result["valid"] = False
                    validation_result["issues"].append(f"Email {i} is empty")
                    continue

                # Check minimum length (should have subject + substantial body)
                if len(email.strip()) < 50:
                    validation_result["valid"] = False
                    validation_result["issues"].append(
                        f"Email {i} too short (minimum 50 characters)"
                    )

                # Check if it has both subject and body structure
                if not self._has_email_structure(email):
                    validation_result["warnings"].append(
                        f"Email {i} may be missing subject or proper structure"
                    )

                # Check for suspicious content
                if self._has_suspicious_content(email):
                    validation_result["valid"] = False
                    validation_result["issues"].append(
                        f"Email {i} contains suspicious or inappropriate content"
                    )

            # Final validation
            if not validation_result["valid"]:
                issues_text = "; ".join(validation_result["issues"])
                raise InvalidEmailExamples(f"Email examples validation failed: {issues_text}")

            logger.info(
                "Email examples validated successfully",
                example_count=len(email_examples),
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
        style_type: Literal["casual", "professional", "custom"],
        style_profile: dict[str, Any],
    ) -> bool:
        """
        Store user's email style preferences in database.

        Args:
            user_id: UUID string of the user
            style_type: Type of style selected
            style_profile: Complete style profile data

        Returns:
            bool: True if storage successful

        Raises:
            EmailStyleError: If storage fails
        """
        try:
            # Prepare preferences structure
            preferences = {
                "style_type": style_type,
                "style_profile": style_profile,
                "created_at": self._get_current_timestamp(),
                "version": "1.0",  # For future migrations
            }

            # Store in database
            success = await store_email_style_preferences(user_id, preferences)

            if not success:
                raise EmailStyleError("Database storage failed", user_id=user_id)

            logger.info(
                "Email style stored successfully",
                user_id=user_id,
                style_type=style_type,
                has_custom_profile=style_type == "custom",
            )

            return True

        except Exception as e:
            logger.error(
                "Error storing email style", user_id=user_id, style_type=style_type, error=str(e)
            )
            raise EmailStyleError(f"Failed to store email style: {e}", user_id=user_id) from e

    async def get_user_email_style_preferences(self, user_id: str) -> dict[str, Any] | None:
        """
        Get user's current email style preferences.

        Args:
            user_id: UUID string of the user

        Returns:
            dict: Email style preferences or None if not found
        """
        try:
            preferences = await get_email_style_preferences(user_id)

            if preferences:
                logger.debug(
                    "Email style preferences retrieved",
                    user_id=user_id,
                    style_type=preferences.get("style_type"),
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
        Get available email style options and current selection for user.

        Args:
            user_id: UUID string of the user

        Returns:
            dict: Available options and current selection
        """
        try:
            # Get current preferences
            current_preferences = await self.get_user_email_style_preferences(user_id)

            # Get rate limit status for custom option
            rate_limit_status = None
            try:
                from app.services.email_style_rate_limiter import get_email_extraction_status

                rate_limit_status = await get_email_extraction_status(user_id)
            except Exception as e:
                logger.warning("Could not get rate limit status", user_id=user_id, error=str(e))

            # Build available options
            options = {
                "casual": {
                    "name": "Casual",
                    "description": "Friendly, informal communication style",
                    "example": {
                        "greeting": "Hey [name]!",
                        "closing": "Thanks!",
                        "tone": "Friendly and direct",
                    },
                    "available": True,
                },
                "professional": {
                    "name": "Professional",
                    "description": "Formal, business-appropriate communication style",
                    "example": {
                        "greeting": "Dear [name],",
                        "closing": "Best regards,",
                        "tone": "Formal and polite",
                    },
                    "available": True,
                },
                "custom": {
                    "name": "Custom",
                    "description": "Personalized style learned from your email examples",
                    "example": {
                        "greeting": "Based on your writing style",
                        "closing": "Matches your preferences",
                        "tone": "Uniquely yours",
                    },
                    "available": rate_limit_status and rate_limit_status.get("can_extract", False),
                    "rate_limit_info": rate_limit_status,
                },
            }

            result = {
                "available_options": options,
                "current_selection": {
                    "style_type": (
                        current_preferences.get("style_type") if current_preferences else None
                    ),
                    "created_at": (
                        current_preferences.get("created_at") if current_preferences else None
                    ),
                },
                "has_selection": current_preferences is not None
                and current_preferences.get("style_type") is not None,
                "can_advance": current_preferences is not None
                and current_preferences.get("style_type") is not None,
            }

            logger.info(
                "Email style options retrieved",
                user_id=user_id,
                current_style=result["current_selection"]["style_type"],
                can_advance=result["can_advance"],
            )

            return result

        except Exception as e:
            logger.error("Error getting email style options", user_id=user_id, error=str(e))
            raise EmailStyleError(f"Failed to get email style options: {e}", user_id=user_id) from e

    async def select_predefined_style(
        self, user_id: str, style_type: Literal["casual", "professional"]
    ) -> dict[str, Any]:
        """
        Select a predefined email style (casual or professional).

        Args:
            user_id: UUID string of the user
            style_type: Either "casual" or "professional"

        Returns:
            dict: Selection result with style profile

        Raises:
            EmailStyleError: If selection fails
        """
        try:
            # Get predefined style profile
            style_profile = self.get_predefined_style_profile(style_type)

            # Store user's selection
            success = await self.store_user_email_style(user_id, style_type, style_profile)

            if not success:
                raise EmailStyleError(
                    f"Failed to store {style_type} style selection", user_id=user_id
                )

            logger.info("Predefined email style selected", user_id=user_id, style_type=style_type)

            return {
                "success": True,
                "style_type": style_type,
                "style_profile": style_profile,
                "message": f"{style_type.title()} email style selected successfully!",
            }

        except Exception as e:
            logger.error(
                "Error selecting predefined style",
                user_id=user_id,
                style_type=style_type,
                error=str(e),
            )
            raise EmailStyleError(
                f"Failed to select {style_type} style: {e}", user_id=user_id
            ) from e

    async def create_custom_style_with_rate_limiting(
        self, user_id: str, email_examples: list[str]
    ) -> dict[str, Any]:
        """
        Create custom email style with rate limiting and OpenAI integration.
        This is the main entry point for custom style creation.

        Args:
            user_id: UUID string of the user
            email_examples: List of 3 email examples

        Returns:
            dict: Custom style creation result

        Raises:
            RateLimitExceeded: If user has exceeded daily limit
            InvalidEmailExamples: If examples are invalid
            EmailStyleError: If creation fails
        """
        try:
            # Step 1: Validate email examples
            validation_result = await self.validate_email_examples(email_examples)

            # Step 2: Check rate limiting
            try:
                rate_limit_check = await check_email_extraction_limit(user_id)
                logger.info(
                    "Rate limit check passed for custom style creation",
                    user_id=user_id,
                    remaining=rate_limit_check.get("remaining"),
                    daily_limit=rate_limit_check.get("daily_limit"),
                )
            except RateLimitExceeded as e:
                # Generate user-friendly error message
                error_message = get_rate_limit_error_message(e.used, e.limit, e.reset_time)

                logger.warning(
                    "Custom style creation blocked by rate limit",
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

            # Step 3: Extract custom style (this will be implemented when we add OpenAI)
            # Step 3: Extract custom style using OpenAI
            try:
                from app.services.openai_service import extract_custom_email_style
                
                openai_result = await extract_custom_email_style(email_examples)
                
                extraction_success = True
                extraction_result = openai_result["style_profile"]
                extraction_grade = openai_result["extraction_grade"]
                extraction_error = None
                
                logger.info(
                    "OpenAI extraction completed successfully",
                    user_id=user_id,
                    extraction_grade=extraction_grade,
                    style_profile_keys=list(extraction_result.keys()) if extraction_result else []
                )
                
            except Exception as openai_error:
                extraction_success = False
                extraction_result = {}
                extraction_grade = "C"
                extraction_error = f"OpenAI extraction failed: {str(openai_error)}"
                
                logger.error(
                    "OpenAI extraction failed",
                    user_id=user_id,
                    error=str(openai_error),
                    error_type=type(openai_error).__name__
                )

            # Step 4: Always record the attempt (OpenAI charges even for failures)
            try:
                await record_email_extraction_attempt(
                    user_id,
                    success=extraction_success,
                    metadata={
                        "email_count": len(email_examples),
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
                # Store the custom style
                storage_success = await self.store_user_email_style(
                    user_id, "custom", extraction_result
                )

                if storage_success:
                    # TO THIS:
                    return {
                        "success": True,
                        "style_type": "custom", 
                        "style_profile": extraction_result,
                        "extraction_grade": extraction_grade,  # ✅ Use actual grade from OpenAI
                        "message": f"Custom email style created successfully! Quality grade: {extraction_grade}",
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
                "Unexpected error creating custom style",
                user_id=user_id,
                error=str(e),
                error_type=type(e).__name__,
            )
            raise EmailStyleError(f"Custom style creation failed: {e}", user_id=user_id) from e

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
            # Test predefined styles
            casual_profile = self.get_predefined_style_profile("casual")
            professional_profile = self.get_predefined_style_profile("professional")

            predefined_styles_ok = (
                isinstance(casual_profile, dict)
                and isinstance(professional_profile, dict)
                and "greeting" in casual_profile
                and "greeting" in professional_profile
            )

            return {
                "healthy": predefined_styles_ok,
                "service": "email_style_service",
                "predefined_styles": predefined_styles_ok,
                "casual_style_keys": list(casual_profile.keys()) if predefined_styles_ok else [],
                "professional_style_keys": (
                    list(professional_profile.keys()) if predefined_styles_ok else []
                ),
                "timestamp": self._get_current_timestamp(),
            }

        except Exception as e:
            logger.error("Email style service health check failed", error=str(e))
            return {"healthy": False, "service": "email_style_service", "error": str(e)}


# Singleton instance for application use
email_style_service = EmailStyleService()


# Convenience functions for easy import
def get_predefined_email_style(style_type: Literal["casual", "professional"]) -> dict[str, Any]:
    """Get predefined email style profile."""
    return email_style_service.get_predefined_style_profile(style_type)


async def validate_custom_email_examples(email_examples: list[str]) -> dict[str, Any]:
    """Validate email examples for custom style extraction."""
    return await email_style_service.validate_email_examples(email_examples)


async def store_email_style_selection(
    user_id: str,
    style_type: Literal["casual", "professional", "custom"],
    style_profile: dict[str, Any],
) -> bool:
    """Store user's email style selection."""
    return await email_style_service.store_user_email_style(user_id, style_type, style_profile)


async def get_user_email_style(user_id: str) -> dict[str, Any] | None:
    """Get user's current email style preferences."""
    return await email_style_service.get_user_email_style_preferences(user_id)


async def get_email_style_selection_options(user_id: str) -> dict[str, Any]:
    """Get available email style options for user."""
    return await email_style_service.get_email_style_options(user_id)


async def select_predefined_email_style(
    user_id: str, style_type: Literal["casual", "professional"]
) -> dict[str, Any]:
    """Select casual or professional email style."""
    return await email_style_service.select_predefined_style(user_id, style_type)


async def create_custom_email_style(user_id: str, email_examples: list[str]) -> dict[str, Any]:
    """Create custom email style with rate limiting."""
    return await email_style_service.create_custom_style_with_rate_limiting(user_id, email_examples)


async def email_style_service_health() -> dict[str, Any]:
    """Check email style service health."""
    return await email_style_service.health_check()
