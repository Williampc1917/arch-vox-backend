"""
Test encryption service functionality.
"""

from app.services.infrastructure.encryption_service import (
    decrypt_token,
    encrypt_token,
    validate_encryption_config,
)


def test_basic_encryption_decryption():
    """Test that encryption and decryption work correctly."""
    test_token = "fake_oauth_token_12345"

    # Encrypt the token
    encrypted = encrypt_token(test_token)

    # Verify encryption produces different output
    assert encrypted != test_token
    assert len(encrypted) > 0

    # Decrypt the token
    decrypted = decrypt_token(encrypted)

    # Verify decryption works correctly
    assert decrypted == test_token
    print("✅ Encryption/decryption test passed")
    print(f"Original:  {test_token}")
    print(f"Encrypted: {encrypted[:50]}...")
    print(f"Decrypted: {decrypted}")


def test_encryption_config_validation():
    """Test that encryption configuration is valid."""
    is_valid = validate_encryption_config()
    assert is_valid is True
    print(f"✅ Encryption config validation passed: {is_valid}")


def test_encryption_with_different_tokens():
    """Test encryption with various token formats."""
    test_tokens = [
        "simple_token",
        "token_with_special_chars_!@#$%^&*()",
        "very_long_token_" + "x" * 100,
        "token_with_unicode__test",
        # Removed empty string - encryption service doesn't allow it
    ]

    for token in test_tokens:
        encrypted = encrypt_token(token)
        decrypted = decrypt_token(encrypted)
        assert decrypted == token
        print(f"✅ Token '{token[:20]}...' encrypted/decrypted successfully")


def test_encryption_consistency():
    """Test that the same token produces consistent encryption."""
    test_token = "consistent_test_token"

    # Encrypt the same token multiple times
    encrypted1 = encrypt_token(test_token)
    encrypted2 = encrypt_token(test_token)

    # Note: Depending on your encryption implementation,
    # this might be different (if using random IVs) or the same
    # This test will help you understand your encryption behavior
    print(f"Encryption 1: {encrypted1[:30]}...")
    print(f"Encryption 2: {encrypted2[:30]}...")

    # Both should decrypt to the same value
    decrypted1 = decrypt_token(encrypted1)
    decrypted2 = decrypt_token(encrypted2)

    assert decrypted1 == test_token
    assert decrypted2 == test_token
    print("✅ Encryption consistency test passed")
