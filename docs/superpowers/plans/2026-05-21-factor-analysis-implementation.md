# Factor Analysis Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an independent Factor Analysis page backed by reusable per-bar factor performance caches so users can rank all factors for one stock pool, target, frequency, and time window without repeated full recomputation.

**Architecture:** Add a backend `factor_performance_bar_cache` model/repository, then a ranking service that resolves a stock pool snapshot, reuses target/factor value caches, computes only missing per-bar IC metrics, and aggregates cached bars into rankings. Expose ranking query, refresh, and task-status endpoints under `/api/analysis`, then add a React/Ant Design page that reads rankings, starts refresh tasks, polls status, and renders the table plus cache summaries.

**Tech Stack:** Python 3.10+, FastAPI, SQLAlchemy 2, pandas/numpy, pytest, React 19, TypeScript, Ant Design, ECharts, Vite, Vitest.

---

## File Structure

- Create `backend/repositories/factor_performance_repository.py`
  - Owns all reads/writes for `factor_performance_bar_cache`.
  - Provides upsert, existing-bar lookup, target-bar expectation lookup, ranking aggregation, and cache summary helpers.
- Modify `backend/models/factor.py`
  - Adds `FactorPerformanceBarCacheModel`.
- Modify `backend/core/database.py`
  - Imports the new model during `init_db()` so the table is created.
- Create `backend/services/factor_ranking_service.py`
  - Owns stock-pool snapshot hashing, incremental refresh, per-bar IC calculation, ranking aggregation, and in-memory task state.
- Modify `backend/api/routers/analysis.py`
  - Adds `/factor-rankings`, `/factor-rankings/refresh`, and `/factor-rankings/tasks/{task_id}`.
- Create `tests/test_factor_performance_repository.py`
  - Repository-level tests for unique keys, force behavior, and ranking aggregation.
- Create `tests/test_factor_ranking_service.py`
  - Service-level tests proving cached bars skip computation and missing bars compute only once.
- Create `tests/test_factor_ranking_api.py`
  - Route tests for query, refresh, status, validation, and response shape.
- Modify `tests/conftest.py`
  - Adds reusable in-memory SQLite fixtures.
- Modify `frontend/react-antd/package.json`
  - Adds a `test` script and Vitest dev dependency.
- Modify `frontend/react-antd/vite.config.ts`
  - Adds Vitest config.
- Modify `frontend/react-antd/src/services/api.ts`
  - Adds typed factor ranking API methods.
- Create `frontend/react-antd/src/pages/FactorAnalysis.utils.ts`
  - Pure helpers for query defaults, target/frequency linkage, percent formatting, and cache summary normalization.
- Create `frontend/react-antd/src/pages/FactorAnalysis.utils.test.ts`
  - Vitest coverage for the page helpers.
- Create `frontend/react-antd/src/pages/FactorAnalysis.tsx`
  - Independent page with filters, cache status, refresh polling, ranking table, and summary charts.
- Create `frontend/react-antd/src/pages/FactorAnalysis.css`
  - Scoped layout styles for the page.
- Modify `frontend/react-antd/src/utils/router.tsx`
  - Adds `/factor-analysis` route and menu entry.
- Modify `frontend/react-antd/src/App.tsx`
  - Imports `BarChartOutlined` and supports it in `createIcon`.

---

### Task 1: Backend Cache Model And Repository

**Files:**
- Modify: `tests/conftest.py`
- Create: `tests/test_factor_performance_repository.py`
- Modify: `backend/models/factor.py`
- Modify: `backend/core/database.py`
- Create: `backend/repositories/factor_performance_repository.py`

- [ ] **Step 1: Write the failing repository tests**

Put this in `tests/conftest.py`:

```python
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.core.database import Base


@pytest.fixture()
def db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)
```

Create `tests/test_factor_performance_repository.py`:

```python
from datetime import datetime

import pytest

from backend.models.factor import FactorModel, TargetReturnCacheModel
from backend.repositories.factor_performance_repository import FactorPerformanceRepository


def _create_factor(db_session, name="momentum_20d", code="close.pct_change(20)"):
    factor = FactorModel(
        name=name,
        code=code,
        category="technical",
        source="user",
        is_active=1,
    )
    db_session.add(factor)
    db_session.commit()
    db_session.refresh(factor)
    return factor


def test_upsert_metric_skips_existing_metric_unless_force(db_session):
    factor = _create_factor(db_session)
    repo = FactorPerformanceRepository(db_session)
    bar_time = datetime(2026, 1, 2)

    first = repo.upsert_metric(
        factor_id=factor.id,
        factor_name=factor.name,
        factor_code_hash="hash-a",
        stock_pool_key="csi300",
        stock_pool_snapshot_hash="pool-hash",
        target="next_1d_return",
        frequency="1d",
        bar_time=bar_time,
        metric_version="v1",
        ic_value=0.12,
        sample_size=300,
        coverage=1.0,
        status="success",
        error_message=None,
        force=False,
    )
    second = repo.upsert_metric(
        factor_id=factor.id,
        factor_name=factor.name,
        factor_code_hash="hash-a",
        stock_pool_key="csi300",
        stock_pool_snapshot_hash="pool-hash",
        target="next_1d_return",
        frequency="1d",
        bar_time=bar_time,
        metric_version="v1",
        ic_value=0.34,
        sample_size=300,
        coverage=1.0,
        status="success",
        error_message=None,
        force=False,
    )
    forced = repo.upsert_metric(
        factor_id=factor.id,
        factor_name=factor.name,
        factor_code_hash="hash-a",
        stock_pool_key="csi300",
        stock_pool_snapshot_hash="pool-hash",
        target="next_1d_return",
        frequency="1d",
        bar_time=bar_time,
        metric_version="v1",
        ic_value=0.34,
        sample_size=280,
        coverage=0.93,
        status="success",
        error_message=None,
        force=True,
    )

    assert first == "created"
    assert second == "skipped"
    assert forced == "updated"

    rows = repo.list_metrics(
        stock_pool_key="csi300",
        stock_pool_snapshot_hash="pool-hash",
        target="next_1d_return",
        frequency="1d",
        start_date="2026-01-01",
        end_date="2026-01-31",
        metric_version="v1",
    )
    assert len(rows) == 1
    assert rows[0]["ic_value"] == pytest.approx(0.34)
    assert rows[0]["sample_size"] == 280


def test_query_rankings_aggregates_metrics_and_sorts(db_session):
    first = _create_factor(db_session, name="factor_a")
    second = _create_factor(db_session, name="factor_b")
    repo = FactorPerformanceRepository(db_session)

    for factor, values in [(first, [0.10, 0.20, -0.05]), (second, [0.03, 0.01, 0.02])]:
        for day, ic in enumerate(values, start=1):
            repo.upsert_metric(
                factor_id=factor.id,
                factor_name=factor.name,
                factor_code_hash=f"hash-{factor.id}",
                stock_pool_key="csi300",
                stock_pool_snapshot_hash="pool-hash",
                target="next_1d_return",
                frequency="1d",
                bar_time=datetime(2026, 1, day),
                metric_version="v1",
                ic_value=ic,
                sample_size=280,
                coverage=0.9,
                status="success",
                error_message=None,
                force=False,
            )

    result = repo.query_rankings(
        stock_pool_key="csi300",
        stock_pool_snapshot_hash="pool-hash",
        target="next_1d_return",
        frequency="1d",
        start_date="2026-01-01",
        end_date="2026-01-31",
        metric_version="v1",
        sort_by="ic_mean",
        sort_order="desc",
        page=1,
        page_size=20,
        status=None,
    )

    assert result["total"] == 2
    assert [item["factor_name"] for item in result["items"]] == ["factor_a", "factor_b"]
    assert result["items"][0]["rank"] == 1
    assert result["items"][0]["ic_mean"] == pytest.approx((0.10 + 0.20 - 0.05) / 3)
    assert result["items"][0]["bar_count"] == 3
    assert result["items"][0]["ic_positive_ratio"] == pytest.approx(2 / 3)
    assert result["items"][0]["coverage"] == pytest.approx(0.9)


def test_get_expected_bar_times_from_target_return_cache(db_session):
    for stock_code in ["000001.SZ", "000002.SZ", "600000.SH"]:
        for day in [1, 2]:
            db_session.add(
                TargetReturnCacheModel(
                    target="next_1d_return",
                    stock_code=stock_code,
                    frequency="1d",
                    bar_time=datetime(2026, 1, day),
                    value=0.01,
                )
            )
    db_session.commit()

    repo = FactorPerformanceRepository(db_session)
    bars = repo.get_target_bar_counts(
        target="next_1d_return",
        stock_codes=["000001.SZ", "000002.SZ", "600000.SH"],
        start_date="2026-01-01",
        end_date="2026-01-31",
        frequency="1d",
    )

    assert bars == {
        datetime(2026, 1, 1): 3,
        datetime(2026, 1, 2): 3,
    }
```

- [ ] **Step 2: Run repository tests and verify they fail for the right reason**

Run:

```bash
uv run pytest tests/test_factor_performance_repository.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'backend.repositories.factor_performance_repository'` or `ImportError` for `FactorPerformanceBarCacheModel`.

- [ ] **Step 3: Add the cache model**

