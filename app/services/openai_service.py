# app/services/openai_service.py
"""
OpenAI Service for Email Style Extraction
Handles OpenAI API integration for custom email style analysis using GPT-4.
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
    Service for OpenAI API integration focused on email style extraction.

    Uses GPT-4 with your documented prompts to analyze email writing patterns.
    """

    def __init__(self):
        self.client = None
        self._initialize_client()
        logger.info("OpenAI service initialized")

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
        """Get the complete system message with all instructions except data."""
        return """### Role
You are a style-extraction engine. Analyze example emails and produce a structured JSON profile of the writer's communication style.

### Output Requirements
- Return ONLY valid JSON (no backticks, no prose, no markdown formatting).
- Conform exactly to the schema and allowed values below.
- If evidence is insufficient for a field, set it to null (or [] for arrays) rather than guessing.
- Base every value on patterns present in the provided emails only.

### JSON Schema (contract)
{
  "type": "object",
  "properties": {
    "greeting": {
      "type": "object",
      "properties": {
        "style": { "type": ["string","null"], "description": "Pattern, e.g., 'Hi [name]!' or 'Hello [name],'"},
        "warmth": { "type": ["string","null"], "enum": ["formal","professional","casual","friendly", null] }
      },
      "required": ["style","warmth"]
    },
    "closing": {
      "type": "object",
      "properties": {
        "styles": { "type": "array", "items": { "type": "string" }, "uniqueItems": true },
        "includes_name": { "type": ["boolean","null"] }
      },
      "required": ["styles","includes_name"]
    },
    "subject_style": {
      "type": "object",
      "properties": {
        "reply_behavior": { "type": ["string","null"], "enum": ["uses_re_prefix","custom_descriptive","mixed", null] },
        "new_email_style": { "type": ["string","null"], "enum": ["descriptive","direct","creative","professional", null] },
        "tone": { "type": ["string","null"], "enum": ["casual","professional","direct", null] },
        "length": { "type": ["string","null"], "enum": ["short","medium","long", null] },
        "uses_action_words": { "type": ["boolean","null"] },
        "capitalization": { "type": ["string","null"], "enum": ["sentence_case","title_case","all_caps", null] }
      },
      "required": ["reply_behavior","new_email_style","tone","length","uses_action_words","capitalization"]
    },
    "tone": {
      "type": "object",
      "properties": {
        "formality": { "type": ["integer","null"], "minimum": 1, "maximum": 5 },
        "directness": { "type": ["integer","null"], "minimum": 1, "maximum": 5 },
        "enthusiasm": { "type": ["integer","null"], "minimum": 1, "maximum": 5 },
        "politeness": { "type": ["integer","null"], "minimum": 1, "maximum": 5 }
      },
      "required": ["formality","directness","enthusiasm","politeness"]
    },
    "writing_style": {
      "type": "object",
      "properties": {
        "sentence_length": { "type": ["string","null"], "enum": ["short","medium","long","mixed", null] },
        "paragraph_style": { "type": ["string","null"], "enum": ["single_line","short_paragraphs","long_paragraphs", null] },
        "punctuation": { "type": ["string","null"], "enum": ["minimal","standard","heavy","exclamation_heavy", null] },
        "capitalization": { "type": ["string","null"], "enum": ["standard","casual","emphasis_caps", null] }
      },
      "required": ["sentence_length","paragraph_style","punctuation","capitalization"]
    },
    "vocabulary": {
      "type": "object",
      "properties": {
        "complexity": { "type": ["string","null"], "enum": ["simple","professional","technical","academic", null] },
        "common_phrases": { "type": "array", "items": {"type":"string"}, "uniqueItems": true },
        "filler_words": { "type": "array", "items": {"type":"string"}, "uniqueItems": true },
        "transition_words": { "type": "array", "items": {"type":"string"}, "uniqueItems": true }
      },
      "required": ["complexity","common_phrases","filler_words","transition_words"]
    },
    "personal_touches": {
      "type": "object",
      "properties": {
        "uses_emojis": { "type": ["boolean","null"] },
        "shares_context": { "type": ["boolean","null"] },
        "asks_questions": { "type": ["boolean","null"] },
        "uses_humor": { "type": ["boolean","null"] }
      },
      "required": ["uses_emojis","shares_context","asks_questions","uses_humor"]
    }
  },
  "required": ["greeting","closing","subject_style","tone","writing_style","vocabulary","personal_touches"]
}

## Enhanced Extraction Rules (follow strictly)

### Analysis Process (complete in order):
1. **Scan ALL emails first** to identify patterns before categorizing
2. **Count occurrences explicitly** for phrases and transitions
3. **Calculate percentages** for mixed behaviors
4. **Verify against rules** before finalizing

### Core Extraction Rules:

**greeting.style** → Pattern Analysis Method:
1. List each email's greeting format
2. Identify structural commonality (e.g., all use [greeting word] + [name] + punctuation)
3. Extract generalized pattern: "[Greeting] [name]," not specific words or names

**closing.styles** → Include every distinct closing pattern observed (deduplicate, preserve exact punctuation).

**subject_style.reply_behavior** → Count-Based Classification:
- Count emails with "Re:" vs total emails
- If 100% use "Re:" → "uses_re_prefix"
- If 0% use "Re:" → "custom_descriptive"
- If mixed percentage → "mixed"

**vocabulary.common_phrases** → Strict Occurrence Rule:
- MUST appear in ≥2 emails verbatim (case-insensitive)
- If phrase appears only once, exclude it
- When uncertain, use [] rather than guess

**transition_words** → Sentence-Starting Connectors Only:
- Include ONLY: connective/transitional words at sentence beginnings
- Valid examples: "So", "However", "But", "Once", "Although", "Therefore"
- EXCLUDE: greetings ("Hope"), standalone words, non-connective terms
- Preserve original capitalization from sentence starts

**asks_questions** → Literal Rule:
- true ONLY if at least one sentence ends with "?"
- Do NOT infer from indirect requests ("Let me know if...")
- If no literal "?" exists → false (never null)

**writing_style.sentence_length** → Word Count Analysis:
- Count words per sentence across all emails
- "short": mostly <8 words, "medium": mostly 8-15 words, "long": mostly >15 words
- "mixed": significant variation (both <8 AND >15 word sentences present)

**writing_style.paragraph_style** → Sentence Count per Paragraph:
- "single_line": each paragraph = 1 sentence
- "short_paragraphs": 2-4 sentences per paragraph
- "long_paragraphs": 5+ sentences per paragraph

### Verification Checklist (complete before output):
□ Did I analyze ALL emails for patterns, not just the first one?
□ Are common phrases verified to appear ≥2 times exactly?
□ Are transition words actually connective (not greetings/hopes)?
□ Does reply_behavior reflect actual email type distribution?
□ Is greeting pattern generalized across all variations?

### Common Mistakes to Avoid:
- "Hope you're doing well" → NOT a transition word (pleasantry)
- Single occurrence → NOT a common phrase (needs ≥2)
- 1 "Re:" out of 3 emails → "mixed" not "uses_re_prefix"
- Using specific greeting word → extract structural pattern instead

### Analysis Examples (for calibration):

**Pattern Analysis Example:**
- Email 1: "Hello Sarah," Email 2: "Hey Mike," Email 3: "Hi David,"
- Commonality: [greeting word] + [name] + comma
- Extract: "[Greeting] [name]," (generalized pattern)

**Reply Behavior Calculation:**
- Email types: Email1=NEW, Email2=REPLY(Re:), Email3=NEW
- Count: 1 reply / 3 total = 33% use "Re:"
- Result: "mixed" (not 100% either pattern)

**Transition Word Validation:**
- "However, I do have questions" → "However" ✓ (connective)
- "Hope you're well" → "Hope" (greeting, not transitional)

**Closing Name Analysis:**
- "Talk soon, Jordan" → includes name ✓
- "Best regards," → no name
- "Thanks, Alex" → includes name ✓
- Check: Do closings end with sender's name? If ANY include name → true"""

    def _build_user_message(self, email_examples: list[str]) -> str:
        """Build the user message with only the email data."""

        # Format each email with proper labeling
        formatted_emails = []
        for i, email in enumerate(email_examples, 1):
            # Clean up the email content
            cleaned_email = email.strip()
            formatted_emails.append(f"EMAIL {i}:\n{cleaned_email}")

        emails_section = "\n\n".join(formatted_emails)

        # Only the data section as specified
        user_message = f"""### Data
{emails_section}"""

        return user_message

    async def extract_email_style(self, email_examples: list[str]) -> dict[str, Any]:
        """
        Extract email writing style from 3 email examples using GPT-4.

        Args:
            email_examples: List of 3 email examples (subject + body)

        Returns:
            dict: Extracted style profile matching your JSON schema

        Raises:
            OpenAIExtractionError: If extraction fails or returns invalid data
        """
        if not self.client:
            raise OpenAIServiceError("OpenAI client not initialized")

        if len(email_examples) != 3:
            raise OpenAIExtractionError(f"Expected 3 email examples, got {len(email_examples)}")

        try:
            # Build messages using proper separation
            system_message = self._get_system_message()
            user_message = self._build_user_message(email_examples)

            logger.info(
                "Starting email style extraction",
                email_count=len(email_examples),
                model=settings.OPENAI_MODEL,
                max_tokens=settings.OPENAI_MAX_TOKENS,
                system_message_length=len(system_message),
                user_message_length=len(user_message),
            )

            # Call OpenAI API with retry logic
            extraction_result = await self._call_openai_with_retry(system_message, user_message)

            # Validate and grade the extraction result
            style_profile = self._parse_extraction_result(extraction_result)
            grade = self._grade_extraction_quality(style_profile, email_examples)

            logger.info(
                "Email style extraction completed",
                extraction_grade=grade,
                has_greeting=bool(style_profile.get("greeting")),
                has_closing=bool(style_profile.get("closing")),
                has_tone=bool(style_profile.get("tone")),
            )

            return {
                "style_profile": style_profile,
                "extraction_grade": grade,
                "metadata": {
                    "email_count": len(email_examples),
                    "model_used": settings.OPENAI_MODEL,
                    "extraction_timestamp": self._get_current_timestamp(),
                },
            }

        except OpenAIExtractionError:
            raise  # Re-raise extraction errors
        except Exception as e:
            logger.error(
                "Unexpected error during style extraction",
                error=str(e),
                error_type=type(e).__name__,
            )
            raise OpenAIExtractionError(f"Style extraction failed: {e}") from e

    async def _call_openai_with_retry(self, system_message: str, user_message: str) -> str:
        """Call OpenAI API with retry logic for transient failures."""

        last_error = None
        max_retries = getattr(settings, "EMAIL_STYLE_MAX_RETRIES", 3)

        for attempt in range(max_retries):
            try:
                logger.debug(
                    "Calling OpenAI API",
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
        """Parse and validate OpenAI extraction result."""
        try:
            # Parse JSON
            style_profile = json.loads(raw_result)

            # Validate required structure
            required_keys = [
                "greeting",
                "closing",
                "subject_style",
                "tone",
                "writing_style",
                "vocabulary",
                "personal_touches",
            ]

            for key in required_keys:
                if key not in style_profile:
                    logger.warning(f"Missing required key in extraction: {key}")
                    # Add default structure for missing keys
                    style_profile[key] = self._get_default_style_section(key)

            # Validate tone values are numbers
            if "tone" in style_profile and isinstance(style_profile["tone"], dict):
                for tone_key, value in style_profile["tone"].items():
                    if not isinstance(value, (int, float)) or not 1 <= value <= 5:
                        logger.warning(f"Invalid tone value for {tone_key}: {value}")
                        style_profile["tone"][tone_key] = 3  # Default to middle value

            logger.debug("Style profile parsed and validated successfully")
            return style_profile

        except json.JSONDecodeError as e:
            logger.error(
                "Failed to parse OpenAI response as JSON", error=str(e), raw_result=raw_result[:200]
            )
            raise OpenAIExtractionError("OpenAI returned invalid JSON") from e
        except Exception as e:
            logger.error("Error parsing extraction result", error=str(e))
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
        self, style_profile: dict[str, Any], email_examples: list[str]
    ) -> str:
        """Grade the quality of extraction (A/B/C) based on completeness and accuracy."""
        try:
            score = 0
            max_score = 10

            # Check completeness (5 points)
            required_sections = ["greeting", "closing", "tone", "writing_style", "vocabulary"]
            complete_sections = sum(
                1
                for section in required_sections
                if section in style_profile and style_profile[section]
            )
            score += (complete_sections / len(required_sections)) * 5

            # Check tone values are reasonable (2 points)
            if "tone" in style_profile and isinstance(style_profile["tone"], dict):
                tone_values = style_profile["tone"]
                valid_tone_count = sum(
                    1 for v in tone_values.values() if isinstance(v, (int, float)) and 1 <= v <= 5
                )
                score += (valid_tone_count / 4) * 2  # 4 tone categories

            # Check vocabulary has patterns (2 points)
            if "vocabulary" in style_profile:
                vocab = style_profile["vocabulary"]
                has_patterns = (
                    vocab.get("common_phrases") and len(vocab["common_phrases"]) > 0
                ) or (vocab.get("filler_words") and len(vocab["filler_words"]) > 0)
                score += 2 if has_patterns else 0

            # Check greeting/closing patterns (1 point)
            has_greeting_pattern = (
                "greeting" in style_profile
                and style_profile["greeting"].get("style")
                and "[name]" in style_profile["greeting"]["style"]
            )
            has_closing_patterns = (
                "closing" in style_profile
                and style_profile["closing"].get("styles")
                and len(style_profile["closing"]["styles"]) > 0
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

            logger.debug(
                "Extraction quality graded",
                score=score,
                max_score=max_score,
                percentage=round(percentage, 1),
                grade=grade,
            )

            return grade

        except Exception as e:
            logger.warning("Error grading extraction quality", error=str(e))
            return "C"  # Default to lowest grade on error

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
async def extract_custom_email_style(email_examples: list[str]) -> dict[str, Any]:
    """Extract custom email style from examples using OpenAI."""
    return await openai_service.extract_email_style(email_examples)


async def openai_service_health() -> dict[str, Any]:
    """Check OpenAI service health."""
    return await openai_service.health_check()
