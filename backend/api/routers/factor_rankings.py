"""
因子排名 API 路由

GET  /analysis/factor-rankings            - 查询排名
POST /analysis/factor-rankings/refresh   - 补齐缺失缓存
GET  /analysis/factor-rankings/tasks/{id} - 任务状态
"""
import logging
import json
import hashlib
import multiprocessing
import os
import signal
import uuid
from datetime import datetime
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional, List
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from backend.core.database import get_db_session
from backend.core.settings import settings
from backend.core.factor_targets import (
    DEFAULT_FACTOR_TARGET,
    DEFAULT_FREQUENCY,
    validate_factor_target,
    validate_frequency,
)
from backend.core.stock_pools import DEFAULT_STOCK_POOL, get_stock_pool
from backend.services.factor_ranking_service import factor_ranking_service

router = APIRouter()
logger = logging.getLogger(__name__)


# ========== 任务存储（内存） ==========
ranking_tasks = {}
TASK_DIR = settings.CACHE_DIR / "factor_ranking_tasks"
TASK_DIR.mkdir(parents=True, exist_ok=True)
TASK_STALE_SECONDS = 300


# ========== 数据模型 ==========

class RefreshRequest(BaseModel):
    """排名刷新请求"""
    stock_pool_key: str = DEFAULT_STOCK_POOL
    target: str = DEFAULT_FACTOR_TARGET
    frequency: str = DEFAULT_FREQUENCY
    start_date: str
    end_date: str
    factor_ids: Optional[List[int]] = None
    force: bool = False
    retry_statuses: Optional[List[str]] = None


# ========== API 端点 ==========

