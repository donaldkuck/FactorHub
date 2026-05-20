"""
本地股票池定义与更新工具。

第一版把常用指数成分股固化到 data/stock_pools.json，挖掘时只从本地读取；
需要更新时通过手动 API 或命令触发 AkShare 拉取并覆盖本地快照。
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional

import pandas as pd

from backend.core.settings import settings


CUSTOM_STOCK_POOL = "custom"
DEFAULT_STOCK_POOL = "sse50"
STOCK_POOLS_FILE: Path = settings.DATA_DIR / "stock_pools.json"
STOCK_POOL_SOURCE = "akshare.index_stock_cons_csindex"

STOCK_POOL_DEFINITIONS: Dict[str, dict] = {
    "sse50": {"key": "sse50", "label": "上证50", "index_symbol": "000016"},
    "csi300": {"key": "csi300", "label": "沪深300", "index_symbol": "000300"},
    "csi500": {"key": "csi500", "label": "中证500", "index_symbol": "000905"},
    "csi1000": {"key": "csi1000", "label": "中证1000", "index_symbol": "000852"},
}


def normalize_stock_code(raw_code: str, exchange: Optional[str] = None) -> str:
    """Normalize a stock code to 000001.SZ / 600000.SH style."""
    code = str(raw_code or "").strip().upper()
    if not code:
        return ""
    if "." in code:
        number, suffix = code.split(".", 1)
        return f"{number.zfill(6)}.{suffix[:2]}"

    number = "".join(ch for ch in code if ch.isdigit()).zfill(6)
    exchange_text = str(exchange or "")
    if "上海" in exchange_text or exchange_text.upper() in {"SH", "SSE"}:
        suffix = "SH"
    elif "深圳" in exchange_text or exchange_text.upper() in {"SZ", "SZSE"}:
        suffix = "SZ"
    elif "北京" in exchange_text or exchange_text.upper() in {"BJ", "BSE"}:
        suffix = "BJ"
    elif number.startswith("6"):
        suffix = "SH"
    elif number.startswith(("4", "8")):
        suffix = "BJ"
    else:
        suffix = "SZ"
    return f"{number}.{suffix}"


def _dedupe_codes(codes: Iterable[str]) -> List[str]:
    normalized = []
    seen = set()
    for code in codes:
        normalized_code = normalize_stock_code(code)
        if not normalized_code or normalized_code in seen:
            continue
        normalized.append(normalized_code)
        seen.add(normalized_code)
    return normalized


def _load_snapshot() -> Dict[str, dict]:
    if not STOCK_POOLS_FILE.exists():
        return {}
    try:
        return json.loads(STOCK_POOLS_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _save_snapshot(snapshot: Dict[str, dict]) -> None:
    STOCK_POOLS_FILE.parent.mkdir(parents=True, exist_ok=True)
    STOCK_POOLS_FILE.write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def list_stock_pools(include_codes: bool = False) -> List[dict]:
    """List preset stock pools plus custom manual input option."""
    snapshot = _load_snapshot()
    pools = []
    for key, definition in STOCK_POOL_DEFINITIONS.items():
        entry = snapshot.get(key, {})
        item = {
            **definition,
            "source": entry.get("source", STOCK_POOL_SOURCE),
            "updated_at": entry.get("updated_at"),
            "trade_date": entry.get("trade_date"),
            "stock_count": len(entry.get("stock_codes", [])),
            "is_custom": False,
        }
        if include_codes:
            item["stock_codes"] = entry.get("stock_codes", [])
        pools.append(item)

    pools.append(
        {
            "key": CUSTOM_STOCK_POOL,
            "label": "自定义",
            "index_symbol": None,
            "source": "manual",
            "updated_at": None,
            "trade_date": None,
            "stock_count": 0,
            "is_custom": True,
            **({"stock_codes": []} if include_codes else {}),
        }
    )
    return pools


def get_stock_pool(pool_key: str, include_codes: bool = True) -> dict:
    key = (pool_key or "").strip()
    if key == CUSTOM_STOCK_POOL:
        return next(
            item
            for item in list_stock_pools(include_codes=include_codes)
            if item["key"] == CUSTOM_STOCK_POOL
        )
    if key not in STOCK_POOL_DEFINITIONS:
        raise ValueError(f"未知股票池: {pool_key}")

    snapshot = _load_snapshot()
    entry = snapshot.get(key)
    if not entry:
        definition = STOCK_POOL_DEFINITIONS[key]
        entry = {
            **definition,
            "source": STOCK_POOL_SOURCE,
            "updated_at": None,
            "trade_date": None,
            "stock_codes": [],
        }
    result = {
        **STOCK_POOL_DEFINITIONS[key],
        "source": entry.get("source", STOCK_POOL_SOURCE),
        "updated_at": entry.get("updated_at"),
        "trade_date": entry.get("trade_date"),
        "stock_count": len(entry.get("stock_codes", [])),
        "is_custom": False,
    }
    if include_codes:
        result["stock_codes"] = entry.get("stock_codes", [])
    return result


def resolve_stock_codes(stock_pool: Optional[str], stock_codes: Optional[Iterable[str]] = None) -> List[str]:
    """Resolve request stock pool to concrete local stock codes."""
    pool_key = (stock_pool or CUSTOM_STOCK_POOL).strip()
    manual_codes = _dedupe_codes(stock_codes or [])

    if pool_key == CUSTOM_STOCK_POOL:
        if not manual_codes:
            raise ValueError("请至少输入一只股票")
        return manual_codes

    pool = get_stock_pool(pool_key, include_codes=True)
    codes = pool.get("stock_codes", [])
    if not codes:
        raise ValueError(f"股票池 {pool.get('label', pool_key)} 未初始化，请先刷新股票池")
    return codes


def _pick_column(df: pd.DataFrame, candidates: List[str]) -> str:
    for column in candidates:
        if column in df.columns:
            return column
    raise ValueError(f"AkShare 返回数据缺少字段: {', '.join(candidates)}")


def _normalize_index_cons_df(pool_key: str, df: pd.DataFrame) -> dict:
    definition = STOCK_POOL_DEFINITIONS[pool_key]
    code_col = _pick_column(df, ["成分券代码", "证券代码", "品种代码", "代码"])
    exchange_col = next((column for column in ["交易所", "市场", "交易市场"] if column in df.columns), None)
    date_col = next((column for column in ["日期", "生效日期", "更新时间"] if column in df.columns), None)

    codes = []
    seen = set()
    for _, row in df.iterrows():
        code = normalize_stock_code(row[code_col], row[exchange_col] if exchange_col else None)
        if code and code not in seen:
            codes.append(code)
            seen.add(code)

    trade_date = None
    if date_col and not df.empty:
        trade_date = str(df[date_col].dropna().iloc[0]) if not df[date_col].dropna().empty else None

    return {
        **definition,
        "source": STOCK_POOL_SOURCE,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "trade_date": trade_date,
        "stock_count": len(codes),
        "stock_codes": codes,
    }


def _default_fetcher(symbol: str) -> pd.DataFrame:
    import akshare as ak

    return ak.index_stock_cons_csindex(symbol=symbol)


def refresh_stock_pool(pool_key: str, fetcher: Optional[Callable[[str], pd.DataFrame]] = None) -> dict:
    """Fetch one preset pool from AkShare and persist it to local snapshot."""
    key = (pool_key or "").strip()
    if key == CUSTOM_STOCK_POOL:
        raise ValueError("自定义股票池不需要刷新")
    if key not in STOCK_POOL_DEFINITIONS:
        raise ValueError(f"未知股票池: {pool_key}")

    fetch = fetcher or _default_fetcher
    raw_df = fetch(STOCK_POOL_DEFINITIONS[key]["index_symbol"])
    entry = _normalize_index_cons_df(key, raw_df)
    if not entry["stock_codes"]:
        raise ValueError(f"股票池 {entry['label']} 未获取到成分股")

    snapshot = _load_snapshot()
    snapshot[key] = entry
    _save_snapshot(snapshot)
    return entry


def refresh_all_stock_pools(fetcher: Optional[Callable[[str], pd.DataFrame]] = None) -> Dict[str, dict]:
    result = {}
    for key in STOCK_POOL_DEFINITIONS:
        result[key] = refresh_stock_pool(key, fetcher=fetcher)
    return result


if __name__ == "__main__":
    refreshed = refresh_all_stock_pools()
    for key, entry in refreshed.items():
        print(f"{key}: {entry['stock_count']} stocks, trade_date={entry.get('trade_date')}")
