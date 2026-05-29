"""
DuckDB-backed raw K-line storage.

The module is optional at runtime: if duckdb is not installed, callers can
fall back to SQLite/AkShare without failing app startup.
"""
from __future__ import annotations

import importlib.util
from contextlib import contextmanager
from typing import Optional

import pandas as pd

from backend.core.settings import settings


class DuckDBBarStore:
    def is_available(self) -> bool:
        return importlib.util.find_spec("duckdb") is not None

    @contextmanager
    def _connect(self):
        if not self.is_available():
            raise RuntimeError("duckdb 未安装")
        import duckdb

        settings.WAREHOUSE_DIR.mkdir(parents=True, exist_ok=True)
        conn = duckdb.connect(str(settings.DUCKDB_PATH))
        try:
            self._ensure_schema(conn)
            yield conn
        finally:
            conn.close()

    def _ensure_schema(self, conn) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS stock_bars (
                stock_code VARCHAR NOT NULL,
                frequency VARCHAR NOT NULL,
                bar_time TIMESTAMP NOT NULL,
                open DOUBLE,
                high DOUBLE,
                low DOUBLE,
                close DOUBLE,
                volume DOUBLE,
                amount DOUBLE,
                source VARCHAR NOT NULL,
                adjust VARCHAR NOT NULL DEFAULT '',
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        try:
            conn.execute("ALTER TABLE stock_bars ADD COLUMN adjust VARCHAR DEFAULT ''")
        except Exception:
            pass
        conn.execute("UPDATE stock_bars SET adjust = '' WHERE adjust IS NULL")
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_stock_bars_lookup_v2
            ON stock_bars(stock_code, frequency, source, adjust, bar_time)
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS factor_values (
                factor_id BIGINT NOT NULL,
                factor_code_hash VARCHAR NOT NULL,
                stock_code VARCHAR NOT NULL,
                frequency VARCHAR NOT NULL,
                adjust VARCHAR NOT NULL DEFAULT 'hfq',
                bar_time TIMESTAMP NOT NULL,
                value DOUBLE,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        try:
            conn.execute("ALTER TABLE factor_values ADD COLUMN adjust VARCHAR DEFAULT 'hfq'")
        except Exception:
            pass
        conn.execute("UPDATE factor_values SET adjust = 'hfq' WHERE adjust IS NULL OR adjust = ''")
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_factor_values_lookup_v2
            ON factor_values(factor_id, factor_code_hash, stock_code, frequency, adjust, bar_time)
            """
        )

    def upsert_bars(self, df: pd.DataFrame, frequency: str, source: str, force: bool = True, adjust: str = "") -> dict:
        if df.empty:
            return {"inserted": 0, "updated": 0, "skipped": 0}

        payload = df[["stock_code", "bar_time", "open", "high", "low", "close", "volume", "amount"]].copy()
        payload["frequency"] = frequency
        payload["source"] = source
        payload["adjust"] = adjust or ""
        payload["updated_at"] = pd.Timestamp.now()
        payload = payload[
            ["stock_code", "frequency", "bar_time", "open", "high", "low", "close", "volume", "amount", "source", "adjust", "updated_at"]
        ]

        with self._connect() as conn:
            before = conn.execute(
                "SELECT count(*) FROM stock_bars WHERE frequency = ? AND source = ? AND adjust = ?",
                [frequency, source, adjust or ""],
            ).fetchone()[0]

            conn.register("incoming_stock_bars", payload)
            if force:
                conn.execute(
                    """
                    DELETE FROM stock_bars
                    USING incoming_stock_bars incoming
                    WHERE stock_bars.stock_code = incoming.stock_code
                      AND stock_bars.frequency = incoming.frequency
                      AND stock_bars.source = incoming.source
                      AND stock_bars.adjust = incoming.adjust
                      AND stock_bars.bar_time = incoming.bar_time
                    """
                )
                inserted = conn.execute("SELECT count(*) FROM incoming_stock_bars").fetchone()[0]
                conn.execute(
                    """
                    INSERT INTO stock_bars (
                        stock_code, frequency, bar_time, open, high, low, close,
                        volume, amount, source, adjust, updated_at
                    )
                    SELECT
                        stock_code, frequency, bar_time, open, high, low, close,
                        volume, amount, source, adjust, updated_at
                    FROM incoming_stock_bars
                    """
                )
            else:
                inserted = conn.execute(
                    """
                    SELECT count(*)
                    FROM incoming_stock_bars incoming
                    ANTI JOIN stock_bars existing
                      ON existing.stock_code = incoming.stock_code
                     AND existing.frequency = incoming.frequency
                     AND existing.source = incoming.source
                     AND existing.adjust = incoming.adjust
                     AND existing.bar_time = incoming.bar_time
                    """
                ).fetchone()[0]
                conn.execute(
                    """
                    INSERT INTO stock_bars (
                        stock_code, frequency, bar_time, open, high, low, close,
                        volume, amount, source, adjust, updated_at
                    )
                    SELECT
                        incoming.stock_code, incoming.frequency, incoming.bar_time,
                        incoming.open, incoming.high, incoming.low, incoming.close,
                        incoming.volume, incoming.amount, incoming.source,
                        incoming.adjust, incoming.updated_at
                    FROM incoming_stock_bars incoming
                    ANTI JOIN stock_bars existing
                     ON existing.stock_code = incoming.stock_code
                     AND existing.frequency = incoming.frequency
                     AND existing.source = incoming.source
                     AND existing.adjust = incoming.adjust
                     AND existing.bar_time = incoming.bar_time
                    """
                )
            after = conn.execute(
                "SELECT count(*) FROM stock_bars WHERE frequency = ? AND source = ? AND adjust = ?",
                [frequency, source, adjust or ""],
            ).fetchone()[0]
            conn.unregister("incoming_stock_bars")

        updated = max(inserted - max(after - before, 0), 0) if force else 0
        skipped = len(payload) - inserted if not force else 0
        return {"inserted": int(inserted), "updated": int(updated), "skipped": int(skipped)}

    def load_bars(
        self,
        stock_code: str,
        frequency: str,
        start_time: pd.Timestamp,
        end_time: pd.Timestamp,
        adjust: Optional[str] = None,
    ) -> Optional[pd.DataFrame]:
        requested_adjusts = [adjust or ""]
        with self._connect() as conn:
            df = pd.DataFrame()
            for candidate_adjust in requested_adjusts:
                df = conn.execute(
                    """
                    SELECT bar_time, open, high, low, close, volume, amount
                    FROM stock_bars
                    WHERE stock_code = ?
                      AND frequency = ?
                      AND adjust = ?
                      AND bar_time >= ?
                      AND bar_time <= ?
                    QUALIFY row_number() OVER (
                        PARTITION BY bar_time
                        ORDER BY updated_at DESC
                    ) = 1
                    ORDER BY bar_time
                    """,
                    [stock_code, frequency, candidate_adjust, start_time.to_pydatetime(), end_time.to_pydatetime()],
                ).fetchdf()
                if not df.empty:
                    break
        if df.empty:
            return None
        df["date"] = pd.to_datetime(df["bar_time"])
        return df.set_index("date")[["open", "high", "low", "close", "volume", "amount"]].sort_index()

    def stats(self, frequency: Optional[str] = None) -> list[dict]:
        with self._connect() as conn:
            sql = """
                SELECT source, adjust, frequency, count(*) AS row_count, count(DISTINCT stock_code) AS stock_count,
                       min(bar_time) start_time, max(bar_time) end_time
                FROM stock_bars
            """
            params = []
            if frequency:
                sql += " WHERE frequency = ?"
                params.append(frequency)
            sql += " GROUP BY source, adjust, frequency ORDER BY frequency, source, adjust"
            rows = conn.execute(sql, params).fetchall()
        return [
            {
                "source": row[0],
                "adjust": row[1],
                "frequency": row[2],
                "rows": row[3],
                "stock_count": row[4],
                "start_time": row[5].isoformat() if row[5] else None,
                "end_time": row[6].isoformat() if row[6] else None,
            }
            for row in rows
        ]

    def coverage(
        self,
        frequency: Optional[str] = None,
        source: Optional[str] = None,
        stock_code: Optional[str] = None,
        adjust: Optional[str] = None,
    ) -> list[dict]:
        with self._connect() as conn:
            sql = """
                SELECT stock_code, source, adjust, frequency, count(*) AS row_count,
                       min(bar_time) start_time, max(bar_time) end_time
                FROM stock_bars
                WHERE 1 = 1
            """
            params = []
            if frequency:
                sql += " AND frequency = ?"
                params.append(frequency)
            if source:
                sql += " AND source = ?"
                params.append(source)
            if stock_code:
                sql += " AND stock_code = ?"
                params.append(stock_code)
            if adjust is not None:
                sql += " AND adjust = ?"
                params.append(adjust)
            sql += " GROUP BY stock_code, source, adjust, frequency"
            rows = conn.execute(sql, params).fetchall()
        return [
            {
                "stock_code": row[0],
                "source": "raw_bar" if row[1] == "import" else f"raw_bar:{row[1]}",
                "adjust": row[2],
                "frequency": row[3],
                "rows": row[4],
                "start_time": row[5].isoformat() if row[5] else None,
                "end_time": row[6].isoformat() if row[6] else None,
            }
            for row in rows
        ]

    def sample(
        self,
        stock_code: str,
        frequency: str,
        source: Optional[str] = None,
        adjust: Optional[str] = None,
        limit: int = 20,
    ) -> list[dict]:
        with self._connect() as conn:
            sql = """
                SELECT stock_code, frequency, bar_time, open, high, low, close, volume, amount, source, adjust
                FROM stock_bars
                WHERE stock_code = ? AND frequency = ?
            """
            params = [stock_code, frequency]
            if source:
                sql += " AND source = ?"
                params.append(source)
            if adjust is not None:
                sql += " AND adjust = ?"
                params.append(adjust)
            sql += " ORDER BY bar_time DESC LIMIT ?"
            params.append(limit)
            rows = conn.execute(sql, params).fetchall()
        return [
            {
                "id": f"{row[0]}-{row[1]}-{row[2]}-{row[9]}-{row[10]}",
                "stock_code": row[0],
                "frequency": row[1],
                "bar_time": row[2].isoformat() if row[2] else None,
                "open": row[3],
                "high": row[4],
                "low": row[5],
                "close": row[6],
                "volume": row[7],
                "amount": row[8],
                "source": row[9],
                "adjust": row[10],
            }
            for row in rows
        ]

    def upsert_factor_values(
        self,
        factor_id: int,
        factor_code_hash: str,
        stock_code: str,
        values: list[dict],
        frequency: str,
        adjust: str = "hfq",
        force: bool = False,
    ) -> int:
        if not values:
            return 0

        payload = pd.DataFrame(values)
        if payload.empty:
            return 0
        time_column = "bar_time" if "bar_time" in payload.columns else "trade_date"
        payload = pd.DataFrame(
            {
                "factor_id": factor_id,
                "factor_code_hash": factor_code_hash,
                "stock_code": stock_code,
                "frequency": frequency,
                "adjust": adjust,
                "bar_time": pd.to_datetime(payload[time_column]),
                "value": pd.to_numeric(payload.get("value"), errors="coerce"),
                "updated_at": pd.Timestamp.now(),
            }
        )
        payload = payload.dropna(subset=["bar_time"])
        payload = payload.drop_duplicates(
            subset=["factor_id", "factor_code_hash", "stock_code", "frequency", "bar_time"],
            keep="last",
        )
        if payload.empty:
            return 0

        with self._connect() as conn:
            conn.register("incoming_factor_values", payload)
            if force:
                conn.execute(
                    """
                    DELETE FROM factor_values
                    USING incoming_factor_values incoming
                    WHERE factor_values.factor_id = incoming.factor_id
                      AND factor_values.factor_code_hash = incoming.factor_code_hash
                      AND factor_values.stock_code = incoming.stock_code
                      AND factor_values.frequency = incoming.frequency
                      AND factor_values.adjust = incoming.adjust
                      AND factor_values.bar_time = incoming.bar_time
                    """
                )
                written = conn.execute("SELECT count(*) FROM incoming_factor_values").fetchone()[0]
                conn.execute(
                    """
                    INSERT INTO factor_values (
                        factor_id, factor_code_hash, stock_code, frequency,
                        adjust, bar_time, value, updated_at
                    )
                    SELECT
                        factor_id, factor_code_hash, stock_code, frequency,
                        adjust, bar_time, value, updated_at
                    FROM incoming_factor_values
                    """
                )
            else:
                written = conn.execute(
                    """
                    SELECT count(*)
                    FROM incoming_factor_values incoming
                    ANTI JOIN factor_values existing
                      ON existing.factor_id = incoming.factor_id
                     AND existing.factor_code_hash = incoming.factor_code_hash
                     AND existing.stock_code = incoming.stock_code
                     AND existing.frequency = incoming.frequency
                     AND existing.adjust = incoming.adjust
                     AND existing.bar_time = incoming.bar_time
                    """
                ).fetchone()[0]
                conn.execute(
                    """
                    INSERT INTO factor_values (
                        factor_id, factor_code_hash, stock_code, frequency,
                        adjust, bar_time, value, updated_at
                    )
                    SELECT
                        incoming.factor_id, incoming.factor_code_hash, incoming.stock_code,
                        incoming.frequency, incoming.adjust, incoming.bar_time,
                        incoming.value, incoming.updated_at
                    FROM incoming_factor_values incoming
                    ANTI JOIN factor_values existing
                      ON existing.factor_id = incoming.factor_id
                     AND existing.factor_code_hash = incoming.factor_code_hash
                     AND existing.stock_code = incoming.stock_code
                     AND existing.frequency = incoming.frequency
                     AND existing.adjust = incoming.adjust
                     AND existing.bar_time = incoming.bar_time
                    """
                )
            conn.unregister("incoming_factor_values")
        return int(written)

    def get_factor_values(
        self,
        factor_id: int,
        factor_code_hash: str,
        stock_code: str,
        start_time,
        end_time,
        frequency: str,
        adjust: str = "hfq",
    ) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT factor_id, factor_code_hash, stock_code, frequency, adjust, bar_time, value, updated_at
                FROM factor_values
                WHERE factor_id = ?
                  AND factor_code_hash = ?
                  AND stock_code = ?
                  AND frequency = ?
                  AND adjust = ?
                  AND bar_time >= ?
                  AND bar_time <= ?
                ORDER BY bar_time
                """,
                [factor_id, factor_code_hash, stock_code, frequency, adjust, start_time, end_time],
            ).fetchall()
        return [
            {
                "id": None,
                "factor_id": row[0],
                "factor_code_hash": row[1],
                "stock_code": row[2],
                "frequency": row[3],
                "adjust": adjust,
                "bar_time": row[5].isoformat() if row[5] else None,
                "value": row[6],
                "created_at": None,
                "updated_at": row[7].isoformat() if row[7] else None,
            }
            for row in rows
        ]

    def delete_factor_values(
        self,
        factor_id: Optional[int] = None,
        factor_code_hash: Optional[str] = None,
        stock_codes: Optional[list[str]] = None,
        frequency: Optional[str] = None,
        adjust: Optional[str] = None,
        start_time=None,
        end_time=None,
    ) -> int:
        conditions = ["1 = 1"]
        params = []
        if factor_id is not None:
            conditions.append("factor_id = ?")
            params.append(factor_id)
        if factor_code_hash:
            conditions.append("factor_code_hash = ?")
            params.append(factor_code_hash)
        if stock_codes:
            placeholders = ", ".join(["?"] * len(stock_codes))
            conditions.append(f"stock_code IN ({placeholders})")
            params.extend(stock_codes)
        if frequency:
            conditions.append("frequency = ?")
            params.append(frequency)
        if adjust:
            conditions.append("adjust = ?")
            params.append(adjust)
        if start_time is not None:
            conditions.append("bar_time >= ?")
            params.append(start_time)
        if end_time is not None:
            conditions.append("bar_time <= ?")
            params.append(end_time)

        with self._connect() as conn:
            before = conn.execute(
                f"SELECT count(*) FROM factor_values WHERE {' AND '.join(conditions)}",
                params,
            ).fetchone()[0]
            conn.execute(
                f"DELETE FROM factor_values WHERE {' AND '.join(conditions)}",
                params,
            )
        return int(before)

    def factor_value_coverage(self, frequency: Optional[str] = None, stock_code: Optional[str] = None) -> list[dict]:
        with self._connect() as conn:
            sql = """
                SELECT stock_code, frequency, count(*) AS row_count,
                       min(bar_time) start_time, max(bar_time) end_time
                FROM factor_values
                WHERE 1 = 1
            """
            params = []
            if frequency:
                sql += " AND frequency = ?"
                params.append(frequency)
            if stock_code:
                sql += " AND stock_code = ?"
                params.append(stock_code)
            sql += " GROUP BY stock_code, frequency"
            rows = conn.execute(sql, params).fetchall()
        return [
            {
                "stock_code": row[0],
                "source": "factor_value:duckdb",
                "frequency": row[1],
                "rows": row[2],
                "start_time": row[3].isoformat() if row[3] else None,
                "end_time": row[4].isoformat() if row[4] else None,
            }
            for row in rows
        ]


duckdb_bar_store = DuckDBBarStore()
