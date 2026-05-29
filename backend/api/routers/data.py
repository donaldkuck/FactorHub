"""
数据管理API路由
"""
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from backend.services.data_service import data_service
from backend.services.data_service import DEFAULT_ADJUST
from backend.services.qmt_data_service import qmt_data_service
from backend.core.factor_targets import DEFAULT_FREQUENCY

router = APIRouter()


# ========== 数据模型 ==========

class StockDataRequest(BaseModel):
    """获取股票数据请求"""
    code: str
    start_date: str
    end_date: str


class ImportBarsRequest(BaseModel):
    """导入本地 K 线文件请求"""
    file_path: str
    frequency: str = DEFAULT_FREQUENCY
    adjust: str = DEFAULT_ADJUST
    source: str = "import"
    force: bool = True
    invalidate_derived: bool = True


class QMTConfigRequest(BaseModel):
    enabled: bool = True
    account_id: str = ""
    data_path: str = ""
    trade_path: str = ""
    auto_download_history: bool = True


class QMTSyncRequest(BaseModel):
    stock_codes: Optional[list[str]] = None
    stock_pool_key: Optional[str] = None
    frequency: str = DEFAULT_FREQUENCY
    start_date: str
    end_date: str
    adjust: str = DEFAULT_ADJUST
    source: str = "qmt"
    force: bool = True
    invalidate_derived: bool = True


class AkShareImportRequest(BaseModel):
    stock_codes: Optional[list[str]] = None
    stock_pool_key: Optional[str] = None
    frequency: str = DEFAULT_FREQUENCY
    start_date: str
    end_date: str
    adjust: str = DEFAULT_ADJUST
    source: str = "akshare_em"
    force: bool = True
    invalidate_derived: bool = True


# ========== API端点 ==========

@router.get("/stock/{code}")
async def get_stock_data(
    code: str,
    start_date: str,
    end_date: str,
    frequency: str = DEFAULT_FREQUENCY,
    adjust: str = DEFAULT_ADJUST,
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


@router.post("/bars/import")
def import_bars(request: ImportBarsRequest):
    """从本地 CSV/parquet 文件导入原始 K 线数据。"""
    try:
        result = data_service.import_bar_file(
            file_path=request.file_path,
            frequency=request.frequency,
            adjust=request.adjust,
            source=request.source,
            force=request.force,
            invalidate_derived=request.invalidate_derived,
        )
        return {
            "success": True,
            "data": result,
            "message": f"已导入 {result['rows']} 行 K 线数据",
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/bars/imported/stats")
def get_imported_bar_stats(frequency: Optional[str] = None):
    """获取已导入 K 线数据覆盖范围。"""
    try:
        return {
            "success": True,
            "data": data_service.get_imported_bar_stats(frequency=frequency),
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/bars/imported/coverage")
def get_imported_bar_coverage(
    frequency: Optional[str] = None,
    source: Optional[str] = None,
    stock_code: Optional[str] = None,
    adjust: Optional[str] = None,
    cache_type: str = "all",
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=500),
):
    """按股票/来源/频率查看已导入 K 线覆盖。"""
    try:
        return {
            "success": True,
            "data": data_service.get_imported_bar_coverage(
                frequency=frequency,
                source=source,
                stock_code=stock_code,
                adjust=adjust,
                cache_type=cache_type,
                page=page,
                page_size=page_size,
            ),
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/bars/imported/sample")
def get_imported_bar_sample(
    stock_code: str,
    frequency: str = DEFAULT_FREQUENCY,
    source: Optional[str] = None,
    adjust: Optional[str] = None,
    limit: int = Query(default=20, ge=1, le=200),
):
    """查看某只股票最近的导入 K 线。"""
    try:
        return {
            "success": True,
            "data": data_service.get_imported_bar_sample(
                stock_code=stock_code,
                frequency=frequency,
                source=source,
                adjust=adjust,
                limit=limit,
            ),
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/qmt/config")
def get_qmt_config():
    return {"success": True, "data": qmt_data_service.get_config()}


@router.post("/qmt/config")
def save_qmt_config(request: QMTConfigRequest):
    try:
        return {"success": True, "data": qmt_data_service.save_config(request.model_dump())}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/qmt/status")
def get_qmt_status():
    return {"success": True, "data": qmt_data_service.check_status()}


@router.post("/qmt/sync-bars")
def sync_qmt_bars(request: QMTSyncRequest):
    try:
        result = qmt_data_service.sync_bars(
            stock_codes=request.stock_codes,
            stock_pool_key=request.stock_pool_key,
            frequency=request.frequency,
            start_date=request.start_date,
            end_date=request.end_date,
            adjust=request.adjust,
            source=request.source,
            force=request.force,
            invalidate_derived=request.invalidate_derived,
        )
        return {"success": True, "data": result, "message": f"已同步 {result['rows']} 行 QMT K 线"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ImportError as e:
        raise HTTPException(status_code=400, detail=f"xtquant 不可用，请在 Windows/QMT 环境运行: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/akshare/import-bars")
def import_akshare_bars(request: AkShareImportRequest):
    """从 AkShare/东方财富拉取原始 K 线并写入本地 raw_bar。"""
    try:
        result = data_service.import_akshare_bars(
            stock_codes=request.stock_codes,
            stock_pool_key=request.stock_pool_key,
            frequency=request.frequency,
            start_date=request.start_date,
            end_date=request.end_date,
            adjust=request.adjust,
            source=request.source,
            force=request.force,
            invalidate_derived=request.invalidate_derived,
        )
        return {
            "success": True,
            "data": result,
            "message": f"已导入 {result['rows']} 行 AkShare/东方财富 K 线",
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
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
