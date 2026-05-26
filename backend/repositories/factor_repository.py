"""
因子数据访问层
"""
from datetime import date, datetime
from typing import List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import select, update, delete, func

from backend.core.factor_targets import (
    DEFAULT_FACTOR_TARGET,
    DEFAULT_FREQUENCY,
    list_factor_targets,
    validate_frequency,
)
from backend.models.factor import (
    FactorModel,
    AnalysisCacheModel,
    FactorValueCacheModel,
    TargetReturnCacheModel,
)
from backend.models.factor_performance import (
    FactorPerformanceBarCacheModel,
    METRIC_VERSION,
)
import hashlib
import json
import numpy as np


def _to_datetime(value) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    value_str = str(value)
    if "T" in value_str:
        value_str = value_str.replace("T", " ")
    if len(value_str) <= 10:
        return datetime.strptime(value_str[:10], "%Y-%m-%d")
    return datetime.strptime(value_str[:19], "%Y-%m-%d %H:%M:%S")


def _to_datetime_end(value) -> datetime:
    """Parse an end bound; date-only values include the whole day."""
    if isinstance(value, datetime):
        return value
    value_str = str(value)
    if "T" in value_str:
        value_str = value_str.replace("T", " ")
    if len(value_str) <= 10:
        return datetime.strptime(value_str[:10], "%Y-%m-%d").replace(
            hour=23, minute=59, second=59, microsecond=999999
        )
    return datetime.strptime(value_str[:19], "%Y-%m-%d %H:%M:%S")


class FactorRepository:
    """因子数据访问类"""

    def __init__(self, db: Session):
        self.db = db

    def get_all(
        self,
        source: Optional[str] = None,
        active_only: bool = False,
        target: Optional[str] = None,
        frequency: Optional[str] = None,
    ) -> List[FactorModel]:
        """获取所有因子。

        target/frequency are accepted for backward-compatible callers, but
        factor definitions are intentionally not scoped to a target or bar
        frequency. Those dimensions belong to evaluation and cached values.
        """
        query = select(FactorModel)
        if source:
            query = query.where(FactorModel.source == source)
        if active_only:
            query = query.where(FactorModel.is_active == 1)
        query = query.order_by(FactorModel.frequency, FactorModel.target, FactorModel.category, FactorModel.name)
        return list(self.db.scalars(query).all())

    def get_by_id(self, factor_id: int) -> Optional[FactorModel]:
        """根据ID获取因子"""
        return self.db.get(FactorModel, factor_id)

    def get_by_name(self, name: str, include_inactive: bool = False) -> Optional[FactorModel]:
        """根据名称获取因子

        Args:
            name: 因子名称
            include_inactive: 是否包含已删除的因子（is_active=0）
        """
        query = select(FactorModel).where(FactorModel.name == name)
        if not include_inactive:
            query = query.where(FactorModel.is_active == 1)
        return self.db.scalar(query)

    def get_active_by_name(self, name: str) -> Optional[FactorModel]:
        """根据名称获取活跃因子（仅返回 is_active=1 的记录）"""
        return self.db.scalar(
            select(FactorModel)
            .where(FactorModel.name == name)
            .where(FactorModel.is_active == 1)
        )

    def create(self, factor: FactorModel) -> FactorModel:
        """创建因子"""
        self.db.add(factor)
        self.db.commit()
        self.db.refresh(factor)
        return factor

    def update(self, factor: FactorModel) -> FactorModel:
        """更新因子"""
        self.db.commit()
        self.db.refresh(factor)
        return factor

    def delete(self, factor_id: int) -> bool:
        """删除因子（硬删除，从数据库中完全移除，仅限用户自定义因子）"""

        factor = self.get_by_id(factor_id)
        if not factor:
            return False
        if factor.source == "preset":
            raise ValueError("预置因子不能删除")

        # 硬删除：直接从数据库中移除记录
        self.db.delete(factor)
        self.db.commit()

        return True

    def get_preset_count(self) -> int:
        """获取预置因子数量（仅统计启用的）"""
        return self.db.scalar(
            select(func.count(FactorModel.id))
            .where(FactorModel.source == "preset")
            .where(FactorModel.is_active == 1)
        ) or 0

    def get_user_count(self) -> int:
        """获取用户自定义因子数量（仅统计启用的）"""
        return self.db.scalar(
            select(func.count(FactorModel.id))
            .where(FactorModel.source == "user")
            .where(FactorModel.is_active == 1)
        ) or 0

    def count_by_target(self) -> dict:
        """按目标统计启用因子数量"""
        rows = self.db.execute(
            select(FactorModel.target, func.count(FactorModel.id))
            .where(FactorModel.is_active == 1)
            .group_by(FactorModel.target)
        ).all()
        counts = {target.key: 0 for target in list_factor_targets()}
        counts.update({target or DEFAULT_FACTOR_TARGET: count for target, count in rows})
        return counts


