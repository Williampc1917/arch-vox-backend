import psycopg

from app.config import settings


def check_db():
    """
    Returns True if SELECT 1 succeeds, otherwise the error string.
    """
    try:
        # autocommit so we don't need explicit commit for simple checks
        with psycopg.connect(settings.SUPABASE_DB_URL, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1;")
                cur.fetchone()
                return True
    except Exception as e:
        return str(e)
