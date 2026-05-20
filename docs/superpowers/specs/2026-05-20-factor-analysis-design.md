# 因子分析页设计

## 背景

新增一个独立的 Web 前端“因子分析”页面，用于展示全量因子在单一股票池、单一 target、单一 K 线频率下的表现排名。最终目标不是前端逐个因子计算，而是前端查询后端持久化的表现指标，并在需要时触发后端补齐缺失缓存。

当前项目已有能力：

- 前端：`frontend/react-antd`，使用 React、Ant Design、ECharts。
- 已有常量：`factorTargets.ts` 定义 target 和 frequency，`stockPools.ts` 定义股票池。
- 已有 API：因子列表、股票池列表、`/analysis/ic`、`/factor-datasets/ensure`。
- 后端已有基础缓存：因子值缓存、target 收益缓存、因子值与 target 收益 join 数据集。

本设计新增“表现指标缓存/排名查询”层，避免全量因子排名反复计算。

## 范围

本次设计覆盖：

- 独立前端页面：顶部菜单新增“因子分析”。
- 单选股票池、单选 target、单选 frequency。
- 时间窗口用于聚合查询，不作为整段结果缓存的主键。
- 后端按 bar 粒度缓存截面 IC 等原子指标，排名查询从原子指标聚合。
- 支持补齐缺失缓存、显示缓存覆盖率、展示失败原因。

暂不覆盖：

- 多股票池横向对比。
- 多 target 同屏矩阵。
- 因子组合构建或自动调参。
- 分布式任务队列；初版沿用项目现有遗传挖掘接口的 `BackgroundTasks + 内存任务状态` 方式。

## 推荐方案

采用“后端原子指标缓存 + 前端排名看板”。

前端不再对每个因子循环调用 `/analysis/ic`。页面只负责：

- 读取因子排名。
- 读取缓存覆盖状态。
- 触发缺失缓存补齐或强制刷新。
- 展示排名表、分布图、Top/Bottom 摘要和详情钻取入口。

后端负责：

- 识别哪些因子、日期 bar、股票池快照、target、frequency 的指标已经存在。
- 只计算缺失或失效的原子指标。
- 从已缓存的原子指标按用户选择的时间窗口聚合排名。

## 缓存模型

新增因子表现原子指标缓存表，命名为 `factor_performance_bar_cache`。

唯一键：

```text
factor_id
factor_code_hash
stock_pool_key
stock_pool_snapshot_hash
target
frequency
bar_time
metric_version
```

核心字段：

```text
id
factor_id
factor_name
factor_code_hash
stock_pool_key
stock_pool_snapshot_hash
target
frequency
bar_time
metric_version
ic_value
sample_size
coverage
status
error_message
created_at
updated_at
```

说明：

- `bar_time` 是原子缓存粒度。日频对应交易日，分钟频对应 K 线时间。
- `ic_value` 是该 `bar_time` 上股票池截面内 `factor_value` 与 `target_return` 的相关系数。
- `sample_size` 是该 `bar_time` 参与截面 IC 计算的有效股票数量。
- `coverage` 是 `sample_size / 当前股票池快照成分数量`。
- `factor_code_hash` 变化时，旧指标不会被误用。
- `stock_pool_snapshot_hash` 变化时，旧股票池成分指标不会被误用。
- `metric_version` 用于未来调整 IC 计算口径时整体隔离旧结果。
- `start_date/end_date` 不进入原子缓存唯一键，只作为排名查询的聚合过滤条件。

## 增量计算

排名刷新流程：

1. 根据 `stock_pool_key` 获取股票池成分和 `stock_pool_snapshot_hash`。
2. 获取目标范围内的因子列表，默认全量活跃因子。
3. 生成每个因子需要的 `bar_time` 列表。
4. 查询原子指标缓存，找出缺失、失败可重试、或因子代码 hash 已变化的组合。
5. 对缺失组合复用现有因子值缓存、target 收益缓存和 join 数据集，只计算缺失 bar 的截面 IC。每个 bar 至少需要 2 个有效股票样本，否则写入 `status=insufficient_data`。
6. 将每个 bar 的指标 upsert 到原子指标缓存。
7. 返回补齐结果：总组合数、命中缓存数、新计算数、失败数。

查询排名流程：

1. 按 `stock_pool_key + snapshot_hash + target + frequency + bar_time range + metric_version` 读取原子指标。
2. 按 `factor_id` 聚合：
   - `ic_mean = mean(ic_value)`
   - `ic_std = std(ic_value)`
   - `ir = ic_mean / ic_std`
   - `ic_positive_ratio = positive ic count / valid ic count`
   - `bar_count`
   - `sample_size_mean`
   - `coverage_mean`
   - `last_updated_at`
   - `status`
