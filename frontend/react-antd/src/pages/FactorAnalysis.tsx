import { useState, useEffect, useCallback, useRef } from 'react'
import {
  Card, Select, DatePicker, Button, Table, Tag, message,
  Row, Col, Statistic, Modal, Empty, Spin, Alert
} from 'antd'
import type { TableProps } from 'antd'
import {
  BarChartOutlined, ReloadOutlined, SyncOutlined,
  WarningOutlined, CheckCircleOutlined, CloseCircleOutlined,
  QuestionCircleOutlined
} from '@ant-design/icons'
import { useNavigate } from 'react-router-dom'
import dayjs, { Dayjs } from 'dayjs'
import ReactEChartsCore from 'echarts-for-react/lib/core'
import * as echarts from 'echarts/core'
import { BarChart } from 'echarts/charts'
import { TooltipComponent, GridComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'

import { api } from '@/services/api'
import {
  DEFAULT_STOCK_POOL,
  FALLBACK_STOCK_POOLS, type StockPoolOption
} from '@/constants/stockPools'
import {
  DEFAULT_FREQUENCY,
  getTargetsByFrequency,
  getDefaultTargetByFrequency,
} from '@/constants/factorTargets'
import './FactorAnalysis.css'

echarts.use([BarChart, TooltipComponent, GridComponent, CanvasRenderer])

const { RangePicker } = DatePicker

interface RankingItem {
  rank: number
  factor_id: number
  factor_name: string
  category?: string
  source?: string
  ic_mean: number | null
  ic_std?: number | null
  ir?: number | null
  ic_positive_ratio?: number | null
  bar_count: number
  total_bar_count?: number
  sample_size: number | null
  coverage: number | null
  status?: string
  error_message?: string | null
  first_bar_time?: string | null
  last_bar_time?: string | null
  last_updated_at: string | null
}

interface CacheSummary {
  completed: number
  failed: number
  insufficient_data: number
  pending: number
  total_bars: number
  distinct_bar_count?: number
  first_bar_time?: string | null
  last_bar_time?: string | null
  requested_span_days?: number | null
  actual_span_days?: number | null
  time_coverage?: number | null
}

interface RankingsData {
  items: RankingItem[]
  total: number
  page: number
  page_size: number
  cache_summary: CacheSummary
  problem_factors?: Array<{
    factor_id: number
    factor_name: string
    status: string
    count: number
    error_message?: string | null
  }>
}

interface TaskStatus {
  task_id: string
  status: string
  progress: number
  total_items: number
  cache_hits: number
  computed_items: number
  failed_items: number
  skipped_items?: number
  current_factor: string | null
  error: string | null
}

export default function FactorAnalysis() {
  const navigate = useNavigate()

  // Filter state
  const [stockPools, setStockPools] = useState<StockPoolOption[]>(FALLBACK_STOCK_POOLS)
  const [stockPoolKey, setStockPoolKey] = useState(DEFAULT_STOCK_POOL)
  const [frequency, setFrequency] = useState(DEFAULT_FREQUENCY)
  const [target, setTarget] = useState(getDefaultTargetByFrequency(DEFAULT_FREQUENCY))
  const [dateRange, setDateRange] = useState<[Dayjs, Dayjs]>([
    dayjs().subtract(1, 'year'),
    dayjs()
  ])
  const [sortBy, setSortBy] = useState('ic_mean')
  const [sortOrder, setSortOrder] = useState<'desc' | 'asc'>('desc')

  // Data state
  const [rankings, setRankings] = useState<RankingsData | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Refresh state
  const [taskStatus, setTaskStatus] = useState<TaskStatus | null>(null)
  const [refreshing, setRefreshing] = useState(false)
  const pollingRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const querySeqRef = useRef(0)

  // Pagination
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(50)

  // Remove polling on unmount
  useEffect(() => {
    return () => {
      if (pollingRef.current) clearInterval(pollingRef.current)
    }
  }, [])

  // Load stock pools
  useEffect(() => {
    api.getStockPools({ include_codes: false }).then((res: any) => {
      if (res.success && res.data) {
        setStockPools(res.data)
      }
    }).catch(() => {
      // Use fallback
    })
  }, [])

  // Frequency change → reset target
  const handleFrequencyChange = (value: string) => {
    setFrequency(value)
    setTarget(getDefaultTargetByFrequency(value))
    setPage(1)
  }

  // Query rankings
  const queryRankings = useCallback(async (p?: number, ps?: number) => {
    const querySeq = ++querySeqRef.current
    const startDate = dateRange[0].format('YYYY-MM-DD')
    const endDate = dateRange[1].format('YYYY-MM-DD')

    setLoading(true)
    setError(null)
    try {
      const res: any = await api.getFactorRankings({
        stock_pool_key: stockPoolKey,
        target,
        frequency,
        start_date: startDate,
        end_date: endDate,
        sort_by: sortBy,
        sort_order: sortOrder,
        page: p ?? page,
        page_size: ps ?? pageSize,
      })
      if (querySeq !== querySeqRef.current) return

      if (res.success) {
        setRankings(res.data)
        setPage(res.data.page)
        setPageSize(res.data.page_size)
      } else {
        setError('查询失败')
      }
    } catch (e: any) {
      if (querySeq !== querySeqRef.current) return
      setError(e.message || '查询失败')
    } finally {
      if (querySeq === querySeqRef.current) {
        setLoading(false)
      }
    }
  }, [stockPoolKey, target, frequency, dateRange, sortBy, sortOrder, page, pageSize])

  // Auto-query on filter changes
  useEffect(() => {
    setPage(1)
    queryRankings(1)
  }, [stockPoolKey, target, frequency, dateRange, sortBy, sortOrder])

  // Handle refresh
  const startRefresh = async (force: boolean) => {
    if (force) {
      Modal.confirm({
        title: '强制刷新',
        content: '将重新计算所有因子指标，可能需要较长时间。确认继续？',
        okText: '确认刷新',
        cancelText: '取消',
        onOk: () => doRefresh(force),
      })
    } else {
      doRefresh(force)
    }
  }

  const doRefresh = async (force: boolean, retryStatuses?: string[]) => {
    const startDate = dateRange[0].format('YYYY-MM-DD')
    const endDate = dateRange[1].format('YYYY-MM-DD')

    setRefreshing(true)
    try {
      const res: any = await api.refreshFactorRankings({
        stock_pool_key: stockPoolKey,
        target,
        frequency,
        start_date: startDate,
        end_date: endDate,
        force,
        retry_statuses: retryStatuses,
      })
      if (res.success && res.data) {
        setTaskStatus(null)
        if (res.data.summary?.deduped) {
          message.info('已有相同补齐任务正在运行，已继续跟踪该任务')
        }
        pollTaskStatus(res.data.task_id)
      }
    } catch (e: any) {
      showRefreshError(e.message || '刷新启动失败')
      setRefreshing(false)
    }
  }

  const isLocalBarError = (text?: string | null) => {
    return Boolean(text && (text.includes('本地 raw_bar 缺少') || text.includes('本地 raw_bar 覆盖不足')))
  }

  const showRefreshError = (text: string) => {
    if (isLocalBarError(text)) {
      Modal.warning({
        title: '本地 K 线数据不足',
        content: text,
        okText: '去数据导入',
        onOk: () => navigate('/data-import'),
      })
      return
    }
    message.error(text)
  }

  const cancelRefresh = async () => {
    if (!taskStatus?.task_id) return
    try {
      const res: any = await api.cancelRefreshTask(taskStatus.task_id)
      if (res.success && res.data) {
        if (pollingRef.current) clearInterval(pollingRef.current)
        setTaskStatus(res.data as TaskStatus)
        setRefreshing(false)
        message.warning('已取消刷新任务')
        queryRankings()
      }
    } catch (e: any) {
      message.error(e.message || '取消任务失败')
    }
  }

  const handleTableChange: TableProps<RankingItem>['onChange'] = (pagination, _filters, sorter) => {
    const nextPage = pagination.current ?? 1
    const nextPageSize = pagination.pageSize ?? pageSize
    const activeSorter = Array.isArray(sorter) ? sorter[0] : sorter

    if (activeSorter?.field && activeSorter.order) {
      setSortBy(String(activeSorter.field))
      setSortOrder(activeSorter.order === 'ascend' ? 'asc' : 'desc')
      setPage(1)
      return
    }

    setPage(nextPage)
    setPageSize(nextPageSize)
    queryRankings(nextPage, nextPageSize)
  }

  const pollTaskStatus = (taskId: string) => {
    const poll = () => {
      api.getRefreshTaskStatus(taskId).then((res: any) => {
        if (res.success && res.data) {
          const ts = res.data as TaskStatus
          setTaskStatus(ts)
          if (ts.status === 'completed' || ts.status === 'partial' || ts.status === 'cancelled' || ts.status === 'failed') {
            if (pollingRef.current) clearInterval(pollingRef.current)
            setRefreshing(false)
            if (ts.status === 'completed') {
              message.success('缓存刷新完成')
              queryRankings()
            } else if (ts.status === 'partial') {
              message.warning(ts.error || '本轮补齐已完成，可继续点击补齐缺失')
              queryRankings()
            } else if (ts.status === 'cancelled') {
              message.warning('刷新任务已取消')
              queryRankings()
            } else {
              showRefreshError(ts.error || '刷新失败')
            }
          }
        }
      }).catch(() => {
        if (pollingRef.current) clearInterval(pollingRef.current)
        setRefreshing(false)
      })
    }
    pollingRef.current = setInterval(poll, 2000)
    poll()
  }

  // IC distribution chart option
  const chartOption = rankings?.items?.length ? {
    tooltip: { trigger: 'axis' as const },
    grid: { left: 50, right: 20, top: 20, bottom: 40 },
    xAxis: {
      type: 'category' as const,
      data: rankings.items.slice(0, 20).map(f => f.factor_name.length > 12
        ? f.factor_name.substring(0, 12) + '...'
        : f.factor_name),
      axisLabel: { rotate: 45, fontSize: 10 },
    },
    yAxis: { type: 'value' as const, name: 'IC Mean' },
    series: [{
      type: 'bar' as const,
      data: rankings.items.slice(0, 20).map(f => f.ic_mean ?? 0),
      itemStyle: {
        color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
          { offset: 0, color: '#3b82f6' },
          { offset: 1, color: '#93c5fd' },
        ]),
        borderRadius: [4, 4, 0, 0],
      },
    }],
  } : null

  // Table columns
  const columns = [
    { title: '排名', dataIndex: 'rank', key: 'rank', width: 60, align: 'center' as const },
    {
      title: '因子名称', dataIndex: 'factor_name', key: 'factor_name',
      render: (name: string, record: RankingItem) => (
        <a onClick={() => navigate(`/factor-detail?id=${record.factor_id}`)}>{name}</a>
      ),
    },
    {
      title: 'IC 均值', dataIndex: 'ic_mean', key: 'ic_mean', sorter: true,
      sortOrder: sortBy === 'ic_mean' ? (sortOrder === 'asc' ? 'ascend' as const : 'descend' as const) : null,
      render: (v: number | null) => v != null ? v.toFixed(4) : '-',
    },
    {
      title: 'IR', dataIndex: 'ir', key: 'ir', sorter: true,
      sortOrder: sortBy === 'ir' ? (sortOrder === 'asc' ? 'ascend' as const : 'descend' as const) : null,
      render: (v: number | null) => v != null ? v.toFixed(3) : '-',
    },
    {
      title: '正 IC', dataIndex: 'ic_positive_ratio', key: 'ic_positive_ratio', sorter: true,
      sortOrder: sortBy === 'ic_positive_ratio' ? (sortOrder === 'asc' ? 'ascend' as const : 'descend' as const) : null,
      render: (v: number | null) => v != null ? `${(v * 100).toFixed(1)}%` : '-',
    },
    {
      title: 'Bar 数', dataIndex: 'bar_count', key: 'bar_count', sorter: true,
      sortOrder: sortBy === 'bar_count' ? (sortOrder === 'asc' ? 'ascend' as const : 'descend' as const) : null,
      render: (v: number, record: RankingItem) => (
        <span title={record.first_bar_time && record.last_bar_time
          ? `有效 IC: ${record.first_bar_time.slice(0, 10)} 至 ${record.last_bar_time.slice(0, 10)}`
          : undefined}
        >
          {v}
          {record.total_bar_count && record.total_bar_count !== v ? ` / ${record.total_bar_count}` : ''}
        </span>
      ),
    },
    {
      title: '平均样本', dataIndex: 'sample_size', key: 'sample_size',
      render: (v: number | null) => v != null ? v.toFixed(0) : '-',
    },
    {
      title: '覆盖率', dataIndex: 'coverage', key: 'coverage', sorter: true,
      sortOrder: sortBy === 'coverage' ? (sortOrder === 'asc' ? 'ascend' as const : 'descend' as const) : null,
      render: (v: number | null) => v != null ? `${(v * 100).toFixed(1)}%` : '-',
    },
    {
      title: '状态', dataIndex: 'status', key: 'status',
      render: (status: string, record: RankingItem) => {
        if (status === 'failed') return <Tag color="error" title={record.error_message || undefined}>失败</Tag>
        if (status === 'insufficient_data') return <Tag color="warning">数据不足</Tag>
        return <Tag color="success">完成</Tag>
      },
    },
  ]

  const cacheSummary = rankings?.cache_summary
  const timeCoverage = cacheSummary?.time_coverage ?? null
  const showTimeCoverageWarning = Boolean(
    cacheSummary
    && cacheSummary.total_bars > 0
    && timeCoverage != null
    && timeCoverage < 0.5
  )
  const processedTaskItems = taskStatus
    ? taskStatus.cache_hits + taskStatus.computed_items + taskStatus.failed_items + (taskStatus.skipped_items || 0)
    : 0

  return (
    <div className="factor-analysis-page">
      {/* Title */}
      <div className="page-header">
        <BarChartOutlined style={{ fontSize: 24, color: '#3b82f6', marginRight: 12 }} />
        <h2 style={{ margin: 0, fontFamily: '"SF Mono", monospace', fontWeight: 700, color: '#1f2937' }}>
          因子分析
        </h2>
      </div>

      {/* Filter area */}
      <Card className="filter-card" size="small">
        <Row gutter={[16, 12]} align="middle">
          <Col xs={24} sm={12} md={6}>
            <label className="filter-label">股票池</label>
            <Select
              value={stockPoolKey}
              onChange={setStockPoolKey}
              style={{ width: '100%' }}
            >
              {stockPools.map(pool => (
                <Select.Option key={pool.key} value={pool.key}>{pool.label}</Select.Option>
              ))}
            </Select>
          </Col>
          <Col xs={24} sm={12} md={4}>
            <label className="filter-label">频率</label>
            <Select
              value={frequency}
              onChange={handleFrequencyChange}
              style={{ width: '100%' }}
            >
              <Select.Option value="1d">日频</Select.Option>
              <Select.Option value="60m">60分钟</Select.Option>
            </Select>
          </Col>
          <Col xs={24} sm={12} md={4}>
            <label className="filter-label">目标</label>
            <Select
              value={target}
              onChange={setTarget}
              style={{ width: '100%' }}
            >
              {getTargetsByFrequency(frequency).map(t => (
                <Select.Option key={t.value} value={t.value}>{t.label}</Select.Option>
              ))}
            </Select>
          </Col>
          <Col xs={24} sm={12} md={5}>
            <label className="filter-label">时间窗口</label>
            <RangePicker
              value={dateRange}
              onChange={(dates) => { if (dates) setDateRange([dates[0]!, dates[1]!]) }}
              style={{ width: '100%' }}
              allowClear={false}
            />
          </Col>
          <Col xs={24} sm={12} md={3}>
            <label className="filter-label">排序</label>
            <Select
              value={sortBy}
              onChange={setSortBy}
              style={{ width: '100%' }}
            >
              <Select.Option value="ic_mean">IC 均值</Select.Option>
              <Select.Option value="ir">IR</Select.Option>
              <Select.Option value="ic_positive_ratio">正 IC</Select.Option>
              <Select.Option value="bar_count">Bar 数</Select.Option>
              <Select.Option value="coverage">覆盖率</Select.Option>
            </Select>
          </Col>
          <Col xs={24} sm={12} md={2}>
            <label className="filter-label">方向</label>
            <Select
              value={sortOrder}
              onChange={setSortOrder}
              style={{ width: '100%' }}
            >
              <Select.Option value="desc">降序</Select.Option>
              <Select.Option value="asc">升序</Select.Option>
            </Select>
          </Col>
          <Col xs={24} sm={12} md={2}>
            <Button
              type="primary"
              icon={<ReloadOutlined />}
              onClick={() => queryRankings()}
              loading={loading}
              block
            >
              查询
            </Button>
          </Col>
        </Row>
      </Card>

      {/* Cache status */}
      {cacheSummary && (
        <Card className="cache-status-card" size="small">
          <Row gutter={[16, 8]}>
            <Col xs={12} sm={6}>
              <Statistic
                title="已完成"
                value={cacheSummary.completed}
                prefix={<CheckCircleOutlined style={{ color: '#52c41a' }} />}
              />
            </Col>
            <Col xs={12} sm={6}>
              <Statistic
                title="失败"
                value={cacheSummary.failed}
                prefix={<CloseCircleOutlined style={{ color: '#ff4d4f' }} />}
              />
            </Col>
            <Col xs={12} sm={6}>
              <Statistic
                title="数据不足"
                value={cacheSummary.insufficient_data}
                prefix={<WarningOutlined style={{ color: '#faad14' }} />}
              />
            </Col>
            <Col xs={12} sm={6}>
              <Statistic
                title="总 Bar"
                value={cacheSummary.total_bars}
                prefix={<QuestionCircleOutlined style={{ color: '#1677ff' }} />}
              />
            </Col>
          </Row>
          <div className="cache-actions">
            <Button
              icon={<SyncOutlined spin={refreshing} />}
              onClick={() => startRefresh(false)}
              loading={refreshing}
              disabled={refreshing}
            >
              补齐缺失
            </Button>
            <Button
              icon={<ReloadOutlined />}
              onClick={() => startRefresh(true)}
              disabled={refreshing}
              danger
            >
              强制刷新
            </Button>
            <Button
              onClick={() => doRefresh(false, ['failed'])}
              disabled={refreshing || !cacheSummary.failed}
            >
              重试失败
            </Button>
            <Button
              onClick={() => doRefresh(false, ['insufficient_data'])}
              disabled={refreshing || !cacheSummary.insufficient_data}
            >
              重试数据不足
            </Button>
            {taskStatus && (
              <span className="task-progress">
                {taskStatus.status === 'running' && (
                  <>
                    处理中... {processedTaskItems}/{taskStatus.total_items}
                    {taskStatus.current_factor ? ` · ${taskStatus.current_factor}` : ''}
                    {taskStatus.cache_hits ? ` · 命中 ${taskStatus.cache_hits}` : ''}
                    {taskStatus.computed_items ? ` · 新算 ${taskStatus.computed_items}` : ''}
                    {taskStatus.skipped_items ? ` · 跳过 ${taskStatus.skipped_items}` : ''}
                  </>
                )}
                {taskStatus.status === 'running' && (
                  <Button size="small" danger onClick={cancelRefresh} style={{ marginLeft: 8 }}>
                    取消
                  </Button>
                )}
                {taskStatus.status === 'completed' && (
                  <Tag color="success">
                    完成 {processedTaskItems} 项，命中 {taskStatus.cache_hits}，新算 {taskStatus.computed_items}
                  </Tag>
                )}
                {taskStatus.status === 'partial' && (
                  <Tag color="warning">
                    本轮完成 {processedTaskItems}/{taskStatus.total_items}
                  </Tag>
                )}
                {taskStatus.status === 'failed' && (
                  <Tag color="error" title={taskStatus.error || undefined}>失败</Tag>
                )}
                {taskStatus.status === 'stalled' && (
                  <Tag color="warning" title={taskStatus.error || undefined}>已停滞</Tag>
                )}
                {taskStatus.status === 'cancelled' && (
                  <Tag color="default">已取消</Tag>
                )}
              </span>
            )}
          </div>
        </Card>
      )}

      {showTimeCoverageWarning && cacheSummary && (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 16 }}
          message="当前排名只覆盖了所选时间窗口的一小段数据"
          description={
            `实际参与排名的 Bar 时间范围：${cacheSummary.first_bar_time?.slice(0, 16) || '-'} 至 ${cacheSummary.last_bar_time?.slice(0, 16) || '-'}，` +
            `去重 Bar ${cacheSummary.distinct_bar_count || 0} 个，` +
            `时间跨度约 ${cacheSummary.actual_span_days ?? 0} / ${cacheSummary.requested_span_days ?? 0} 天。` +
            `${frequency === '60m' ? '60分钟补齐只能使用当前行情源返回的分钟数据，不能补出行情源未提供的更早历史分钟 Bar。' : ''}` +
            '这种情况下 IC/IR 容易偏高，不建议直接按排名判断因子有效。'
          }
        />
      )}

      {/* Error */}
      {error && (
        <Alert
          message={error}
          type="error"
          showIcon
          style={{ marginBottom: 16 }}
          action={<Button size="small" onClick={() => queryRankings()}>重试</Button>}
        />
      )}

      {/* Main content */}
      <Row gutter={[16, 16]}>
        <Col xs={24} lg={16}>
          <Card className="rankings-card" title="因子排名" size="small">
            {!rankings && loading ? (
              <div style={{ textAlign: 'center', padding: 60 }}><Spin /></div>
            ) : !rankings?.items?.length ? (
              <Empty description="暂无排名数据，请先补齐缓存" />
            ) : (
              <Table
                dataSource={rankings.items}
                columns={columns}
                rowKey="factor_id"
                size="small"
                pagination={{
                  current: rankings.page,
                  pageSize: rankings.page_size,
                  total: rankings.total,
                  showSizeChanger: true,
                  showTotal: (t) => `共 ${t} 个因子`,
                }}
                onChange={handleTableChange}
                expandable={{
                  rowExpandable: record => record.status === 'failed' && Boolean(record.error_message),
                  expandedRowRender: record => (
                    <Alert
                      type="error"
                      showIcon
                      message="计算失败"
                      description={record.error_message}
                    />
                  ),
                }}
                scroll={{ x: 900 }}
              />
            )}
          </Card>
        </Col>

        <Col xs={24} lg={8}>
          {/* IC Distribution Chart */}
          {chartOption && (
            <Card title="IC 分布 (Top 20)" size="small" style={{ marginBottom: 16 }}>
              <ReactEChartsCore
                echarts={echarts}
                option={chartOption}
                style={{ height: 280 }}
                notMerge
              />
            </Card>
          )}

          {/* Top/Bottom Summary */}
          {rankings?.items?.length ? (
            <>
              <Card title="极值摘要" size="small" style={{ marginBottom: 16 }}>
                <div className="summary-item">
                  <Tag color="blue">最高 IC</Tag>
                  <span>{rankings.items[0]?.factor_name}</span>
                  <strong style={{ marginLeft: 'auto' }}>
                    {rankings.items[0]?.ic_mean?.toFixed(4) ?? '-'}
                  </strong>
                </div>
                {rankings.items.length > 1 && (
                  <div className="summary-item">
                    <Tag color="red">最低 IC</Tag>
                    <span>{rankings.items[rankings.items.length - 1]?.factor_name}</span>
                    <strong style={{ marginLeft: 'auto' }}>
                      {rankings.items[rankings.items.length - 1]?.ic_mean?.toFixed(4) ?? '-'}
                    </strong>
                  </div>
                )}
                <div className="summary-item">
                  <Tag>因子总数</Tag>
                  <strong>{rankings.total}</strong>
                </div>
              </Card>

              {rankings.problem_factors?.length ? (
                <Card title="问题因子" size="small">
                  {rankings.problem_factors.slice(0, 8).map(item => (
                    <div className="summary-item" key={`${item.factor_id}-${item.status}`}>
                      <Tag color={item.status === 'failed' ? 'error' : 'warning'}>
                        {item.status === 'failed' ? '失败' : '数据不足'}
                      </Tag>
                      <span title={item.error_message || undefined}>{item.factor_name}</span>
                      <strong style={{ marginLeft: 'auto' }}>{item.count}</strong>
                    </div>
                  ))}
                </Card>
              ) : null}
            </>
          ) : null}
        </Col>
      </Row>
    </div>
  )
}
