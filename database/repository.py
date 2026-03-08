"""
database/repository.py — Data-access layer (async SQLAlchemy).

Provides ``Repository`` with CRUD helpers for trades, analyses, and settings.
All methods are async and use ``aiosqlite`` under the hood.
Config precedence (M-26):
    .env  →  config/settings.py  →  config/strategy_rules.py
    Environment variables loaded via ``dotenv`` always win over Python defaults.
    ``strategy_rules.py`` holds scoring/validation logic and is the canonical
    source for trading-rule constants (weights, thresholds, validation flags).
    ``settings.py`` holds infra and environment knobs (API keys, DB, pairs).
Usage::

    repo = Repository()          # uses DATABASE_URL from settings
    await repo.init_db()         # create tables if needed
    await repo.save_trade(trade)
    trades = await repo.list_trades(pair="EURUSD", limit=50)
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select, update, delete, func
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from config.settings import DATABASE_URL
from database.models import (
    Base,
    Trade,
    AnalysisSession,
    SettingsKV,
    EquityPoint,
)

logger = logging.getLogger(__name__)


class Repository:
    """Async data-access layer backed by SQLite (aiosqlite)."""

    def __init__(self, db_url: str | None = None):
        url = db_url or DATABASE_URL
        self._engine = create_async_engine(url, echo=False)
        self._session_factory = async_sessionmaker(
            self._engine, expire_on_commit=False
        )

    async def init_db(self) -> None:
        """Create all tables if they don't exist."""
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("Database tables ensured.")

    # ------------------------------------------------------------------
    # Trades
    # ------------------------------------------------------------------

    async def save_trade(self, trade: Trade) -> Trade:
        """Insert or update a trade record.

        Returns:
            The merged Trade instance.  The caller can read ``trade.trade_id``
            (the business key) or ``trade.id`` (the auto-incremented PK) from
            the returned object (L-41).
        """
        async with self._session_factory() as session:
            merged = await session.merge(trade)
            await session.commit()
            return merged

    async def get_trade(self, trade_id: str) -> Optional[Trade]:
        async with self._session_factory() as session:
            stmt = select(Trade).where(Trade.trade_id == trade_id)
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def list_trades(
        self,
        pair: Optional[str] = None,
        mode: Optional[str] = None,
        limit: int = 100,
    ) -> list[Trade]:
        # L-44: Default 100 trades — covers dashboard pagination.
        # M-27: limit is applied via SQLAlchemy .limit() so the value is always
        # parameterised (no SQL-injection risk).  Clamp to [1, 10_000].
        limit = max(1, min(limit, 10_000))
        async with self._session_factory() as session:
            stmt = select(Trade).order_by(Trade.opened_at.desc()).limit(limit)
            if pair:
                stmt = stmt.where(Trade.pair == pair)
            if mode:
                stmt = stmt.where(Trade.mode == mode)
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def count_trades(self, mode: str = "demo") -> int:
        async with self._session_factory() as session:
            stmt = select(func.count(Trade.id)).where(Trade.mode == mode)
            result = await session.execute(stmt)
            return result.scalar_one()

    async def trade_stats(self, mode: str = "demo") -> dict:
        """Compute win-rate, avg pips, total trades for a given mode.

        Win criteria (H-13):
          - TP1_HIT, TP2_HIT, TRAIL_PROFIT  → always win
          - MANUAL_CLOSE, BE_HIT            → win only when pips > 0
        Loss: SL_HIT always.
        Excluded from denominator: CANCELLED (never reached market).

        Returns:
            dict with keys: total, wins, losses, winrate, avg_pips, total_pips
        """
        trades = await self.list_trades(mode=mode, limit=10_000)
        if not trades:
            return {
                "total": 0,
                "wins": 0,
                "losses": 0,
                "winrate": 0.0,
                "avg_pips": 0.0,
                "total_pips": 0.0,
            }

        # H-13: TRAIL_PROFIT is always a win; BE_HIT/MANUAL_CLOSE are wins
        # only when the trade closed in profit (pips > 0).
        _ALWAYS_WIN = {"TP1_HIT", "TP2_HIT", "TRAIL_PROFIT"}
        _CONDITIONAL_WIN = {"MANUAL_CLOSE", "BE_HIT"}  # win if pips > 0

        wins = sum(
            1
            for t in trades
            if t.result in _ALWAYS_WIN
            or (t.result in _CONDITIONAL_WIN and (t.pips or 0) > 0)
        )
        losses = sum(
            1 for t in trades if t.result == "SL_HIT"
        )
        # Exclude CANCELLED from denominator — never reached market.
        relevant = [t for t in trades if t.result != "CANCELLED"]
        total_pips = sum(t.pips or 0 for t in relevant)
        n = len(relevant)
        return {
            "total": len(trades),
            "wins": wins,
            "losses": losses,
            "winrate": wins / n if n else 0.0,
            "avg_pips": total_pips / n if n else 0.0,
            "total_pips": total_pips,
        }

    # ------------------------------------------------------------------
    # Analysis Sessions
    # ------------------------------------------------------------------

    async def save_session(self, session_obj: AnalysisSession) -> AnalysisSession:
        async with self._session_factory() as session:
            merged = await session.merge(session_obj)
            await session.commit()
            return merged

    async def get_session(self, session_id: str) -> Optional[AnalysisSession]:
        async with self._session_factory() as session:
            stmt = select(AnalysisSession).where(
                AnalysisSession.session_id == session_id
            )
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def active_sessions(self) -> list[AnalysisSession]:
        """Return sessions NOT in terminal state."""
        async with self._session_factory() as session:
            stmt = (
                select(AnalysisSession)
                .where(AnalysisSession.state.notin_(["CLOSED", "CANCELLED"]))
                .order_by(AnalysisSession.updated_at.desc())
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    # ------------------------------------------------------------------
    # Settings KV
    # ------------------------------------------------------------------

    async def get_setting(self, key: str, default: str = "") -> str:
        async with self._session_factory() as session:
            stmt = select(SettingsKV).where(SettingsKV.key == key)
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            return row.value if row else default

    async def set_setting(self, key: str, value: str) -> None:
        async with self._session_factory() as session:
            existing = await session.get(SettingsKV, key)
            if existing:
                existing.value = value
                existing.updated_at = datetime.now(timezone.utc)
            else:
                session.add(SettingsKV(key=key, value=value))
            await session.commit()

    async def get_setting_json(self, key: str, default: dict | None = None) -> dict:
        raw = await self.get_setting(key, "")
        if not raw:
            return default or {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return default or {}

    async def set_setting_json(self, key: str, data: dict) -> None:
        await self.set_setting(key, json.dumps(data))

    # ------------------------------------------------------------------
    # Equity persistence
    # ------------------------------------------------------------------

    async def save_equity_point(self, balance: float, hwm: float) -> None:
        """Persist a single equity snapshot."""
        async with self._session_factory() as session:
            point = EquityPoint(
                timestamp=datetime.now(timezone.utc),
                balance=round(balance, 2),
                high_water_mark=round(hwm, 2),
            )
            session.add(point)
            await session.commit()

    async def load_equity_history(self, limit: int = 500) -> list[dict]:
        """Load equity history from DB, most recent *limit* points."""
        async with self._session_factory() as session:
            # Subquery to get last N rows by id desc, then re-order asc
            stmt = (
                select(EquityPoint)
                .order_by(EquityPoint.id.desc())
                .limit(limit)
            )
            result = await session.execute(stmt)
            rows = list(result.scalars().all())
            rows.reverse()  # oldest first
            return [
                {
                    "date": r.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                    "timestamp": r.timestamp.isoformat(),
                    "label": r.timestamp.strftime("%m-%d %H:%M"),
                    "balance": r.balance,
                    "hwm": r.high_water_mark,
                }
                for r in rows
            ]

    async def trim_equity_history(self, keep: int = 2000) -> int:
        """Delete oldest equity points beyond *keep* count."""
        async with self._session_factory() as session:
            count_stmt = select(func.count(EquityPoint.id))
            total = (await session.execute(count_stmt)).scalar() or 0
            if total <= keep:
                return 0
            to_delete = total - keep
            oldest_stmt = (
                select(EquityPoint.id)
                .order_by(EquityPoint.id.asc())
                .limit(to_delete)
            )
            oldest_ids = [r for r in (await session.execute(oldest_stmt)).scalars().all()]
            if oldest_ids:
                del_stmt = delete(EquityPoint).where(EquityPoint.id.in_(oldest_ids))
                await session.execute(del_stmt)
                await session.commit()
            return len(oldest_ids)

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    async def close(self) -> None:
        await self._engine.dispose()
