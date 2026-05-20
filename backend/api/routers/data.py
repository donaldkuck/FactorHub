"""
数据管理API路由
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from backend.services.data_service import data_service
from backend.core.factor_targets import DEFAULT_FREQUENCY

router = APIRouter()


# ========== 数据模型 ==========

class StockDataRequest(BaseModel):
    """获取股票数据请求"""
    code: str
    start_date: str
    end_date: str


# ========== API端点 ==========

@router.get("/stock/{code}")
async def get_stock_data(
    code: str,
    start_date: str,
    end_date: str,
    frequency: str = DEFAULT_FREQUENCY,
    adjust: str = "qfq",
):
    """
    获取股票数据

    参数:
    - code: 股票代码
    - start_date: 开始日期 (YYYY-MM-DD)
    - end_date: 结束日期 (YYYY-MM-DD)
    """
    try:
        data = data_service.get_stock_bars(
            stock_code=code,
            start_date=start_date,
            end_date=end_date,
            frequency=frequency,
            adjust=adjust,
        )

        if data is None or len(data) == 0:
            raise HTTPException(status_code=404, detail="未获取到数据")

        # 转换为JSON格式
        data_dict = {
            "index": data.index.astype(str).tolist(),
            "columns": data.columns.tolist(),
            "data": data.values.tolist()
        }

        return {
            "success": True,
            "data": data_dict
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/cache/stats")
async def get_cache_stats():
    """获取缓存统计"""
    try:
        stats = data_service.get_cache_stats()
        return {
            "success": True,
            "data": stats
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/cache/cleanup")
async def cleanup_cache():
    """清理过期缓存"""
    try:
        cleaned = data_service.cleanup_cache()
        return {
            "success": True,
            "data": {
                "cleaned_count": cleaned
            },
            "message": f"已清理 {cleaned} 个过期缓存"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/cache/clear")
async def clear_cache():
    """清空全部缓存"""
    try:
        cleared = data_service.clear_cache()
        return {
            "success": True,
            "data": {
                "cleared_count": cleared
            },
            "message": f"已清空 {cleared} 个缓存"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
