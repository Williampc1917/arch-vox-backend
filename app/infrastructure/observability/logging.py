"""
Structured logging setup for the voice Gmail assistant.
Provides JSON-formatted logs with consistent fields for production monitoring.
"""

import logging
import sys
from typing import Any

import structlog
from structlog.stdlib import LoggerFactory


def setup_logging(log_level: str = "INFO") -> None:
    """
    Configure structured logging with JSON output for production.

    Args:
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
    """

    # Configure structlog
    structlog.configure(
        processors=[
            # Add timestamp
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            structlog.processors.TimeStamper(fmt="iso"),
            # Add trace context if available
            _add_trace_context,
            # JSON formatting for production
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=LoggerFactory(),
        context_class=dict,
        cache_logger_on_first_use=True,
    )

    # Configure stdlib logging
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, log_level.upper()),
    )

    # Suppress noisy third-party loggers
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


def _add_trace_context(logger, method_name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    """Add trace context to log entries if available."""
    # For now, just return the event dict as-is
    # Later we can add trace_id, user_id, session_id from FastAPI context
    return event_dict


def get_logger(name: str = None) -> structlog.stdlib.BoundLogger:
    """
    Get a structured logger instance.

    Args:
        name: Logger name (usually __name__)

    Returns:
        Configured structlog logger
    """
    return structlog.get_logger(name)


# Convenience functions for common log patterns
def log_health_check(service: str, healthy: bool, latency_ms: float, error: str = None):
    """Log health check results with consistent fields."""
    logger = get_logger("health")

    log_data = {
        "service": service,
        "healthy": healthy,
        "latency_ms": latency_ms,
        "event": "health_check",
    }

    if error:
        log_data["error"] = error

    if healthy:
        logger.info("Health check passed", **log_data)
    else:
        logger.error("Health check failed", **log_data)


def log_request(method: str, path: str, status_code: int, duration_ms: float, user_id: str = None):
    """Log HTTP requests with consistent fields."""
    logger = get_logger("http")

    log_data = {
        "method": method,
        "path": path,
        "status_code": status_code,
        "duration_ms": duration_ms,
        "event": "http_request",
    }

    if user_id:
        log_data["user_id"] = user_id

    if status_code >= 400:
        logger.warning("HTTP request failed", **log_data)
    else:
        logger.info("HTTP request completed", **log_data)
