"""
Tests for external service connections.
"""

from unittest.mock import MagicMock, patch

import requests

from app.db.postgres import check_db
from app.services.redis_store import get, ping, set_with_ttl


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
    """Tests for Redis connection via Upstash REST API."""

    @patch("requests.post")
    def test_ping_success(self, mock_post):
        """Test successful Redis ping."""
        mock_response = MagicMock()
        mock_response.ok = True
        mock_response.text = "PONG"
        mock_post.return_value = mock_response

        result = ping()

        assert result is True
        mock_post.assert_called_once()

        # Check that the call was made to the ping endpoint
        args, kwargs = mock_post.call_args
        assert args[0].endswith("/ping")
        assert "Authorization" in kwargs["headers"]

    @patch("requests.post")
    def test_ping_failure_bad_response(self, mock_post):
        """Test Redis ping with bad HTTP response."""
        mock_response = MagicMock()
        mock_response.ok = False
        mock_response.text = "ERROR"
        mock_post.return_value = mock_response

        result = ping()

        assert result is False

    @patch("requests.post")
    def test_ping_failure_no_pong(self, mock_post):
        """Test Redis ping with successful HTTP but wrong response."""
        mock_response = MagicMock()
        mock_response.ok = True
        mock_response.text = "ERROR"
        mock_post.return_value = mock_response

        result = ping()

        assert result is False

    @patch("requests.post")
    def test_ping_exception(self, mock_post):
        """Test Redis ping with network exception."""
        mock_post.side_effect = requests.exceptions.RequestException("Network error")

        # ping() should handle exceptions gracefully and return False
        result = ping()
        assert result is False

    @patch("requests.post")
    def test_set_with_ttl_success(self, mock_post):
        """Test Redis SET with TTL."""
        mock_response = MagicMock()
        mock_response.ok = True
        mock_post.return_value = mock_response

        result = set_with_ttl("test_key", "test_value", 60)

        assert result is True

        # Check that the call includes TTL parameter
        args, kwargs = mock_post.call_args
        assert "EX=60" in args[0]
        assert "test_key" in args[0]
        assert "test_value" in args[0]

    @patch("requests.post")
    def test_set_without_ttl(self, mock_post):
        """Test Redis SET without TTL."""
        mock_response = MagicMock()
        mock_response.ok = True
        mock_post.return_value = mock_response

        result = set_with_ttl("test_key", "test_value")

        assert result is True

        # Check that no TTL parameter is included
        args, kwargs = mock_post.call_args
        assert "EX=" not in args[0]

    @patch("requests.post")
    def test_get_success(self, mock_post):
        """Test Redis GET success."""
        mock_response = MagicMock()
        mock_response.ok = True
        mock_response.json.return_value = "test_value"
        mock_post.return_value = mock_response

        result = get("test_key")

        assert result == "test_value"

        # Check that the call was made to the get endpoint
        args, kwargs = mock_post.call_args
        assert "get/test_key" in args[0]

    @patch("requests.post")
    def test_get_failure(self, mock_post):
        """Test Redis GET failure."""
        mock_response = MagicMock()
        mock_response.ok = False
        mock_post.return_value = mock_response

        result = get("test_key")

        assert result is None
