"""
数据库连接管理模块
"""
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker, Session
from sqlalchemy.pool import StaticPool
from contextlib import contextmanager
from typing import Generator

from backend.core.settings import settings
from backend.core.factor_targets import DEFAULT_FACTOR_TARGET, DEFAULT_FREQUENCY


# 创建数据库引擎
engine = create_engine(
    settings.DATABASE_URL,
    connect_args={"check_same_thread": False, "timeout": 30},
    echo=False,
)

# 创建 Session 工厂
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    """数据库模型基类"""
    pass


def init_db() -> None:
    """初始化数据库，创建所有表"""
    from backend.models.factor import (
        FactorModel,
        AnalysisCacheModel,
        FactorValueCacheModel,
        TargetReturnCacheModel,
        StockBarCacheModel,
    )
    from backend.models.backtest import BacktestResultModel, TradeRecordModel
    from backend.models.cache_metadata import CacheMetadataModel
    from backend.models.factor_version import FactorVersionModel
    from backend.models.factor_performance import FactorPerformanceBarCacheModel

    Base.metadata.create_all(bind=engine)
    ensure_factor_target_column(engine)
    ensure_factor_frequency_column(engine)
    reset_legacy_factor_cache_tables(engine)
    ensure_cache_query_indexes(engine)
    configure_sqlite_runtime(engine)


def ensure_factor_target_column(bind) -> None:
    """Add the factor target column for existing SQLite databases."""
    inspector = inspect(bind)
    if "factors" not in inspector.get_table_names():
        return

    columns = {column["name"] for column in inspector.get_columns("factors")}
    if "target" in columns:
        return

    with bind.begin() as conn:
        conn.execute(
            text(
                "ALTER TABLE factors "
                f"ADD COLUMN target VARCHAR(50) NOT NULL DEFAULT '{DEFAULT_FACTOR_TARGET}'"
            )
        )


def ensure_factor_frequency_column(bind) -> None:
    """Add the factor frequency column for existing SQLite databases."""
    inspector = inspect(bind)
    if "factors" not in inspector.get_table_names():
        return

    columns = {column["name"] for column in inspector.get_columns("factors")}
    if "frequency" in columns:
        return

    with bind.begin() as conn:
        conn.execute(
            text(
                "ALTER TABLE factors "
                f"ADD COLUMN frequency VARCHAR(20) NOT NULL DEFAULT '{DEFAULT_FREQUENCY}'"
            )
        )


def reset_legacy_factor_cache_tables(bind) -> None:
    """Drop old cache tables that cannot represent intraday bars.

    Cache data is explicitly disposable; factor definitions are preserved.
    """
    required_columns = {
        "factor_value_cache": {"factor_id", "factor_code_hash", "stock_code", "frequency", "adjust", "bar_time", "value"},
        "target_return_cache": {"target", "stock_code", "frequency", "adjust", "bar_time", "value"},
        "factor_performance_bar_cache": {
            "factor_id", "factor_code_hash", "stock_pool_key", "stock_pool_snapshot_hash",
            "target", "frequency", "adjust", "bar_time", "metric_version",
        },
    }
    inspector = inspect(bind)
    existing_tables = set(inspector.get_table_names())
    tables_to_reset = []

    for table_name, columns in required_columns.items():
        if table_name not in existing_tables:
            continue
        existing_columns = {column["name"] for column in inspector.get_columns(table_name)}
        if not columns.issubset(existing_columns) or "trade_date" in existing_columns:
            tables_to_reset.append(table_name)

    if not tables_to_reset:
        return

    with bind.begin() as conn:
        for table_name in tables_to_reset:
            conn.execute(text(f"DROP TABLE IF EXISTS {table_name}"))

    for table_name in tables_to_reset:
        Base.metadata.tables[table_name].create(bind=bind, checkfirst=True)


def ensure_cache_query_indexes(bind) -> None:
    """Add composite indexes for the hottest cache query patterns."""
    inspector = inspect(bind)
    existing_indexes = {
        index["name"]
        for table_name in inspector.get_table_names()
        for index in inspector.get_indexes(table_name)
        if index.get("name")
    }
    index_sql = {
        "idx_perf_rank_query": (
            "CREATE INDEX idx_perf_rank_query ON factor_performance_bar_cache "
            "(stock_pool_key, stock_pool_snapshot_hash, target, frequency, adjust, metric_version, bar_time, factor_id)"
        ),
    }
    with bind.begin() as conn:
        for name, sql in index_sql.items():
            if name not in existing_indexes:
                conn.execute(text(sql))


def configure_sqlite_runtime(bind) -> None:
    """Use WAL-friendly settings for local API + background worker access."""
    if not str(settings.DATABASE_URL).startswith("sqlite"):
        return
    with bind.connect() as conn:
        conn.execute(text("PRAGMA journal_mode=WAL"))
        conn.execute(text("PRAGMA synchronous=NORMAL"))
        conn.execute(text("PRAGMA busy_timeout=30000"))


@contextmanager
def get_db() -> Generator[Session, None, None]:
    """获取数据库会话的上下文管理器"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_db_session() -> Session:
    """获取数据库会话（非上下文管理器方式）"""
    return SessionLocal()
