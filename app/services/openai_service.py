# app/services/openai_service.py
# app/services/openai_service.py
"""
OpenAI Service for Email Style Extraction
Handles OpenAI API integration for 3-profile email style analysis using GPT-4.
"""

import asyncio
import json
from typing import Any

import openai
from openai import AsyncOpenAI

from app.config import settings
from app.infrastructure.observability.logging import get_logger

logger = get_logger(__name__)


class OpenAIExtractionError(Exception):
    """Raised when OpenAI style extraction fails."""

    def __init__(self, message: str, api_error: str | None = None, recoverable: bool = True):
        super().__init__(message)
        self.api_error = api_error
        self.recoverable = recoverable


class OpenAIServiceError(Exception):
    """Base exception for OpenAI service errors."""

    def __init__(self, message: str, recoverable: bool = True):
        super().__init__(message)
        self.recoverable = recoverable


class OpenAIService:
    """
    Service for OpenAI API integration focused on 3-profile email style extraction.

    Uses GPT-4 to analyze 3 labeled emails and extract distinct writing patterns.
    """

    def __init__(self):
        self.client = None
        self._initialize_client()
        logger.info("OpenAI service initialized for 3-profile extraction")

    def _initialize_client(self):
        """Initialize OpenAI async client with configuration."""
        try:
            if not settings.OPENAI_API_KEY:
                raise OpenAIServiceError("OPENAI_API_KEY not configured in settings")

            self.client = AsyncOpenAI(
                api_key=settings.OPENAI_API_KEY,
                timeout=getattr(settings, "EMAIL_STYLE_TIMEOUT_SECONDS", 30),
            )

            logger.info(
                "OpenAI client initialized",
                model=settings.OPENAI_MODEL,
                timeout=getattr(settings, "EMAIL_STYLE_TIMEOUT_SECONDS", 30),
            )

        except Exception as e:
            logger.error("Failed to initialize OpenAI client", error=str(e))
            raise OpenAIServiceError(f"OpenAI client initialization failed: {e}") from e

    def _get_system_message(self) -> str:
        """Get the complete system message for 3-profile extraction."""
        return """### Role
You are a style-extraction engine. Analyze 3 labeled emails (professional, casual, friendly) and produce 3 distinct JSON profiles of writing styles.

### Output Requirements
- Return ONLY valid JSON (no backticks, no prose, no markdown formatting)
- Output structure: {"professional": {...}, "casual": {...}, "friendly": {...}}
- Each profile must be distinct with different tone characteristics
- Base every value on patterns present in the provided emails only

### JSON Schema (for EACH profile)
{
  "professional": {
    "greeting": {
      "style": "string (pattern like 'Hi [name]!')",
      "warmth": "formal|professional|casual|friendly"
    },
    "closing": {
      "styles": ["array of closing phrases"],
      "includes_name": true|false
    },
    "subject_style": {
      "reply_behavior": "uses_re_prefix|custom_descriptive|mixed",
      "new_email_style": "descriptive|direct|creative|professional",
      "tone": "casual|professional|direct",
      "length": "short|medium|long",
      "uses_action_words": true|false,
      "capitalization": "sentence_case|title_case|all_caps"
    },
    "tone": {
      "formality": 1-5,
      "directness": 1-5,
      "enthusiasm": 1-5,
      "politeness": 1-5
    },
    "writing_style": {
      "sentence_length": "short|medium|long|mixed",
      "paragraph_style": "single_line|short_paragraphs|long_paragraphs",
      "punctuation": "minimal|standard|heavy|exclamation_heavy",
      "capitalization": "standard|casual|emphasis_caps"
    },
    "vocabulary": {
      "complexity": "simple|professional|technical|academic",
      "common_phrases": ["array of phrases"],
      "filler_words": ["array of filler words"],
      "transition_words": ["array of transition words"]
    },
    "personal_touches": {
      "uses_emojis": true|false,
      "shares_context": true|false,
      "asks_questions": true|false,
      "uses_humor": true|false
    }
  },
  "casual": { /* same structure */ },
  "friendly": { /* same structure */ }
}

### Extraction Rules (apply to each profile separately)

**greeting.style** → Extract pattern like "Hello [name]," not actual names
- ✅ Correct: "Hello [name],"
- ❌ Wrong: "Hello Mr. Thompson,"

**closing.styles** → Extract exact closings with punctuation
- Extract as array: ["Best regards,", "Sincerely,"]
- Preserve exact punctuation

**subject_style.reply_behavior** → "uses_re_prefix" if starts with "Re:", else "custom_descriptive"
- Check if subject line starts with "Re:"
- If yes → "uses_re_prefix"
- If no → "custom_descriptive"

**transition_words** → Only sentence-starting connectors (However, But, So, etc.) - NOT greetings
- ✅ Include: "However", "But", "So", "Therefore", "Although"
- ❌ Exclude: "Hope" (greeting), standalone words

**tone scores** → 1=low, 5=high (formality, directness, enthusiasm, politeness)
- formality: 1=very casual, 5=very formal
- directness: 1=indirect, 5=very direct
- enthusiasm: 1=neutral, 5=very enthusiastic
- politeness: 1=blunt, 5=very polite

**sentence_length** → short <8 words, medium 8-15, long >15

**CRITICAL: Ensure distinct profiles**
- Professional should have HIGH formality (4-5)
- Casual should have LOW formality (1-2)
- Friendly should have HIGH enthusiasm (4-5)
- Each profile must feel different from the others

### Common Phrases Rule
- For single emails: common_phrases can be empty [] if no notable patterns
- Only include phrases that appear in the specific email being analyzed
"""

    def _build_user_message(self, labeled_emails: dict[str, str]) -> str:
        """Build the user message with labeled email data."""
        
        user_message = f"""### Data

PROFESSIONAL EMAIL:
{labeled_emails.get('professional', '').strip()}

---

CASUAL EMAIL:
{labeled_emails.get('casual', '').strip()}

---

FRIENDLY EMAIL:
{labeled_emails.get('friendly', '').strip()}"""
        
        return user_message

    async def extract_email_style(self, labeled_emails: dict[str, str]) -> dict[str, Any]:
        """
        Extract 3 distinct email writing styles from labeled examples using GPT-4.
        
        Args:
            labeled_emails: {"professional": "...", "casual": "...", "friendly": "..."}
        
        Returns:
            {
                "style_profiles": {
                    "professional": {...},
                    "casual": {...},
                    "friendly": {...}
                },
                "extraction_grades": {
                    "professional": "A",
                    "casual": "B",
                    "friendly": "A"
                },
                "metadata": {
                    "email_labels": ["professional", "casual", "friendly"],
                    "model_used": "gpt-4-turbo",
                    "extraction_timestamp": "2025-10-08T..."
                }
            }
        
        Raises:
            OpenAIExtractionError: If extraction fails or returns invalid data
        """
        if not self.client:
            raise OpenAIServiceError("OpenAI client not initialized")

        # Validate all 3 labeled emails exist
        required_labels = ["professional", "casual", "friendly"]
        for label in required_labels:
            if label not in labeled_emails or not labeled_emails[label].strip():
                raise OpenAIExtractionError(f"Missing or empty {label} email")

        try:
            # Build messages
            system_message = self._get_system_message()
            user_message = self._build_user_message(labeled_emails)

            logger.info(
                "Starting 3-profile email style extraction",
                email_labels=list(labeled_emails.keys()),
                model=settings.OPENAI_MODEL,
                max_tokens=settings.OPENAI_MAX_TOKENS,
            )

            # Call OpenAI API with retry logic
            extraction_result = await self._call_openai_with_retry(system_message, user_message)

            # Parse and validate the 3-profile result
            all_profiles = self._parse_extraction_result(extraction_result)

            # Grade each profile separately
            grades = self._grade_extraction_quality(all_profiles, labeled_emails)

            logger.info(
                "3-profile extraction completed successfully",
                grades=grades,
                profiles_extracted=list(all_profiles.keys()),
            )

            return {
                "style_profiles": all_profiles,  # All 3 profiles
                "extraction_grades": grades,  # Grade per profile
                "metadata": {
                    "email_labels": list(labeled_emails.keys()),
                    "model_used": settings.OPENAI_MODEL,
                    "extraction_timestamp": self._get_current_timestamp(),
                },
            }

        except OpenAIExtractionError:
            raise  # Re-raise extraction errors
        except Exception as e:
            logger.error(
                "Unexpected error during 3-profile extraction",
                error=str(e),
                error_type=type(e).__name__,
            )
            raise OpenAIExtractionError(f"3-profile extraction failed: {e}") from e

    async def _call_openai_with_retry(self, system_message: str, user_message: str) -> str:
        """Call OpenAI API with retry logic for transient failures."""

        last_error = None
        max_retries = getattr(settings, "EMAIL_STYLE_MAX_RETRIES", 3)

        for attempt in range(max_retries):
            try:
                logger.debug(
                    "Calling OpenAI API for 3-profile extraction",
                    attempt=attempt + 1,
                    max_retries=max_retries,
                    model=settings.OPENAI_MODEL,
                )

                response = await self.client.chat.completions.create(
                    model=settings.OPENAI_MODEL,
                    messages=[
                        {"role": "system", "content": system_message},
                        {"role": "user", "content": user_message},
                    ],
                    max_tokens=settings.OPENAI_MAX_TOKENS,
                    temperature=settings.OPENAI_TEMPERATURE,
                    response_format={"type": "json_object"},  # Ensure JSON response
                )

                if not response.choices or not response.choices[0].message.content:
                    raise OpenAIExtractionError("Empty response from OpenAI API")

                result = response.choices[0].message.content.strip()

                logger.info(
                    "OpenAI API call successful",
                    attempt=attempt + 1,
                    response_length=len(result),
                    usage_tokens=response.usage.total_tokens if response.usage else 0,
                )

                return result

            except openai.RateLimitError as e:
                last_error = e
                wait_time = min(2**attempt, 30)  # Exponential backoff, max 30s

                logger.warning(
                    "OpenAI rate limit hit, retrying",
                    attempt=attempt + 1,
                    wait_time=wait_time,
                    error=str(e),
                )

                if attempt < max_retries - 1:
                    await asyncio.sleep(wait_time)

            except openai.APITimeoutError as e:
                last_error = e
                logger.warning(
                    "OpenAI API timeout, retrying",
                    attempt=attempt + 1,
                    timeout=getattr(settings, "EMAIL_STYLE_TIMEOUT_SECONDS", 30),
                    error=str(e),
                )

            except openai.APIError as e:
                last_error = e
                # Don't retry on client errors (4xx)
                if hasattr(e, "status_code") and 400 <= e.status_code < 500:
                    logger.error("OpenAI client error (not retrying)", error=str(e))
                    break

                logger.warning("OpenAI API error, retrying", attempt=attempt + 1, error=str(e))

            except Exception as e:
                last_error = e
                logger.warning(
                    "Unexpected error calling OpenAI, retrying",
                    attempt=attempt + 1,
                    error=str(e),
                    error_type=type(e).__name__,
                )

        # All retries failed
        logger.error(
            "OpenAI API call failed after all retries",
            max_retries=max_retries,
            final_error=str(last_error),
        )

        raise OpenAIExtractionError(
            f"OpenAI API failed after {max_retries} attempts",
            api_error=str(last_error),
            recoverable=True,
        ) from last_error

    def _parse_extraction_result(self, raw_result: str) -> dict[str, Any]:
        """Parse and validate 3-profile OpenAI extraction result."""
        try:
            # Parse JSON
            result = json.loads(raw_result)

            # Validate top-level structure - must have all 3 profile types
            required_profile_types = ["professional", "casual", "friendly"]
            for profile_type in required_profile_types:
                if profile_type not in result:
                    raise OpenAIExtractionError(f"Missing {profile_type} profile in extraction")

            # Validate each profile's internal structure
            required_keys = [
                "greeting",
                "closing",
                "subject_style",
                "tone",
                "writing_style",
                "vocabulary",
                "personal_touches",
            ]

            for profile_type in required_profile_types:
                profile = result[profile_type]

                for key in required_keys:
                    if key not in profile:
                        logger.warning(f"Missing key '{key}' in {profile_type} profile")
                        profile[key] = self._get_default_style_section(key)

                # Validate tone values for each profile
                if "tone" in profile and isinstance(profile["tone"], dict):
                    for tone_key, value in profile["tone"].items():
                        if not isinstance(value, (int, float)) or not 1 <= value <= 5:
                            logger.warning(
                                f"Invalid tone value in {profile_type}.{tone_key}: {value}"
                            )
                            profile["tone"][tone_key] = 3  # Default to middle value

            logger.debug("3-profile extraction parsed and validated successfully")
            return result

        except json.JSONDecodeError as e:
            logger.error(
                "Failed to parse OpenAI response as JSON", error=str(e), raw_result=raw_result[:200]
            )
            raise OpenAIExtractionError("OpenAI returned invalid JSON") from e
        except Exception as e:
            logger.error("Error parsing 3-profile extraction result", error=str(e))
            raise OpenAIExtractionError(f"Failed to parse extraction result: {e}") from e

    def _get_default_style_section(self, section_key: str) -> dict[str, Any]:
        """Get default values for missing style sections."""
        defaults = {
            "greeting": {"style": "Hi [name],", "warmth": "professional"},
            "closing": {"styles": ["Best regards,"], "includes_name": True},
            "subject_style": {
                "reply_behavior": "uses_re_prefix",
                "new_email_style": "descriptive",
                "tone": "professional",
                "length": "medium",
                "uses_action_words": False,
                "capitalization": "sentence_case",
            },
            "tone": {"formality": 3, "directness": 3, "enthusiasm": 3, "politeness": 3},
            "writing_style": {
                "sentence_length": "medium",
                "paragraph_style": "short_paragraphs",
                "punctuation": "standard",
                "capitalization": "standard",
            },
            "vocabulary": {
                "complexity": "professional",
                "common_phrases": [],
                "filler_words": [],
                "transition_words": ["however", "therefore"],
            },
            "personal_touches": {
                "uses_emojis": False,
                "shares_context": False,
                "asks_questions": False,
                "uses_humor": False,
            },
        }
        return defaults.get(section_key, {})

    def _grade_extraction_quality(
        self, all_profiles: dict[str, Any], labeled_emails: dict[str, str]
    ) -> dict[str, str]:
        """
        Grade the quality of each profile extraction (A/B/C).
        
        Returns:
            {"professional": "A", "casual": "B", "friendly": "A"}
        """
        grades = {}

        for profile_type in ["professional", "casual", "friendly"]:
            profile = all_profiles.get(profile_type, {})

            score = 0
            max_score = 10

            # Check completeness (5 points)
            required_sections = ["greeting", "closing", "tone", "writing_style", "vocabulary"]
            complete_sections = sum(
                1 for section in required_sections if section in profile and profile[section]
            )
            score += (complete_sections / len(required_sections)) * 5

            # Check tone values are reasonable (2 points)
            if "tone" in profile and isinstance(profile["tone"], dict):
                tone_values = profile["tone"]
                valid_tone_count = sum(
                    1
                    for v in tone_values.values()
                    if isinstance(v, (int, float)) and 1 <= v <= 5
                )
                score += (valid_tone_count / 4) * 2  # 4 tone categories

            # Check vocabulary has patterns (2 points)
            if "vocabulary" in profile:
                vocab = profile["vocabulary"]
                has_patterns = (vocab.get("common_phrases") and len(vocab["common_phrases"]) > 0) or (
                    vocab.get("filler_words") and len(vocab["filler_words"]) > 0
                )
                score += 2 if has_patterns else 0

            # Check greeting/closing patterns (1 point)
            has_greeting_pattern = (
                "greeting" in profile
                and profile["greeting"].get("style")
                and "[name]" in profile["greeting"]["style"]
            )
            has_closing_patterns = (
                "closing" in profile
                and profile["closing"].get("styles")
                and len(profile["closing"]["styles"]) > 0
            )
            score += 0.5 if has_greeting_pattern else 0
            score += 0.5 if has_closing_patterns else 0

            # Convert score to letter grade
            percentage = (score / max_score) * 100

            if percentage >= 85:
                grade = "A"
            elif percentage >= 70:
                grade = "B"
            else:
                grade = "C"

            grades[profile_type] = grade

            logger.debug(
                f"Extraction quality graded for {profile_type}",
                score=score,
                max_score=max_score,
                percentage=round(percentage, 1),
                grade=grade,
            )

        return grades

    def _get_current_timestamp(self) -> str:
        """Get current UTC timestamp as ISO string."""
        from datetime import UTC, datetime

        return datetime.now(UTC).isoformat()

    async def health_check(self) -> dict[str, Any]:
        """
        Health check for OpenAI service.

        Returns:
            dict: Health status and configuration
        """
        try:
            health_data = {
                "healthy": True,
                "service": "openai_service",
                "client_initialized": self.client is not None,
                "extraction_mode": "3-profile",
                "configuration": {
                    "model": settings.OPENAI_MODEL,
                    "max_tokens": settings.OPENAI_MAX_TOKENS,
                    "temperature": settings.OPENAI_TEMPERATURE,
                    "timeout_seconds": getattr(settings, "EMAIL_STYLE_TIMEOUT_SECONDS", 30),
                    "max_retries": getattr(settings, "EMAIL_STYLE_MAX_RETRIES", 3),
                },
            }

            # Test API connectivity with minimal request
            if self.client:
                try:
                    # Simple test to verify API key and connectivity
                    test_response = await asyncio.wait_for(
                        self.client.chat.completions.create(
                            model=settings.OPENAI_MODEL,
                            messages=[{"role": "user", "content": "Test"}],
                            max_tokens=1,
                        ),
                        timeout=5,  # Quick test
                    )

                    health_data["api_connectivity"] = "ok"
                    health_data["test_response_id"] = test_response.id if test_response else None

                except Exception as api_error:
                    health_data["healthy"] = False
                    health_data["api_connectivity"] = "error"
                    health_data["api_error"] = str(api_error)
            else:
                health_data["healthy"] = False
                health_data["api_connectivity"] = "client_not_initialized"

            return health_data

        except Exception as e:
            logger.error("OpenAI service health check failed", error=str(e))
            return {"healthy": False, "service": "openai_service", "error": str(e)}


# Singleton instance for application use
openai_service = OpenAIService()


# Convenience functions for easy import
async def extract_custom_email_style(labeled_emails: dict[str, str]) -> dict[str, Any]:
    """
    Extract 3 custom email styles from labeled examples using OpenAI.
    
    Args:
        labeled_emails: {"professional": "...", "casual": "...", "friendly": "..."}
    
    Returns:
        {
            "style_profiles": {"professional": {...}, "casual": {...}, "friendly": {...}},
            "extraction_grades": {"professional": "A", "casual": "B", "friendly": "A"},
            "metadata": {...}
        }
    """
    return await openai_service.extract_email_style(labeled_emails)


async def openai_service_health() -> dict[str, Any]:
    """Check OpenAI service health."""
    return await openai_service.health_check()