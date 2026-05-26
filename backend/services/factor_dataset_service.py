"""
因子值缓存与 target 收益数据集服务
"""
from __future__ import annotations

import hashlib
import time
from datetime import datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd

from backend.core.factor_targets import (
    DEFAULT_FREQUENCY,
    add_target_return_column,
    get_factor_target,
    validate_frequency,
    validate_factor_target,
)
from backend.repositories.factor_repository import (
    FactorRepository,
    FactorValueCacheRepository,
    TargetReturnCacheRepository,
)
from backend.services.data_service import data_service
from backend.services.factor_service import factor_service


def hash_factor_code(code: str) -> str:
    """Hash factor code so edited definitions never reuse stale values."""
    return hashlib.sha256((code or "").encode("utf-8")).hexdigest()


def _bar_time_str(value: Any) -> str:
    if hasattr(value, "strftime"):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return str(value).replace("T", " ")[:19]


def _extend_start(start_date: str, warmup_days: int) -> str:
    return (pd.to_datetime(start_date).to_pydatetime() - timedelta(days=warmup_days)).strftime("%Y-%m-%d %H:%M:%S")


def _extend_end_for_target(end_date: str, target: str) -> str:
    horizon = get_factor_target(target).horizon
    extra_days = max(horizon * 3 + 10, 20)
    return (pd.to_datetime(end_date).to_pydatetime() + timedelta(days=extra_days)).strftime("%Y-%m-%d %H:%M:%S")


def _end_bound(end_date: str) -> pd.Timestamp:
    value = str(end_date).replace("T", " ")
    if len(value) <= 10:
        return pd.to_datetime(value) + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1)
    return pd.to_datetime(value)


def _finite_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if np.isnan(number) or np.isinf(number):
        return None
    return number