In `backend/models/factor.py`, add this class after `TargetReturnCacheModel`:

```python
class FactorPerformanceBarCacheModel(Base):
    """Cached per-bar factor performance metrics for one stock-pool snapshot."""

    __tablename__ = "factor_performance_bar_cache"
    __table_args__ = (
        UniqueConstraint(
            "factor_id",
            "factor_code_hash",
            "stock_pool_key",
            "stock_pool_snapshot_hash",
            "target",
            "frequency",
            "bar_time",
            "metric_version",
            name="uq_factor_performance_bar_cache_key",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    factor_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    factor_name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    factor_code_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    stock_pool_key: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    stock_pool_snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    target: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    frequency: Mapped[str] = mapped_column(String(20), nullable=False, default=DEFAULT_FREQUENCY, index=True)
    bar_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    metric_version: Mapped[str] = mapped_column(String(20), nullable=False, default="v1", index=True)
    ic_value: Mapped[float] = mapped_column(Float, nullable=True)
    sample_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    coverage: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="success", index=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "factor_id": self.factor_id,
            "factor_name": self.factor_name,
            "factor_code_hash": self.factor_code_hash,
            "stock_pool_key": self.stock_pool_key,
            "stock_pool_snapshot_hash": self.stock_pool_snapshot_hash,
            "target": self.target,
            "frequency": self.frequency,
            "bar_time": self.bar_time.isoformat() if self.bar_time else None,
            "metric_version": self.metric_version,
            "ic_value": self.ic_value,
            "sample_size": self.sample_size,
            "coverage": self.coverage,
            "status": self.status,
            "error_message": self.error_message,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
```

Update the import list in `backend/core/database.py:init_db()`:

```python
from backend.models.factor import (
    FactorModel,
    AnalysisCacheModel,
    FactorValueCacheModel,
    TargetReturnCacheModel,
    FactorPerformanceBarCacheModel,
)
```

- [ ] **Step 4: Add the repository implementation**

Create `backend/repositories/factor_performance_repository.py` with:

```python
from __future__ import annotations

from datetime import date, datetime
from typing import Optional

import numpy as np
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from backend.core.factor_targets import DEFAULT_FREQUENCY, validate_frequency
from backend.models.factor import FactorModel, FactorPerformanceBarCacheModel, TargetReturnCacheModel
from backend.repositories.factor_repository import _to_datetime


VALID_RANKING_SORTS = {
    "ic_mean",
    "ic_std",
    "ir",
    "ic_positive_ratio",
    "bar_count",
    "sample_size",
    "coverage",
    "last_updated_at",
}


class FactorPerformanceRepository:
    def __init__(self, db: Session):
        self.db = db

    def upsert_metric(
        self,
        *,
        factor_id: int,
        factor_name: str,
        factor_code_hash: str,
        stock_pool_key: str,
        stock_pool_snapshot_hash: str,
        target: str,
        frequency: str,
        bar_time,
        metric_version: str,
        ic_value: float | None,
        sample_size: int,
        coverage: float,
        status: str,
        error_message: str | None,
        force: bool = False,
    ) -> str:
        frequency_key = validate_frequency(frequency or DEFAULT_FREQUENCY)
        parsed_bar_time = _to_datetime(bar_time)
        existing = self.db.scalar(
            select(FactorPerformanceBarCacheModel)
            .where(FactorPerformanceBarCacheModel.factor_id == factor_id)
            .where(FactorPerformanceBarCacheModel.factor_code_hash == factor_code_hash)
            .where(FactorPerformanceBarCacheModel.stock_pool_key == stock_pool_key)
            .where(FactorPerformanceBarCacheModel.stock_pool_snapshot_hash == stock_pool_snapshot_hash)
            .where(FactorPerformanceBarCacheModel.target == target)
            .where(FactorPerformanceBarCacheModel.frequency == frequency_key)
            .where(FactorPerformanceBarCacheModel.bar_time == parsed_bar_time)
            .where(FactorPerformanceBarCacheModel.metric_version == metric_version)
        )
        if existing:
            if not force:
                return "skipped"
            existing.factor_name = factor_name
            existing.ic_value = ic_value
            existing.sample_size = int(sample_size)
            existing.coverage = float(coverage)
            existing.status = status
            existing.error_message = error_message
            self.db.commit()
            return "updated"

        self.db.add(
            FactorPerformanceBarCacheModel(
                factor_id=factor_id,
                factor_name=factor_name,
                factor_code_hash=factor_code_hash,
                stock_pool_key=stock_pool_key,
                stock_pool_snapshot_hash=stock_pool_snapshot_hash,
                target=target,
                frequency=frequency_key,
                bar_time=parsed_bar_time,
                metric_version=metric_version,
                ic_value=ic_value,
                sample_size=int(sample_size),
                coverage=float(coverage),
                status=status,
                error_message=error_message,
            )
        )
        self.db.commit()
        return "created"

    def list_metrics(
        self,
        *,
        stock_pool_key: str,
        stock_pool_snapshot_hash: str,
        target: str,
        frequency: str,
        start_date: str,
        end_date: str,
        metric_version: str,
        factor_id: int | None = None,
    ) -> list[dict]:
        frequency_key = validate_frequency(frequency)
        query = (
            select(FactorPerformanceBarCacheModel)
            .where(FactorPerformanceBarCacheModel.stock_pool_key == stock_pool_key)
            .where(FactorPerformanceBarCacheModel.stock_pool_snapshot_hash == stock_pool_snapshot_hash)
            .where(FactorPerformanceBarCacheModel.target == target)
            .where(FactorPerformanceBarCacheModel.frequency == frequency_key)
            .where(FactorPerformanceBarCacheModel.bar_time >= _to_datetime(start_date))
            .where(FactorPerformanceBarCacheModel.bar_time <= _to_datetime(end_date))
            .where(FactorPerformanceBarCacheModel.metric_version == metric_version)
            .order_by(FactorPerformanceBarCacheModel.factor_id, FactorPerformanceBarCacheModel.bar_time)
        )
        if factor_id is not None:
            query = query.where(FactorPerformanceBarCacheModel.factor_id == factor_id)
        return [row.to_dict() for row in self.db.scalars(query).all()]

    def get_existing_bar_times(
        self,
        *,
        factor_id: int,
        factor_code_hash: str,
        stock_pool_key: str,
        stock_pool_snapshot_hash: str,
        target: str,
        frequency: str,
        start_date: str,
        end_date: str,
        metric_version: str,
    ) -> set[datetime]:
        rows = self.db.scalars(
            select(FactorPerformanceBarCacheModel.bar_time)
            .where(FactorPerformanceBarCacheModel.factor_id == factor_id)
            .where(FactorPerformanceBarCacheModel.factor_code_hash == factor_code_hash)
            .where(FactorPerformanceBarCacheModel.stock_pool_key == stock_pool_key)
            .where(FactorPerformanceBarCacheModel.stock_pool_snapshot_hash == stock_pool_snapshot_hash)
            .where(FactorPerformanceBarCacheModel.target == target)
            .where(FactorPerformanceBarCacheModel.frequency == validate_frequency(frequency))
            .where(FactorPerformanceBarCacheModel.metric_version == metric_version)
            .where(FactorPerformanceBarCacheModel.bar_time >= _to_datetime(start_date))
            .where(FactorPerformanceBarCacheModel.bar_time <= _to_datetime(end_date))
            .where(FactorPerformanceBarCacheModel.status.in_(["success", "insufficient_data"]))
        ).all()
        return set(rows)

    def get_target_bar_counts(
        self,
        *,
        target: str,
        stock_codes: list[str],
        start_date: str,
        end_date: str,
        frequency: str,
    ) -> dict[datetime, int]:
        if not stock_codes:
            return {}
        rows = self.db.execute(
            select(TargetReturnCacheModel.bar_time, func.count(TargetReturnCacheModel.stock_code))
            .where(TargetReturnCacheModel.target == target)
            .where(TargetReturnCacheModel.stock_code.in_(stock_codes))
            .where(TargetReturnCacheModel.frequency == validate_frequency(frequency))
            .where(TargetReturnCacheModel.bar_time >= _to_datetime(start_date))
            .where(TargetReturnCacheModel.bar_time <= _to_datetime(end_date))
            .where(TargetReturnCacheModel.value.is_not(None))
            .group_by(TargetReturnCacheModel.bar_time)
            .order_by(TargetReturnCacheModel.bar_time)
        ).all()
        return {bar_time: count for bar_time, count in rows if count >= 2}

    def query_rankings(
        self,
        *,
        stock_pool_key: str,
        stock_pool_snapshot_hash: str,
        target: str,
        frequency: str,
        start_date: str,
        end_date: str,
        metric_version: str,
        sort_by: str,
        sort_order: str,
        page: int,
        page_size: int,
        status: Optional[str],
    ) -> dict:
        rows = self.list_metrics(
            stock_pool_key=stock_pool_key,
            stock_pool_snapshot_hash=stock_pool_snapshot_hash,
            target=target,
            frequency=frequency,
            start_date=start_date,
            end_date=end_date,
            metric_version=metric_version,
        )
        if status:
            rows = [row for row in rows if row["status"] == status]

        factors = {
            factor.id: factor
            for factor in self.db.scalars(select(FactorModel).where(FactorModel.is_active == 1)).all()
        }
        grouped: dict[int, list[dict]] = {}
        for row in rows:
            grouped.setdefault(row["factor_id"], []).append(row)

        items = []
        for factor_id, factor_rows in grouped.items():
            ic_values = [
                float(row["ic_value"])
                for row in factor_rows
                if row["status"] == "success" and row["ic_value"] is not None
            ]
            bar_count = len(ic_values)
            ic_mean = float(np.mean(ic_values)) if ic_values else None
            ic_std = float(np.std(ic_values)) if len(ic_values) > 1 else 0.0
            ir = float(ic_mean / ic_std) if ic_mean is not None and ic_std else 0.0
            factor = factors.get(factor_id)
            item = {
                "rank": 0,
                "factor_id": factor_id,
                "factor_name": factor_rows[0]["factor_name"],
                "category": factor.category if factor else "",
                "source": factor.source if factor else "",
                "ic_mean": ic_mean,
                "ic_std": ic_std,
                "ir": ir,
                "ic_positive_ratio": float(sum(value > 0 for value in ic_values) / bar_count) if bar_count else None,
                "bar_count": bar_count,
                "sample_size": float(np.mean([row["sample_size"] for row in factor_rows])) if factor_rows else 0.0,
                "coverage": float(np.mean([row["coverage"] for row in factor_rows])) if factor_rows else 0.0,
                "status": "failed" if any(row["status"] == "failed" for row in factor_rows) else "success",
                "error_message": next((row["error_message"] for row in factor_rows if row["error_message"]), None),
                "last_updated_at": max(row["updated_at"] for row in factor_rows if row["updated_at"]),
            }
            items.append(item)

        sort_key = sort_by if sort_by in VALID_RANKING_SORTS else "ic_mean"
        reverse = sort_order != "asc"
        sortable_items = [item for item in items if item[sort_key] is not None]
        null_items = [item for item in items if item[sort_key] is None]
        sortable_items.sort(key=lambda item: item[sort_key] or 0, reverse=reverse)
        items = sortable_items + null_items
        for index, item in enumerate(items, start=1):
            item["rank"] = index

        total = len(items)
        safe_page = max(int(page), 1)
        safe_page_size = min(max(int(page_size), 1), 200)
        start = (safe_page - 1) * safe_page_size
        return {
            "items": items[start : start + safe_page_size],
            "total": total,
        }

    def get_cache_summary(
        self,
        *,
        stock_pool_key: str,
        stock_pool_snapshot_hash: str,
        target: str,
        frequency: str,
        start_date: str,
        end_date: str,
        metric_version: str,
        active_factor_count: int,
    ) -> dict:
        rows = self.list_metrics(
            stock_pool_key=stock_pool_key,
            stock_pool_snapshot_hash=stock_pool_snapshot_hash,
            target=target,
            frequency=frequency,
            start_date=start_date,
            end_date=end_date,
            metric_version=metric_version,
        )
        cached_factor_ids = {row["factor_id"] for row in rows}
        failed_factor_ids = {row["factor_id"] for row in rows if row["status"] == "failed"}
        latest_bar_time = max((row["bar_time"] for row in rows), default="")
        return {
            "stock_pool_key": stock_pool_key,
            "stock_pool_snapshot_hash": stock_pool_snapshot_hash,
            "target": target,
            "frequency": frequency,
            "active_factor_count": active_factor_count,
            "cached_factor_count": len(cached_factor_ids),
            "missing_factor_count": max(active_factor_count - len(cached_factor_ids), 0),
            "failed_factor_count": len(failed_factor_ids),
            "latest_bar_time": latest_bar_time,
        }
```

