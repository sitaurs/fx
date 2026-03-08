"""
tests/test_database.py — Tests for database models & repository.

Tests SQLite persistence layer: trades, analysis sessions, settings KV.
"""

from __future__ import annotations

import asyncio
import pytest
import pytest_asyncio
from datetime import datetime, timezone

from database.models import Base, Trade, AnalysisSession, SettingsKV
from database.repository import Repository


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def repo():
    """In-memory SQLite repository for testing."""
    r = Repository(db_url="sqlite+aiosqlite:///:memory:")
    await r.init_db()
    yield r
    await r.close()


def _make_trade(
    trade_id: str = "TEST_001",
    pair: str = "EURUSD",
    result: str = "TP1_HIT",
    pips: float = 25.0,
    mode: str = "demo",
) -> Trade:
    return Trade(
        trade_id=trade_id,
        pair=pair,
        direction="buy",
        strategy_mode="sniper_confluence",
        mode=mode,
        entry_price=1.0480,
        stop_loss=1.0450,
        take_profit_1=1.0520,
        exit_price=1.0505,
        result=result,
        pips=pips,
        rr_achieved=1.5,
        duration_minutes=45,
        confluence_score=9,
        voting_confidence=0.8,
    )


# ---------------------------------------------------------------------------
# Trade CRUD
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_save_and_get_trade(repo: Repository):
    trade = _make_trade()
    saved = await repo.save_trade(trade)
    assert saved.trade_id == "TEST_001"

    fetched = await repo.get_trade("TEST_001")
    assert fetched is not None
    assert fetched.pair == "EURUSD"
    assert fetched.pips == 25.0


@pytest.mark.asyncio
async def test_list_trades_pair_filter(repo: Repository):
    await repo.save_trade(_make_trade("T1", pair="EURUSD"))
    await repo.save_trade(_make_trade("T2", pair="XAUUSD"))
    await repo.save_trade(_make_trade("T3", pair="EURUSD"))

    eu = await repo.list_trades(pair="EURUSD")
    assert len(eu) == 2
    all_t = await repo.list_trades()
    assert len(all_t) == 3


@pytest.mark.asyncio
async def test_list_trades_mode_filter(repo: Repository):
    await repo.save_trade(_make_trade("T1", mode="demo"))
    await repo.save_trade(_make_trade("T2", mode="real"))

    demo = await repo.list_trades(mode="demo")
    assert len(demo) == 1
    assert demo[0].mode == "demo"


@pytest.mark.asyncio
async def test_count_trades(repo: Repository):
    for i in range(5):
        await repo.save_trade(_make_trade(f"T{i}", mode="demo"))
    await repo.save_trade(_make_trade("R1", mode="real"))

    assert await repo.count_trades(mode="demo") == 5
    assert await repo.count_trades(mode="real") == 1


@pytest.mark.asyncio
async def test_trade_stats(repo: Repository):
    # 3 TP wins + 1 TRAIL_PROFIT + 1 BE_HIT(positive) = 5 wins, 1 loss
    await repo.save_trade(_make_trade("W1", result="TP1_HIT", pips=20))
    await repo.save_trade(_make_trade("W2", result="TP2_HIT", pips=40))
    await repo.save_trade(_make_trade("W3", result="TP1_HIT", pips=15))
    await repo.save_trade(_make_trade("W4", result="TRAIL_PROFIT", pips=30))
    await repo.save_trade(_make_trade("W5", result="BE_HIT", pips=2))
    await repo.save_trade(_make_trade("L1", result="SL_HIT", pips=-25))

    stats = await repo.trade_stats(mode="demo")
    assert stats["total"] == 6
    assert stats["wins"] == 5
    assert stats["losses"] == 1
    assert stats["winrate"] == pytest.approx(5 / 6)
    assert stats["total_pips"] == pytest.approx(82.0)


# ---------------------------------------------------------------------------
# Analysis Sessions
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_save_and_get_session(repo: Repository):
    sess = AnalysisSession(
        session_id="S001",
        pair="XAUUSD",
        state="WATCHING",
        direction="sell",
        score=8,
        confidence=0.75,
    )
    saved = await repo.save_session(sess)
    assert saved.session_id == "S001"

    fetched = await repo.get_session("S001")
    assert fetched is not None
    assert fetched.state == "WATCHING"


@pytest.mark.asyncio
async def test_active_sessions(repo: Repository):
    await repo.save_session(
        AnalysisSession(session_id="S1", pair="EURUSD", state="WATCHING")
    )
    await repo.save_session(
        AnalysisSession(session_id="S2", pair="XAUUSD", state="CLOSED")
    )
    await repo.save_session(
        AnalysisSession(session_id="S3", pair="GBPJPY", state="APPROACHING")
    )

    active = await repo.active_sessions()
    ids = {s.session_id for s in active}
    assert ids == {"S1", "S3"}


# ---------------------------------------------------------------------------
# Settings KV
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_settings_kv(repo: Repository):
    await repo.set_setting("mode", "demo")
    val = await repo.get_setting("mode")
    assert val == "demo"

    # Update
    await repo.set_setting("mode", "real")
    val = await repo.get_setting("mode")
    assert val == "real"

    # Default
    val = await repo.get_setting("nonexistent", "fallback")
    assert val == "fallback"


@pytest.mark.asyncio
async def test_settings_json(repo: Repository):
    data = {"balance": 10500.0, "trades": 12}
    await repo.set_setting_json("tracker_state", data)

    loaded = await repo.get_setting_json("tracker_state")
    assert loaded["balance"] == 10500.0
    assert loaded["trades"] == 12

    # Default for missing key
    default = await repo.get_setting_json("missing")
    assert default == {}
