"""Fixed factor prediction target definitions."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, Optional

import pandas as pd


DEFAULT_FREQUENCY = "1d"
DEFAULT_FACTOR_TARGET = "next_1d_return"
VALID_FREQUENCIES = {"1d", "60m"}


@dataclass(frozen=True)
class FactorTarget:
    """Metadata for a fixed factor prediction target."""

    key: str
    label: str
    frequency: str
    horizon_bars: int

    @property
    def horizon(self) -> int:
        """Backward-compatible alias for horizon_bars."""
        return self.horizon_bars


FACTOR_TARGETS: Dict[str, FactorTarget] = {
    "next_1d_return": FactorTarget("next_1d_return", "次日收益率", "1d", 1),
    "next_5d_return": FactorTarget("next_5d_return", "未来5日收益率", "1d", 5),
    "next_10d_return": FactorTarget("next_10d_return", "未来10日收益率", "1d", 10),
    "next_1h_return": FactorTarget("next_1h_return", "未来1小时收益率", "60m", 1),
    "next_2h_return": FactorTarget("next_2h_return", "未来2小时收益率", "60m", 2),
    "next_4h_return": FactorTarget("next_4h_return", "未来4小时收益率", "60m", 4),
}


def validate_frequency(frequency: Optional[str]) -> str:
    """Validate and normalize a bar frequency."""
    value = frequency or DEFAULT_FREQUENCY
    if value not in VALID_FREQUENCIES:
        raise ValueError(f"未知数据频率: {value}")
    return value


def list_factor_targets(frequency: Optional[str] = None) -> Iterable[FactorTarget]:
    """Return the configured factor targets."""
    if frequency is None:
        return FACTOR_TARGETS.values()
    frequency_key = validate_frequency(frequency)
    return [target for target in FACTOR_TARGETS.values() if target.frequency == frequency_key]


def get_factor_target(target: Optional[str]) -> FactorTarget:
    """Return target metadata, defaulting empty values to the default target."""
    key = target or DEFAULT_FACTOR_TARGET
    if key not in FACTOR_TARGETS:
        raise ValueError(f"未知因子目标: {key}")
    return FACTOR_TARGETS[key]


def validate_factor_target(target: Optional[str], frequency: Optional[str] = None) -> str:
    """Validate and normalize a factor target key."""
    factor_target = get_factor_target(target)
    if frequency is not None and factor_target.frequency != validate_frequency(frequency):
        raise ValueError(f"目标 {factor_target.key} 不属于 {frequency} 频率")
    return factor_target.key


def add_target_return_column(data: pd.DataFrame, target: Optional[str]) -> pd.DataFrame:
    """Add the selected forward-return target column to stock data."""
    factor_target = get_factor_target(target)
    if "close" not in data.columns:
        raise ValueError("数据缺少 close 列，无法计算目标收益率")

    horizon = factor_target.horizon_bars
    data[factor_target.key] = data["close"].shift(-horizon) / data["close"] - 1
    return data
