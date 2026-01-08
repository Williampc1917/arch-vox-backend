from unittest.mock import AsyncMock

import pytest
from starlette.requests import Request

from app.features.vip_onboarding.api.router import (
    VipManualContactRequest,
    add_manual_contact,
)
from app.features.vip_onboarding.pipeline.scoring.repository import VipScoringRepository
from app.features.vip_onboarding.pipeline.scoring.service import ScoringService
from app.security.hashing import hash_email


@pytest.mark.asyncio
async def test_manual_add_returns_hash(monkeypatch):
    request = Request({"type": "http", "method": "POST", "path": "/onboarding/vips/manual"})
    payload = VipManualContactRequest(email="Test@Example.com", display_name="Test")
    claims = {"sub": "user-123"}

    ensure_mock = AsyncMock()
    upsert_mock = AsyncMock()
    audit_mod_mock = AsyncMock()
    audit_pii_mock = AsyncMock()

    monkeypatch.setattr(
        "app.features.vip_onboarding.api.router.ContactAggregationRepository.ensure_contact_exists",
        ensure_mock,
    )
    monkeypatch.setattr(
        "app.features.vip_onboarding.api.router.ContactIdentityRepository.upsert_identities",
        upsert_mock,
    )
    monkeypatch.setattr(
        "app.features.vip_onboarding.api.router.audit_data_modification",
        audit_mod_mock,
    )
    monkeypatch.setattr(
        "app.features.vip_onboarding.api.router.audit_pii_access",
        audit_pii_mock,
    )
    monkeypatch.setattr(
        "app.features.vip_onboarding.api.router.encrypt_data",
        lambda value: b\"encrypted\",
    )

    result = await add_manual_contact(request, payload, claims=claims)

    expected_hash = hash_email("test@example.com")
    assert result["contact_hash"] == expected_hash
    ensure_mock.assert_awaited_once_with("user-123", expected_hash, manual_added=True)
    args, _ = upsert_mock.await_args
    assert args[0][0].contact_hash == expected_hash


@pytest.mark.asyncio
async def test_manual_add_duplicate_email_returns_same_hash(monkeypatch):
    request = Request({"type": "http", "method": "POST", "path": "/onboarding/vips/manual"})
    payload = VipManualContactRequest(email="dup@example.com", display_name=None)
    claims = {"sub": "user-123"}

    ensure_mock = AsyncMock()
    upsert_mock = AsyncMock()
    audit_mod_mock = AsyncMock()
    audit_pii_mock = AsyncMock()

    monkeypatch.setattr(
        "app.features.vip_onboarding.api.router.ContactAggregationRepository.ensure_contact_exists",
        ensure_mock,
    )
    monkeypatch.setattr(
        "app.features.vip_onboarding.api.router.ContactIdentityRepository.upsert_identities",
        upsert_mock,
    )
    monkeypatch.setattr(
        "app.features.vip_onboarding.api.router.audit_data_modification",
        audit_mod_mock,
    )
    monkeypatch.setattr(
        "app.features.vip_onboarding.api.router.audit_pii_access",
        audit_pii_mock,
    )
    monkeypatch.setattr(
        "app.features.vip_onboarding.api.router.encrypt_data",
        lambda value: b\"encrypted\",
    )

    first = await add_manual_contact(request, payload, claims=claims)
    second = await add_manual_contact(request, payload, claims=claims)

    assert first["contact_hash"] == second["contact_hash"]


@pytest.mark.asyncio
async def test_selection_rejects_unknown_hash(monkeypatch):
    async def fake_fetch_all(_query, _params):
        return [{"contact_hash": "known", "id": "contact-1"}]

    monkeypatch.setattr(
        "app.features.vip_onboarding.pipeline.scoring.repository.fetch_all",
        fake_fetch_all,
    )

    with pytest.raises(ValueError):
        await VipScoringRepository.replace_vip_selection(
            "user-123", ["known", "unknown"]
        )


@pytest.mark.asyncio
async def test_selection_accepts_known_hashes(monkeypatch):
    async def fake_fetch_all(_query, _params):
        return [
            {"contact_hash": "known-1", "id": "contact-1"},
            {"contact_hash": "known-2", "id": "contact-2"},
        ]

    execute_mock = AsyncMock()

    monkeypatch.setattr(
        "app.features.vip_onboarding.pipeline.scoring.repository.fetch_all",
        fake_fetch_all,
    )
    monkeypatch.setattr(
        "app.features.vip_onboarding.pipeline.scoring.repository.execute_transaction",
        execute_mock,
    )

    await VipScoringRepository.replace_vip_selection(
        "user-123", ["known-1", "known-2"]
    )

    execute_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_selection_rejects_over_20():
    service = ScoringService()
    with pytest.raises(ValueError):
        await service.save_vip_selection("user-123", [f"hash-{i}" for i in range(21)])