class AnalysisCacheRepository:
    """分析结果缓存数据访问类"""

    def __init__(self, db: Session):
        self.db = db

    def get_by_key(self, cache_key: str) -> Optional[AnalysisCacheModel]:
        """根据缓存键获取缓存"""
        return self.db.scalar(select(AnalysisCacheModel).where(AnalysisCacheModel.cache_key == cache_key))

    def create(self, cache: AnalysisCacheModel) -> AnalysisCacheModel:
        """创建缓存"""
        self.db.add(cache)
        self.db.commit()
        self.db.refresh(cache)
        return cache

    def update(self, cache: AnalysisCacheModel) -> AnalysisCacheModel:
        """更新缓存"""
        self.db.commit()
        self.db.refresh(cache)
        return cache

    def delete(self, cache_id: int) -> bool:
        """删除缓存"""
        cache = self.db.get(AnalysisCacheModel, cache_id)
        if not cache:
            return False
        self.db.delete(cache)
        self.db.commit()
        return True

    def delete_old_cache(self, days: int = 7) -> int:
        """删除旧缓存"""
        from datetime import datetime, timedelta
        cutoff_date = datetime.now() - timedelta(days=days)
        stmt = delete(AnalysisCacheModel).where(AnalysisCacheModel.created_at < cutoff_date)
        result = self.db.execute(stmt)
        self.db.commit()
        return result.rowcount


class FactorValueCacheRepository:
    """因子值缓存数据访问类"""

    def __init__(self, db: Session):
        self.db = db

    def upsert_values(
        self,
        factor_id: int,
        factor_code_hash: str,
        stock_code: str,
        values: list[dict],
        frequency: str = DEFAULT_FREQUENCY,
        force: bool = False,
    ) -> int:
        """批量写入因子值。默认保留已有缓存，force=True 时覆盖。"""
        from sqlalchemy.exc import IntegrityError
        frequency_key = validate_frequency(frequency)
        written = 0
        for item in values:
            bar_time = _to_datetime(item.get("bar_time", item.get("trade_date")))
            existing = self.db.scalar(
                select(FactorValueCacheModel)
                .where(FactorValueCacheModel.factor_id == factor_id)
                .where(FactorValueCacheModel.factor_code_hash == factor_code_hash)
                .where(FactorValueCacheModel.stock_code == stock_code)
                .where(FactorValueCacheModel.frequency == frequency_key)
                .where(FactorValueCacheModel.bar_time == bar_time)
            )
            if existing:
                if force:
                    existing.value = item.get("value")
                    written += 1
                continue
            try:
                with self.db.begin_nested():
                    self.db.add(
                        FactorValueCacheModel(
                            factor_id=factor_id,
                            factor_code_hash=factor_code_hash,
                            stock_code=stock_code,
                            frequency=frequency_key,
                            bar_time=bar_time,
                            value=item.get("value"),
                        )
                    )
                    self.db.flush()
                    written += 1
            except IntegrityError:
                pass
        self.db.commit()
        return written

    def get_values(
        self,
        factor_id: int,
        factor_code_hash: str,
        stock_code: str,
        start_date: str,
        end_date: str,
        frequency: str = DEFAULT_FREQUENCY,
    ) -> list[dict]:
        """按因子定义、股票和日期范围读取因子值缓存"""
        frequency_key = validate_frequency(frequency)
        rows = self.db.scalars(
            select(FactorValueCacheModel)
            .where(FactorValueCacheModel.factor_id == factor_id)
            .where(FactorValueCacheModel.factor_code_hash == factor_code_hash)
            .where(FactorValueCacheModel.stock_code == stock_code)
            .where(FactorValueCacheModel.frequency == frequency_key)
            .where(FactorValueCacheModel.bar_time >= _to_datetime(start_date))
            .where(FactorValueCacheModel.bar_time <= _to_datetime_end(end_date))
            .order_by(FactorValueCacheModel.bar_time)
        ).all()
        return [row.to_dict() for row in rows]

    def delete_for_factor(self, factor_id: int, factor_code_hash: Optional[str] = None) -> int:
        """删除某个因子的缓存，可限定到某个代码 hash"""
        stmt = delete(FactorValueCacheModel).where(FactorValueCacheModel.factor_id == factor_id)
        if factor_code_hash:
            stmt = stmt.where(FactorValueCacheModel.factor_code_hash == factor_code_hash)
        result = self.db.execute(stmt)
        self.db.commit()
        return result.rowcount


