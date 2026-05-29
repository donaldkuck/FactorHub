"""
因子排名服务

负责逐 bar 计算截面 IC 并缓存，以及按因子聚合排名查询。
"""
from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any, Optional

import numpy as np
import pandas as pd

from backend.core.factor_targets import (
    validate_factor_target,
    validate_frequency,
)
from backend.core.stock_pools import get_stock_pool
from backend.repositories.factor_repository import (
    FactorRepository,
    FactorValueCacheRepository,
    TargetReturnCacheRepository,
    FactorPerformanceCacheRepository,
    hash_snapshot,
)
from backend.services.factor_dataset_service import (
    hash_factor_code,
    factor_dataset_service,
)

logger = logging.getLogger(__name__)


def _bar_time_to_str(bar_time: Any) -> str:
    """Convert bar_time to string suitable for DB date filtering."""
    if hasattr(bar_time, "strftime"):
        return bar_time.strftime("%Y-%m-%d %H:%M:%S")
    return str(bar_time).replace("T", " ")[:19]


def _finite_or_none(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if np.isnan(number) or np.isinf(number):
        return None
    return number


class FactorRankingService:
    """全量因子表现排名服务"""

    def compute_rankings(
        self,
        db,
        stock_pool_key: str,
        target: str,
        frequency: str,
        start_date: str,
        end_date: str,
        sort_by: str = "ic_mean",
        sort_order: str = "desc",
        page: int = 1,
        page_size: int = 50,
    ) -> dict:
        """查询因子排名（只读缓存）"""
        frequency_key = validate_frequency(frequency)
        target_key = validate_factor_target(target, frequency_key)
        pool = get_stock_pool(stock_pool_key, include_codes=True)
        snapshot_hash = hash_snapshot(pool["stock_codes"])

        repo = FactorPerformanceCacheRepository(db)
        rankings = repo.get_all_rankings(
            stock_pool_key=stock_pool_key,
            stock_pool_snapshot_hash=snapshot_hash,
            target=target_key,
            frequency=frequency_key,
            start_date=start_date,
            end_date=end_date,
        )

        # Sort
        rankings.sort(
            key=lambda r: r.get(sort_by, 0) or 0,
            reverse=(sort_order == "desc"),
        )

        # Paginate
        total = len(rankings)
        start = (page - 1) * page_size
        paged = rankings[start : start + page_size]

        # Attach rank
        for i, item in enumerate(paged):
            item["rank"] = start + i + 1

        # Cache summary
        cache_summary = repo.get_cache_summary(
            stock_pool_key=stock_pool_key,
            stock_pool_snapshot_hash=snapshot_hash,
            target=target_key,
            frequency=frequency_key,
            start_date=start_date,
            end_date=end_date,
        )
        problem_factors = repo.get_problem_factor_summary(
            stock_pool_key=stock_pool_key,
            stock_pool_snapshot_hash=snapshot_hash,
            target=target_key,
            frequency=frequency_key,
            start_date=start_date,
            end_date=end_date,
        )

        return {
            "items": paged,
            "total": total,
            "page": page,
            "page_size": page_size,
            "cache_summary": cache_summary,
            "problem_factors": problem_factors,
        }

    def refresh_rankings(
        self,
        db,
        stock_pool_key: str,
        target: str,
        frequency: str,
        start_date: str,
        end_date: str,
        factor_ids: Optional[list[int]] = None,
        force: bool = False,
        retry_statuses: Optional[list[str]] = None,
        progress_callback=None,
        max_runtime_seconds: float = 240,
        max_factor_seconds: float = 45,
    ) -> dict:
        """补齐缺失缓存：逐因子逐 bar 计算截面 IC"""
        frequency_key = validate_frequency(frequency)
        target_key = validate_factor_target(target, frequency_key)
        pool = get_stock_pool(stock_pool_key, include_codes=True)
        stock_codes = pool["stock_codes"]
        snapshot_hash = hash_snapshot(stock_codes)

        if not stock_codes:
            raise ValueError(f"股票池 {pool.get('label', stock_pool_key)} 为空")

        started_at = time.monotonic()
        stopped_reason: Optional[str] = None

        def time_exceeded() -> bool:
            return time.monotonic() - started_at > max_runtime_seconds

        # Get factors to process
        factor_repo = FactorRepository(db)
        if factor_ids:
            factors = [factor_repo.get_by_id(fid) for fid in factor_ids]
            factors = [f for f in factors if f is not None]
        else:
            factors = factor_repo.get_all(active_only=True)

        if not factors:
            raise ValueError("没有可用因子")

        perf_repo = FactorPerformanceCacheRepository(db)
        factor_value_repo = FactorValueCacheRepository(db)
        target_return_repo = TargetReturnCacheRepository(db)

        # Backfill target returns once (same for all factors)
        factor_dataset_service.backfill_target_returns(
            db=db,
            target=target_key,
            stock_codes=stock_codes,
            start_date=start_date,
            end_date=end_date,
            frequency=frequency_key,
            force=force,
        )

        all_target_bar_times: set[datetime] = set()
        for stock_code in stock_codes:
            rows = target_return_repo.get_values(target_key, stock_code, start_date, end_date, frequency_key)
            for row in rows:
                bt = row["bar_time"]
                if isinstance(bt, str):
                    bt = pd.Timestamp(bt).to_pydatetime()
                all_target_bar_times.add(bt)
        target_bar_times = sorted(all_target_bar_times)

        total_combos = len(factors) * len(target_bar_times)
        cache_hits = 0
        computed_items = 0
        failed_items = 0
        skipped_items = 0

        def report(current_factor: Optional[str] = None):
            if progress_callback:
                progress_callback(
                    {
                        "total_items": total_combos,
                        "cache_hits": cache_hits,
                        "computed_items": computed_items,
                        "failed_items": failed_items,
                        "skipped_items": skipped_items,
                        "current_factor": current_factor,
                    }
                )

        report()

        for factor in factors:
            if time_exceeded():
                stopped_reason = f"本轮补齐达到 {max_runtime_seconds:.0f} 秒时间预算，可再次点击补齐继续"
                break

            code_hash = hash_factor_code(factor.code)
            report(factor.name)

            existing_bars = perf_repo.get_cached_bars(
                factor_id=factor.id,
                stock_pool_key=stock_pool_key,
                stock_pool_snapshot_hash=snapshot_hash,
                target=target_key,
                frequency=frequency_key,
                start_date=start_date,
                end_date=end_date,
            )
            completed_cache_keys = {
                _bar_time_to_str(row.get("bar_time"))
                for row in existing_bars
                if row.get("factor_code_hash") == code_hash and row.get("status") == "completed"
            }
            status_by_bar_key = {
                _bar_time_to_str(row.get("bar_time")): row.get("status")
                for row in existing_bars
                if row.get("factor_code_hash") == code_hash
            }
            if not force and len(completed_cache_keys) >= len(target_bar_times):
                cache_hits += len(target_bar_times)
                report(factor.name)
                continue

            retry_bar_keys = None
            if retry_statuses:
                retry_bar_keys = {
                    _bar_time_to_str(row.get("bar_time"))
                    for row in existing_bars
                    if row.get("factor_code_hash") == code_hash
                    and row.get("status") in retry_statuses
                }
                if not retry_bar_keys:
                    skipped_items += len(target_bar_times)
                    report(factor.name)
                    continue

            try:
                # Backfill factor values (per-factor, since factor code differs)
                factor_dataset_service.backfill_factor_values(
                    db=db,
                    factor_id=factor.id,
                    stock_codes=stock_codes,
                    start_date=start_date,
                    end_date=end_date,
                    frequency=frequency_key,
                    force=force,
                    max_seconds=max_factor_seconds,
                )
            except TimeoutError as e:
                logger.warning(f"Factor {factor.name} backfill timed out: {e}")
                stopped_reason = (
                    f"因子 {factor.name} 回填超过 {max_factor_seconds:.0f} 秒，"
                    "本轮已暂停，未覆盖已有排名缓存"
                )
                skipped_items += len(target_bar_times)
                report(factor.name)
                break
            except Exception as e:
                logger.warning(f"Factor {factor.name} backfill failed: {e}")
                if target_bar_times:
                    for bar_time in target_bar_times:
                        perf_repo.upsert_bar(
                            factor_id=factor.id,
                            factor_name=factor.name,
                            factor_code_hash=code_hash,
                            stock_pool_key=stock_pool_key,
                            stock_pool_snapshot_hash=snapshot_hash,
                            target=target_key,
                            frequency=frequency_key,
                            bar_time=bar_time,
                            ic_value=None,
                            sample_size=0,
                            coverage=0.0,
                            status="failed",
                            error_message=str(e),
                        )
                    failed_items += len(target_bar_times)
                else:
                    failed_items += 1
                report(factor.name)
                continue

            factor_value_by_stock: dict[str, dict[str, float]] = {}
            target_value_by_stock: dict[str, dict[str, float]] = {}
            for stock_code in stock_codes:
                factor_value_by_stock[stock_code] = {
                    _bar_time_to_str(row.get("bar_time")): row.get("value")
                    for row in factor_value_repo.get_values(
                        factor.id, code_hash, stock_code, start_date, end_date, frequency_key
                    )
                }
                target_value_by_stock[stock_code] = {
                    _bar_time_to_str(row.get("bar_time")): row.get("value")
                    for row in target_return_repo.get_values(
                        target_key, stock_code, start_date, end_date, frequency_key
                    )
                }

            for bar_time in target_bar_times:
                if time_exceeded():
                    stopped_reason = f"本轮补齐达到 {max_runtime_seconds:.0f} 秒时间预算，可再次点击补齐继续"
                    break

                bt_str = _bar_time_to_str(bar_time)

                if retry_bar_keys is not None and bt_str not in retry_bar_keys:
                    skipped_items += 1
                    report(factor.name)
                    continue

                if bt_str in completed_cache_keys and not force:
                    cache_hits += 1
                    report(factor.name)
                    continue

                if (
                    not force
                    and retry_bar_keys is None
                    and status_by_bar_key.get(bt_str) == "failed"
                ):
                    skipped_items += 1
                    report(factor.name)
                    continue

                # Compute cross-sectional IC for this bar
                factor_vals = []
                target_vals = []
                for stock_code in stock_codes:
                    fv = _finite_or_none(factor_value_by_stock.get(stock_code, {}).get(bt_str))
                    tv = _finite_or_none(target_value_by_stock.get(stock_code, {}).get(bt_str))
                    if fv is not None and tv is not None:
                        factor_vals.append(fv)
                        target_vals.append(tv)

                sample_size = len(factor_vals)
                total_pool_size = len(stock_codes)
                coverage = sample_size / total_pool_size if total_pool_size > 0 else 0.0

                if sample_size < 2:
                    perf_repo.upsert_bar(
                        factor_id=factor.id,
                        factor_name=factor.name,
                        factor_code_hash=code_hash,
                        stock_pool_key=stock_pool_key,
                        stock_pool_snapshot_hash=snapshot_hash,
                        target=target_key,
                        frequency=frequency_key,
                        bar_time=bar_time,
                        ic_value=None,
                        sample_size=sample_size,
                        coverage=coverage,
                        status="insufficient_data",
                        error_message=f"有效样本仅 {sample_size}，至少需要 2",
                    )
                    computed_items += 1
                    report(factor.name)
                    continue

                try:
                    ic = float(np.corrcoef(factor_vals, target_vals)[0, 1])
                    if np.isnan(ic) or np.isinf(ic):
                        ic = 0.0

                    perf_repo.upsert_bar(
                        factor_id=factor.id,
                        factor_name=factor.name,
                        factor_code_hash=code_hash,
                        stock_pool_key=stock_pool_key,
                        stock_pool_snapshot_hash=snapshot_hash,
                        target=target_key,
                        frequency=frequency_key,
                        bar_time=bar_time,
                        ic_value=ic,
                        sample_size=sample_size,
                        coverage=coverage,
                        status="completed",
                    )
                    computed_items += 1
                    report(factor.name)
                except Exception as e:
                    logger.warning(f"Factor {factor.name} bar {bt_str} IC failed: {e}")
                    perf_repo.upsert_bar(
                        factor_id=factor.id,
                        factor_name=factor.name,
                        factor_code_hash=code_hash,
                        stock_pool_key=stock_pool_key,
                        stock_pool_snapshot_hash=snapshot_hash,
                        target=target_key,
                        frequency=frequency_key,
                        bar_time=bar_time,
                        ic_value=None,
                        sample_size=sample_size,
                        coverage=coverage,
                        status="failed",
                        error_message=str(e),
                    )
                    failed_items += 1
                    report(factor.name)

            if stopped_reason:
                break

        return {
            "status": "partial" if stopped_reason else "completed",
            "stopped_reason": stopped_reason,
            "total_combos": total_combos,
            "cache_hits": cache_hits,
            "computed_items": computed_items,
            "failed_items": failed_items,
            "skipped_items": skipped_items,
            "stock_pool_key": stock_pool_key,
            "target": target_key,
            "frequency": frequency_key,
            "start_date": start_date,
            "end_date": end_date,
            "factor_count": len(factors),
        }


factor_ranking_service = FactorRankingService()
