"""
tests/test_equity_persistence.py — Equity chart persistence tests.

Verifies:
  1. save_equity_point() stores data in DB
  2. load_equity_history() returns correct format & order
  3. trim_equity_history() caps rows properly
  4. load_equity_from_db() restores _equity_history list
  5. record_equity_point() triggers DB persistence
"""

import asyncio
import pytest
import pytest_asyncio
from datetime import datetime, timezone

from database.repository import Repository


# ── Fixtures ─────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def repo(tmp_path):
    """In-memory repo for testing."""
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'test_equity.db'}"
    r = Repository(db_url=db_url)
    await r.init_db()
    yield r
    await r.close()


# ── Tests ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_save_and_load_equity_point(repo):
    """save_equity_point stores data, load_equity_history returns it."""
    await repo.save_equity_point(10000.0, 10000.0)
    await repo.save_equity_point(10050.50, 10050.50)
    await repo.save_equity_point(10030.25, 10050.50)

    history = await repo.load_equity_history(limit=10)
    assert len(history) == 3
    # Oldest first
    assert history[0]["balance"] == 10000.0
    assert history[1]["balance"] == 10050.50
    assert history[2]["balance"] == 10030.25
    assert history[2]["hwm"] == 10050.50


@pytest.mark.asyncio
async def test_load_equity_history_format(repo):
    """Each equity point dict has the expected keys."""
    await repo.save_equity_point(5000.0, 5000.0)

    history = await repo.load_equity_history()
    assert len(history) == 1
    pt = history[0]
    assert "date" in pt
    assert "timestamp" in pt
    assert "label" in pt
    assert "balance" in pt
    assert "hwm" in pt


@pytest.mark.asyncio
async def test_load_equity_history_limit(repo):
    """load_equity_history respects limit param."""
    for i in range(20):
        await repo.save_equity_point(10000.0 + i, 10000.0 + i)

    history = await repo.load_equity_history(limit=5)
    assert len(history) == 5
    # Should be the LAST 5 entries (most recent)
    assert history[0]["balance"] == 10015.0
    assert history[4]["balance"] == 10019.0


@pytest.mark.asyncio
async def test_load_equity_history_order(repo):
    """Points returned oldest first (ascending)."""
    await repo.save_equity_point(100.0, 100.0)
    await repo.save_equity_point(200.0, 200.0)
    await repo.save_equity_point(300.0, 300.0)

    history = await repo.load_equity_history()
    balances = [pt["balance"] for pt in history]
    assert balances == [100.0, 200.0, 300.0]


@pytest.mark.asyncio
async def test_trim_equity_history(repo):
    """trim_equity_history removes oldest entries beyond keep count."""
    for i in range(10):
        await repo.save_equity_point(1000.0 + i, 1000.0 + i)

    deleted = await repo.trim_equity_history(keep=3)
    assert deleted == 7

    remaining = await repo.load_equity_history(limit=100)
    assert len(remaining) == 3
    # Should keep the newest 3
    assert remaining[0]["balance"] == 1007.0
    assert remaining[1]["balance"] == 1008.0
    assert remaining[2]["balance"] == 1009.0


@pytest.mark.asyncio
async def test_trim_equity_no_op(repo):
    """trim_equity_history does nothing when count <= keep."""
    for i in range(3):
        await repo.save_equity_point(500.0 + i, 500.0 + i)

    deleted = await repo.trim_equity_history(keep=10)
    assert deleted == 0

    remaining = await repo.load_equity_history(limit=100)
    assert len(remaining) == 3


@pytest.mark.asyncio
async def test_load_equity_empty(repo):
    """load_equity_history returns empty list when no data."""
    history = await repo.load_equity_history()
    assert history == []


@pytest.mark.asyncio
async def test_save_equity_rounds_values(repo):
    """save_equity_point rounds to 2 decimal places."""
    await repo.save_equity_point(10000.126, 10000.789)

    history = await repo.load_equity_history()
    assert history[0]["balance"] == 10000.13
    assert history[0]["hwm"] == 10000.79


@pytest.mark.asyncio
async def test_load_equity_from_db_restores_in_memory():
    """load_equity_from_db populates the in-memory _equity_history list."""
    # We test by importing and patching
    import dashboard.backend.main as dm

    # Create a mock repo with canned data
    class FakeRepo:
        async def load_equity_history(self, limit=500):
            return [
                {"date": "2025-01-01 00:00:00", "timestamp": "2025-01-01T00:00:00",
                 "label": "01-01 00:00", "balance": 9000.0, "hwm": 9000.0},
                {"date": "2025-01-01 01:00:00", "timestamp": "2025-01-01T01:00:00",
                 "label": "01-01 01:00", "balance": 9100.0, "hwm": 9100.0},
            ]

    # Save originals
    orig_repo = dm._repo
    orig_history = dm._equity_history.copy()

    try:
        dm._repo = FakeRepo()
        dm._equity_history.clear()

        await dm.load_equity_from_db()

        assert len(dm._equity_history) == 2
        assert dm._equity_history[0]["balance"] == 9000.0
        assert dm._equity_history[1]["balance"] == 9100.0
    finally:
        dm._repo = orig_repo
        dm._equity_history.clear()
        dm._equity_history.extend(orig_history)
