import asyncio
from services.hardware_check.hardware_check_service import run_full_check


def test_run_full_check_returns_statuses():
    statuses = asyncio.run(run_full_check())
    assert isinstance(statuses, list)
    assert len(statuses) >= 1
    for s in statuses:
        assert "name" in s
        assert "ok" in s
        assert "message" in s