class TargetReturnCacheRepository:
    """目标收益缓存数据访问类"""

    def __init__(self, db: Session):
        self.db = db

    def upsert_values(
        self,
        target: str,
        stock_code: str,
        values: list[dict],
        frequency: str = DEFAULT_FREQUENCY,
        force: bool = False,
    ) -> int:
        """批量写入 target 收益。默认保留已有缓存，force=True 时覆盖。"""
        from sqlalchemy.exc import IntegrityError
        frequency_key = validate_frequency(frequency)
        written = 0
        for item in values:
            bar_time = _to_datetime(item.get("bar_time", item.get("trade_date")))
            existing = self.db.scalar(
                select(TargetReturnCacheModel)
                .where(TargetReturnCacheModel.target == target)
                .where(TargetReturnCacheModel.stock_code == stock_code)
                .where(TargetReturnCacheModel.frequency == frequency_key)
                .where(TargetReturnCacheModel.bar_time == bar_time)
            )
            if existing:
                if force:
                    existing.value = item.get("value")
                    written += 1
                continue
            try:
                with self.db.begin_nested():
                    self.db.add(
                        TargetReturnCacheModel(
                            target=target,
                            stock_code=stock_code,
                            frequency=frequency_key,
                            bar_time=bar_time,
                            value=item.get("value"),
                        )
                    )
                    self.db.flush()
                    written += 1
            except IntegrityError:
                pass
        self.db.commit()
        return written

    def get_values(
        self,
        target: str,
        stock_code: str,
        start_date: str,
        end_date: str,
        frequency: str = DEFAULT_FREQUENCY,
    ) -> list[dict]:
        """按目标、股票和日期范围读取 target 收益缓存"""
        frequency_key = validate_frequency(frequency)
        rows = self.db.scalars(
            select(TargetReturnCacheModel)
            .where(TargetReturnCacheModel.target == target)
            .where(TargetReturnCacheModel.stock_code == stock_code)
            .where(TargetReturnCacheModel.frequency == frequency_key)
            .where(TargetReturnCacheModel.bar_time >= _to_datetime(start_date))
            .where(TargetReturnCacheModel.bar_time <= _to_datetime_end(end_date))
            .order_by(TargetReturnCacheModel.bar_time)
        ).all()
        return [row.to_dict() for row in rows]

    def delete_for_target(self, target: str, stock_code: Optional[str] = None) -> int:
        """删除某个 target 的收益缓存，可限定到某只股票"""
        stmt = delete(TargetReturnCacheModel).where(TargetReturnCacheModel.target == target)
        if stock_code:
            stmt = stmt.where(TargetReturnCacheModel.stock_code == stock_code)
        result = self.db.execute(stmt)
        self.db.commit()
        return result.rowcount


