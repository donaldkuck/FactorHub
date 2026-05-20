"""
因子值与 target 收益显式数据集 API
"""
from typing import List

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.core.database import get_db_session
from backend.core.factor_targets import DEFAULT_FREQUENCY
from backend.services.factor_dataset_service import factor_dataset_service

router = APIRouter()


class FactorDatasetEnsureRequest(BaseModel):
    """确保并读取因子值/target 收益 join 数据集"""
    factor_id: int
    target: str
    frequency: str = DEFAULT_FREQUENCY
    stock_codes: List[str]
    start_date: str
    end_date: str
    force: bool = False


@router.post("/ensure")
async def ensure_factor_dataset(request: FactorDatasetEnsureRequest):
    """补齐因子值和 target 收益缓存，并返回显式 join 结果"""
    db = get_db_session()
    try:
        result = factor_dataset_service.ensure_dataset(
            db=db,
            factor_id=request.factor_id,
            target=request.target,
            frequency=request.frequency,
            stock_codes=request.stock_codes,
            start_date=request.start_date,
            end_date=request.end_date,
            force=request.force,
        )
        return {"success": True, "data": result}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        db.close()