- [ ] **Step 5: Run repository tests and verify they pass**

Run:

```bash
uv run pytest tests/test_factor_performance_repository.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit Task 1**

Run:

```bash
git add tests/conftest.py tests/test_factor_performance_repository.py backend/models/factor.py backend/core/database.py backend/repositories/factor_performance_repository.py
git commit -m "feat: add factor performance cache repository"
```

---

### Task 2: Backend Ranking Service With Incremental Refresh

**Files:**
- Create: `tests/test_factor_ranking_service.py`
- Create: `backend/services/factor_ranking_service.py`

- [ ] **Step 1: Write failing service tests**

Create `tests/test_factor_ranking_service.py`:

```python
from datetime import datetime

import pytest

from backend.models.factor import FactorModel, TargetReturnCacheModel
from backend.repositories.factor_performance_repository import FactorPerformanceRepository
from backend.services.factor_dataset_service import hash_factor_code
from backend.services.factor_ranking_service import (
    METRIC_VERSION,
    factor_ranking_service,
    hash_stock_pool_snapshot,
)


def _factor(db_session, name="factor_a", code="close.pct_change(1)"):
    factor = FactorModel(
        name=name,
        code=code,
        category="technical",
        source="user",
        is_active=1,
    )
    db_session.add(factor)
    db_session.commit()
    db_session.refresh(factor)
    return factor


def _seed_target_returns(db_session, target="next_1d_return"):
    for stock_code, values in {
        "000001.SZ": [0.01, 0.03],
        "000002.SZ": [0.02, 0.01],
        "600000.SH": [-0.01, 0.02],
    }.items():
        for day, value in enumerate(values, start=1):
            db_session.add(
                TargetReturnCacheModel(
                    target=target,
                    stock_code=stock_code,
                    frequency="1d",
                    bar_time=datetime(2026, 1, day),
                    value=value,
                )
            )
    db_session.commit()


def test_hash_stock_pool_snapshot_is_order_insensitive():
    assert hash_stock_pool_snapshot(["000002.SZ", "000001.SZ"]) == hash_stock_pool_snapshot(
        ["000001.SZ", "000002.SZ"]
    )


def test_refresh_skips_factor_when_all_expected_bars_cached(db_session, monkeypatch):
    factor = _factor(db_session)
    stock_codes = ["000001.SZ", "000002.SZ", "600000.SH"]
    snapshot_hash = hash_stock_pool_snapshot(stock_codes)
    _seed_target_returns(db_session)
    repo = FactorPerformanceRepository(db_session)
    for day in [1, 2]:
        repo.upsert_metric(
            factor_id=factor.id,
            factor_name=factor.name,
            factor_code_hash=hash_factor_code(factor.code),
            stock_pool_key="csi300",
            stock_pool_snapshot_hash=snapshot_hash,
            target="next_1d_return",
            frequency="1d",
            bar_time=datetime(2026, 1, day),
            metric_version=METRIC_VERSION,
            ic_value=0.1,
            sample_size=3,
            coverage=1.0,
            status="success",
            error_message=None,
            force=False,
        )

    monkeypatch.setattr(
        "backend.services.factor_ranking_service.get_stock_pool",
        lambda stock_pool_key, include_codes=True: {"key": stock_pool_key, "stock_codes": stock_codes},
    )
    calls = []
    target_backfill_calls = []
    monkeypatch.setattr(
        "backend.services.factor_ranking_service.factor_dataset_service.backfill_target_returns",
        lambda *args, **kwargs: target_backfill_calls.append(kwargs),
    )
    monkeypatch.setattr(
        "backend.services.factor_ranking_service.factor_dataset_service.ensure_dataset",
        lambda *args, **kwargs: calls.append(kwargs),
    )

    result = factor_ranking_service.refresh_rankings(
        db=db_session,
        stock_pool_key="csi300",
        target="next_1d_return",
        frequency="1d",
        start_date="2026-01-01",
        end_date="2026-01-02",
        factor_ids=None,
        force=False,
    )

    assert target_backfill_calls == []
    assert calls == []
    assert result["cache_hits"] == 2
    assert result["computed_items"] == 0


def test_refresh_computes_only_missing_bar_metric(db_session, monkeypatch):
    factor = _factor(db_session)
    stock_codes = ["000001.SZ", "000002.SZ", "600000.SH"]
    snapshot_hash = hash_stock_pool_snapshot(stock_codes)
    _seed_target_returns(db_session)
    repo = FactorPerformanceRepository(db_session)
    repo.upsert_metric(
        factor_id=factor.id,
        factor_name=factor.name,
        factor_code_hash=hash_factor_code(factor.code),
        stock_pool_key="csi300",
        stock_pool_snapshot_hash=snapshot_hash,
        target="next_1d_return",
        frequency="1d",
        bar_time=datetime(2026, 1, 1),
        metric_version=METRIC_VERSION,
        ic_value=0.1,
        sample_size=3,
        coverage=1.0,
        status="success",
        error_message=None,
        force=False,
    )

    monkeypatch.setattr(
        "backend.services.factor_ranking_service.get_stock_pool",
        lambda stock_pool_key, include_codes=True: {"key": stock_pool_key, "stock_codes": stock_codes},
    )
    monkeypatch.setattr(
        "backend.services.factor_ranking_service.factor_dataset_service.backfill_target_returns",
        lambda *args, **kwargs: None,
    )
    datasets = []

    def fake_ensure_dataset(**kwargs):
        datasets.append(kwargs)
        return {
            "rows": [
                {"stock_code": "000001.SZ", "bar_time": "2026-01-02T00:00:00", "factor_value": 1.0, "target_return": 0.03},
                {"stock_code": "000002.SZ", "bar_time": "2026-01-02T00:00:00", "factor_value": 2.0, "target_return": 0.01},
                {"stock_code": "600000.SH", "bar_time": "2026-01-02T00:00:00", "factor_value": 3.0, "target_return": 0.02},
            ]
        }

    monkeypatch.setattr(
        "backend.services.factor_ranking_service.factor_dataset_service.ensure_dataset",
        fake_ensure_dataset,
    )

    result = factor_ranking_service.refresh_rankings(
        db=db_session,
        stock_pool_key="csi300",
        target="next_1d_return",
        frequency="1d",
        start_date="2026-01-01",
        end_date="2026-01-02",
        factor_ids=None,
        force=False,
    )

    assert len(datasets) == 1
    assert datasets[0]["start_date"] == "2026-01-02"
    assert datasets[0]["end_date"] == "2026-01-02"
    assert result["cache_hits"] == 1
    assert result["computed_items"] == 1

    metrics = repo.list_metrics(
        stock_pool_key="csi300",
        stock_pool_snapshot_hash=snapshot_hash,
        target="next_1d_return",
        frequency="1d",
        start_date="2026-01-01",
        end_date="2026-01-02",
        metric_version=METRIC_VERSION,
        factor_id=factor.id,
    )
    assert len(metrics) == 2
