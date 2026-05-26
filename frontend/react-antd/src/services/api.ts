import axios from 'axios'

// 创建 axios 实例
const request = axios.create({
  baseURL: '/api',
  timeout: 30000,
  headers: {
    'Content-Type': 'application/json'
  }
})

// 请求拦截器
request.interceptors.request.use(
  config => {
    // 可以在这里添加 token
    return config
  },
  error => {
    console.error('请求错误:', error)
    return Promise.reject(error)
  }
)

// 响应拦截器
request.interceptors.response.use(
  response => {
    return response.data
  },
  error => {
    console.error('响应错误:', error)

    let message = '请求失败'

    if (error.response) {
      const { status, data } = error.response

      switch (status) {
        case 400:
          message = data.message || '请求参数错误'
          break
        case 401:
          message = '未授权，请重新登录'
          break
        case 403:
          message = '拒绝访问'
          break
        case 404:
          message = '请求的资源不存在'
          break
        case 500:
          message = data.message || '服务器错误'
          break
        default:
          message = data.message || `请求失败 (${status})`
      }
    } else if (error.request) {
      message = '网络错误，请检查网络连接'
    } else {
      message = error.message || '请求失败'
    }

    return Promise.reject(new Error(message))
  }
)

// API 接口
export const api = {
  // 获取因子统计
  getFactorStats() {
    return request.get('/factors/stats')
  },

  // 获取因子列表
  getFactors(params?: any) {
    return request.get('/factors', { params })
  },

  // 创建因子
  createFactor(data: any) {
    return request.post('/factors', data)
  },

  // 更新因子
  updateFactor(id: number, data: any) {
    return request.put(`/factors/${id}`, data)
  },

  // 删除因子
  deleteFactor(id: number) {
    return request.delete(`/factors/${id}`)
  },

  // 获取因子详情
  getFactorDetail(id: number) {
    return request.get(`/factors/${id}`)
  },

  // IC分析
  calculateIC(data: {
    factor_name: string
    stock_codes: string[]
    start_date: string
    end_date: string
    target?: string
    frequency?: string
  }) {
    return request.post('/analysis/ic', data)
  },

  // 因子值计算
  calculateFactor(data: {
    factor_name: string
    stock_codes: string[]
    start_date: string
    end_date: string
  }) {
    return request.post('/analysis/calculate', data)
  },

  // 获取股票数据
  getStockData(code: string, startDate: string, endDate: string, frequency?: string) {
    return request.get(`/data/stock/${code}`, {
      params: { start_date: startDate, end_date: endDate, frequency }
    })
  },

  // 组合分析
  analyzePortfolio(data: any) {
    return request.post('/portfolio/analyze', data)
  },

  // 策略回测
  runBacktest(data: any) {
    return request.post('/backtesting/run', data)
  },

  // 获取回测结果
  getBacktestResult(taskId: string) {
    return request.get(`/backtesting/results/${taskId}`)
  },

  // 验证因子公式
  validateFactor(data: any) {
    return request.post('/factors/validate', data)
  },

  // 批量生成因子
  batchGenerateFactors(data: any) {
    return request.post('/factors/batch-generate', data)
  },

  // 复制因子
  copyFactor(id: number) {
    return request.post(`/factors/${id}/copy`)
  },

  // 因子暴露度分析
  analyzeExposure(data: {
    factor_name: string
    stock_codes: string[]
    start_date: string
    end_date: string
  }) {
    return request.post('/analysis/exposure', data)
  },

  // 因子有效性分析
  analyzeEffectiveness(data: {
    factor_name: string
    stock_codes: string[]
    start_date: string
    end_date: string
  }) {
    return request.post('/analysis/effectiveness', data)
  },

  // 因子贡献度分解
  analyzeAttribution(data: {
    factor_name: string
    stock_codes: string[]
    start_date: string
    end_date: string
  }) {
    return request.post('/analysis/attribution', data)
  },

  // 时间序列动态监测
  analyzeMonitoring(data: {
    factor_name: string
    stock_codes: string[]
    start_date: string
    end_date: string
  }) {
    return request.post('/analysis/monitoring', data)
  },

  // 回填因子值缓存
  backfillFactorValues(id: number, data: {
    stock_codes: string[]
    start_date: string
    end_date: string
    frequency?: string
    force?: boolean
  }) {
    return request.post(`/factors/${id}/backfill-values`, data)
  },

  // 获取因子值缓存
  getFactorValues(id: number, params: {
    stock_code: string
    start_date: string
    end_date: string
    frequency?: string
  }) {
    return request.get(`/factors/${id}/values`, { params })
  },

  // 回填 target 收益缓存
  backfillTargetReturns(target: string, data: {
    stock_codes: string[]
    start_date: string
    end_date: string
    frequency?: string
    force?: boolean
  }) {
    return request.post(`/targets/${target}/backfill-returns`, data)
  },

  // 获取股票池
  getStockPools(params?: { include_codes?: boolean }) {
    return request.get('/stock-pools', { params })
  },

  // 手动刷新股票池
  refreshStockPool(poolKey: string) {
    return request.post(`/stock-pools/${poolKey}/refresh`, {}, { timeout: 300000 })
  },

  // 确保因子值和 target 收益 join 数据集
  ensureFactorDataset(data: {
    factor_id: number
    target: string
    frequency?: string
    stock_codes: string[]
    start_date: string
    end_date: string
    force?: boolean
  }) {
    return request.post('/factor-datasets/ensure', data)
  },

  // 遗传算法挖掘
  startGeneticMining(data: {
    stock_code?: string
    stock_codes?: string[]
    stock_pool?: string
    base_factors: string[]
    start_date: string
    end_date: string
    frequency: string
    target: string
    population_size: number
    n_generations: number
    cx_prob: number
    mut_prob: number
    elite_size: number
    fitness_objective: string
    ic_threshold: number
  }) {
    return request.post('/mining/genetic', data, { timeout: 300000 }) // 5分钟超时
  },

  // 获取挖掘状态
  getMiningStatus(taskId: string) {
    return request.get(`/mining/status/${taskId}`, { timeout: 300000 }) // 5分钟超时
  },

  // 获取挖掘结果
  getMiningResults(taskId: string) {
    return request.get(`/mining/results/${taskId}`, { timeout: 300000 }) // 5分钟超时
  },

  // 因子排名查询
  getFactorRankings(params: {
    stock_pool_key?: string
    target?: string
    frequency?: string
    start_date: string
    end_date: string
    sort_by?: string
    sort_order?: string
    page?: number
    page_size?: number
  }) {
    return request.get('/analysis/factor-rankings', { params })
  },

  // 刷新因子排名缓存
  refreshFactorRankings(data: {
    stock_pool_key?: string
    target?: string
    frequency?: string
    start_date: string
    end_date: string
    factor_ids?: number[]
    force?: boolean
    retry_statuses?: string[]
  }) {
    return request.post('/analysis/factor-rankings/refresh', data)
  },

  // 获取刷新任务状态
  getRefreshTaskStatus(taskId: string) {
    return request.get(`/analysis/factor-rankings/tasks/${taskId}`)
  },

  // 取消刷新任务
  cancelRefreshTask(taskId: string) {
    return request.post(`/analysis/factor-rankings/tasks/${taskId}/cancel`)
  }
}

export default request
