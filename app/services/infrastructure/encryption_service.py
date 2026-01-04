"""
Encryption service for OAuth tokens.
Uses Fernet symmetric encryption for secure token storage.
"""

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings
from app.infrastructure.observability.logging import get_logger

logger = get_logger(__name__)


class EncryptionError(Exception):
    """Custom exception for encryption/decryption errors."""

    pass


def _get_fernet() -> Fernet:
    """
    Get Fernet instance with encryption key from environment.

    Returns:
        Fernet: Configured Fernet instance

    Raises:
        EncryptionError: If encryption key is not configured
    """
    if not settings.ENCRYPTION_KEY:
        raise EncryptionError("ENCRYPTION_KEY not configured in environment")

    try:
        # Convert string key to bytes
        key_bytes = settings.ENCRYPTION_KEY.encode("utf-8")
        return Fernet(key_bytes)
    except Exception as e:
        logger.error("Failed to initialize Fernet cipher", error=str(e))
        raise EncryptionError(f"Invalid encryption key: {e}") from e


def encrypt_token(token: str) -> bytes:
    """
    Encrypt a token string for database storage.

    Args:
        token: Plain text token to encrypt

    Returns:
        bytes: Encrypted token as bytes (ready for BYTEA storage)

    Raises:
        EncryptionError: If encryption fails
    """
    if not token or not isinstance(token, str):
        raise EncryptionError("Token must be a non-empty string")

    try:
        fernet = _get_fernet()
        token_bytes = token.encode("utf-8")
        encrypted_bytes = fernet.encrypt(token_bytes)

        logger.debug(
            "Token encrypted successfully",
            token_length=len(token),
            encrypted_length=len(encrypted_bytes),
        )

        return encrypted_bytes

    except Exception as e:
        logger.error("Failed to encrypt token", error=str(e))
        raise EncryptionError(f"Encryption failed: {e}") from e


def decrypt_token(encrypted_token: bytes) -> str:
    """
    Decrypt a token from database storage.

    Args:
        encrypted_token: Encrypted token bytes from database

    Returns:
        str: Decrypted plain text token

    Raises:
        EncryptionError: If decryption fails or token is invalid
    """
    if not encrypted_token or not isinstance(encrypted_token, bytes):
        raise EncryptionError("Encrypted token must be non-empty bytes")

    try:
        fernet = _get_fernet()
        decrypted_bytes = fernet.decrypt(encrypted_token)
        token = decrypted_bytes.decode("utf-8")

        logger.debug(
            "Token decrypted successfully",
            encrypted_length=len(encrypted_token),
            decrypted_length=len(token),
        )

        return token

    except InvalidToken as e:
        logger.error("Token decryption failed - invalid token", error=str(e))
        raise EncryptionError("Invalid or corrupted token") from e
    except Exception as e:
        logger.error("Failed to decrypt token", error=str(e))
        raise EncryptionError(f"Decryption failed: {e}") from e


def encrypt_data(data: str) -> bytes:
    """
    Encrypt arbitrary string data.

    Args:
        data: Plain text data to encrypt

    Returns:
        bytes: Encrypted data as bytes

    Raises:
        EncryptionError: If encryption fails
    """
    return encrypt_token(data)  # Same implementation


def decrypt_data(encrypted_data: bytes) -> str:
    """
    Decrypt arbitrary string data.

    Args:
        encrypted_data: Encrypted data bytes

    Returns:
        str: Decrypted plain text data

    Raises:
        EncryptionError: If decryption fails
    """
    return decrypt_token(encrypted_data)  # Same implementation


def validate_encryption_config() -> bool:
    """
    Validate that encryption is properly configured.

    Returns:
        bool: True if encryption is configured and working
    """
    try:
        # Test encryption/decryption with dummy data
        test_data = "test_encryption_12345"
        encrypted = encrypt_token(test_data)
        decrypted = decrypt_token(encrypted)

        is_valid = decrypted == test_data

        if is_valid:
            logger.info("Encryption configuration validated successfully")
        else:
            logger.error("Encryption validation failed - data mismatch")

        return is_valid

    except Exception as e:
        logger.error("Encryption configuration validation failed", error=str(e))
        return False


def generate_new_key() -> str:
    """
    Generate a new Fernet encryption key.

    Returns:
        str: Base64-encoded Fernet key

    Note:
        Use this for initial setup or key rotation.
        Store the result in your environment variables.
    """
    key = Fernet.generate_key()
    key_str = key.decode("utf-8")

    logger.info("New encryption key generated")

    return key_str


# Convenience functions for common patterns
def encrypt_oauth_tokens(
    access_token: str, refresh_token: str | None = None
) -> tuple[bytes, bytes | None]:
    """
    Encrypt OAuth access and refresh tokens.

    Args:
        access_token: OAuth access token
        refresh_token: OAuth refresh token (optional)

    Returns:
        tuple: (encrypted_access_token, encrypted_refresh_token)
    """
    encrypted_access = encrypt_token(access_token)
    encrypted_refresh = encrypt_token(refresh_token) if refresh_token else None

    logger.info("OAuth tokens encrypted", has_refresh_token=bool(refresh_token))

    return encrypted_access, encrypted_refresh


def decrypt_oauth_tokens(
    encrypted_access: bytes, encrypted_refresh: bytes | None = None
) -> tuple[str, str | None]:
    """
    Decrypt OAuth access and refresh tokens.

    Args:
        encrypted_access: Encrypted access token bytes
        encrypted_refresh: Encrypted refresh token bytes (optional)

    Returns:
        tuple: (access_token, refresh_token)
    """
    access_token = decrypt_token(encrypted_access)
    refresh_token = decrypt_token(encrypted_refresh) if encrypted_refresh else None

    logger.info("OAuth tokens decrypted", has_refresh_token=bool(refresh_token))

    return access_token, refresh_token
