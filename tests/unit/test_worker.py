import pytest

from app.jobs import worker


@pytest.mark.asyncio
async def test_run_worker_runs_job(monkeypatch):
    called = {"ok": False}

    async def dummy_job():
        called["ok"] = True

    monkeypatch.setitem(worker.JOB_REGISTRY, "dummy", dummy_job)

    await worker.run_worker("dummy")

    assert called["ok"] is True


@pytest.mark.asyncio
async def test_run_worker_unknown_job():
    with pytest.raises(ValueError):
        await worker.run_worker("missing")
