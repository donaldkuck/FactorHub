"""
QMT / miniQMT data source integration.

This module is intentionally import-safe on macOS/Linux. xtquant is imported
only inside runtime methods, so the web app can save configuration anywhere,
while actual sync runs on a Windows machine with QMT/miniQMT installed.
"""
from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Optional

import pandas as pd

from backend.core.factor_targets import DEFAULT_FREQUENCY, validate_frequency
from backend.core.settings import settings
from backend.core.stock_pools import get_stock_pool
from backend.services.data_service import data_service
from backend.services.data_service import DEFAULT_ADJUST


QMT_CONFIG_PATH = settings.CONFIG_DIR / "qmt_data_source.json"


def _normalize_stock_code(code: str) -> str:
    code = str(code).strip().upper()
    if code.startswith(("SH", "SZ", "BJ")) and len(code) >= 8:
        market = code[:2]
        return f"{code[2:]}.{market}"
    if "." in code:
        raw_code, market = code.split(".", 1)
        market = market.upper()
        if market in {"SH", "SSE", "XSHG"}:
            return f"{raw_code}.SH"
        if market in {"SZ", "SZSE", "XSHE"}:
            return f"{raw_code}.SZ"
        if market in {"BJ", "BSE"}:
            return f"{raw_code}.BJ"
    if code.startswith("6"):
        return f"{code}.SH"
    if code.startswith(("0", "3")):
        return f"{code}.SZ"
    if code.startswith(("4", "8")):
        return f"{code}.BJ"
    return code


def _to_qmt_symbol(code: str) -> str:
    normalized = _normalize_stock_code(code)
    return normalized


def _qmt_period(frequency: str) -> str:
    frequency_key = validate_frequency(frequency)
    if frequency_key == DEFAULT_FREQUENCY:
        return "1d"
    if frequency_key == "60m":
        return "60m"
    return frequency_key


def _qmt_dividend_type(adjust: str) -> str:
    if adjust == "qfq":
        return "front"
    if adjust == "hfq":
        return "back"
    return "none"