```

- [ ] **Step 2: Run service tests and verify they fail for the right reason**

Run:

```bash
uv run pytest tests/test_factor_ranking_service.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'backend.services.factor_ranking_service'`.

- [ ] **Step 3: Implement the ranking service**

Create `backend/services/factor_ranking_service.py` with:

```python
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime
from typing import Callable, Optional

import numpy as np
import pandas as pd

from backend.core.database import get_db_session
from backend.core.factor_targets import DEFAULT_FREQUENCY, validate_factor_target, validate_frequency
from backend.core.stock_pools import get_stock_pool
from backend.repositories.factor_performance_repository import FactorPerformanceRepository
from backend.repositories.factor_repository import FactorRepository
from backend.services.factor_dataset_service import factor_dataset_service, hash_factor_code


METRIC_VERSION = "v1"
ranking_tasks: dict[str, dict] = {}


def hash_stock_pool_snapshot(stock_codes: list[str]) -> str:
    normalized = sorted({str(code).strip().upper() for code in stock_codes if str(code).strip()})
    return hashlib.sha256("|".join(normalized).encode("utf-8")).hexdigest()


def _date_from_bar_time(value) -> str:
    return pd.to_datetime(value).strftime("%Y-%m-%d")


def _finite_float(value) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if np.isnan(number) or np.isinf(number):
        return None
    return number


def _compute_ic_for_bar(rows: list[dict], stock_count: int) -> dict:
    frame = pd.DataFrame(rows).dropna(subset=["factor_value", "target_return"])
    sample_size = int(len(frame))
    coverage = float(sample_size / stock_count) if stock_count else 0.0
    if sample_size < 2:
        return {
            "ic_value": None,
            "sample_size": sample_size,
            "coverage": coverage,
            "status": "insufficient_data",
            "error_message": "有效股票样本少于2只，无法计算截面IC",
        }
    ic_value = _finite_float(frame["factor_value"].corr(frame["target_return"]))
    if ic_value is None:
        return {
            "ic_value": None,
            "sample_size": sample_size,
            "coverage": coverage,
            "status": "failed",
            "error_message": "IC计算结果为空或非有限数",
        }
    return {
        "ic_value": ic_value,
        "sample_size": sample_size,
        "coverage": coverage,
        "status": "success",
        "error_message": None,
    }


class FactorRankingService:
    def get_rankings(
        self,
        db,
        *,
        stock_pool_key: str,
        target: str,
        frequency: str = DEFAULT_FREQUENCY,
        start_date: str,
        end_date: str,
        sort_by: str = "ic_mean",
        sort_order: str = "desc",
        page: int = 1,
        page_size: int = 50,
        status: Optional[str] = None,
    ) -> dict:
        frequency_key = validate_frequency(frequency)
        target_key = validate_factor_target(target, frequency_key)
        pool = get_stock_pool(stock_pool_key, include_codes=True)
        stock_codes = pool.get("stock_codes", [])
        if not stock_codes:
            raise ValueError(f"股票池 {pool.get('label', stock_pool_key)} 未初始化，请先刷新股票池")
        snapshot_hash = hash_stock_pool_snapshot(stock_codes)
        repo = FactorPerformanceRepository(db)
        ranking = repo.query_rankings(
            stock_pool_key=stock_pool_key,
            stock_pool_snapshot_hash=snapshot_hash,
            target=target_key,
            frequency=frequency_key,
            start_date=start_date,
            end_date=end_date,
            metric_version=METRIC_VERSION,
            sort_by=sort_by,
            sort_order=sort_order,
            page=page,
            page_size=page_size,
            status=status,
        )
        active_factor_count = len(FactorRepository(db).get_all(active_only=True))
        ranking["cache_summary"] = repo.get_cache_summary(
            stock_pool_key=stock_pool_key,
            stock_pool_snapshot_hash=snapshot_hash,
            target=target_key,
            frequency=frequency_key,
            start_date=start_date,
            end_date=end_date,
            metric_version=METRIC_VERSION,
            active_factor_count=active_factor_count,
        )
        ranking["query"] = {
            "stock_pool_key": stock_pool_key,
            "target": target_key,
            "frequency": frequency_key,
            "start_date": start_date,
            "end_date": end_date,
            "sort_by": sort_by,
            "sort_order": sort_order,
            "page": page,
            "page_size": page_size,
            "status": status,
        }
        return ranking

    def refresh_rankings(
        self,
        db,
        *,
        stock_pool_key: str,
        target: str,
        frequency: str,
        start_date: str,
        end_date: str,
        factor_ids: list[int] | None = None,
        force: bool = False,
        progress_callback: Callable[[dict], None] | None = None,
    ) -> dict:
        frequency_key = validate_frequency(frequency)
        target_key = validate_factor_target(target, frequency_key)
        pool = get_stock_pool(stock_pool_key, include_codes=True)
        stock_codes = pool.get("stock_codes", [])
        if not stock_codes:
            raise ValueError(f"股票池 {pool.get('label', stock_pool_key)} 未初始化，请先刷新股票池")
        snapshot_hash = hash_stock_pool_snapshot(stock_codes)
        perf_repo = FactorPerformanceRepository(db)
        factor_repo = FactorRepository(db)

        expected_bars = perf_repo.get_target_bar_counts(
            target=target_key,
            stock_codes=stock_codes,
            start_date=start_date,
            end_date=end_date,
            frequency=frequency_key,
        )
        if force or not expected_bars:
            factor_dataset_service.backfill_target_returns(
                db,
                target_key,
                stock_codes,
                start_date,
                end_date,
                frequency=frequency_key,
                force=force,
            )
            expected_bars = perf_repo.get_target_bar_counts(
                target=target_key,
                stock_codes=stock_codes,
                start_date=start_date,
                end_date=end_date,
                frequency=frequency_key,
            )
        factors = factor_repo.get_all(active_only=True)
        if factor_ids:
            allowed = set(factor_ids)
            factors = [factor for factor in factors if factor.id in allowed]

        summary = {
            "total_items": len(factors) * len(expected_bars),
            "cache_hits": 0,
            "computed_items": 0,
            "failed_items": 0,
            "stock_pool_snapshot_hash": snapshot_hash,
        }

        for index, factor in enumerate(factors, start=1):
            code_hash = hash_factor_code(factor.code)
            existing = set() if force else perf_repo.get_existing_bar_times(
                factor_id=factor.id,
                factor_code_hash=code_hash,
                stock_pool_key=stock_pool_key,
                stock_pool_snapshot_hash=snapshot_hash,
                target=target_key,
                frequency=frequency_key,
                start_date=start_date,
                end_date=end_date,
                metric_version=METRIC_VERSION,
            )
            missing_bars = sorted(set(expected_bars) - existing)
            summary["cache_hits"] += len(existing)
            if not missing_bars:
                if progress_callback:
                    progress_callback({"current_factor": factor.name, "completed_factors": index, **summary})
                continue

            try:
                dataset = factor_dataset_service.ensure_dataset(
                    db=db,
                    factor_id=factor.id,
                    target=target_key,
                    frequency=frequency_key,
                    stock_codes=stock_codes,
                    start_date=_date_from_bar_time(missing_bars[0]),
                    end_date=_date_from_bar_time(missing_bars[-1]),
                    force=force,
                )
                frame = pd.DataFrame(dataset.get("rows", []))
                if frame.empty:
                    grouped_rows = {}
                else:
                    frame["bar_time"] = pd.to_datetime(frame["bar_time"])
                    grouped_rows = {
                        bar_time.to_pydatetime(): group.to_dict("records")
                        for bar_time, group in frame.groupby("bar_time")
                    }
                for bar_time in missing_bars:
                    metric = _compute_ic_for_bar(grouped_rows.get(bar_time, []), len(stock_codes))
                    perf_repo.upsert_metric(
                        factor_id=factor.id,
                        factor_name=factor.name,
                        factor_code_hash=code_hash,
                        stock_pool_key=stock_pool_key,
                        stock_pool_snapshot_hash=snapshot_hash,
                        target=target_key,
                        frequency=frequency_key,
                        bar_time=bar_time,
                        metric_version=METRIC_VERSION,
                        ic_value=metric["ic_value"],
                        sample_size=metric["sample_size"],
                        coverage=metric["coverage"],
                        status=metric["status"],
                        error_message=metric["error_message"],
                        force=force,
                    )
                    if metric["status"] == "failed":
                        summary["failed_items"] += 1
                    else:
                        summary["computed_items"] += 1
            except Exception as exc:
                summary["failed_items"] += len(missing_bars)
                for bar_time in missing_bars:
                    perf_repo.upsert_metric(
                        factor_id=factor.id,
                        factor_name=factor.name,
                        factor_code_hash=code_hash,
                        stock_pool_key=stock_pool_key,
                        stock_pool_snapshot_hash=snapshot_hash,
                        target=target_key,
                        frequency=frequency_key,
                        bar_time=bar_time,
                        metric_version=METRIC_VERSION,
                        ic_value=None,
                        sample_size=0,
                        coverage=0.0,
                        status="failed",
                        error_message=str(exc),
                        force=True,
                    )
            if progress_callback:
                progress_callback({"current_factor": factor.name, "completed_factors": index, **summary})
        return summary

    def start_refresh_task(self, **kwargs) -> str:
        task_id = str(uuid.uuid4())
        ranking_tasks[task_id] = {
            "task_id": task_id,
            "status": "pending",
            "progress": 0,
            "total_items": 0,
            "cache_hits": 0,
            "computed_items": 0,
            "failed_items": 0,
            "current_factor": None,
            "error": None,
        }
        return task_id

    def run_refresh_task(self, task_id: str, **kwargs) -> None:
        db = get_db_session()
        try:
            ranking_tasks[task_id]["status"] = "running"

            def update_progress(update: dict) -> None:
                ranking_tasks[task_id].update(update)
                total = ranking_tasks[task_id].get("total_items") or update.get("total_items") or 1
                done = update.get("cache_hits", 0) + update.get("computed_items", 0) + update.get("failed_items", 0)
                ranking_tasks[task_id]["progress"] = min(int(done / total * 100), 99)

            summary = self.refresh_rankings(db=db, progress_callback=update_progress, **kwargs)
            ranking_tasks[task_id].update(summary)
            ranking_tasks[task_id]["status"] = "completed"
            ranking_tasks[task_id]["progress"] = 100
        except Exception as exc:
            ranking_tasks[task_id]["status"] = "failed"
            ranking_tasks[task_id]["error"] = str(exc)
        finally:
            db.close()

    def get_task(self, task_id: str) -> dict | None:
        return ranking_tasks.get(task_id)


