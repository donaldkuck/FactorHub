"""
股票池 API 路由
"""
from fastapi import APIRouter, HTTPException

from backend.core.stock_pools import (
    get_stock_pool,
    list_stock_pools,
    refresh_all_stock_pools,
    refresh_stock_pool,
)


router = APIRouter()


@router.get("")
async def get_stock_pools(include_codes: bool = False):
    """获取本地股票池列表。"""
    return {
        "success": True,
        "data": list_stock_pools(include_codes=include_codes),
    }


@router.get("/{pool_key}")
async def get_stock_pool_detail(pool_key: str, include_codes: bool = True):
    """获取某个股票池详情。"""
    try:
        return {
            "success": True,
            "data": get_stock_pool(pool_key, include_codes=include_codes),
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/{pool_key}/refresh")
async def refresh_one_stock_pool(pool_key: str):
    """手动刷新单个预置股票池，并覆盖本地快照。"""
    try:
        return {
            "success": True,
            "data": refresh_stock_pool(pool_key),
            "message": "股票池已刷新",
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"刷新股票池失败: {exc}")


@router.post("/refresh")
async def refresh_all():
    """手动刷新全部预置股票池，并覆盖本地快照。"""
    try:
        return {
            "success": True,
            "data": refresh_all_stock_pools(),
            "message": "股票池已全部刷新",
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"刷新股票池失败: {exc}")