@router.get("/factor-rankings")
def get_factor_rankings(
    stock_pool_key: str = Query(default=DEFAULT_STOCK_POOL, description="股票池 key"),
    target: str = Query(default=DEFAULT_FACTOR_TARGET, description="预测目标"),
    frequency: str = Query(default=DEFAULT_FREQUENCY, description="K线频率"),
    start_date: str = Query(..., description="开始日期"),
    end_date: str = Query(..., description="结束日期"),
    sort_by: str = Query(default="ic_mean", description="排序字段"),
    sort_order: str = Query(default="desc", description="排序方向 asc/desc"),
    page: int = Query(default=1, ge=1, description="页码"),
    page_size: int = Query(default=50, ge=1, le=200, description="每页数量"),
):
    """查询全量因子排名（只读缓存）"""
    try:
        validate_frequency(frequency)
        validate_factor_target(target, frequency)
        _verify_stock_pool(stock_pool_key)

        db = get_db_session()
        try:
            result = factor_ranking_service.compute_rankings(
                db=db,
                stock_pool_key=stock_pool_key,
                target=target,
                frequency=frequency,
                start_date=start_date,
                end_date=end_date,
                sort_by=sort_by,
                sort_order=sort_order,
                page=page,
                page_size=page_size,
            )
        finally:
            db.close()

        return {
            "success": True,
            "data": result,
        }
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"排名查询失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/factor-rankings/refresh")
def refresh_factor_rankings(
    request: RefreshRequest,
):
    """启动异步补齐任务"""
    try:
        validate_frequency(request.frequency)
        validate_factor_target(request.target, request.frequency)
        _verify_stock_pool(request.stock_pool_key)

        task_key = _make_task_key(request)
        existing_task = _find_running_task(task_key)
        if existing_task:
            return {
                "success": True,
                "data": {
                    "task_id": existing_task["task_id"],
                    "summary": {
                        "status": existing_task.get("status", "running"),
                        "deduped": True,
                    },
                },
            }

        task_id = str(uuid.uuid4())
        ranking_tasks[task_id] = {
            "task_key": task_key,
            "status": "pending",
            "progress": 0,
            "total_items": 0,
            "cache_hits": 0,
            "computed_items": 0,
            "failed_items": 0,
            "skipped_items": 0,
            "current_factor": None,
            "error": None,
            "pid": None,
        }
        _write_task_status(task_id, ranking_tasks[task_id])

        process = multiprocessing.Process(
            target=_run_refresh_task_process,
            kwargs={
                "task_id": task_id,
                "stock_pool_key": request.stock_pool_key,
                "target": request.target,
                "frequency": request.frequency,
                "start_date": request.start_date,
                "end_date": request.end_date,
                "factor_ids": request.factor_ids,
                "force": request.force,
                "retry_statuses": request.retry_statuses,
            },
            daemon=False,
        )
        process.start()
        ranking_tasks[task_id]["pid"] = process.pid
        _write_task_status(task_id, ranking_tasks[task_id])

        factor_count = len(request.factor_ids) if request.factor_ids else 0
        db = get_db_session()
        try:
            from backend.repositories.factor_repository import FactorRepository
            if not request.factor_ids:
                factors = FactorRepository(db).get_all(active_only=True)
                factor_count = len(factors)
        finally:
            db.close()

        return {
            "success": True,
            "data": {
                "task_id": task_id,
                "summary": {
                    "status": "pending",
                    "estimated_factor_count": factor_count,
                },
            },
        }
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"刷新任务启动失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/factor-rankings/tasks/{task_id}")
def get_refresh_task_status(task_id: str):
    """查询补齐任务状态"""
    task = _read_task_status(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    return {
        "success": True,
        "data": {
            "task_id": task_id,
            "status": task["status"],
            "progress": task["progress"],
            "total_items": task["total_items"],
            "cache_hits": task["cache_hits"],
            "computed_items": task["computed_items"],
            "failed_items": task["failed_items"],
            "skipped_items": task.get("skipped_items", 0),
            "current_factor": task["current_factor"],
            "error": task["error"],
            "pid": task.get("pid"),
        },
    }


@router.post("/factor-rankings/tasks/{task_id}/cancel")
def cancel_refresh_task(task_id: str):
    """取消补齐任务"""
    task = _read_task_status(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    if task.get("status") not in {"pending", "running"}:
        return {"success": True, "data": task}

    pid = task.get("pid")
    if pid:
        try:
            os.kill(int(pid), signal.SIGTERM)
        except ProcessLookupError:
            pass
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"取消任务失败: {e}")

    _update_task_status(task_id, {
        "status": "cancelled",
        "current_factor": None,
        "error": "任务已取消",
    })
    return {"success": True, "data": _read_task_status(task_id)}


# ========== 后台任务 ==========

def _run_refresh_task_process(
    task_id: str,
    stock_pool_key: str,
    target: str,
    frequency: str,
    start_date: str,
    end_date: str,
    factor_ids: Optional[List[int]],
    force: bool,
    retry_statuses: Optional[List[str]] = None,
):
    """在独立子进程中运行排名刷新，避免阻塞 API 进程。"""
    try:
        _update_task_status(task_id, {"status": "running", "pid": os.getpid()})
        logger.info(f"Starting ranking refresh task {task_id}")

        db = get_db_session()
        try:
            def update_progress(progress: dict):
                total_items = progress.get("total_items", 0) or 0
                done_items = (
                    progress.get("cache_hits", 0)
                    + progress.get("computed_items", 0)
                    + progress.get("failed_items", 0)
                    + progress.get("skipped_items", 0)
                )
                _update_task_status(task_id, {
                    "progress": int(done_items * 100 / total_items) if total_items else 0,
                    "total_items": total_items,
                    "cache_hits": progress.get("cache_hits", 0),
                    "computed_items": progress.get("computed_items", 0),
                    "failed_items": progress.get("failed_items", 0),
                    "skipped_items": progress.get("skipped_items", 0),
                    "current_factor": progress.get("current_factor"),
                })

            result = factor_ranking_service.refresh_rankings(
                db=db,
                stock_pool_key=stock_pool_key,
                target=target,
                frequency=frequency,
                start_date=start_date,
                end_date=end_date,
                factor_ids=factor_ids,
                force=force,
                retry_statuses=retry_statuses,
                progress_callback=update_progress,
            )

            final_status = result.get("status", "completed")
            _update_task_status(task_id, {
                "status": final_status,
                "progress": 100 if final_status == "completed" else _read_task_status(task_id).get("progress", 0),
                "total_items": result["total_combos"],
                "cache_hits": result["cache_hits"],
                "computed_items": result["computed_items"],
                "failed_items": result["failed_items"],
                "skipped_items": result.get("skipped_items", 0),
                "current_factor": None,
                "error": result.get("stopped_reason"),
            })
        finally:
            db.close()

        logger.info(
            f"Task {task_id} completed: "
            f"total={result['total_combos']}, hits={result['cache_hits']}, "
            f"computed={result['computed_items']}, failed={result['failed_items']}"
        )
    except Exception as e:
        logger.error(f"Task {task_id} failed: {e}")
        _update_task_status(task_id, {
            "status": "failed",
            "error": str(e),
        })


def _task_status_path(task_id: str) -> Path:
    return TASK_DIR / f"{task_id}.json"


def _make_task_key(request: RefreshRequest) -> str:
    payload = {
        "stock_pool_key": request.stock_pool_key,
        "target": request.target,
        "frequency": request.frequency,
        "start_date": request.start_date,
        "end_date": request.end_date,
        "factor_ids": sorted(request.factor_ids or []),
        "force": request.force,
        "retry_statuses": sorted(request.retry_statuses or []),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _find_running_task(task_key: str) -> Optional[dict]:
    for path in TASK_DIR.glob("*.json"):
        try:
            task = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        task = _mark_stale_task_if_needed(task)
        if task.get("task_key") == task_key and task.get("status") in {"pending", "running"}:
            return task
    return None


def _write_task_status(task_id: str, status: dict) -> None:
    payload = {
        **status,
        "task_id": task_id,
        "updated_at": datetime.now().isoformat(),
    }
    path = _task_status_path(task_id)
    tmp_path = path.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp_path.replace(path)


def _read_task_status(task_id: str) -> Optional[dict]:
    path = _task_status_path(task_id)
    if not path.exists():
        return ranking_tasks.get(task_id)
    try:
        status = json.loads(path.read_text(encoding="utf-8"))
        status = _mark_stale_task_if_needed(status)
        ranking_tasks[task_id] = status
        return status
    except Exception:
        logger.warning(f"读取任务状态失败: {task_id}", exc_info=True)
        return None


def _mark_stale_task_if_needed(task: dict) -> dict:
    if task.get("status") not in {"pending", "running"}:
        return task

    task_id = task.get("task_id")
    pid = task.get("pid")
    if pid and not _is_process_alive(pid):
        task = {
            **task,
            "status": "failed",
            "error": f"任务进程 {pid} 已退出，最后停在 {task.get('current_factor') or '未知因子'}",
        }
        if task_id:
            _write_task_status(task_id, task)
        return task

    updated_at = task.get("updated_at")
    if not updated_at:
        return task

    try:
        updated = datetime.fromisoformat(updated_at)
    except ValueError:
        return task

    if (datetime.now() - updated).total_seconds() < TASK_STALE_SECONDS:
        return task

    if not pid or _is_process_alive(pid):
        task = {
            **task,
            "status": "stalled",
            "error": f"任务超过 {TASK_STALE_SECONDS} 秒未更新，可能卡在行情接口或数据库等待",
        }
        if task_id:
            _write_task_status(task_id, task)
    return task


def _is_process_alive(pid: int | str) -> bool:
    try:
        os.kill(int(pid), 0)
        return True
    except ProcessLookupError:
        return False
    except Exception:
        return True


def _update_task_status(task_id: str, updates: dict) -> None:
    status = _read_task_status(task_id) or {
        "task_key": None,
        "status": "pending",
        "progress": 0,
        "total_items": 0,
        "cache_hits": 0,
        "computed_items": 0,
        "failed_items": 0,
        "skipped_items": 0,
        "current_factor": None,
        "error": None,
        "pid": os.getpid(),
    }
    status.update(updates)
    ranking_tasks[task_id] = status
    _write_task_status(task_id, status)


# ========== 工具函数 ==========

def _verify_stock_pool(stock_pool_key: str):
    """验证股票池有效性"""
    try:
        get_stock_pool(stock_pool_key, include_codes=True)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