factor_ranking_service = FactorRankingService()
```

- [ ] **Step 4: Run service tests and verify they pass**

Run:

```bash
uv run pytest tests/test_factor_ranking_service.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit Task 2**

Run:

```bash
git add tests/test_factor_ranking_service.py backend/services/factor_ranking_service.py
git commit -m "feat: add incremental factor ranking service"
```

---

### Task 3: Backend Ranking API Routes

**Files:**
- Create: `tests/test_factor_ranking_api.py`
- Modify: `backend/api/routers/analysis.py`

- [ ] **Step 1: Write failing API tests**

Create `tests/test_factor_ranking_api.py`:

```python
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.routers import analysis


def _client():
    app = FastAPI()
    app.include_router(analysis.router, prefix="/api/analysis")
    return TestClient(app)


def test_get_factor_rankings_returns_service_payload(monkeypatch):
    def fake_get_rankings(db, **kwargs):
        assert kwargs["stock_pool_key"] == "csi300"
        assert kwargs["target"] == "next_1d_return"
        return {
            "items": [{"rank": 1, "factor_id": 1, "factor_name": "factor_a"}],
            "total": 1,
            "cache_summary": {"missing_factor_count": 0},
            "query": kwargs,
        }

    monkeypatch.setattr(analysis.factor_ranking_service, "get_rankings", fake_get_rankings)
    response = _client().get(
        "/api/analysis/factor-rankings",
        params={
            "stock_pool_key": "csi300",
            "target": "next_1d_return",
            "frequency": "1d",
            "start_date": "2026-01-01",
            "end_date": "2026-01-31",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["data"]["total"] == 1
    assert payload["data"]["items"][0]["factor_name"] == "factor_a"


def test_refresh_factor_rankings_starts_background_task(monkeypatch):
    calls = []
    monkeypatch.setattr(analysis.factor_ranking_service, "start_refresh_task", lambda **kwargs: "task-1")

    def fake_run_refresh_task(task_id, **kwargs):
        calls.append((task_id, kwargs))

    monkeypatch.setattr(analysis.factor_ranking_service, "run_refresh_task", fake_run_refresh_task)
    response = _client().post(
        "/api/analysis/factor-rankings/refresh",
        json={
            "stock_pool_key": "csi300",
            "target": "next_1d_return",
            "frequency": "1d",
            "start_date": "2026-01-01",
            "end_date": "2026-01-31",
            "force": False,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["data"]["task_id"] == "task-1"
    assert calls and calls[0][0] == "task-1"


def test_get_factor_ranking_task_returns_404_for_unknown_task(monkeypatch):
    monkeypatch.setattr(analysis.factor_ranking_service, "get_task", lambda task_id: None)
    response = _client().get("/api/analysis/factor-rankings/tasks/missing")

    assert response.status_code == 404
    assert response.json()["detail"] == "任务不存在"
```

- [ ] **Step 2: Run API tests and verify they fail for missing routes**

Run:

```bash
uv run pytest tests/test_factor_ranking_api.py -q
```

Expected: FAIL with `404 Not Found` for `/api/analysis/factor-rankings`.

- [ ] **Step 3: Add request models and routes**

In `backend/api/routers/analysis.py`, add imports near the existing imports:

```python
from fastapi import BackgroundTasks, Query
from backend.services.factor_ranking_service import factor_ranking_service
```

Add this request model near `ICAnalysisRequest`:

```python
class FactorRankingRefreshRequest(BaseModel):
    stock_pool_key: str
    target: str = DEFAULT_FACTOR_TARGET
    frequency: str = DEFAULT_FREQUENCY
    start_date: str
    end_date: str
    factor_ids: Optional[List[int]] = None
    force: bool = False
```

Add these routes before `@router.post("/calculate")`:

```python
@router.get("/factor-rankings")
async def get_factor_rankings(
    stock_pool_key: str,
    target: str = DEFAULT_FACTOR_TARGET,
    frequency: str = DEFAULT_FREQUENCY,
    start_date: str = Query(...),
    end_date: str = Query(...),
    sort_by: str = "ic_mean",
    sort_order: str = "desc",
    page: int = 1,
    page_size: int = 50,
    status: Optional[str] = None,
):
    db = get_db_session()
    try:
        result = factor_ranking_service.get_rankings(
            db,
            stock_pool_key=stock_pool_key,
            target=target,
            frequency=frequency,
            start_date=start_date,
            end_date=end_date,
            sort_by=sort_by,
            sort_order=sort_order,
            page=page,
            page_size=page_size,
            status=status,
        )
        return {"success": True, "data": result}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    finally:
        db.close()


@router.post("/factor-rankings/refresh")
async def refresh_factor_rankings(request: FactorRankingRefreshRequest, background_tasks: BackgroundTasks):
    try:
        task_kwargs = {
            "stock_pool_key": request.stock_pool_key,
            "target": request.target,
            "frequency": request.frequency,
            "start_date": request.start_date,
            "end_date": request.end_date,
            "factor_ids": request.factor_ids,
            "force": request.force,
        }
        task_id = factor_ranking_service.start_refresh_task(**task_kwargs)
        background_tasks.add_task(factor_ranking_service.run_refresh_task, task_id, **task_kwargs)
        return {
            "success": True,
            "data": {
                "task_id": task_id,
                "summary": factor_ranking_service.get_task(task_id),
            },
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/factor-rankings/tasks/{task_id}")
async def get_factor_ranking_task(task_id: str):
    task = factor_ranking_service.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return {"success": True, "data": task}
```

- [ ] **Step 4: Run API tests and backend focused tests**

Run:

