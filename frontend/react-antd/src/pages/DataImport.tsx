import { useEffect, useState } from 'react'
import { Alert, Button, Card, Checkbox, DatePicker, Form, Input, Select, Space, Statistic, Table, Tag, message } from 'antd'
import { DatabaseOutlined, ImportOutlined, ReloadOutlined } from '@ant-design/icons'
import dayjs from 'dayjs'

import { api } from '@/services/api'
import { DEFAULT_FREQUENCY, FREQUENCIES } from '@/constants/factorTargets'
import { DEFAULT_STOCK_POOL, FALLBACK_STOCK_POOLS, type StockPoolOption } from '@/constants/stockPools'

interface ImportedBarStat {
  source: string
  adjust?: string
  frequency: string
  rows: number
  stock_count: number
  start_time: string | null
  end_time: string | null
}

export default function DataImport() {
  const [form] = Form.useForm()
  const [qmtForm] = Form.useForm()
  const [syncForm] = Form.useForm()
  const [akshareForm] = Form.useForm()
  const [loading, setLoading] = useState(false)
  const [qmtLoading, setQmtLoading] = useState(false)
  const [syncLoading, setSyncLoading] = useState(false)
  const [akshareLoading, setAkshareLoading] = useState(false)
  const [statsLoading, setStatsLoading] = useState(false)
  const [stats, setStats] = useState<ImportedBarStat[]>([])
  const [lastResult, setLastResult] = useState<any>(null)
  const [qmtStatus, setQmtStatus] = useState<any>(null)
  const [stockPools, setStockPools] = useState<StockPoolOption[]>(FALLBACK_STOCK_POOLS)

  const loadStats = async () => {
    setStatsLoading(true)
    try {
      const res: any = await api.getImportedBarStats()
      if (res.success) setStats(res.data || [])
    } catch (error: any) {
      message.error(error.message || '加载导入统计失败')
    } finally {
      setStatsLoading(false)
    }
  }

  useEffect(() => {
    loadStats()
    api.getStockPools({ include_codes: false }).then((res: any) => {
      if (res.success && res.data) setStockPools(res.data)
    }).catch(() => {})
    api.getQMTConfig().then((res: any) => {
      if (res.success) qmtForm.setFieldsValue(res.data)
    }).catch(() => {})
  }, [])

  const handleImport = async (values: any) => {
    setLoading(true)
    setLastResult(null)
    try {
      const res: any = await api.importBars(values)
      if (res.success) {
        setLastResult(res.data)
        message.success(res.message || '导入完成')
        loadStats()
      }
    } catch (error: any) {
      message.error(error.message || '导入失败')
    } finally {
      setLoading(false)
    }
  }

  const checkQMT = async () => {
    try {
      const res: any = await api.getQMTStatus()
      if (res.success) setQmtStatus(res.data)
    } catch (error: any) {
      message.error(error.message || '检查 QMT 状态失败')
    }
  }

  const saveQMT = async (values: any) => {
    setQmtLoading(true)
    try {
      const res: any = await api.saveQMTConfig(values)
      if (res.success) {
        qmtForm.setFieldsValue(res.data)
        message.success('QMT 配置已保存')
        checkQMT()
      }
    } catch (error: any) {
      message.error(error.message || '保存 QMT 配置失败')
    } finally {
      setQmtLoading(false)
    }
  }

  const syncQMT = async (values: any) => {
    setSyncLoading(true)
    setLastResult(null)
    try {
      const stockText = String(values.stock_codes || '').trim()
      const range = values.date_range || []
      const payload = {
        stock_codes: stockText ? stockText.split(/[\s,，]+/).filter(Boolean) : undefined,
        stock_pool_key: stockText ? undefined : values.stock_pool_key,
        frequency: values.frequency,
        adjust: values.adjust,
        start_date: range[0].format('YYYY-MM-DD'),
        end_date: range[1].format('YYYY-MM-DD'),
        source: values.source,
        force: values.force,
        invalidate_derived: values.invalidate_derived,
      }
      const res: any = await api.syncQMTBars(payload)
      if (res.success) {
        setLastResult(res.data)
        message.success(res.message || 'QMT 同步完成')
        loadStats()
      }
    } catch (error: any) {
      message.error(error.message || 'QMT 同步失败')
    } finally {
      setSyncLoading(false)
    }
  }

  const importAkshare = async (values: any) => {
    setAkshareLoading(true)
    setLastResult(null)
    try {
      const stockText = String(values.stock_codes || '').trim()
      const range = values.date_range || []
      const payload = {
        stock_codes: stockText ? stockText.split(/[\s,，]+/).filter(Boolean) : undefined,
        stock_pool_key: stockText ? undefined : values.stock_pool_key,
        frequency: values.frequency,
        start_date: range[0].format('YYYY-MM-DD'),
        end_date: range[1].format('YYYY-MM-DD'),
        adjust: values.adjust,
        source: values.source,
        force: values.force,
        invalidate_derived: values.invalidate_derived,
      }
      const res: any = await api.importAkshareBars(payload)
      if (res.success) {
        setLastResult(res.data)
        message.success(res.message || 'AkShare/东方财富导入完成')
        loadStats()
      }
    } catch (error: any) {
      message.error(error.message || 'AkShare/东方财富导入失败')
    } finally {
      setAkshareLoading(false)
    }
  }

  const columns = [
    { title: '来源', dataIndex: 'source', key: 'source', render: (value: string) => <Tag>{value}</Tag> },
    { title: '复权', dataIndex: 'adjust', key: 'adjust', render: (value: string) => value || '未标记' },
    { title: '频率', dataIndex: 'frequency', key: 'frequency' },
    { title: '股票数', dataIndex: 'stock_count', key: 'stock_count', align: 'right' as const },
    { title: '行数', dataIndex: 'rows', key: 'rows', align: 'right' as const },
    { title: '开始时间', dataIndex: 'start_time', key: 'start_time', render: (v: string | null) => v?.slice(0, 16) || '-' },
    { title: '结束时间', dataIndex: 'end_time', key: 'end_time', render: (v: string | null) => v?.slice(0, 16) || '-' },
  ]

  return (
    <div style={{ padding: 24 }}>
      <div style={{ display: 'flex', alignItems: 'center', marginBottom: 16 }}>
        <DatabaseOutlined style={{ fontSize: 24, color: '#1677ff', marginRight: 12 }} />
        <h2 style={{ margin: 0 }}>数据导入</h2>
      </div>

      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 16 }}
        message="先导入原始 K 线，再做因子分析"
        description="建议把行情拉取和因子计算分开：本页负责把 AkShare/东方财富、QMT 或文件数据写入本地 raw_bar；因子分析页优先消费本地 K 线。导入后默认清理对应时间段的因子值、target 和排名缓存。"
      />

      <Card title="导入文件" size="small" style={{ marginBottom: 16 }}>
        <Form
          form={form}
          layout="vertical"
          initialValues={{
            frequency: DEFAULT_FREQUENCY,
            adjust: 'hfq',
            source: 'qmt',
            force: true,
            invalidate_derived: true,
          }}
          onFinish={handleImport}
        >
          <Form.Item
            label="文件路径"
            name="file_path"
            rules={[{ required: true, message: '请输入本机文件路径' }]}
          >
            <Input placeholder="/Users/.../qmt_60m.csv" />
          </Form.Item>

          <Space size="large" wrap>
            <Form.Item label="频率" name="frequency" rules={[{ required: true }]} style={{ minWidth: 180 }}>
              <Select>
                {FREQUENCIES.map(item => (
                  <Select.Option key={item.value} value={item.value}>{item.label}</Select.Option>
                ))}
              </Select>
            </Form.Item>

            <Form.Item label="复权口径" name="adjust" style={{ minWidth: 140 }}>
              <Select>
                <Select.Option value="hfq">后复权</Select.Option>
                <Select.Option value="qfq">前复权</Select.Option>
                <Select.Option value="">不复权</Select.Option>
              </Select>
            </Form.Item>

            <Form.Item label="来源标记" name="source" style={{ minWidth: 180 }}>
              <Input placeholder="qmt" />
            </Form.Item>

            <Form.Item name="force" valuePropName="checked" style={{ marginTop: 30 }}>
              <Checkbox>覆盖同来源已有 K 线</Checkbox>
            </Form.Item>

            <Form.Item name="invalidate_derived" valuePropName="checked" style={{ marginTop: 30 }}>
              <Checkbox>清理衍生缓存</Checkbox>
            </Form.Item>
          </Space>

          <Form.Item>
            <Button type="primary" htmlType="submit" icon={<ImportOutlined />} loading={loading}>
              开始导入
            </Button>
          </Form.Item>
        </Form>
      </Card>

      <Card title="从 AkShare/东方财富导入 K 线" size="small" style={{ marginBottom: 16 }}>
        <Form
          form={akshareForm}
          layout="vertical"
          initialValues={{
            stock_pool_key: DEFAULT_STOCK_POOL,
            frequency: '60m',
            date_range: [dayjs().subtract(1, 'year'), dayjs()],
            adjust: 'hfq',
            source: 'akshare_em',
            force: true,
            invalidate_derived: true,
          }}
          onFinish={importAkshare}
        >
          <Space size="large" wrap align="start">
            <Form.Item label="股票池" name="stock_pool_key" style={{ minWidth: 180 }}>
              <Select>
                {stockPools.map(pool => (
                  <Select.Option key={pool.key} value={pool.key}>{pool.label}</Select.Option>
                ))}
              </Select>
            </Form.Item>
            <Form.Item label="股票代码覆盖" name="stock_codes" style={{ minWidth: 260 }}>
              <Input placeholder="可选：600519.SH,000001.SZ" />
            </Form.Item>
            <Form.Item label="频率" name="frequency" style={{ minWidth: 160 }}>
              <Select>
                {FREQUENCIES.map(item => (
                  <Select.Option key={item.value} value={item.value}>{item.label}</Select.Option>
                ))}
              </Select>
            </Form.Item>
            <Form.Item label="时间窗口" name="date_range" rules={[{ required: true }]}>
              <DatePicker.RangePicker />
            </Form.Item>
            <Form.Item label="复权口径" name="adjust" style={{ minWidth: 140 }}>
              <Select>
                <Select.Option value="hfq">后复权</Select.Option>
                <Select.Option value="qfq">前复权</Select.Option>
                <Select.Option value="">不复权</Select.Option>
              </Select>
            </Form.Item>
            <Form.Item label="来源标记" name="source" style={{ minWidth: 150 }}>
              <Input />
            </Form.Item>
            <Form.Item name="force" valuePropName="checked" style={{ marginTop: 30 }}>
              <Checkbox>覆盖已有 K 线</Checkbox>
            </Form.Item>
            <Form.Item name="invalidate_derived" valuePropName="checked" style={{ marginTop: 30 }}>
              <Checkbox>清理衍生缓存</Checkbox>
            </Form.Item>
          </Space>
          <Form.Item>
            <Button type="primary" htmlType="submit" loading={akshareLoading}>
              从 AkShare/东方财富导入
            </Button>
          </Form.Item>
        </Form>
      </Card>

      <Card title="QMT 数据源" size="small" style={{ marginBottom: 16 }}>
        <Form
          form={qmtForm}
          layout="vertical"
          initialValues={{
            enabled: true,
            account_id: '',
            data_path: '',
            trade_path: '',
            auto_download_history: true,
          }}
          onFinish={saveQMT}
        >
          <Space size="large" wrap align="start">
            <Form.Item name="enabled" valuePropName="checked" style={{ marginTop: 30 }}>
              <Checkbox>启用 QMT</Checkbox>
            </Form.Item>
            <Form.Item label="账号" name="account_id" style={{ minWidth: 180 }}>
              <Input placeholder="8886168010" />
            </Form.Item>
            <Form.Item label="data_path" name="data_path" style={{ minWidth: 360 }}>
              <Input placeholder="D:\\国金证券QMT交易端\\userdata_mini\\datadir" />
            </Form.Item>
            <Form.Item label="trade_path" name="trade_path" style={{ minWidth: 360 }}>
              <Input placeholder="D:\\国金证券QMT交易端\\userdata_mini" />
            </Form.Item>
            <Form.Item name="auto_download_history" valuePropName="checked" style={{ marginTop: 30 }}>
              <Checkbox>同步前下载历史数据</Checkbox>
            </Form.Item>
          </Space>
          <Space>
            <Button type="primary" htmlType="submit" loading={qmtLoading}>保存配置</Button>
            <Button onClick={checkQMT}>检查状态</Button>
          </Space>
        </Form>
        {qmtStatus && (
          <Alert
            style={{ marginTop: 16 }}
            type={qmtStatus.xtquant_available ? 'success' : 'warning'}
            showIcon
            message={qmtStatus.xtquant_available ? 'xtquant 可用' : 'xtquant 不可用'}
            description={qmtStatus.error || `data_path: ${qmtStatus.data_path || '-'}`}
          />
        )}
      </Card>

      <Card title="从 QMT 同步 K 线" size="small" style={{ marginBottom: 16 }}>
        <Form
          form={syncForm}
          layout="vertical"
          initialValues={{
            stock_pool_key: DEFAULT_STOCK_POOL,
            frequency: '60m',
            date_range: [dayjs().subtract(1, 'year'), dayjs()],
            adjust: 'hfq',
            source: 'qmt',
            force: true,
            invalidate_derived: true,
          }}
          onFinish={syncQMT}
        >
          <Space size="large" wrap align="start">
            <Form.Item label="股票池" name="stock_pool_key" style={{ minWidth: 180 }}>
              <Select>
                {stockPools.map(pool => (
                  <Select.Option key={pool.key} value={pool.key}>{pool.label}</Select.Option>
                ))}
              </Select>
            </Form.Item>
            <Form.Item label="股票代码覆盖" name="stock_codes" style={{ minWidth: 260 }}>
              <Input placeholder="可选：600519.SH,000001.SZ" />
            </Form.Item>
            <Form.Item label="频率" name="frequency" style={{ minWidth: 160 }}>
              <Select>
                {FREQUENCIES.map(item => (
                  <Select.Option key={item.value} value={item.value}>{item.label}</Select.Option>
                ))}
              </Select>
            </Form.Item>
            <Form.Item label="时间窗口" name="date_range" rules={[{ required: true }]}>
              <DatePicker.RangePicker />
            </Form.Item>
            <Form.Item label="复权口径" name="adjust" style={{ minWidth: 140 }}>
              <Select>
                <Select.Option value="hfq">后复权</Select.Option>
                <Select.Option value="qfq">前复权</Select.Option>
                <Select.Option value="">不复权</Select.Option>
              </Select>
            </Form.Item>
            <Form.Item label="来源标记" name="source" style={{ minWidth: 140 }}>
              <Input />
            </Form.Item>
            <Form.Item name="force" valuePropName="checked" style={{ marginTop: 30 }}>
              <Checkbox>覆盖已有 K 线</Checkbox>
            </Form.Item>
            <Form.Item name="invalidate_derived" valuePropName="checked" style={{ marginTop: 30 }}>
              <Checkbox>清理衍生缓存</Checkbox>
            </Form.Item>
          </Space>
          <Form.Item>
            <Button type="primary" htmlType="submit" loading={syncLoading}>
              从 QMT 同步
            </Button>
          </Form.Item>
        </Form>
      </Card>

      {lastResult && (
        <Card size="small" style={{ marginBottom: 16 }}>
          <Space size="large" wrap>
            <Statistic title="导入行数" value={lastResult.rows} />
            <Statistic title="新增" value={lastResult.inserted} />
            <Statistic title="更新" value={lastResult.updated} />
            <Statistic title="股票数" value={lastResult.stock_count} />
            <Statistic title="开始" value={lastResult.start_time?.slice(0, 16) || '-'} />
            <Statistic title="结束" value={lastResult.end_time?.slice(0, 16) || '-'} />
          </Space>
        </Card>
      )}

      <Card
        title="已导入数据"
        size="small"
        extra={<Button icon={<ReloadOutlined />} onClick={loadStats} loading={statsLoading}>刷新</Button>}
      >
        <Table
          size="small"
          rowKey={(record) => `${record.source}-${record.adjust || ''}-${record.frequency}`}
          columns={columns}
          dataSource={stats}
          loading={statsLoading}
          pagination={false}
        />
      </Card>
    </div>
  )
}
