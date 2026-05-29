"""
数据服务模块 - 股票数据获取与缓存
"""
import hashlib
import signal
import threading
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional
import pandas as pd
import akshare as ak
import requests
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from backend.core.settings import settings
from backend.core.factor_targets import DEFAULT_FREQUENCY, validate_frequency
from backend.core.database import get_db_session
from backend.core.stock_pools import get_stock_pool
from backend.models.factor import FactorValueCacheModel, StockBarCacheModel, TargetReturnCacheModel
from backend.models.factor_performance import FactorPerformanceBarCacheModel
from backend.services.cache_service import cache_service
from backend.services.data_preprocessing_service import data_preprocessing_service
from backend.services.duckdb_bar_store import duckdb_bar_store


DEFAULT_ADJUST = "hfq"


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
        adjust: str = DEFAULT_ADJUST,
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
        adjust: str = DEFAULT_ADJUST,
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
            adjust=DEFAULT_ADJUST,
            use_cache=use_cache,
            allow_online=True,
        )

    def get_stock_bars(
        self,
        stock_code: str,
        start_date: str,
        end_date: str,
        frequency: str = DEFAULT_FREQUENCY,
        adjust: str = DEFAULT_ADJUST,
        use_cache: bool = True,
        allow_online: bool = True,
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
            allow_online: 是否允许本地缺失时在线拉取行情

        Returns:
            包含OHLCV数据的DataFrame
        """
        # 标准化股票代码
        stock_code = self._normalize_stock_code(stock_code)
        frequency = validate_frequency(frequency)
        adjust = self._normalize_adjust(adjust)

        imported_data = self._load_imported_bars(stock_code, start_date, end_date, frequency, adjust)
        if imported_data is not None and not imported_data.empty:
            return imported_data

        if not allow_online:
            raise ValueError(
                f"本地 raw_bar 缺少 {stock_code} {frequency} K 线: {start_date} - {end_date}，"
                "请先在数据导入页导入 AkShare/东方财富、QMT 或文件数据"
            )

        # 检查智能缓存
        if use_cache and settings.AKSHARE_CACHE_ENABLED:
            cache_key = self._get_cache_key(stock_code, start_date, end_date, frequency, adjust)
            cached_data = self._load_from_cache(cache_key)
            if cached_data is not None:
                return cached_data

        last_error = None
        for attempt in range(3):
            try:
                return self._fetch_stock_bars(
                    stock_code=stock_code,
                    start_date=start_date,
                    end_date=end_date,
                    frequency=frequency,
                    adjust=adjust,
                    use_cache=use_cache,
                    persist_raw=True,
                )
            except Exception as e:
                last_error = e
                if isinstance(e, TimeoutError):
                    break
                if attempt < 2:
                    time.sleep(1.5 * (attempt + 1))

        raise ValueError(f"获取股票 {stock_code} 数据失败: {last_error}")

    def _fetch_stock_bars(
        self,
        stock_code: str,
        start_date: str,
        end_date: str,
        frequency: str,
        adjust: str,
        use_cache: bool,
        persist_raw: bool = True,
    ) -> pd.DataFrame:
        """Fetch bars from AkShare once."""
        with self._akshare_time_limit(settings.AKSHARE_REQUEST_TIMEOUT):
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
                minute_start = self._normalize_datetime_arg(start_date, market_open=True)
                minute_end = self._normalize_datetime_arg(end_date, market_open=False)
                try:
                    df = self._fetch_minute_bars_batched(
                        symbol=symbol,
                        start_date=minute_start,
                        end_date=minute_end,
                        period=period,
                        adjust=adjust,
                    )
                except Exception:
                    df = pd.DataFrame()
                if df is None or df.empty:
                    df = ak.stock_zh_a_hist_min_em(
                        symbol=symbol,
                        start_date=minute_start,
                        end_date=minute_end,
                        period=period,
                        adjust=adjust,
                    )

        if df is None or df.empty:
            raise ValueError("行情接口返回空数据")

        # 标准化列名
        df = self._standardize_columns(df)

        # 数据预处理
        df = self._preprocess_data(df)

        # 保存到智能缓存
        if use_cache and settings.AKSHARE_CACHE_ENABLED:
            cache_key = self._get_cache_key(stock_code, start_date, end_date, frequency, adjust)
            self._save_to_cache(df, cache_key)

        if persist_raw:
            self._persist_fetched_bars(stock_code, df, frequency, adjust)
        return df

    def import_bar_file(
        self,
        file_path: str,
        frequency: str = DEFAULT_FREQUENCY,
        adjust: str = DEFAULT_ADJUST,
        source: str = "import",
        force: bool = True,
        invalidate_derived: bool = True,
    ) -> dict:
        """Import OHLCV bars from a local CSV/parquet file into the raw bar store."""
        frequency_key = validate_frequency(frequency)
        adjust = self._normalize_adjust(adjust)
        path = Path(file_path).expanduser()
        if not path.exists() or not path.is_file():
            raise ValueError(f"文件不存在: {file_path}")

        if path.suffix.lower() in {".parquet", ".pq"}:
            raw_df = pd.read_parquet(path)
        else:
            raw_df = pd.read_csv(path)

        df = self._normalize_imported_bar_frame(raw_df, frequency_key)
        return self.import_bar_dataframe(
            df,
            frequency=frequency_key,
            adjust=adjust,
            source=source,
            force=force,
            invalidate_derived=invalidate_derived,
            file_path=str(path),
        )

    def import_bar_dataframe(
        self,
        df: pd.DataFrame,
        frequency: str = DEFAULT_FREQUENCY,
        adjust: str = DEFAULT_ADJUST,
        source: str = "import",
        force: bool = True,
        invalidate_derived: bool = True,
        file_path: Optional[str] = None,
    ) -> dict:
        """Import a normalized OHLCV DataFrame into the raw bar store."""
        frequency_key = validate_frequency(frequency)
        adjust = self._normalize_adjust(adjust)
        if "bar_time" not in df.columns:
            df = self._normalize_imported_bar_frame(df, frequency_key)
        if df.empty:
            raise ValueError("没有可导入的 K 线数据")

        source = (source or "import").strip()[:50] or "import"
        inserted = 0
        updated = 0
        skipped = 0

        if duckdb_bar_store.is_available():
            write_result = duckdb_bar_store.upsert_bars(df, frequency_key, source, force=force, adjust=adjust)
            inserted = write_result["inserted"]
            updated = write_result["updated"]
            skipped = write_result["skipped"]
            stocks = sorted(df["stock_code"].unique().tolist())
            start_time = df["bar_time"].min()
            end_time = df["bar_time"].max()
            invalidated = {}
            db = get_db_session()
            try:
                if invalidate_derived:
                    invalidated = self._invalidate_derived_caches(
                        db=db,
                        stock_codes=stocks,
                        frequency=frequency_key,
                        start_time=start_time,
                        end_time=end_time,
                    )
            finally:
                db.close()
            return {
                "file_path": file_path,
                "frequency": frequency_key,
                "source": source,
                "adjust": adjust,
                "storage": "duckdb",
                "rows": len(df),
                "inserted": inserted,
                "updated": updated,
                "skipped": skipped,
                "stock_count": len(stocks),
                "stock_codes": stocks[:20],
                "start_time": start_time.isoformat() if pd.notna(start_time) else None,
                "end_time": end_time.isoformat() if pd.notna(end_time) else None,
                "invalidated": invalidated,
            }

        db = get_db_session()
        try:
            for row in df.itertuples(index=False):
                existing = db.scalar(
                    select(StockBarCacheModel)
                    .where(StockBarCacheModel.stock_code == row.stock_code)
                    .where(StockBarCacheModel.frequency == frequency_key)
                    .where(StockBarCacheModel.bar_time == row.bar_time)
                    .where(StockBarCacheModel.source == source)
                )
                if existing:
                    if not force:
                        skipped += 1
                        continue
                    existing.open = row.open
                    existing.high = row.high
                    existing.low = row.low
                    existing.close = row.close
                    existing.volume = row.volume
                    existing.amount = row.amount
                    existing.updated_at = datetime.now()
                    updated += 1
                    continue

                try:
                    with db.begin_nested():
                        db.add(
                            StockBarCacheModel(
                                stock_code=row.stock_code,
                                frequency=frequency_key,
                                bar_time=row.bar_time,
                                open=row.open,
                                high=row.high,
                                low=row.low,
                                close=row.close,
                                volume=row.volume,
                                amount=row.amount,
                                source=source,
                            )
                        )
                        db.flush()
                        inserted += 1
                except IntegrityError:
                    skipped += 1
            db.commit()

            stocks = sorted(df["stock_code"].unique().tolist())
            start_time = df["bar_time"].min()
            end_time = df["bar_time"].max()
            invalidated = {}
            if invalidate_derived:
                invalidated = self._invalidate_derived_caches(
                    db=db,
                    stock_codes=stocks,
                    frequency=frequency_key,
                    start_time=start_time,
                    end_time=end_time,
                )

            return {
                "file_path": file_path,
                "frequency": frequency_key,
                "source": source,
                "adjust": adjust,
                "storage": "sqlite",
                "rows": len(df),
                "inserted": inserted,
                "updated": updated,
                "skipped": skipped,
                "stock_count": len(stocks),
                "stock_codes": stocks[:20],
                "start_time": start_time.isoformat() if pd.notna(start_time) else None,
                "end_time": end_time.isoformat() if pd.notna(end_time) else None,
                "invalidated": invalidated,
            }
        finally:
            db.close()

    def import_akshare_bars(
        self,
        stock_codes: Optional[list[str]] = None,
        stock_pool_key: Optional[str] = None,
        start_date: str = "",
        end_date: str = "",
        frequency: str = DEFAULT_FREQUENCY,
        adjust: str = DEFAULT_ADJUST,
        source: str = "akshare_em",
        force: bool = True,
        invalidate_derived: bool = True,
    ) -> dict:
        """Fetch bars from AkShare/EastMoney and persist them to the raw bar store."""
        frequency_key = validate_frequency(frequency)
        adjust = self._normalize_adjust(adjust)
        symbols = self._resolve_import_symbols(stock_codes, stock_pool_key)
        if not start_date or not end_date:
            raise ValueError("开始日期和结束日期不能为空")

        total_rows = 0
        inserted = 0
        updated = 0
        skipped = 0
        imported_stocks = []
        failed = []
        start_times = []
        end_times = []

        for stock_code in symbols:
            try:
                df = self._fetch_stock_bars(
                    stock_code=self._normalize_stock_code(stock_code),
                    start_date=start_date,
                    end_date=end_date,
                    frequency=frequency_key,
                    adjust=adjust,
                    use_cache=True,
                    persist_raw=False,
                )
                payload = df.reset_index()
                if "date" not in payload.columns:
                    payload = payload.rename(columns={payload.columns[0]: "date"})
                payload["stock_code"] = self._normalize_stock_code(stock_code)
                payload = payload.rename(columns={"date": "bar_time"})
                result = self.import_bar_dataframe(
                    payload[["stock_code", "bar_time", "open", "high", "low", "close", "volume", "amount"]],
                    frequency=frequency_key,
                    adjust=adjust,
                    source=source,
                    force=force,
                    invalidate_derived=invalidate_derived,
                )
                total_rows += result["rows"]
                inserted += result["inserted"]
                updated += result["updated"]
                skipped += result["skipped"]
                imported_stocks.append(stock_code)
                if result.get("start_time"):
                    start_times.append(pd.to_datetime(result["start_time"]))
                if result.get("end_time"):
                    end_times.append(pd.to_datetime(result["end_time"]))
            except Exception as exc:
                failed.append({"stock_code": stock_code, "error": str(exc)})

        return {
            "frequency": frequency_key,
            "source": source,
            "adjust": adjust,
            "rows": total_rows,
            "inserted": inserted,
            "updated": updated,
            "skipped": skipped,
            "stock_count": len(imported_stocks),
            "stock_codes": imported_stocks[:20],
            "failed_count": len(failed),
            "failed": failed[:50],
            "start_time": min(start_times).isoformat() if start_times else None,
            "end_time": max(end_times).isoformat() if end_times else None,
        }

    def _resolve_import_symbols(
        self,
        stock_codes: Optional[list[str]],
        stock_pool_key: Optional[str],
    ) -> list[str]:
        if stock_codes:
            symbols = [self._normalize_stock_code(code) for code in stock_codes if str(code).strip()]
        elif stock_pool_key:
            pool = get_stock_pool(stock_pool_key, include_codes=True)
            symbols = [self._normalize_stock_code(code) for code in pool.get("stock_codes", [])]
        else:
            symbols = []
        symbols = sorted(set(symbols))
        if not symbols:
            raise ValueError("请提供股票代码或股票池")
        return symbols

    def _persist_fetched_bars(self, stock_code: str, df: pd.DataFrame, frequency: str, adjust: str) -> None:
        """Persist online-fetched bars as raw bars for later inspection/reuse."""
        if df is None or df.empty or not duckdb_bar_store.is_available():
            return
        try:
            payload = df.reset_index()
            if "date" not in payload.columns:
                first_column = payload.columns[0]
                payload = payload.rename(columns={first_column: "date"})
            payload["stock_code"] = stock_code
            payload = payload.rename(columns={"date": "bar_time"})
            self.import_bar_dataframe(
                payload[["stock_code", "bar_time", "open", "high", "low", "close", "volume", "amount"]],
                frequency=frequency,
                adjust=adjust,
                source="akshare",
                force=True,
                invalidate_derived=False,
            )
        except Exception:
            # Raw persistence is an optimization; fetching data should not fail because of it.
            return

    def get_imported_bar_stats(self, frequency: Optional[str] = None) -> list[dict]:
        """Return imported raw bar coverage grouped by source and frequency."""
        db = get_db_session()
        try:
            query = select(
                StockBarCacheModel.source,
                StockBarCacheModel.frequency,
                func.count(StockBarCacheModel.id).label("rows"),
                func.count(func.distinct(StockBarCacheModel.stock_code)).label("stock_count"),
                func.min(StockBarCacheModel.bar_time).label("start_time"),
                func.max(StockBarCacheModel.bar_time).label("end_time"),
            )
            if frequency:
                query = query.where(StockBarCacheModel.frequency == validate_frequency(frequency))
            rows = db.execute(
                query.group_by(StockBarCacheModel.source, StockBarCacheModel.frequency)
                .order_by(StockBarCacheModel.frequency, StockBarCacheModel.source)
            ).all()
            sqlite_stats = [
                {
                    "source": row.source,
                    "frequency": row.frequency,
                    "rows": row.rows,
                    "stock_count": row.stock_count,
                    "start_time": row.start_time.isoformat() if row.start_time else None,
                    "end_time": row.end_time.isoformat() if row.end_time else None,
                }
                for row in rows
            ]
            if duckdb_bar_store.is_available():
                return duckdb_bar_store.stats(frequency=frequency) + sqlite_stats
            return sqlite_stats
        finally:
            db.close()

    def get_imported_bar_coverage(
        self,
        frequency: Optional[str] = None,
        source: Optional[str] = None,
        stock_code: Optional[str] = None,
        adjust: Optional[str] = None,
        cache_type: str = "all",
        page: int = 1,
        page_size: int = 50,
    ) -> dict:
        """Return data coverage grouped by scope/source/frequency."""
        db = get_db_session()
        try:
            frequency_key = validate_frequency(frequency) if frequency else None
            normalized_stock = self._normalize_stock_code(stock_code) if stock_code else None
            adjust = self._normalize_adjust(adjust) if adjust is not None else None
            cache_type_key = (cache_type or "all").lower()
            items = []

            if cache_type_key in {"all", "raw"}:
                if duckdb_bar_store.is_available():
                    raw_source = source.split(":", 1)[1] if source and source.startswith("raw_bar:") else None
                    if not source or source == "raw_bar" or raw_source:
                        items.extend(duckdb_bar_store.coverage(
                            frequency=frequency_key,
                            source=raw_source,
                            stock_code=normalized_stock,
                            adjust=adjust,
                        ))
                items.extend(self._coverage_rows_for_stock_model(
                    db, StockBarCacheModel, "raw_bar", frequency_key, source, normalized_stock
                ))
            if cache_type_key in {"all", "factor_value"}:
                if duckdb_bar_store.is_available() and (
                    not source or source in {"factor_value", "factor_value:duckdb"}
                ):
                    items.extend(duckdb_bar_store.factor_value_coverage(
                        frequency=frequency_key,
                        stock_code=normalized_stock,
                    ))
                items.extend(self._coverage_rows_for_stock_model(
                    db, FactorValueCacheModel, "factor_value", frequency_key, source, normalized_stock
                ))
            if cache_type_key in {"all", "target_return"}:
                items.extend(self._target_return_coverage_rows(db, frequency_key, source, normalized_stock))
            if cache_type_key in {"all", "ranking"}:
                items.extend(self._ranking_coverage_rows(db, frequency_key, source))

            items.sort(key=lambda item: (item["frequency"], item["source"], item["stock_code"]))
            total = len(items)
            start = max(page - 1, 0) * page_size
            return {
                "items": items[start:start + page_size],
                "total": total,
                "page": page,
                "page_size": page_size,
            }
        finally:
            db.close()

    def _coverage_rows_for_stock_model(
        self,
        db,
        model,
        label: str,
        frequency: Optional[str],
        source: Optional[str],
        stock_code: Optional[str],
    ) -> list[dict]:
        if source and source != label:
            return []
        query = select(
            model.stock_code,
            model.frequency,
            func.count(model.id).label("rows"),
            func.min(model.bar_time).label("start_time"),
            func.max(model.bar_time).label("end_time"),
        )
        if frequency:
            query = query.where(model.frequency == frequency)
        if stock_code:
            query = query.where(model.stock_code == stock_code)
        rows = db.execute(query.group_by(model.stock_code, model.frequency)).all()
        return [
            {
                "stock_code": row.stock_code,
                "source": label,
                "frequency": row.frequency,
                "rows": row.rows,
                "start_time": row.start_time.isoformat() if row.start_time else None,
                "end_time": row.end_time.isoformat() if row.end_time else None,
            }
            for row in rows
        ]

    def _target_return_coverage_rows(
        self,
        db,
        frequency: Optional[str],
        source: Optional[str],
        stock_code: Optional[str],
    ) -> list[dict]:
        query = select(
            TargetReturnCacheModel.stock_code,
            TargetReturnCacheModel.frequency,
            TargetReturnCacheModel.target,
            func.count(TargetReturnCacheModel.id).label("rows"),
            func.min(TargetReturnCacheModel.bar_time).label("start_time"),
            func.max(TargetReturnCacheModel.bar_time).label("end_time"),
        )
        if frequency:
            query = query.where(TargetReturnCacheModel.frequency == frequency)
        if stock_code:
            query = query.where(TargetReturnCacheModel.stock_code == stock_code)
        rows = db.execute(
            query.group_by(
                TargetReturnCacheModel.stock_code,
                TargetReturnCacheModel.frequency,
                TargetReturnCacheModel.target,
            )
        ).all()
        return [
            {
                "stock_code": row.stock_code,
                "source": f"target:{row.target}",
                "frequency": row.frequency,
                "rows": row.rows,
                "start_time": row.start_time.isoformat() if row.start_time else None,
                "end_time": row.end_time.isoformat() if row.end_time else None,
            }
            for row in rows
            if not source or source in {"target_return", f"target:{row.target}"}
        ]

    def _ranking_coverage_rows(self, db, frequency: Optional[str], source: Optional[str]) -> list[dict]:
        query = select(
            FactorPerformanceBarCacheModel.stock_pool_key,
            FactorPerformanceBarCacheModel.frequency,
            FactorPerformanceBarCacheModel.target,
            func.count(FactorPerformanceBarCacheModel.id).label("rows"),
            func.min(FactorPerformanceBarCacheModel.bar_time).label("start_time"),
            func.max(FactorPerformanceBarCacheModel.bar_time).label("end_time"),
        )
        if frequency:
            query = query.where(FactorPerformanceBarCacheModel.frequency == frequency)
        rows = db.execute(
            query.group_by(
                FactorPerformanceBarCacheModel.stock_pool_key,
                FactorPerformanceBarCacheModel.frequency,
                FactorPerformanceBarCacheModel.target,
            )
        ).all()
        return [
            {
                "stock_code": f"股票池:{row.stock_pool_key}",
                "source": f"ranking:{row.target}",
                "frequency": row.frequency,
                "rows": row.rows,
                "start_time": row.start_time.isoformat() if row.start_time else None,
                "end_time": row.end_time.isoformat() if row.end_time else None,
            }
            for row in rows
            if not source or source in {"ranking", f"ranking:{row.target}"}
        ]

    def get_imported_bar_sample(
        self,
        stock_code: str,
        frequency: str,
        source: Optional[str] = None,
        adjust: Optional[str] = None,
        limit: int = 20,
    ) -> list[dict]:
        """Return latest imported bars for one stock."""
        db = get_db_session()
        try:
            adjust = self._normalize_adjust(adjust) if adjust is not None else None
            if source == "raw_bar":
                duckdb_source = None
            elif source and source.startswith("raw_bar:"):
                duckdb_source = source.split(":", 1)[1]
            else:
                duckdb_source = source
            if duckdb_bar_store.is_available():
                rows = duckdb_bar_store.sample(
                    stock_code=self._normalize_stock_code(stock_code),
                    frequency=validate_frequency(frequency),
                    source=duckdb_source,
                    adjust=adjust,
                    limit=max(min(limit, 200), 1),
                )
                if rows:
                    return rows
            query = (
                select(StockBarCacheModel)
                .where(StockBarCacheModel.stock_code == self._normalize_stock_code(stock_code))
                .where(StockBarCacheModel.frequency == validate_frequency(frequency))
            )
            if duckdb_source:
                query = query.where(StockBarCacheModel.source == duckdb_source)
            rows = db.scalars(
                query.order_by(StockBarCacheModel.bar_time.desc()).limit(max(min(limit, 200), 1))
            ).all()
            return [row.to_dict() for row in rows]
        finally:
            db.close()

    def _load_imported_bars(
        self,
        stock_code: str,
        start_date: str,
        end_date: str,
        frequency: str,
        adjust: str = "",
    ) -> Optional[pd.DataFrame]:
        if frequency == DEFAULT_FREQUENCY:
            start_time = pd.to_datetime(str(start_date)[:10])
            end_time = pd.to_datetime(str(end_date)[:10]) + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1)
        else:
            start_time = pd.to_datetime(self._normalize_datetime_arg(start_date, market_open=True))
            end_time = pd.to_datetime(self._normalize_datetime_arg(end_date, market_open=False))
        adjust = self._normalize_adjust(adjust)
        if duckdb_bar_store.is_available():
            data = duckdb_bar_store.load_bars(stock_code, frequency, start_time, end_time, adjust=adjust)
            if data is not None and not data.empty:
                return data
        db = get_db_session()
        try:
            rows = db.scalars(
                select(StockBarCacheModel)
                .where(StockBarCacheModel.stock_code == stock_code)
                .where(StockBarCacheModel.frequency == frequency)
                .where(StockBarCacheModel.bar_time >= start_time.to_pydatetime())
                .where(StockBarCacheModel.bar_time <= end_time.to_pydatetime())
                .order_by(StockBarCacheModel.bar_time, StockBarCacheModel.updated_at)
            ).all()
            if not rows:
                return None
            data = pd.DataFrame([row.to_dict() for row in rows])
        finally:
            db.close()

        data["date"] = pd.to_datetime(data["bar_time"])
        data = data.drop_duplicates(subset=["date"], keep="last")
        data = data.set_index("date").sort_index()
        return data[["open", "high", "low", "close", "volume", "amount"]]

    def _normalize_imported_bar_frame(self, df: pd.DataFrame, frequency: str) -> pd.DataFrame:
        column_map = {}
        aliases = {
            "stock_code": {"stock_code", "code", "symbol", "ts_code", "证券代码", "股票代码"},
            "bar_time": {"bar_time", "datetime", "time", "date", "trade_time", "trade_date", "时间", "日期"},
            "open": {"open", "开盘", "open_price"},
            "high": {"high", "最高", "high_price"},
            "low": {"low", "最低", "low_price"},
            "close": {"close", "收盘", "close_price"},
            "volume": {"volume", "vol", "成交量"},
            "amount": {"amount", "成交额", "money"},
        }
        normalized_columns = {str(col).strip(): col for col in df.columns}
        lower_lookup = {str(col).strip().lower(): col for col in df.columns}
        for canonical, names in aliases.items():
            for name in names:
                if name in normalized_columns:
                    column_map[normalized_columns[name]] = canonical
                    break
                if name.lower() in lower_lookup:
                    column_map[lower_lookup[name.lower()]] = canonical
                    break

        imported = df.rename(columns=column_map)
        required = {"stock_code", "bar_time", "open", "high", "low", "close"}
        missing = sorted(required - set(imported.columns))
        if missing:
            raise ValueError(f"缺少必要列: {', '.join(missing)}")

        if "volume" not in imported.columns:
            imported["volume"] = 0
        if "amount" not in imported.columns:
            imported["amount"] = None

        imported = imported[["stock_code", "bar_time", "open", "high", "low", "close", "volume", "amount"]].copy()
        imported["stock_code"] = imported["stock_code"].map(lambda value: self._normalize_stock_code(str(value)))
        imported["bar_time"] = pd.to_datetime(imported["bar_time"], errors="coerce")
        for column in ["open", "high", "low", "close", "volume", "amount"]:
            imported[column] = pd.to_numeric(imported[column], errors="coerce")

        imported = imported.dropna(subset=["stock_code", "bar_time", "open", "high", "low", "close"])
        imported = imported.drop_duplicates(subset=["stock_code", "bar_time"], keep="last")
        imported = imported.sort_values(["stock_code", "bar_time"])
        imported["bar_time"] = imported["bar_time"].map(lambda value: value.to_pydatetime())
        return imported

    def _invalidate_derived_caches(
        self,
        db,
        stock_codes: list[str],
        frequency: str,
        start_time: datetime,
        end_time: datetime,
    ) -> dict:
        duckdb_factor_deleted = 0
        if duckdb_bar_store.is_available():
            duckdb_factor_deleted = duckdb_bar_store.delete_factor_values(
                stock_codes=stock_codes,
                frequency=frequency,
                start_time=start_time,
                end_time=end_time,
            )
        factor_result = db.execute(
            delete(FactorValueCacheModel)
            .where(FactorValueCacheModel.stock_code.in_(stock_codes))
            .where(FactorValueCacheModel.frequency == frequency)
            .where(FactorValueCacheModel.bar_time >= start_time)
            .where(FactorValueCacheModel.bar_time <= end_time)
        )
        target_result = db.execute(
            delete(TargetReturnCacheModel)
            .where(TargetReturnCacheModel.stock_code.in_(stock_codes))
            .where(TargetReturnCacheModel.frequency == frequency)
            .where(TargetReturnCacheModel.bar_time >= start_time)
            .where(TargetReturnCacheModel.bar_time <= end_time)
        )
        performance_result = db.execute(
            delete(FactorPerformanceBarCacheModel)
            .where(FactorPerformanceBarCacheModel.frequency == frequency)
            .where(FactorPerformanceBarCacheModel.bar_time >= start_time)
            .where(FactorPerformanceBarCacheModel.bar_time <= end_time)
        )
        db.commit()
        return {
            "factor_value_cache": (factor_result.rowcount or 0) + duckdb_factor_deleted,
            "target_return_cache": target_result.rowcount or 0,
            "factor_performance_bar_cache": performance_result.rowcount or 0,
        }

    def _fetch_minute_bars_batched(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        period: str,
        adjust: str,
    ) -> pd.DataFrame:
        """Fetch minute bars with EastMoney beg/end chunks.

        AkShare's stock_zh_a_hist_min_em currently requests beg=0/end=20500000
        for period > 1 and filters locally, so passing date chunks to that
        function does not request older ranges. This uses the same endpoint
        with explicit beg/end dates and falls back to AkShare if unavailable.
        """
        adjust_map = {"": "0", "qfq": "1", "hfq": "2"}
        market_code = 1 if symbol.startswith("6") else 0
        start_ts = pd.to_datetime(start_date)
        end_ts = pd.to_datetime(end_date)
        if pd.isna(start_ts) or pd.isna(end_ts) or start_ts > end_ts:
            return pd.DataFrame()

        url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
        frames = []
        chunk_days = max(int(settings.AKSHARE_MINUTE_BATCH_DAYS), 1)
        chunk_start = start_ts.normalize()

        while chunk_start <= end_ts:
            chunk_end = min(chunk_start + pd.Timedelta(days=chunk_days - 1), end_ts)
            params = {
                "fields1": "f1,f2,f3,f4,f5,f6",
                "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
                "ut": "7eea3edcaed734bea9cbfc24409ed989",
                "klt": period,
                "fqt": adjust_map.get(adjust, "0"),
                "secid": f"{market_code}.{symbol}",
                "beg": chunk_start.strftime("%Y%m%d"),
                "end": chunk_end.strftime("%Y%m%d"),
            }
            response = requests.get(
                url,
                params=params,
                timeout=min(settings.AKSHARE_REQUEST_TIMEOUT, 20),
                headers={
                    "User-Agent": "Mozilla/5.0",
                    "Referer": "https://quote.eastmoney.com/",
                },
            )
            response.raise_for_status()
            data_json = response.json()
            klines = (data_json.get("data") or {}).get("klines") or []
            if klines:
                temp_df = pd.DataFrame([item.split(",") for item in klines])
                temp_df.columns = [
                    "时间",
                    "开盘",
                    "收盘",
                    "最高",
                    "最低",
                    "成交量",
                    "成交额",
                    "振幅",
                    "涨跌幅",
                    "涨跌额",
                    "换手率",
                ]
                frames.append(temp_df)
            chunk_start = chunk_end.normalize() + pd.Timedelta(days=1)

        if not frames:
            return pd.DataFrame()

        df = pd.concat(frames, ignore_index=True)
        df["时间"] = pd.to_datetime(df["时间"])
        df = df[(df["时间"] >= start_ts) & (df["时间"] <= end_ts)]
        df = df.drop_duplicates(subset=["时间"]).sort_values("时间")
        for column in ["开盘", "收盘", "最高", "最低", "涨跌幅", "涨跌额", "成交量", "成交额", "振幅", "换手率"]:
            df[column] = pd.to_numeric(df[column], errors="coerce")
        df["时间"] = df["时间"].astype(str)
        return df

    @contextmanager
    def _akshare_time_limit(self, seconds: int):
        """Bound AkShare calls that can otherwise block a ranking task indefinitely."""
        if (
            not seconds
            or seconds <= 0
            or threading.current_thread() is not threading.main_thread()
            or not hasattr(signal, "SIGALRM")
        ):
            yield
            return

        def _raise_timeout(signum, frame):
            raise TimeoutError(f"行情接口调用超过 {seconds} 秒")

        old_handler = signal.getsignal(signal.SIGALRM)
        signal.signal(signal.SIGALRM, _raise_timeout)
        signal.setitimer(signal.ITIMER_REAL, seconds)
        try:
            yield
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, old_handler)

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

    def _normalize_adjust(self, adjust: Optional[str]) -> str:
        """Normalize adjusted-price convention."""
        value = (adjust or DEFAULT_ADJUST).strip()
        if value not in {"", "qfq", "hfq"}:
            raise ValueError(f"未知复权口径: {value}")
        return value

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