```bash
uv run pytest tests/test_factor_ranking_api.py tests/test_factor_ranking_service.py tests/test_factor_performance_repository.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit Task 3**

Run:

```bash
git add tests/test_factor_ranking_api.py backend/api/routers/analysis.py
git commit -m "feat: expose factor ranking analysis api"
```

---

### Task 4: Frontend API Client And Helper Tests

**Files:**
- Modify: `frontend/react-antd/package.json`
- Modify: `frontend/react-antd/vite.config.ts`
- Modify: `frontend/react-antd/src/services/api.ts`
- Create: `frontend/react-antd/src/pages/FactorAnalysis.utils.ts`
- Create: `frontend/react-antd/src/pages/FactorAnalysis.utils.test.ts`

- [ ] **Step 1: Add Vitest dependencies and script**

Run:

```bash
cd frontend/react-antd
npm install -D vitest
```

Expected: `package.json` and lockfile update with `vitest`.

If the install fails because of network sandboxing, rerun the same command with escalated permission.

Then ensure `frontend/react-antd/package.json` has:

```json
{
  "scripts": {
    "dev": "vite",
    "build": "tsc -b && vite build",
    "lint": "eslint .",
    "preview": "vite preview",
    "test": "vitest"
  }
}
```

- [ ] **Step 2: Configure Vitest**

Modify `frontend/react-antd/vite.config.ts` so it includes `test`. Preserve the existing server port, host, strictPort, and proxy settings. The final shape should include:

```typescript
import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import path from 'path'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src')
    }
  },
  server: {
    port: 5173,
    host: 'localhost',
    strictPort: false,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true
      }
    }
  },
  test: {
    environment: 'node',
    globals: true
  }
})
```

- [ ] **Step 3: Write failing helper tests**

Create `frontend/react-antd/src/pages/FactorAnalysis.utils.test.ts`:

```typescript
import { describe, expect, it } from 'vitest'
import {
  buildRankingQuery,
  getNextTargetForFrequency,
  normalizeCacheSummary,
  formatMetric,
} from './FactorAnalysis.utils'

describe('FactorAnalysis utilities', () => {
  it('switches target when frequency changes', () => {
    expect(getNextTargetForFrequency('60m')).toBe('next_1h_return')
    expect(getNextTargetForFrequency('1d')).toBe('next_1d_return')
  })

  it('builds ranking query with pagination and sort defaults', () => {
    expect(
      buildRankingQuery({
        stockPoolKey: 'csi300',
        target: 'next_5d_return',
        frequency: '1d',
        startDate: '2026-01-01',
        endDate: '2026-01-31',
      }),
    ).toEqual({
      stock_pool_key: 'csi300',
      target: 'next_5d_return',
      frequency: '1d',
      start_date: '2026-01-01',
      end_date: '2026-01-31',
      sort_by: 'ic_mean',
      sort_order: 'desc',
      page: 1,
      page_size: 50,
      status: undefined,
    })
  })

  it('normalizes missing cache summary fields to zero', () => {
    expect(normalizeCacheSummary({ active_factor_count: 12 })).toEqual({
      stock_pool_key: '',
      stock_pool_snapshot_hash: '',
      target: '',
      frequency: '',
      active_factor_count: 12,
      cached_factor_count: 0,
      missing_factor_count: 0,
      failed_factor_count: 0,
      latest_bar_time: '',
    })
  })

  it('formats metrics without showing NaN', () => {
    expect(formatMetric(0.12345)).toBe('0.1235')
    expect(formatMetric(null)).toBe('--')
    expect(formatMetric(Number.NaN)).toBe('--')
  })
})
```

- [ ] **Step 4: Run helper tests and verify they fail for missing module**

Run:

```bash
cd frontend/react-antd
npm run test -- FactorAnalysis.utils.test.ts --run
```

Expected: FAIL with `Failed to resolve import "./FactorAnalysis.utils"`.

- [ ] **Step 5: Implement frontend helpers and API methods**

Create `frontend/react-antd/src/pages/FactorAnalysis.utils.ts`:

```typescript
import { DEFAULT_FACTOR_TARGET, getDefaultTargetByFrequency } from '@/constants/factorTargets'

export interface RankingQueryInput {
  stockPoolKey: string
  target: string
  frequency: string
  startDate: string
  endDate: string
  sortBy?: string
  sortOrder?: 'asc' | 'desc'
  page?: number
  pageSize?: number
  status?: string
}

export interface CacheSummary {
  stock_pool_key: string
  stock_pool_snapshot_hash: string
  target: string
  frequency: string
  active_factor_count: number
  cached_factor_count: number
  missing_factor_count: number
  failed_factor_count: number
  latest_bar_time: string
}

export const getNextTargetForFrequency = (frequency: string) =>
  getDefaultTargetByFrequency(frequency) || DEFAULT_FACTOR_TARGET

export const buildRankingQuery = (input: RankingQueryInput) => ({
  stock_pool_key: input.stockPoolKey,
  target: input.target,
  frequency: input.frequency,
  start_date: input.startDate,
  end_date: input.endDate,
  sort_by: input.sortBy || 'ic_mean',
  sort_order: input.sortOrder || 'desc',
  page: input.page || 1,
  page_size: input.pageSize || 50,
  status: input.status,
})

export const normalizeCacheSummary = (summary: Partial<CacheSummary> | null | undefined): CacheSummary => ({
  stock_pool_key: summary?.stock_pool_key || '',
  stock_pool_snapshot_hash: summary?.stock_pool_snapshot_hash || '',
  target: summary?.target || '',
  frequency: summary?.frequency || '',
  active_factor_count: summary?.active_factor_count || 0,
  cached_factor_count: summary?.cached_factor_count || 0,
  missing_factor_count: summary?.missing_factor_count || 0,
  failed_factor_count: summary?.failed_factor_count || 0,
  latest_bar_time: summary?.latest_bar_time || '',
})

export const formatMetric = (value: number | null | undefined, digits = 4) => {
  if (value === null || value === undefined || Number.isNaN(value) || !Number.isFinite(value)) {
    return '--'
  }
  return value.toFixed(digits)
}
```

In `frontend/react-antd/src/services/api.ts`, add these methods before the mining methods:

```typescript
  getFactorRankings(params: {
    stock_pool_key: string
    target: string
    frequency: string
    start_date: string
    end_date: string
    sort_by?: string
    sort_order?: string
    page?: number
    page_size?: number
    status?: string
  }) {
    return request.get('/analysis/factor-rankings', { params })
  },

  refreshFactorRankings(data: {
    stock_pool_key: string
    target: string
    frequency: string
    start_date: string
    end_date: string
    factor_ids?: number[]
    force?: boolean
  }) {
    return request.post('/analysis/factor-rankings/refresh', data, { timeout: 300000 })
  },

  getFactorRankingTask(taskId: string) {
    return request.get(`/analysis/factor-rankings/tasks/${taskId}`, { timeout: 300000 })
  },
```

- [ ] **Step 6: Run frontend helper tests**

Run:

```bash
cd frontend/react-antd
npm run test -- FactorAnalysis.utils.test.ts --run
```

Expected: PASS.

- [ ] **Step 7: Commit Task 4**

Run:

```bash
git add frontend/react-antd/package.json frontend/react-antd/package-lock.json frontend/react-antd/pnpm-lock.yaml frontend/react-antd/vite.config.ts frontend/react-antd/src/services/api.ts frontend/react-antd/src/pages/FactorAnalysis.utils.ts frontend/react-antd/src/pages/FactorAnalysis.utils.test.ts
git commit -m "feat: add factor analysis frontend api helpers"
```

Only add lockfiles that actually changed.

---

### Task 5: Frontend Factor Analysis Page

**Files:**
- Create: `frontend/react-antd/src/pages/FactorAnalysis.tsx`
- Create: `frontend/react-antd/src/pages/FactorAnalysis.css`
- Modify: `frontend/react-antd/src/utils/router.tsx`
- Modify: `frontend/react-antd/src/App.tsx`

- [ ] **Step 1: Add the route first and verify build fails**

Modify `frontend/react-antd/src/utils/router.tsx` by adding the lazy import:

```typescript
const FactorAnalysis = lazy(() => import('@/pages/FactorAnalysis'))
```

Add this route after Factor Management:

```typescript
{
  path: '/factor-analysis',
  key: 'factor-analysis',
  label: '因子分析',
  icon: 'BarChartOutlined',
  component: FactorAnalysis
},
```

Run:

```bash
cd frontend/react-antd
npm run build
```

Expected: FAIL because `@/pages/FactorAnalysis` does not exist or `BarChartOutlined` is unsupported in `App.tsx`.

- [ ] **Step 2: Add icon support in App**

In `frontend/react-antd/src/App.tsx`, add `BarChartOutlined` to the icon import:

```typescript
import {
  DashboardOutlined,
  FileTextOutlined,
  ExperimentOutlined,
  PieChartOutlined,
  SyncOutlined,
  MenuOutlined,
  BarChartOutlined
} from '@ant-design/icons'
```

In the `createIcon` map near the bottom of `frontend/react-antd/src/App.tsx`, add:

```typescript
BarChartOutlined
```

to the supported icon mapping.

- [ ] **Step 3: Implement the page component**

Create `frontend/react-antd/src/pages/FactorAnalysis.tsx`:

```typescript
import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Alert,
  Button,
  Card,
  Col,
  DatePicker,
  Empty,
  message,
  Modal,
  Progress,
  Row,
  Select,
  Space,
  Spin,
  Statistic,
  Table,
  Tag,
} from 'antd'
import type { ColumnsType, TablePaginationConfig } from 'antd/es/table'
import {
  BarChartOutlined,
  EyeOutlined,
  ReloadOutlined,
  SyncOutlined,
} from '@ant-design/icons'
import * as echarts from 'echarts'
import dayjs from 'dayjs'
import { api } from '@/services/api'
import {
  DEFAULT_FACTOR_TARGET,
  DEFAULT_FREQUENCY,
  FREQUENCIES,
  getTargetLabel,
  getTargetsByFrequency,
} from '@/constants/factorTargets'
import {
  DEFAULT_STOCK_POOL,
  FALLBACK_STOCK_POOLS,
  type StockPoolOption,
} from '@/constants/stockPools'
import {
  buildRankingQuery,
  formatMetric,
  getNextTargetForFrequency,
  normalizeCacheSummary,
  type CacheSummary,
} from './FactorAnalysis.utils'
import './FactorAnalysis.css'

