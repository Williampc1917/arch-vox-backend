"""
Tests for external service connections.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.db.postgres import check_db
from app.services.infrastructure.redis_client import fast_redis


class TestPostgresConnection:
    """Tests for Postgres database connection."""

    @patch("psycopg.connect")
    def test_check_db_success(self, mock_connect):
        """Test successful database connection."""
        # Mock the connection and cursor
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_connect.return_value.__enter__.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
        mock_cursor.fetchone.return_value = (1,)

        result = check_db()

        assert result is True
        mock_cursor.execute.assert_called_once_with("SELECT 1;")
        mock_cursor.fetchone.assert_called_once()

    @patch("psycopg.connect")
    def test_check_db_connection_failure(self, mock_connect):
        """Test database connection failure."""
        mock_connect.side_effect = Exception("Connection failed")

        result = check_db()

        assert result == "Connection failed"
        assert isinstance(result, str)

    @patch("psycopg.connect")
    def test_check_db_query_failure(self, mock_connect):
        """Test database query failure."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_connect.return_value.__enter__.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
        mock_cursor.execute.side_effect = Exception("Query failed")

        result = check_db()

        assert result == "Query failed"


class TestRedisConnection:
    """Tests for Redis connection via fast Redis client."""

    @pytest.mark.asyncio
    async def test_ping_success(self):
        """Test successful Redis ping."""
        client = AsyncMock()
        client.ping.return_value = True

        with patch.object(fast_redis, "_ensure_initialized", new=AsyncMock()):
            with patch.object(fast_redis, "client", client):
                result = await fast_redis.ping()

        assert result is True
        client.ping.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_ping_exception(self):
        """Test Redis ping with exception."""
        client = AsyncMock()
        client.ping.side_effect = Exception("Network error")

        with patch.object(fast_redis, "_ensure_initialized", new=AsyncMock()):
            with patch.object(fast_redis, "client", client):
                result = await fast_redis.ping()

        assert result is False

    @pytest.mark.asyncio
    async def test_set_with_ttl_success(self):
        """Test Redis SET with TTL."""
        client = AsyncMock()
        client.setex.return_value = True

        with patch.object(fast_redis, "_ensure_initialized", new=AsyncMock()):
            with patch.object(fast_redis, "client", client):
                result = await fast_redis.set_with_ttl("test_key", "test_value", 60)

        assert result is True
        client.setex.assert_awaited_once_with("test_key", 60, "test_value")

    @pytest.mark.asyncio
    async def test_set_without_ttl(self):
        """Test Redis SET without TTL."""
        client = AsyncMock()
        client.set.return_value = True

        with patch.object(fast_redis, "_ensure_initialized", new=AsyncMock()):
            with patch.object(fast_redis, "client", client):
                result = await fast_redis.set_with_ttl("test_key", "test_value")

        assert result is True
        client.set.assert_awaited_once_with("test_key", "test_value")

    @pytest.mark.asyncio
    async def test_get_success(self):
        """Test Redis GET success."""
        client = AsyncMock()
        client.get.return_value = "test_value"

        with patch.object(fast_redis, "_ensure_initialized", new=AsyncMock()):
            with patch.object(fast_redis, "client", client):
                result = await fast_redis.get("test_key")

        assert result == "test_value"
        client.get.assert_awaited_once_with("test_key")

    @pytest.mark.asyncio
    async def test_get_failure(self):
        """Test Redis GET failure."""
        client = AsyncMock()
        client.get.side_effect = Exception("GET failed")

        with patch.object(fast_redis, "_ensure_initialized", new=AsyncMock()):
            with patch.object(fast_redis, "client", client):
                result = await fast_redis.get("test_key")

        assert result is None

    @pytest.mark.asyncio
    async def test_delete_success(self):
        """Test Redis DELETE success."""
        client = AsyncMock()
        client.delete.return_value = 1

        with patch.object(fast_redis, "_ensure_initialized", new=AsyncMock()):
            with patch.object(fast_redis, "client", client):
                result = await fast_redis.delete("test_key")

        assert result is True
        client.delete.assert_awaited_once_with("test_key")
