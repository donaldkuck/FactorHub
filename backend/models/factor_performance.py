"""
因子表现原子指标缓存模型

每个唯一的 (因子, 股票池快照, target, frequency, bar_time) 组合
存储一条截面 IC 原子记录，供排名聚合查询使用。
"""
from datetime import datetime
from sqlalchemy import String, Integer, Float, DateTime, UniqueConstraint, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.core.database import Base

METRIC_VERSION = 1


class FactorPerformanceBarCacheModel(Base):
    """因子表现原子指标缓存"""

    __tablename__ = "factor_performance_bar_cache"
    __table_args__ = (
        UniqueConstraint(
            "factor_id",
            "factor_code_hash",
            "stock_pool_key",
            "stock_pool_snapshot_hash",
            "target",
            "frequency",
            "bar_time",
            "metric_version",
            name="uq_factor_perf_bar",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    factor_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    factor_name: Mapped[str] = mapped_column(String(200), nullable=False)
    factor_code_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    stock_pool_key: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    stock_pool_snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    target: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    frequency: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    bar_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    metric_version: Mapped[int] = mapped_column(Integer, nullable=False, default=METRIC_VERSION)
    ic_value: Mapped[float] = mapped_column(Float, nullable=True)
    sample_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    coverage: Mapped[float] = mapped_column(Float, nullable=True, default=0.0)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending")
    error_message: Mapped[str] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)

    def to_dict(self) -> dict:
        """转换为字典"""
        return {
            "id": self.id,
            "factor_id": self.factor_id,
            "factor_name": self.factor_name,
            "factor_code_hash": self.factor_code_hash,
            "stock_pool_key": self.stock_pool_key,
            "stock_pool_snapshot_hash": self.stock_pool_snapshot_hash,
            "target": self.target,
            "frequency": self.frequency,
            "bar_time": self.bar_time.isoformat() if self.bar_time else None,
            "metric_version": self.metric_version,
            "ic_value": self.ic_value,
            "sample_size": self.sample_size,
            "coverage": self.coverage,
            "status": self.status,
            "error_message": self.error_message,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