const { RangePicker } = DatePicker
const { Option } = Select

interface RankingItem {
  rank: number
  factor_id: number
  factor_name: string
  category?: string
  source?: string
  ic_mean: number | null
  ic_std: number | null
  ir: number | null
  ic_positive_ratio: number | null
  bar_count: number
  sample_size: number
  coverage: number
  status: string
  error_message?: string | null
  last_updated_at?: string
}

interface RankingTask {
  task_id: string
  status: 'pending' | 'running' | 'completed' | 'failed'
  progress: number
  total_items: number
  cache_hits: number
  computed_items: number
  failed_items: number
  current_factor?: string | null
  error?: string | null
}

const buildHistogramBins = (values: number[], binCount = 8) => {
  if (!values.length) {
    return { labels: [], counts: [] }
  }
  const min = Math.min(...values)
  const max = Math.max(...values)
  if (min === max) {
    return { labels: [min.toFixed(3)], counts: [values.length] }
  }
  const step = (max - min) / binCount
  const counts = Array.from({ length: binCount }, () => 0)
  values.forEach(value => {
    const index = Math.min(Math.floor((value - min) / step), binCount - 1)
    counts[index] += 1
  })
  const labels = counts.map((_, index) => {
    const left = min + index * step
    const right = index === binCount - 1 ? max : left + step
    return `${left.toFixed(3)}~${right.toFixed(3)}`
  })
  return { labels, counts }
}

const FactorAnalysis: React.FC = () => {
  const navigate = useNavigate()
  const distributionChartRef = useRef<HTMLDivElement>(null)
  const chartInstanceRef = useRef<echarts.ECharts | null>(null)

  const [stockPools, setStockPools] = useState<StockPoolOption[]>(FALLBACK_STOCK_POOLS)
  const [stockPoolKey, setStockPoolKey] = useState(DEFAULT_STOCK_POOL)
  const [frequency, setFrequency] = useState(DEFAULT_FREQUENCY)
  const [target, setTarget] = useState(DEFAULT_FACTOR_TARGET)
  const [dateRange, setDateRange] = useState<[dayjs.Dayjs, dayjs.Dayjs]>([
    dayjs().subtract(1, 'year'),
    dayjs(),
  ])
  const [sortBy, setSortBy] = useState('ic_mean')
  const [sortOrder, setSortOrder] = useState<'asc' | 'desc'>('desc')
  const [pagination, setPagination] = useState({ current: 1, pageSize: 50, total: 0 })
  const [loading, setLoading] = useState(false)
  const [refreshing, setRefreshing] = useState(false)
  const [rankings, setRankings] = useState<RankingItem[]>([])
  const [cacheSummary, setCacheSummary] = useState<CacheSummary>(normalizeCacheSummary(null))
  const [task, setTask] = useState<RankingTask | null>(null)

  const query = useMemo(() => buildRankingQuery({
    stockPoolKey,
    target,
    frequency,
    startDate: dateRange[0].format('YYYY-MM-DD'),
    endDate: dateRange[1].format('YYYY-MM-DD'),
    sortBy,
    sortOrder,
    page: pagination.current,
    pageSize: pagination.pageSize,
  }), [stockPoolKey, target, frequency, dateRange, sortBy, sortOrder, pagination.current, pagination.pageSize])

  const loadStockPools = async () => {
    try {
      const response = await api.getStockPools() as any
      if (response.success && Array.isArray(response.data)) {
        setStockPools(response.data)
      }
    } catch (error) {
      setStockPools(FALLBACK_STOCK_POOLS)
    }
  }

  const loadRankings = async () => {
    setLoading(true)
    try {
      const response = await api.getFactorRankings(query) as any
      if (response.success) {
        setRankings(response.data.items || [])
        setCacheSummary(normalizeCacheSummary(response.data.cache_summary))
        setPagination(prev => ({ ...prev, total: response.data.total || 0 }))
      }
    } catch (error) {
      message.error(error instanceof Error ? error.message : '加载因子排名失败')
    } finally {
      setLoading(false)
    }
  }

  const startRefresh = async (force = false) => {
    if (force) {
      const confirmed = await new Promise<boolean>(resolve => {
        Modal.confirm({
          title: '确认强制刷新当前范围？',
          content: '强制刷新会覆盖当前筛选范围内已缓存的表现指标。',
          onOk: () => resolve(true),
          onCancel: () => resolve(false),
        })
      })
      if (!confirmed) return
    }

    setRefreshing(true)
    try {
      const response = await api.refreshFactorRankings({ ...query, force }) as any
      if (response.success) {
        setTask(response.data.summary)
        pollTask(response.data.task_id)
      }
    } catch (error) {
      setRefreshing(false)
      message.error(error instanceof Error ? error.message : '启动刷新任务失败')
    }
  }

  const pollTask = (taskId: string) => {
    const timer = window.setInterval(async () => {
      try {
        const response = await api.getFactorRankingTask(taskId) as any
        if (response.success) {
          setTask(response.data)
          if (response.data.status === 'completed') {
            window.clearInterval(timer)
            setRefreshing(false)
            message.success('因子表现缓存已补齐')
            loadRankings()
          }
          if (response.data.status === 'failed') {
            window.clearInterval(timer)
            setRefreshing(false)
            message.error(response.data.error || '刷新任务失败')
          }
        }
      } catch (error) {
        window.clearInterval(timer)
        setRefreshing(false)
        message.error('查询刷新任务状态失败')
      }
    }, 1500)
  }

  useEffect(() => {
    loadStockPools()
  }, [])

  useEffect(() => {
    loadRankings()
  }, [query])

  useEffect(() => {
    if (!distributionChartRef.current) return
    if (!chartInstanceRef.current) {
      chartInstanceRef.current = echarts.init(distributionChartRef.current)
    }
    const values = rankings
      .map(item => item.ic_mean)
      .filter((value): value is number => typeof value === 'number' && Number.isFinite(value))
    const histogram = buildHistogramBins(values)
    chartInstanceRef.current.setOption({
      grid: { left: 36, right: 16, top: 24, bottom: 28 },
      tooltip: { trigger: 'axis' },
      xAxis: { type: 'category', data: histogram.labels, axisLabel: { rotate: 30 } },
      yAxis: { type: 'value', name: '因子数' },
      series: [{ type: 'bar', data: histogram.counts, name: '因子数' }],
    })
  }, [rankings])

  const columns: ColumnsType<RankingItem> = [
    { title: '排名', dataIndex: 'rank', width: 72, fixed: 'left' },
    {
      title: '因子',
      dataIndex: 'factor_name',
      fixed: 'left',
      render: (name, record) => (
        <Space direction="vertical" size={2}>
          <Button type="link" size="small" onClick={() => navigate(`/factor-detail?id=${record.factor_id}`)}>{name}</Button>
          <Space size={4}>
            {record.category && <Tag>{record.category}</Tag>}
            {record.source && <Tag color={record.source === 'preset' ? 'blue' : 'green'}>{record.source}</Tag>}
          </Space>
        </Space>
      ),
    },
    { title: 'IC均值', dataIndex: 'ic_mean', sorter: true, render: value => formatMetric(value) },
    { title: 'IC标准差', dataIndex: 'ic_std', sorter: true, render: value => formatMetric(value) },
    { title: 'IR', dataIndex: 'ir', sorter: true, render: value => formatMetric(value) },
    { title: 'IC>0', dataIndex: 'ic_positive_ratio', render: value => value == null ? '--' : `${(value * 100).toFixed(1)}%` },
    { title: '覆盖率', dataIndex: 'coverage', render: value => `${((value || 0) * 100).toFixed(1)}%` },
    { title: '样本', dataIndex: 'sample_size', render: value => Math.round(value || 0) },
    {
      title: '状态',
      dataIndex: 'status',
      render: (status, record) => status === 'success'
        ? <Tag color="green">已缓存</Tag>
        : <Tag color="red">{record.error_message || status}</Tag>,
    },
    {
      title: '操作',
      render: (_, record) => (
        <Button icon={<EyeOutlined />} onClick={() => navigate(`/factor-detail?id=${record.factor_id}`)}>
          详情
        </Button>
      ),
    },
  ]

  const handleTableChange = (nextPagination: TablePaginationConfig, _: any, sorter: any) => {
    setPagination({
      current: nextPagination.current || 1,
      pageSize: nextPagination.pageSize || 50,
      total: nextPagination.total || 0,
    })
    if (sorter?.field) {
      setSortBy(String(sorter.field))
      setSortOrder(sorter.order === 'ascend' ? 'asc' : 'desc')
    }
  }

  return (
    <div className="factor-analysis-container">
      <div className="factor-analysis-content">
        <div className="page-header">
          <div className="header-content">
            <BarChartOutlined className="header-icon" />
            <div>
              <h1 className="page-title">因子分析</h1>
              <p className="page-subtitle">按股票池、target 与K线频率查看全量因子表现排名</p>
            </div>
          </div>
          <Space>
            <Button icon={<ReloadOutlined />} onClick={loadRankings}>刷新视图</Button>
            <Button icon={<SyncOutlined />} type="primary" loading={refreshing} onClick={() => startRefresh(false)}>补齐缺失</Button>
            <Button danger loading={refreshing} onClick={() => startRefresh(true)}>强制刷新</Button>
          </Space>
        </div>

        <Card className="filter-card">
          <Row gutter={[12, 12]}>
            <Col xs={24} md={6}>
              <Select value={stockPoolKey} onChange={setStockPoolKey} style={{ width: '100%' }}>
                {stockPools.filter(pool => !pool.is_custom).map(pool => (
                  <Option key={pool.key} value={pool.key}>{pool.label}</Option>
                ))}
              </Select>
            </Col>
            <Col xs={24} md={5}>
              <Select value={frequency} onChange={(value) => {
                setFrequency(value)
                setTarget(getNextTargetForFrequency(value))
              }} style={{ width: '100%' }}>
                {FREQUENCIES.map(item => <Option key={item.value} value={item.value}>{item.label}</Option>)}
              </Select>
            </Col>
            <Col xs={24} md={6}>
              <Select value={target} onChange={setTarget} style={{ width: '100%' }}>
                {getTargetsByFrequency(frequency).map(item => (
                  <Option key={item.value} value={item.value}>{item.label}</Option>
                ))}
              </Select>
            </Col>
            <Col xs={24} md={7}>
              <RangePicker value={dateRange} onChange={(value) => {
                if (value?.[0] && value?.[1]) setDateRange([value[0], value[1]])
              }} style={{ width: '100%' }} />
            </Col>
          </Row>
        </Card>

        {task && refreshing && (
          <Alert
            className="task-alert"
            type="info"
            showIcon
            message={`刷新任务：${task.status}`}
            description={<Progress percent={task.progress || 0} />}
          />
        )}

        <Row gutter={[16, 16]}>
          <Col xs={24} lg={18}>
            <Card className="ranking-card" title={`因子表现排名：${getTargetLabel(target)}`}>
              <Spin spinning={loading}>
                {rankings.length ? (
                  <Table
                    rowKey="factor_id"
                    columns={columns}
                    dataSource={rankings}
                    pagination={pagination}
                    onChange={handleTableChange}
                    scroll={{ x: 1100 }}
                  />
                ) : (
                  <Empty description="当前筛选范围暂无缓存结果，请点击补齐缺失" />
                )}
              </Spin>
            </Card>
          </Col>
          <Col xs={24} lg={6}>
            <Space direction="vertical" size={16} style={{ width: '100%' }}>
              <Card className="summary-card" title="缓存状态">
                <Row gutter={[8, 12]}>
                  <Col span={12}><Statistic title="全量因子" value={cacheSummary.active_factor_count} /></Col>
                  <Col span={12}><Statistic title="已缓存" value={cacheSummary.cached_factor_count} /></Col>
                  <Col span={12}><Statistic title="缺失" value={cacheSummary.missing_factor_count} /></Col>
                  <Col span={12}><Statistic title="失败" value={cacheSummary.failed_factor_count} /></Col>
                </Row>
              </Card>
              <Card className="summary-card" title="IC分布">
                <div ref={distributionChartRef} className="ic-distribution-chart" />
              </Card>
            </Space>
          </Col>
        </Row>
      </div>
    </div>
  )
}