class FactorDatasetService:
    """Backfill and join factor values with selected target returns."""

    def __init__(self):
        self.data_service = data_service
        self.factor_calculator = factor_service.calculator

    def backfill_factor_values(
        self,
        db,
        factor_id: int,
        stock_codes: list[str],
        start_date: str,
        end_date: str,
        frequency: str | None = None,
        force: bool = False,
        warmup_days: int = 252,
        max_seconds: float | None = None,
    ) -> dict:
        """Backfill factor values only; target labels are deliberately excluded."""
        factor = FactorRepository(db).get_by_id(factor_id)
        if not factor:
            raise ValueError("因子不存在")

        frequency_key = validate_frequency(frequency or DEFAULT_FREQUENCY)

        code_hash = hash_factor_code(factor.code)
        repo = FactorValueCacheRepository(db)
        written_count = 0
        stock_results = {}
        raw_start = _extend_start(start_date, warmup_days)
        started_at = time.monotonic()

        for stock_code in stock_codes:
            if max_seconds is not None and time.monotonic() - started_at > max_seconds:
                raise TimeoutError(
                    f"因子 {factor.name} 回填超过 {max_seconds:.0f} 秒，已跳过"
                )

            data = self.data_service.get_stock_bars(stock_code, raw_start, end_date, frequency=frequency_key)
            expected_index = data.loc[
                (data.index >= pd.to_datetime(start_date))
                & (data.index <= _end_bound(end_date))
            ].index
            expected_bar_times = {_bar_time_str(idx) for idx in expected_index}

            if not force:
                existing_rows = repo.get_values(
                    factor_id,
                    code_hash,
                    stock_code,
                    start_date,
                    end_date,
                    frequency_key,
                )
                existing_bar_times = {
                    _bar_time_str(row["bar_time"])
                    for row in existing_rows
                    if row.get("value") is not None
                }
                if expected_bar_times and expected_bar_times.issubset(existing_bar_times):
                    stock_results[stock_code] = {
                        "written_count": 0,
                        "cached_count": len(existing_rows),
                    }
                    continue

            values = self.factor_calculator.calculate(data, factor.code)
            if values is None:
                stock_results[stock_code] = {"written_count": 0, "error": "因子计算失败"}
                continue

            series = pd.Series(values, index=data.index)
            series = series.loc[
                (series.index >= pd.to_datetime(start_date))
                & (series.index <= _end_bound(end_date))
            ]
            payload = [
                {"bar_time": _bar_time_str(idx), "value": _finite_or_none(value)}
                for idx, value in series.dropna().items()
            ]
            written = repo.upsert_values(
                factor_id,
                code_hash,
                stock_code,
                payload,
                frequency=frequency_key,
                force=force,
            )
            written_count += written
            stock_results[stock_code] = {"written_count": written}

        return {
            "factor_id": factor.id,
            "factor_name": factor.name,
            "factor_code_hash": code_hash,
            "frequency": frequency_key,
            "start_date": start_date,
            "end_date": end_date,
            "written_count": written_count,
            "stocks": stock_results,
        }

    def backfill_target_returns(
        self,
        db,
        target: str,
        stock_codes: list[str],
        start_date: str,
        end_date: str,
        frequency: str = DEFAULT_FREQUENCY,
        force: bool = False,
    ) -> dict:
        """Backfill target-label returns only; factor identity is deliberately excluded."""
        frequency_key = validate_frequency(frequency)
        target_key = validate_factor_target(target, frequency_key)
        repo = TargetReturnCacheRepository(db)
        written_count = 0
        stock_results = {}
        raw_end = _extend_end_for_target(end_date, target_key)

        for stock_code in stock_codes:
            data = self.data_service.get_stock_bars(stock_code, start_date, raw_end, frequency=frequency_key)
            add_target_return_column(data, target_key)
            series = data[target_key].loc[
                (data.index >= pd.to_datetime(start_date))
                & (data.index <= _end_bound(end_date))
            ]
            payload = [
                {"bar_time": _bar_time_str(idx), "value": _finite_or_none(value)}
                for idx, value in series.dropna().items()
            ]
            written = repo.upsert_values(target_key, stock_code, payload, frequency=frequency_key, force=force)
            written_count += written
            stock_results[stock_code] = {"written_count": written}

        return {
            "target": target_key,
            "frequency": frequency_key,
            "start_date": start_date,
            "end_date": end_date,
            "written_count": written_count,
            "stocks": stock_results,
        }

    def ensure_dataset(
        self,
        db,
        factor_id: int,
        target: str,
        stock_codes: list[str],
        start_date: str,
        end_date: str,
        frequency: str = DEFAULT_FREQUENCY,
        force: bool = False,
    ) -> dict:
        """Ensure caches exist and return the explicit factor-value/target-label join."""
        factor = FactorRepository(db).get_by_id(factor_id)
        if not factor:
            raise ValueError("因子不存在")

        frequency_key = validate_frequency(frequency)
        target_key = validate_factor_target(target, frequency_key)
        self.backfill_factor_values(db, factor_id, stock_codes, start_date, end_date, frequency=frequency_key, force=force)
        self.backfill_target_returns(db, target_key, stock_codes, start_date, end_date, frequency=frequency_key, force=force)

        code_hash = hash_factor_code(factor.code)
        factor_repo = FactorValueCacheRepository(db)
        target_repo = TargetReturnCacheRepository(db)
        rows = []

        for stock_code in stock_codes:
            factor_rows = factor_repo.get_values(factor_id, code_hash, stock_code, start_date, end_date, frequency_key)
            target_rows = target_repo.get_values(target_key, stock_code, start_date, end_date, frequency_key)
            factor_by_time = {row["bar_time"]: row["value"] for row in factor_rows}
            target_by_time = {row["bar_time"]: row["value"] for row in target_rows}
            for bar_time in sorted(set(factor_by_time) & set(target_by_time)):
                rows.append(
                    {
                        "stock_code": stock_code,
                        "bar_time": bar_time,
                        "factor_value": factor_by_time[bar_time],
                        "target_return": target_by_time[bar_time],
                    }
                )

        return {
            "factor_id": factor_id,
            "factor_name": factor.name,
            "factor_code_hash": code_hash,
            "target": target_key,
            "frequency": frequency_key,
            "start_date": start_date,
            "end_date": end_date,
            "rows": rows,
        }

    def get_factor_value_series(
        self,
        db,
        factor_id: int,
        stock_code: str,
        start_date: str,
        end_date: str,
        frequency: str | None = None,
    ) -> pd.Series:
        """Read cached factor values as a time-indexed series."""
        factor = FactorRepository(db).get_by_id(factor_id)
        if not factor:
            raise ValueError("因子不存在")
        frequency_key = validate_frequency(frequency or DEFAULT_FREQUENCY)

        rows = FactorValueCacheRepository(db).get_values(
            factor_id,
            hash_factor_code(factor.code),
            stock_code,
            start_date,
            end_date,
            frequency_key,
        )
        data = {
            pd.Timestamp(row["bar_time"]): row["value"]
            for row in rows
            if row["value"] is not None
        }
        return pd.Series(data).sort_index()


factor_dataset_service = FactorDatasetService()
