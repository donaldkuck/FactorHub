"""
数据服务模块 - 股票数据获取与缓存
"""
import hashlib
from pathlib import Path
from typing import Optional
import pandas as pd
import akshare as ak

from backend.core.settings import settings
from backend.core.factor_targets import DEFAULT_FREQUENCY, validate_frequency
from backend.services.cache_service import cache_service
from backend.services.data_preprocessing_service import data_preprocessing_service


class DataService:
    """数据服务类 - 负责股票数据获取和缓存"""

    def __init__(self):
        self.cache_dir = settings.AKSHARE_CACHE_DIR
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cache_service = cache_service
        self.preprocessing = data_preprocessing_service

    def _get_cache_key(
        self,
        stock_code: str,
        start_date: str,
        end_date: str,
        frequency: str = DEFAULT_FREQUENCY,
        adjust: str = "qfq",
    ) -> str:
        """生成缓存键"""
        cache_key = f"{stock_code}_{start_date}_{end_date}_{frequency}_{adjust}"
        return hashlib.md5(cache_key.encode()).hexdigest()

    def _get_cache_path(
        self,
        stock_code: str,
        start_date: str,
        end_date: str,
        frequency: str = DEFAULT_FREQUENCY,
        adjust: str = "qfq",
    ) -> Path:
        """生成缓存文件路径（保留向后兼容）"""
        cache_hash = self._get_cache_key(stock_code, start_date, end_date, frequency, adjust)
        return self.cache_dir / f"{cache_hash}.pkl"

    def _load_from_cache(self, cache_key: str) -> Optional[pd.DataFrame]:
        """从智能缓存加载数据"""
        return self.cache_service.get(cache_key)

    def _save_to_cache(self, data: pd.DataFrame, cache_key: str, ttl: Optional[int] = None) -> None:
        """保存数据到智能缓存"""
        if ttl is None:
            ttl = settings.CACHE_DEFAULT_TTL
        self.cache_service.set(cache_key, data, ttl=ttl)

    def get_stock_data(
        self,
        stock_code: str,
        start_date: str,
        end_date: str,
        use_cache: bool = True,
    ) -> pd.DataFrame:
        """获取日频股票历史数据（兼容旧调用）"""
        return self.get_stock_bars(
            stock_code=stock_code,
            start_date=start_date,
            end_date=end_date,
            frequency=DEFAULT_FREQUENCY,
            adjust="qfq",
            use_cache=use_cache,
        )

    def get_stock_bars(
        self,
        stock_code: str,
        start_date: str,
        end_date: str,
        frequency: str = DEFAULT_FREQUENCY,
        adjust: str = "qfq",
        use_cache: bool = True,
    ) -> pd.DataFrame:
        """
        获取股票K线数据，支持日频和分钟频率。

        Args:
            stock_code: 股票代码，如 "000001" 或 "000001.SZ"
            start_date: 开始日期，日频格式 "YYYY-MM-DD"，分钟频率格式 "YYYY-MM-DD HH:MM:SS"
            end_date: 结束日期，日频格式 "YYYY-MM-DD"，分钟频率格式 "YYYY-MM-DD HH:MM:SS"
            frequency: 数据频率，"1d" 或 "60m"
            adjust: 复权方式
            use_cache: 是否使用缓存

        Returns:
            包含OHLCV数据的DataFrame
        """
        # 标准化股票代码
        stock_code = self._normalize_stock_code(stock_code)
        frequency = validate_frequency(frequency)

        # 检查智能缓存
        if use_cache and settings.AKSHARE_CACHE_ENABLED:
            cache_key = self._get_cache_key(stock_code, start_date, end_date, frequency, adjust)
            cached_data = self._load_from_cache(cache_key)
            if cached_data is not None:
                return cached_data

        # 从 akshare 获取数据
        try:
            if frequency == DEFAULT_FREQUENCY:
                if stock_code.endswith(".SH"):
                    symbol = "sh" + stock_code.replace(".SH", "")
                elif stock_code.endswith(".SZ"):
                    symbol = "sz" + stock_code.replace(".SZ", "")
                else:
                    # 尝试自动识别
                    if stock_code.startswith("6"):
                        symbol = "sh" + stock_code
                    elif stock_code.startswith(("0", "3")):
                        symbol = "sz" + stock_code
                    else:
                        symbol = stock_code

                df = ak.stock_zh_a_daily(
                    symbol=symbol,
                    start_date=start_date[:10].replace("-", ""),
                    end_date=end_date[:10].replace("-", ""),
                    adjust=adjust,
                )
            else:
                symbol = stock_code.replace(".SH", "").replace(".SZ", "")
                period = frequency.replace("m", "")
                df = ak.stock_zh_a_hist_min_em(
                    symbol=symbol,
                    start_date=self._normalize_datetime_arg(start_date, market_open=True),
                    end_date=self._normalize_datetime_arg(end_date, market_open=False),
                    period=period,
                    adjust=adjust,
                )

            # 标准化列名
            df = self._standardize_columns(df)

            # 数据预处理
            df = self._preprocess_data(df)

            # 保存到智能缓存
            if use_cache and settings.AKSHARE_CACHE_ENABLED:
                cache_key = self._get_cache_key(stock_code, start_date, end_date, frequency, adjust)
                self._save_to_cache(df, cache_key)

            return df

        except Exception as e:
            raise ValueError(f"获取股票 {stock_code} 数据失败: {e}")

    def _normalize_datetime_arg(self, value: str, market_open: bool) -> str:
        """Normalize AkShare minute datetime arguments."""
        value = str(value)
        if len(value) > 10:
            return value.replace("T", " ")[:19]
        suffix = "09:30:00" if market_open else "15:00:00"
        return f"{value} {suffix}"

    def _normalize_stock_code(self, code: str) -> str:
        """标准化股票代码格式"""
        code = code.strip().upper()
        if not code.endswith((".SH", ".SZ")):
            # 自动判断上海或深圳
            if code.startswith("6"):
                return f"{code}.SH"
            elif code.startswith(("0", "3")):
                return f"{code}.SZ"
        return code

    def _standardize_columns(self, df: pd.DataFrame) -> pd.DataFrame:
        """标准化DataFrame列名"""
        # akshare 返回的列名映射
        column_mapping = {
            "日期": "date",
            "时间": "date",
            "开盘": "open",
            "收盘": "close",
            "最高": "high",
            "最低": "low",
            "成交量": "volume",
            "成交额": "amount",
            "振幅": "amplitude",
            "涨跌幅": "pct_change",
            "涨跌额": "change",
            "换手率": "turnover",
        }

        df = df.rename(columns=column_mapping)

        # 确保日期列是datetime类型
        if "date" in df.columns:
            df["date"] = pd.to_datetime(df["date"])
            df.set_index("date", inplace=True)

        # 确保数值列是正确的类型
        numeric_columns = ["open", "high", "low", "close", "volume"]
        for col in numeric_columns:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        return df.sort_index()

    def get_multiple_stocks_data(
        self,
        stock_codes: list[str],
        start_date: str,
        end_date: str,
        use_cache: bool = True,
    ) -> dict[str, pd.DataFrame]:
        """
        获取多个股票的数据

        Args:
            stock_codes: 股票代码列表
            start_date: 开始日期
            end_date: 结束日期
            use_cache: 是否使用缓存

        Returns:
            字典，key为股票代码，value为对应的DataFrame
        """
        result = {}
        for code in stock_codes:
            try:
                df = self.get_stock_data(code, start_date, end_date, use_cache)
                result[code] = df
            except Exception as e:
                print(f"Warning: 获取股票 {code} 数据失败: {e}")
        return result

    def _preprocess_data(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        预处理数据

        Args:
            df: 原始数据框

        Returns:
            预处理后的数据框
        """
        # 填充缺失值
        if settings.DATA_FILL_MISSING:
            df = self.preprocessing.fill_missing_values(
                df,
                method=settings.DATA_FILL_METHOD,
            )

        # 异常值检测和处理
        if settings.DATA_OUTLIER_DETECTION:
            df, _ = self.preprocessing.detect_and_handle_anomalies(
                df,
                price_columns=["open", "high", "low", "close"],
                n_sigma=settings.DATA_OUTLIER_N_SIGMA,
                handle_method=settings.DATA_OUTLIER_METHOD,
            )

        return df

    def get_cache_stats(self) -> dict:
        """获取缓存统计信息"""
        return self.cache_service.get_stats()

    def cleanup_cache(self) -> int:
        """清理过期缓存"""
        return self.cache_service.cleanup_expired()

    def clear_cache(self) -> int:
        """清空所有缓存"""
        return self.cache_service.clear_all()

    def incremental_update(
        self,
        stock_code: str,
        existing_df: pd.DataFrame,
        end_date: str,
    ) -> pd.DataFrame:
        """
        增量更新股票数据

        Args:
            stock_code: 股票代码
            existing_df: 现有的数据框
            end_date: 新的结束日期

        Returns:
            更新后的数据框
        """
        # 获取现有数据的最后日期
        last_date = existing_df.index.max()
        start_date = (last_date + pd.Timedelta(days=1)).strftime("%Y-%m-%d")

        # 如果新日期在现有数据之前，直接返回现有数据
        if start_date > end_date:
            return existing_df

        # 获取新数据
        new_df = self.get_stock_data(
            stock_code=stock_code,
            start_date=start_date,
            end_date=end_date,
            use_cache=True,
        )

        # 增量合并
        combined_df = self.preprocessing.incremental_update(
            existing_df=existing_df,
            new_df=new_df,
        )

        return combined_df


# 全局数据服务实例
data_service = DataService()