export default FactorAnalysis
```

- [ ] **Step 4: Add page styles**

Create `frontend/react-antd/src/pages/FactorAnalysis.css`:

```css
.factor-analysis-container {
  min-height: 100vh;
  padding: 16px;
  background: transparent;
}

.factor-analysis-content {
  position: relative;
}

.factor-analysis-container .page-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 16px;
  margin-bottom: 16px;
  padding: 18px 20px;
  background: rgba(255, 255, 255, 0.9);
  border: 1px solid rgba(59, 130, 246, 0.12);
  border-radius: 8px;
  box-shadow: 0 2px 12px rgba(59, 130, 246, 0.06);
}

.factor-analysis-container .header-content {
  display: flex;
  align-items: center;
  gap: 14px;
}

.factor-analysis-container .header-icon {
  font-size: 34px;
  color: #2563eb;
}

.factor-analysis-container .page-title {
  margin: 0 0 4px 0;
  color: #0f172a;
  font-size: 24px;
  font-weight: 700;
}

.factor-analysis-container .page-subtitle {
  margin: 0;
  color: #64748b;
  font-size: 14px;
}

.factor-analysis-container .filter-card,
.factor-analysis-container .ranking-card,
.factor-analysis-container .summary-card {
  margin-bottom: 16px;
  border-radius: 8px;
  border: 1px solid rgba(59, 130, 246, 0.12);
  box-shadow: 0 2px 12px rgba(59, 130, 246, 0.05);
}

.factor-analysis-container .task-alert {
  margin-bottom: 16px;
}

.factor-analysis-container .ic-distribution-chart {
  height: 220px;
}

@media (max-width: 768px) {
  .factor-analysis-container {
    padding: 12px;
  }

  .factor-analysis-container .page-header {
    flex-direction: column;
    align-items: flex-start;
  }
}
```

- [ ] **Step 5: Run frontend tests and build**

Run:

```bash
cd frontend/react-antd
npm run test -- FactorAnalysis.utils.test.ts --run
npm run build
```

Expected: both PASS.

- [ ] **Step 6: Commit Task 5**

Run:

```bash
git add frontend/react-antd/src/pages/FactorAnalysis.tsx frontend/react-antd/src/pages/FactorAnalysis.css frontend/react-antd/src/utils/router.tsx frontend/react-antd/src/App.tsx
git commit -m "feat: add factor analysis page"
```

---

### Task 6: End-To-End Verification And Polish

**Files:**
- Modify only files touched in Tasks 1-5 if verification reveals issues.

- [ ] **Step 1: Run backend focused tests**

Run:

```bash
uv run pytest tests/test_factor_performance_repository.py tests/test_factor_ranking_service.py tests/test_factor_ranking_api.py -q
```

Expected: PASS.

- [ ] **Step 2: Run frontend tests**

Run:

```bash
cd frontend/react-antd
npm run test -- FactorAnalysis.utils.test.ts --run
```

Expected: PASS.

- [ ] **Step 3: Run frontend production build**

Run:

```bash
cd frontend/react-antd
npm run build
```

Expected: PASS with generated `dist/`.

- [ ] **Step 4: Run backend import smoke check**

Run:

```bash
uv run python -c "from backend.api.main import app; print('ok')"
```

Expected: prints `ok`.

- [ ] **Step 5: Start frontend dev server for manual check**

Run:

```bash
cd frontend/react-antd
npm run dev -- --host 127.0.0.1
```

Expected: Vite prints a local URL. Open `/factor-analysis`, confirm:

- Top menu has “因子分析”.
- Filters render with default stock pool, frequency, target, and near-one-year range.
- Empty cached state invites “补齐缺失”.
- Clicking “补齐缺失” starts a task and shows progress.
- Ranking table renders once backend data exists.
- “强制刷新” opens a confirmation modal.

- [ ] **Step 6: Stop dev server**

Press `Ctrl+C` in the dev server session.

- [ ] **Step 7: Final git status check**

Run:

```bash
git status --short
```

Expected: only intentional source changes are present. Do not commit `.superpowers/` or generated `dist/`.

- [ ] **Step 8: Handle verification fixes**

Expected: no fixes are needed. If a verification command fails, return to the task that introduced that file, make the smallest correction there, rerun that task's verification command, and commit using that task's exact `git add ...` command plus `git commit -m "fix: polish factor analysis verification"`.

---

## Implementation Notes

- The ranking service intentionally uses `start_date/end_date` as query filters, not as the core cache identity. The durable identity is `factor_id + factor_code_hash + stock_pool_snapshot_hash + target + frequency + bar_time + metric_version`.
- `refresh_rankings()` must check existing `success` and `insufficient_data` bars before calling `factor_dataset_service.ensure_dataset()`. This is the main guard against repeated computation.
- `force=True` is allowed to recompute the current range and should overwrite matching per-bar metrics.
- The initial task store is process-local to match existing `backend/api/routers/mining.py`; do not introduce Celery/RQ in this iteration.
- Keep `.superpowers/` untracked. It was created by the visual brainstorming companion.

## Self-Review Checklist

- Spec coverage:
  - Independent page: Task 5.
  - Single stock pool/target/frequency filters: Task 5.
  - Per-bar cache model: Task 1.
  - Incremental missing-only refresh: Task 2.
  - Ranking query/refresh/task APIs: Task 3.
  - Frontend API and helper coverage: Task 4.
  - Verification: Task 6.
- Red-flag scan: no vague implementation steps are intentionally left.
- Type consistency:
  - Backend uses `stock_pool_key`, `target`, `frequency`, `start_date`, `end_date`.
  - Frontend API helpers map camelCase page state to backend snake_case query params.
  - Task status fields match `task_id`, `status`, `progress`, `total_items`, `cache_hits`, `computed_items`, `failed_items`, `current_factor`, `error`.