def hash_snapshot(stock_codes: list[str]) -> str:
    """Hash stock pool snapshot for cache keying."""
    payload = json.dumps(sorted(stock_codes), ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class FactorPerformanceCacheRepository:
    """因子表现原子指标缓存数据访问类"""

    def __init__(self, db: Session):
        self.db = db

    def upsert_bar(
        self,
        factor_id: int,
        factor_name: str,
        factor_code_hash: str,
        stock_pool_key: str,
        stock_pool_snapshot_hash: str,
        target: str,
        frequency: str,
        bar_time: datetime,
        ic_value: Optional[float],
        sample_size: int,
        coverage: float,
        status: str = "completed",
        error_message: Optional[str] = None,
    ) -> FactorPerformanceBarCacheModel:
        """Upsert a single bar's IC record."""
        existing = self.db.scalar(
            select(FactorPerformanceBarCacheModel)
            .where(FactorPerformanceBarCacheModel.factor_id == factor_id)
            .where(FactorPerformanceBarCacheModel.factor_code_hash == factor_code_hash)
            .where(FactorPerformanceBarCacheModel.stock_pool_key == stock_pool_key)
            .where(FactorPerformanceBarCacheModel.stock_pool_snapshot_hash == stock_pool_snapshot_hash)
            .where(FactorPerformanceBarCacheModel.target == target)
            .where(FactorPerformanceBarCacheModel.frequency == frequency)
            .where(FactorPerformanceBarCacheModel.bar_time == bar_time)
            .where(FactorPerformanceBarCacheModel.metric_version == METRIC_VERSION)
        )
        if existing:
            existing.ic_value = ic_value
            existing.sample_size = sample_size
            existing.coverage = coverage
            existing.status = status
            existing.error_message = error_message
            existing.updated_at = datetime.now()
            self.db.commit()
            return existing

        record = FactorPerformanceBarCacheModel(
            factor_id=factor_id,
            factor_name=factor_name,
            factor_code_hash=factor_code_hash,
            stock_pool_key=stock_pool_key,
            stock_pool_snapshot_hash=stock_pool_snapshot_hash,
            target=target,
            frequency=frequency,
            bar_time=bar_time,
            metric_version=METRIC_VERSION,
            ic_value=ic_value,
            sample_size=sample_size,
            coverage=coverage,
            status=status,
            error_message=error_message,
        )
        self.db.add(record)
        self.db.commit()
        return record

    def get_cached_bars(
        self,
        factor_id: int,
        stock_pool_key: str,
        stock_pool_snapshot_hash: str,
        target: str,
        frequency: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> list[dict]:
        """Query cached performance bars for one factor."""
        query = (
            select(FactorPerformanceBarCacheModel)
            .where(FactorPerformanceBarCacheModel.factor_id == factor_id)
            .where(FactorPerformanceBarCacheModel.stock_pool_key == stock_pool_key)
            .where(FactorPerformanceBarCacheModel.stock_pool_snapshot_hash == stock_pool_snapshot_hash)
            .where(FactorPerformanceBarCacheModel.target == target)
            .where(FactorPerformanceBarCacheModel.frequency == frequency)
            .where(FactorPerformanceBarCacheModel.metric_version == METRIC_VERSION)
        )
        if start_date:
            query = query.where(FactorPerformanceBarCacheModel.bar_time >= _to_datetime(start_date))
        if end_date:
            query = query.where(FactorPerformanceBarCacheModel.bar_time <= _to_datetime_end(end_date))
        query = query.order_by(FactorPerformanceBarCacheModel.bar_time)
        return [row.to_dict() for row in self.db.scalars(query).all()]

    def get_all_rankings(
        self,
        stock_pool_key: str,
        stock_pool_snapshot_hash: str,
        target: str,
        frequency: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> list[dict]:
        """Query and aggregate factor rankings from cached bars."""
        query = (
            select(
                FactorPerformanceBarCacheModel,
                FactorModel.category,
                FactorModel.source,
            )
            .join(FactorModel, FactorModel.id == FactorPerformanceBarCacheModel.factor_id, isouter=True)
            .where(FactorPerformanceBarCacheModel.stock_pool_key == stock_pool_key)
            .where(FactorPerformanceBarCacheModel.stock_pool_snapshot_hash == stock_pool_snapshot_hash)
            .where(FactorPerformanceBarCacheModel.target == target)
            .where(FactorPerformanceBarCacheModel.frequency == frequency)
            .where(FactorPerformanceBarCacheModel.metric_version == METRIC_VERSION)
        )
        if start_date:
            query = query.where(FactorPerformanceBarCacheModel.bar_time >= _to_datetime(start_date))
        if end_date:
            query = query.where(FactorPerformanceBarCacheModel.bar_time <= _to_datetime_end(end_date))
        query = query.order_by(FactorPerformanceBarCacheModel.factor_id, FactorPerformanceBarCacheModel.bar_time)

        rows = self.db.execute(query).all()
        grouped: dict[int, dict] = {}
        for record, category, source in rows:
            item = grouped.setdefault(
                record.factor_id,
                {
                    "factor_id": record.factor_id,
                    "factor_name": record.factor_name,
                    "category": category or "",
                    "source": source or "",
                    "ic_values": [],
                    "completed_sample_sizes": [],
                    "completed_coverages": [],
                    "status_counts": {},
                    "error_message": None,
                    "last_updated_at": None,
                    "first_completed_bar_time": None,
                    "last_completed_bar_time": None,
                },
            )
            if record.ic_value is not None and record.status == "completed":
                item["ic_values"].append(float(record.ic_value))
                item["completed_sample_sizes"].append(record.sample_size)
                item["completed_coverages"].append(record.coverage or 0.0)
                if item["first_completed_bar_time"] is None or record.bar_time < item["first_completed_bar_time"]:
                    item["first_completed_bar_time"] = record.bar_time
                if item["last_completed_bar_time"] is None or record.bar_time > item["last_completed_bar_time"]:
                    item["last_completed_bar_time"] = record.bar_time
            item["status_counts"][record.status] = item["status_counts"].get(record.status, 0) + 1
            if record.error_message and not item["error_message"]:
                item["error_message"] = record.error_message
            if record.updated_at and (
                item["last_updated_at"] is None or record.updated_at > item["last_updated_at"]
            ):
                item["last_updated_at"] = record.updated_at

        results = []
        for item in grouped.values():
            ic_values = item.pop("ic_values")
            sample_sizes = item.pop("completed_sample_sizes")
            coverages = item.pop("completed_coverages")
            status_counts = item.pop("status_counts")
            first_completed_bar_time = item.pop("first_completed_bar_time")
            last_completed_bar_time = item.pop("last_completed_bar_time")
            completed_count = status_counts.get("completed", 0)
            if completed_count:
                status = "completed"
            elif status_counts.get("failed", 0):
                status = "failed"
            elif status_counts.get("insufficient_data", 0):
                status = "insufficient_data"
            else:
                status = "pending"

            ic_mean = float(sum(ic_values) / len(ic_values)) if ic_values else None
            ic_std = float(np.std(ic_values, ddof=1)) if len(ic_values) > 1 else 0.0 if ic_values else None
            ir = ic_mean / ic_std if ic_mean is not None and ic_std else None
            positive_ratio = (
                sum(1 for value in ic_values if value > 0) / len(ic_values)
                if ic_values
                else None
            )
            results.append({
                **item,
                "ic_mean": round(ic_mean, 6) if ic_mean is not None else None,
                "ic_std": round(ic_std, 6) if ic_std is not None else None,
                "ir": round(ir, 6) if ir is not None else None,
                "ic_positive_ratio": round(positive_ratio, 4) if positive_ratio is not None else None,
                "bar_count": len(ic_values),
                "total_bar_count": sum(status_counts.values()),
                "sample_size": round(float(sum(sample_sizes) / len(sample_sizes)), 1) if sample_sizes else None,
                "coverage": round(float(sum(coverages) / len(coverages)), 4) if coverages else None,
                "status": status,
                "status_counts": status_counts,
                "first_bar_time": first_completed_bar_time.isoformat() if first_completed_bar_time else None,
                "last_bar_time": last_completed_bar_time.isoformat() if last_completed_bar_time else None,
                "last_updated_at": item["last_updated_at"].isoformat() if item["last_updated_at"] else None,
            })
        return results

    def get_cache_summary(
        self,
        stock_pool_key: str,
        stock_pool_snapshot_hash: str,
        target: str,
        frequency: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> dict:
        """Summary of cache coverage across all factors."""
        query = (
            select(
                FactorPerformanceBarCacheModel.status,
                func.count(func.distinct(FactorPerformanceBarCacheModel.id)).label("count"),
                func.count(func.distinct(FactorPerformanceBarCacheModel.factor_id)).label("factor_count"),
            )
            .where(FactorPerformanceBarCacheModel.stock_pool_key == stock_pool_key)
            .where(FactorPerformanceBarCacheModel.stock_pool_snapshot_hash == stock_pool_snapshot_hash)
            .where(FactorPerformanceBarCacheModel.target == target)
            .where(FactorPerformanceBarCacheModel.frequency == frequency)
            .where(FactorPerformanceBarCacheModel.metric_version == METRIC_VERSION)
        )
        if start_date:
            query = query.where(FactorPerformanceBarCacheModel.bar_time >= _to_datetime(start_date))
        if end_date:
            query = query.where(FactorPerformanceBarCacheModel.bar_time <= _to_datetime_end(end_date))
        rows = self.db.execute(query.group_by(FactorPerformanceBarCacheModel.status)).all()

        summary = {"completed": 0, "failed": 0, "insufficient_data": 0, "pending": 0, "total_bars": 0}
        for row in rows:
            summary[row.status] = row.count
            summary["total_bars"] += row.count
        return summary

    def get_problem_factor_summary(
        self,
        stock_pool_key: str,
        stock_pool_snapshot_hash: str,
        target: str,
        frequency: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        limit: int = 20,
    ) -> list[dict]:
        """Top factors with failed or insufficient-data bars."""
        query = (
            select(
                FactorPerformanceBarCacheModel.factor_id,
                FactorPerformanceBarCacheModel.factor_name,
                FactorPerformanceBarCacheModel.status,
                FactorPerformanceBarCacheModel.error_message,
                func.count(FactorPerformanceBarCacheModel.id).label("count"),
            )
            .where(FactorPerformanceBarCacheModel.stock_pool_key == stock_pool_key)
            .where(FactorPerformanceBarCacheModel.stock_pool_snapshot_hash == stock_pool_snapshot_hash)
            .where(FactorPerformanceBarCacheModel.target == target)
            .where(FactorPerformanceBarCacheModel.frequency == frequency)
            .where(FactorPerformanceBarCacheModel.metric_version == METRIC_VERSION)
            .where(FactorPerformanceBarCacheModel.status.in_(["failed", "insufficient_data"]))
        )
        if start_date:
            query = query.where(FactorPerformanceBarCacheModel.bar_time >= _to_datetime(start_date))
        if end_date:
            query = query.where(FactorPerformanceBarCacheModel.bar_time <= _to_datetime_end(end_date))
        rows = self.db.execute(
            query.group_by(
                FactorPerformanceBarCacheModel.factor_id,
                FactorPerformanceBarCacheModel.factor_name,
                FactorPerformanceBarCacheModel.status,
                FactorPerformanceBarCacheModel.error_message,
            )
            .order_by(func.count(FactorPerformanceBarCacheModel.id).desc())
            .limit(limit)
        ).all()

        return [
            {
                "factor_id": row.factor_id,
                "factor_name": row.factor_name,
                "status": row.status,
                "count": row.count,
                "error_message": row.error_message,
            }
            for row in rows
        ]
