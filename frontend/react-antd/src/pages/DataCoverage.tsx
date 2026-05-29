import { useEffect, useState } from 'react'
import { Button, Card, Form, Input, Select, Space, Table, Tag, message, Drawer } from 'antd'
import { DatabaseOutlined, ReloadOutlined } from '@ant-design/icons'
import ReactEChartsCore from 'echarts-for-react/lib/core'
import * as echarts from 'echarts/core'
import { CandlestickChart, BarChart } from 'echarts/charts'
import { DataZoomComponent, GridComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'

import { api } from '@/services/api'
import { FREQUENCIES } from '@/constants/factorTargets'

echarts.use([CandlestickChart, BarChart, DataZoomComponent, GridComponent, TooltipComponent, CanvasRenderer])

interface CoverageItem {
  stock_code: string
  source: string
  adjust?: string
  frequency: string
  rows: number
  start_time: string | null
  end_time: string | null
}

export default function DataCoverage() {
  const [form] = Form.useForm()
  const [loading, setLoading] = useState(false)
  const [items, setItems] = useState<CoverageItem[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(50)
  const [sampleOpen, setSampleOpen] = useState(false)
  const [sampleLoading, setSampleLoading] = useState(false)
  const [sampleRows, setSampleRows] = useState<any[]>([])
  const [sampleTitle, setSampleTitle] = useState('')

  const loadCoverage = async (nextPage = page, nextPageSize = pageSize) => {
    setLoading(true)
    try {
      const values = form.getFieldsValue()
      const res: any = await api.getImportedBarCoverage({
        ...values,
        page: nextPage,
        page_size: nextPageSize,
      })
      if (res.success) {
        setItems(res.data.items || [])
        setTotal(res.data.total || 0)
        setPage(res.data.page || nextPage)
        setPageSize(res.data.page_size || nextPageSize)
      }
    } catch (error: any) {
      message.error(error.message || '加载数据覆盖失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadCoverage(1, pageSize)
  }, [])

  const openSample = async (record: CoverageItem) => {
    const sampleSource = record.source.startsWith('raw_bar:') ? record.source : undefined
    setSampleOpen(true)
    setSampleLoading(true)
    setSampleTitle(`${record.stock_code} · ${record.frequency} · ${record.source}`)
    try {
      const res: any = await api.getImportedBarSample({
        stock_code: record.stock_code,
        frequency: record.frequency,
        source: sampleSource,
        adjust: record.adjust,
        limit: 200,
      })
      if (res.success) setSampleRows(res.data || [])
    } catch (error: any) {
      message.error(error.message || '加载 K 线样本失败')
    } finally {
      setSampleLoading(false)
    }
  }

  const columns = [
    { title: '股票', dataIndex: 'stock_code', key: 'stock_code', width: 120 },
    { title: '来源', dataIndex: 'source', key: 'source', width: 100, render: (v: string) => <Tag>{v}</Tag> },
    { title: '复权', dataIndex: 'adjust', key: 'adjust', width: 90, render: (v: string) => v || '未标记' },
    { title: 'K线', dataIndex: 'frequency', key: 'frequency', width: 90 },
    { title: '开始时间', dataIndex: 'start_time', key: 'start_time', render: (v: string | null) => v?.slice(0, 16) || '-' },
    { title: '结束时间', dataIndex: 'end_time', key: 'end_time', render: (v: string | null) => v?.slice(0, 16) || '-' },
    { title: 'Bar 数', dataIndex: 'rows', key: 'rows', align: 'right' as const, width: 100 },
    {
      title: '操作',
      key: 'action',
      width: 100,
      render: (_: any, record: CoverageItem) => (
        <Button size="small" onClick={() => openSample(record)} disabled={!record.source.startsWith('raw_bar')}>
          查看
        </Button>
      ),
    },
  ]

  const sampleColumns = [
    { title: '时间', dataIndex: 'bar_time', key: 'bar_time', render: (v: string) => v?.slice(0, 19) },
    { title: '开', dataIndex: 'open', key: 'open' },
    { title: '高', dataIndex: 'high', key: 'high' },
    { title: '低', dataIndex: 'low', key: 'low' },
    { title: '收', dataIndex: 'close', key: 'close' },
    { title: '量', dataIndex: 'volume', key: 'volume' },
    { title: '额', dataIndex: 'amount', key: 'amount' },
  ]

  const chartRows = [...sampleRows].sort((a, b) => String(a.bar_time).localeCompare(String(b.bar_time)))
  const chartOption = {
    tooltip: { trigger: 'axis' as const },
    grid: [
      { left: 55, right: 24, top: 24, height: 260 },
      { left: 55, right: 24, top: 320, height: 80 },
    ],
    xAxis: [
      {
        type: 'category' as const,
        data: chartRows.map(row => String(row.bar_time).slice(0, 16)),
        boundaryGap: false,
        axisLine: { lineStyle: { color: '#8c8c8c' } },
      },
      {
        type: 'category' as const,
        gridIndex: 1,
        data: chartRows.map(row => String(row.bar_time).slice(0, 16)),
        boundaryGap: false,
        axisLabel: { show: false },
        axisLine: { lineStyle: { color: '#8c8c8c' } },
      },
    ],
    yAxis: [
      { scale: true, splitLine: { lineStyle: { color: '#f0f0f0' } } },
      { scale: true, gridIndex: 1, splitNumber: 2, splitLine: { show: false } },
    ],
    dataZoom: [
      { type: 'inside' as const, xAxisIndex: [0, 1], start: 40, end: 100 },
      { type: 'slider' as const, xAxisIndex: [0, 1], bottom: 8, height: 20, start: 40, end: 100 },
    ],
    series: [
      {
        type: 'candlestick' as const,
        name: 'K线',
        data: chartRows.map(row => [row.open, row.close, row.low, row.high]),
        itemStyle: {
          color: '#ef5350',
          color0: '#26a69a',
          borderColor: '#ef5350',
          borderColor0: '#26a69a',
        },
      },
      {
        type: 'bar' as const,
        name: '成交量',
        xAxisIndex: 1,
        yAxisIndex: 1,
        data: chartRows.map(row => row.volume || 0),
        itemStyle: { color: '#8c8c8c' },
      },
    ],
  }

  return (
    <div style={{ padding: 24 }}>
      <div style={{ display: 'flex', alignItems: 'center', marginBottom: 16 }}>
        <DatabaseOutlined style={{ fontSize: 24, color: '#1677ff', marginRight: 12 }} />
        <h2 style={{ margin: 0 }}>数据查看</h2>
      </div>

      <Card size="small" style={{ marginBottom: 16 }}>
        <Form form={form} layout="inline" onFinish={() => loadCoverage(1, pageSize)}>
          <Form.Item label="股票" name="stock_code">
            <Input allowClear placeholder="600519.SH" style={{ width: 160 }} />
          </Form.Item>
          <Form.Item label="来源" name="source">
            <Input allowClear placeholder="raw_bar/factor_value/target_return/ranking" style={{ width: 260 }} />
          </Form.Item>
          <Form.Item label="复权" name="adjust" initialValue="hfq">
            <Select allowClear style={{ width: 130 }}>
              <Select.Option value="hfq">后复权</Select.Option>
              <Select.Option value="qfq">前复权</Select.Option>
              <Select.Option value="">不复权</Select.Option>
            </Select>
          </Form.Item>
          <Form.Item label="类型" name="cache_type" initialValue="all">
            <Select style={{ width: 150 }}>
              <Select.Option value="all">全部</Select.Option>
              <Select.Option value="raw">原始K线</Select.Option>
              <Select.Option value="factor_value">因子值</Select.Option>
              <Select.Option value="target_return">Target</Select.Option>
              <Select.Option value="ranking">排名缓存</Select.Option>
            </Select>
          </Form.Item>
          <Form.Item label="K线" name="frequency">
            <Select allowClear style={{ width: 140 }}>
              {FREQUENCIES.map(item => (
                <Select.Option key={item.value} value={item.value}>{item.label}</Select.Option>
              ))}
            </Select>
          </Form.Item>
          <Form.Item>
            <Space>
              <Button type="primary" htmlType="submit">查询</Button>
              <Button icon={<ReloadOutlined />} onClick={() => loadCoverage(1, pageSize)}>刷新</Button>
            </Space>
          </Form.Item>
        </Form>
      </Card>

      <Card title="K线覆盖" size="small">
        <Table
          rowKey={(record) => `${record.stock_code}-${record.source}-${record.frequency}`}
          size="small"
          columns={columns}
          dataSource={items}
          loading={loading}
          pagination={{
            current: page,
            pageSize,
            total,
            showSizeChanger: true,
            showTotal: (t) => `共 ${t} 条覆盖记录`,
          }}
          onChange={(pagination) => {
            loadCoverage(pagination.current || 1, pagination.pageSize || pageSize)
          }}
        />
      </Card>

      <Drawer
        title={sampleTitle}
        open={sampleOpen}
        onClose={() => setSampleOpen(false)}
        width={1040}
      >
        <ReactEChartsCore
          echarts={echarts}
          option={chartOption}
          notMerge
          style={{ height: 380, marginBottom: 16 }}
        />
        <Table
          rowKey="id"
          size="small"
          columns={sampleColumns}
          dataSource={sampleRows}
          loading={sampleLoading}
          pagination={false}
        />
      </Drawer>
    </div>
  )
}
