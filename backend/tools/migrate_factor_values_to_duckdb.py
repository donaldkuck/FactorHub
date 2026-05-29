"""
Migrate SQLite factor_value_cache rows into DuckDB factor_values.

The app already reads/writes factor values through DuckDB first and falls back
to SQLite. This script is for moving old cache rows in controlled chunks.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

import duckdb
import pandas as pd

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.core.settings import settings
from backend.services.duckdb_bar_store import duckdb_bar_store


def migrate_factor_values(batch_size: int, limit: int | None = None) -> int:
    if not duckdb_bar_store.is_available():
        raise RuntimeError("duckdb 未安装")
    sqlite_path = Path(settings.DATABASE_URL.replace("sqlite:///", ""))
    if not sqlite_path.exists():
        raise FileNotFoundError(f"SQLite 数据库不存在: {sqlite_path}")

    migrated = 0
    last_id = 0
    sqlite_conn = sqlite3.connect(sqlite_path)
    sqlite_conn.row_factory = sqlite3.Row
    try:
        while True:
            remaining = None if limit is None else max(limit - migrated, 0)
            if remaining == 0:
                break
            current_batch = batch_size if remaining is None else min(batch_size, remaining)
            rows = sqlite_conn.execute(
                """
                SELECT id, factor_id, factor_code_hash, stock_code, frequency, bar_time, value, updated_at
                FROM factor_value_cache
                WHERE id > ?
                ORDER BY id
                LIMIT ?
                """,
                (last_id, current_batch),
            ).fetchall()
            if not rows:
                break

            last_id = rows[-1]["id"]
            payload = pd.DataFrame([dict(row) for row in rows])
            migrated += len(payload)
            payload = payload.drop(columns=["id"])
            payload["bar_time"] = pd.to_datetime(payload["bar_time"])
            payload["updated_at"] = pd.to_datetime(payload["updated_at"], errors="coerce").fillna(pd.Timestamp.now())

            with duckdb.connect(str(settings.DUCKDB_PATH)) as conn:
                duckdb_bar_store._ensure_schema(conn)
                conn.register("incoming_factor_values", payload)
                conn.execute(
                    """
                    DELETE FROM factor_values
                    USING incoming_factor_values incoming
                    WHERE factor_values.factor_id = incoming.factor_id
                      AND factor_values.factor_code_hash = incoming.factor_code_hash
                      AND factor_values.stock_code = incoming.stock_code
                      AND factor_values.frequency = incoming.frequency
                      AND factor_values.bar_time = incoming.bar_time
                    """
                )
                conn.execute("INSERT INTO factor_values SELECT * FROM incoming_factor_values")
                conn.unregister("incoming_factor_values")

            print(f"migrated={migrated} last_sqlite_id={last_id}", flush=True)
    finally:
        sqlite_conn.close()
    return migrated


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-size", type=int, default=100_000)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    total = migrate_factor_values(batch_size=args.batch_size, limit=args.limit)
    print(f"done migrated={total}")


if __name__ == "__main__":
    main()
