"""
Legacy database connection check function.
REFACTORED: Now uses database connection pool instead of direct psycopg connections.
"""

from app.db.helpers import fetch_one
from app.infrastructure.observability.logging import get_logger

logger = get_logger(__name__)


async def check_db():
    """
    Returns True if SELECT 1 succeeds, otherwise the error string.
    
    This function now uses the database connection pool for consistency
    with the rest of the application.
    """
    try:
        # Use database pool helper function
        row = await fetch_one("SELECT 1")
        
        if row and list(row.values())[0] == 1:
            return True
        else:
            return "Unexpected result from database check"
            
    except Exception as e:
        logger.error("Database health check failed", error=str(e))
        return str(e)
