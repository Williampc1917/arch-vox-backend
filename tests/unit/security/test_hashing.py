import pytest

from app.security import hashing


def _configure_secret(monkeypatch, secret: str = "a" * 32):
    monkeypatch.setattr("app.security.hashing.settings.HASHING_SECRET", secret, raising=False)


def test_compute_hmac_is_deterministic(monkeypatch):
    _configure_secret(monkeypatch)
    first = hashing.compute_hmac("value", namespace="test")
    second = hashing.compute_hmac("value", namespace="test")
    assert first == second


def test_namespaces_change_output(monkeypatch):
    _configure_secret(monkeypatch)
    generic = hashing.compute_hmac("abc", namespace="generic")
    email_hash = hashing.hash_email("abc@example.com")
    thread_hash = hashing.hash_thread_id("abc")
    assert len({generic, email_hash, thread_hash}) == 3


def test_hash_contacts_preserves_order(monkeypatch):
    _configure_secret(monkeypatch)
    addresses = ["a@example.com", "b@example.com"]
    hashed = hashing.hash_contacts(addresses)
    assert len(hashed) == 2
    assert hashed[0] != hashed[1]


def test_missing_secret_raises(monkeypatch):
    monkeypatch.setattr("app.security.hashing.settings.HASHING_SECRET", "", raising=False)
    with pytest.raises(hashing.HashingError):
        hashing.compute_hmac("value", namespace="test")


def test_too_short_secret_raises(monkeypatch):
    monkeypatch.setattr("app.security.hashing.settings.HASHING_SECRET", "short", raising=False)
    with pytest.raises(hashing.HashingError):
        hashing.compute_hmac("value", namespace="test")
