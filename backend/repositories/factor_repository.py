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
            written += 1
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
            .where(FactorValueCacheModel.bar_time <= _to_datetime(end_date))
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
            self.db.add(
                TargetReturnCacheModel(
                    target=target,
                    stock_code=stock_code,
                    frequency=frequency_key,
                    bar_time=bar_time,
                    value=item.get("value"),
                )
            )
            written += 1
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
            .where(TargetReturnCacheModel.bar_time <= _to_datetime(end_date))
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