3. 返回可分页、可排序的排名数据。

日期窗口每天滚动时，只需要补齐新增交易日或缺失 bar，已有历史原子指标会被复用。

## 后端接口

新增读取排名接口：

```text
GET /analysis/factor-rankings
```

参数：

```text
stock_pool_key
target
frequency
start_date
end_date
sort_by
sort_order
page
page_size
status
```

响应数据：

```text
success
data.items[]
data.total
data.cache_summary
data.query
```

`items` 字段：

```text
rank
factor_id
factor_name
category
source
ic_mean
ic_std
ir
ic_positive_ratio
bar_count
sample_size
coverage
status
error_message
last_updated_at
```

新增补齐接口：

```text
POST /analysis/factor-rankings/refresh
```

请求体：

```text
stock_pool_key
target
frequency
start_date
end_date
factor_ids?
force
```

响应数据：

```text
success
data.task_id
data.summary
```

刷新接口初版使用 FastAPI `BackgroundTasks` 启动轻量异步任务，任务状态保存在进程内字典中，风格与 `backend/api/routers/mining.py` 当前实现保持一致。响应立即返回 `task_id` 和待补齐规模预估。

任务状态接口：

```text
GET /analysis/factor-rankings/tasks/{task_id}
```

用于轮询进度。响应包括：

```text
task_id
status
progress
total_items
cache_hits
computed_items
failed_items
current_factor
error
```

## 前端页面

新增页面：

```text
frontend/react-antd/src/pages/FactorAnalysis.tsx
frontend/react-antd/src/pages/FactorAnalysis.css
```

新增路由和菜单项：

```text
/factor-analysis
label: 因子分析
icon: BarChartOutlined
```

页面结构：

- 顶部筛选区：股票池、frequency、target、时间窗口、排序指标。
- 缓存状态区：缓存覆盖因子数、缺失数、失败数、最新 bar、补齐按钮、强制刷新按钮。
- 主表格：全量因子排名，支持分页、排序、筛选状态。
- 桌面端右侧摘要、移动端下方摘要：IC 分布图、Top/Bottom 因子、失败原因摘要。
- 行级动作：进入因子详情页，带上 `id` 和当前查询上下文。

默认值：

- 股票池：`DEFAULT_STOCK_POOL`。
- Frequency：`DEFAULT_FREQUENCY`。
- Target：`getDefaultTargetByFrequency(frequency)`。
- 时间窗口：近 1 年。
- 排序：`ic_mean desc`。

交互规则：

- 修改 frequency 时，target 自动切到该 frequency 下默认 target。
- 查询排名时只读缓存，不自动强制重算。
- 如果缓存覆盖不足，显示“补齐缺失”。
- “补齐缺失”只计算缺失项。
- “强制刷新”需要二次确认，并只刷新当前筛选范围。

## 错误处理

后端：

- 单个因子计算失败不阻断整个排名任务。
- 失败写入原子缓存的 `status=failed` 和 `error_message`。
- 查询接口返回成功结果，同时在 `cache_summary` 中汇总失败数量。
- 对非法 target/frequency 返回 400。
- 对不存在股票池返回 400，与当前 `stock_pools` 路由风格保持一致。

前端：

- 排名接口失败时展示错误提示和重试按钮。
- 补齐任务部分失败时保留成功结果，并展示失败数量。
- 表格中失败因子可展开查看错误。
- 缓存覆盖为 0 时显示空状态，引导用户点击补齐缺失。

## 测试策略

后端测试：

- 原子指标缓存唯一键避免重复写入。
- 同一查询窗口二次刷新只补齐缺失，不重复计算已存在 bar。
- 因子代码 hash 变化后会产生新缓存键。
- 股票池 snapshot hash 变化后不会误用旧结果。
- 排名聚合正确计算 IC 均值、标准差、IR、正 IC 占比和覆盖率。
- 单因子失败不会阻断其他因子结果。

前端测试：

- 页面默认筛选值正确。
- frequency 变更时 target 联动正确。
- 排名请求参数正确。
- 缓存覆盖状态渲染正确。
- 补齐缺失按钮调用 refresh 接口、轮询任务状态，并在完成后刷新排名。
- 表格排序、分页和失败状态展示正确。

验收标准：

- 顶部菜单存在“因子分析”独立入口。
- 能查询单股票池、单 target、单 frequency 下的全量因子排名。
- 日期窗口滚动不会整段重算历史结果，只补齐缺失 bar。
- 前端不承担全量因子计算，只展示后端聚合结果。
- 缓存命中、缺失、失败状态对用户可见。
