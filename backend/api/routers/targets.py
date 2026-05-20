"""
预测目标收益缓存 API
"""
from typing import List

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.core.database import get_db_session
from backend.core.factor_targets import DEFAULT_FREQUENCY, validate_factor_target
from backend.repositories.factor_repository import TargetReturnCacheRepository
from backend.services.factor_dataset_service import factor_dataset_service

router = APIRouter()


class TargetReturnBackfillRequest(BaseModel):
    """回填 target 收益缓存请求"""
    stock_codes: List[str]
    start_date: str
    end_date: str
    frequency: str = DEFAULT_FREQUENCY
    force: bool = False


@router.post("/{target}/backfill-returns")
async def backfill_target_returns(target: str, request: TargetReturnBackfillRequest):
    """回填某个预测目标的历史收益标签缓存"""
    db = get_db_session()
    try:
        result = factor_dataset_service.backfill_target_returns(
            db=db,
            target=target,
            stock_codes=request.stock_codes,
            start_date=request.start_date,
            end_date=request.end_date,
            frequency=request.frequency,
            force=request.force,
        )
        return {"success": True, "data": result}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        db.close()


@router.get("/{target}/returns")
async def get_target_returns(
    target: str,
    stock_code: str,
    start_date: str,
    end_date: str,
    frequency: str = DEFAULT_FREQUENCY,
):
    """读取某个预测目标的历史收益标签缓存"""
    db = get_db_session()
    try:
        target_key = validate_factor_target(target, frequency)
        rows = TargetReturnCacheRepository(db).get_values(
            target_key,
            stock_code,
            start_date,
            end_date,
            frequency,
        )
        return {"success": True, "data": rows}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        db.close()