class QMTDataService:
    def get_config(self) -> dict:
        if not QMT_CONFIG_PATH.exists():
            return {
                "enabled": False,
                "account_id": "",
                "data_path": "",
                "trade_path": "",
                "auto_download_history": True,
            }
        try:
            data = json.loads(QMT_CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        return {
            "enabled": bool(data.get("enabled", False)),
            "account_id": data.get("account_id", ""),
            "data_path": data.get("data_path", ""),
            "trade_path": data.get("trade_path", ""),
            "auto_download_history": bool(data.get("auto_download_history", True)),
        }

    def save_config(self, config: dict) -> dict:
        payload = {
            "enabled": bool(config.get("enabled", True)),
            "account_id": str(config.get("account_id") or "").strip(),
            "data_path": str(config.get("data_path") or "").strip(),
            "trade_path": str(config.get("trade_path") or "").strip(),
            "auto_download_history": bool(config.get("auto_download_history", True)),
        }
        QMT_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        QMT_CONFIG_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return payload

    def check_status(self) -> dict:
        config = self.get_config()
        try:
            xtdata = importlib.import_module("xtquant.xtdata")
            xtquant_available = True
            error = None
        except Exception as exc:
            xtdata = None
            xtquant_available = False
            error = f"xtquant 不可用: {exc}"

        data_path = config.get("data_path") or ""
        data_path_exists = bool(data_path) and Path(data_path).exists()
        if xtdata is not None and data_path:
            try:
                if ":" in data_path and not data_path_exists:
                    xtdata.connect(data_path)
                else:
                    xtdata.data_dir = data_path
            except Exception as exc:
                error = f"设置 QMT data_path 失败: {exc}"

        return {
            "configured": bool(config.get("data_path")),
            "enabled": config.get("enabled", False),
            "xtquant_available": xtquant_available,
            "data_path_exists": data_path_exists,
            "account_id_configured": bool(config.get("account_id")),
            "data_path": data_path,
            "trade_path": config.get("trade_path") or "",
            "error": error,
        }

    def sync_bars(
        self,
        stock_codes: Optional[list[str]] = None,
        stock_pool_key: Optional[str] = None,
        frequency: str = "60m",
        start_date: str = "",
        end_date: str = "",
        adjust: str = DEFAULT_ADJUST,
        source: str = "qmt",
        force: bool = True,
        invalidate_derived: bool = True,
    ) -> dict:
        frequency_key = validate_frequency(frequency)
        config = self.get_config()
        if not config.get("enabled", False):
            raise RuntimeError("QMT 数据源未启用")
        if not config.get("data_path"):
            raise RuntimeError("请先配置 QMT data_path")
        if not start_date or not end_date:
            raise ValueError("start_date/end_date 不能为空")

        symbols = self._resolve_symbols(stock_codes, stock_pool_key)
        if not symbols:
            raise ValueError("没有可同步的股票代码")

        xtdata = importlib.import_module("xtquant.xtdata")
        data_path = config["data_path"]
        if ":" in data_path and not Path(data_path).exists():
            xtdata.connect(data_path)
        else:
            xtdata.data_dir = data_path

        qmt_symbols = [_to_qmt_symbol(code) for code in symbols]
        period = _qmt_period(frequency_key)
        start_time = start_date.replace("-", "")
        end_time = end_date.replace("-", "")

        if config.get("auto_download_history", True):
            self._download_history(xtdata, qmt_symbols, period, start_time, end_time)

        raw = xtdata.get_market_data(
            field_list=["open", "high", "low", "close", "volume", "amount"],
            stock_list=qmt_symbols,
            period=period,
            start_time=start_time,
            end_time=end_time,
            dividend_type=_qmt_dividend_type(adjust),
        ) or {}
        frame = self._market_data_to_frame(raw, qmt_symbols, frequency_key)
        if frame.empty:
            raise RuntimeError("QMT 未返回 K 线数据")

        return data_service.import_bar_dataframe(
            frame,
            frequency=frequency_key,
            adjust=adjust,
            source=source,
            force=force,
            invalidate_derived=invalidate_derived,
            file_path=None,
        )

    def _resolve_symbols(self, stock_codes: Optional[list[str]], stock_pool_key: Optional[str]) -> list[str]:
        if stock_codes:
            return [_normalize_stock_code(code) for code in stock_codes if str(code).strip()]
        if stock_pool_key:
            pool = get_stock_pool(stock_pool_key, include_codes=True)
            return [_normalize_stock_code(code) for code in pool.get("stock_codes", [])]
        return []

    def _download_history(self, xtdata, qmt_symbols: list[str], period: str, start_time: str, end_time: str) -> None:
        try:
            xtdata.download_history_data2(qmt_symbols, period, start_time=start_time, end_time=end_time)
        except Exception:
            for symbol in qmt_symbols:
                try:
                    xtdata.download_history_data2([symbol], period, start_time=start_time, end_time=end_time)
                except Exception:
                    continue

    def _market_data_to_frame(self, raw: dict, qmt_symbols: list[str], frequency: str) -> pd.DataFrame:
        records = []
        fields = ["open", "high", "low", "close", "volume", "amount"]
        for qmt_symbol in qmt_symbols:
            normalized = _normalize_stock_code(qmt_symbol)
            series_by_field = {}
            for field in fields:
                data = raw.get(field)
                if data is None or getattr(data, "empty", False):
                    continue
                if isinstance(data, pd.DataFrame):
                    if qmt_symbol in data.index:
                        series_by_field[field] = data.loc[qmt_symbol]
                    elif qmt_symbol in data.columns:
                        series_by_field[field] = data[qmt_symbol]
                elif isinstance(data, pd.Series):
                    series_by_field[field] = data

            if not {"open", "high", "low", "close"}.issubset(series_by_field):
                continue

            index = series_by_field["close"].index
            for bar_time in index:
                row = {"stock_code": normalized, "bar_time": pd.to_datetime(str(bar_time))}
                for field in fields:
                    series = series_by_field.get(field)
                    row[field] = series.get(bar_time) if series is not None else None
                records.append(row)

        if not records:
            return pd.DataFrame()
        frame = pd.DataFrame(records)
        return frame.drop_duplicates(subset=["stock_code", "bar_time"], keep="last")


qmt_data_service = QMTDataService()
