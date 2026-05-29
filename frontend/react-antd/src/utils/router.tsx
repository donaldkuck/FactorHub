import { lazy } from 'react'

// 懒加载页面组件
const Home = lazy(() => import('@/pages/Home'))
const FactorManagement = lazy(() => import('@/pages/FactorManagement'))
const FactorDetail = lazy(() => import('@/pages/FactorDetail'))
const FactorMining = lazy(() => import('@/pages/FactorMining'))
const FactorAnalysis = lazy(() => import('@/pages/FactorAnalysis'))
const DataImport = lazy(() => import('@/pages/DataImport'))
const DataCoverage = lazy(() => import('@/pages/DataCoverage'))
const PortfolioAnalysis = lazy(() => import('@/pages/PortfolioAnalysis'))
const Backtesting = lazy(() => import('@/pages/Backtesting'))

// 路由配置
export const routes = [
  {
    path: '/',
    key: 'home',
    label: '首页',
    icon: 'DashboardOutlined',
    component: Home
  },
  {
    path: '/factor-management',
    key: 'factor-management',
    label: '因子管理',
    icon: 'FileTextOutlined',
    component: FactorManagement
  },
  {
    path: '/factor-detail',
    key: 'factor-detail',
    label: '因子详情',
    component: FactorDetail,
    hideInMenu: true
  },
  {
    path: '/factor-mining',
    key: 'factor-mining',
    label: '因子挖掘',
    icon: 'ExperimentOutlined',
    component: FactorMining
  },
  {
    path: '/factor-analysis',
    key: 'factor-analysis',
    label: '因子分析',
    icon: 'BarChartOutlined',
    component: FactorAnalysis
  },
  {
    path: '/data-import',
    key: 'data-import',
    label: '数据导入',
    icon: 'DatabaseOutlined',
    component: DataImport
  },
  {
    path: '/data-coverage',
    key: 'data-coverage',
    label: '数据查看',
    icon: 'DatabaseOutlined',
    component: DataCoverage
  },
  {
    path: '/portfolio-analysis',
    key: 'portfolio-analysis',
    label: '组合分析',
    icon: 'PieChartOutlined',
    component: PortfolioAnalysis
  },
  {
    path: '/backtesting',
    key: 'backtesting',
    label: '策略回测',
    icon: 'SyncOutlined',
    component: Backtesting
  }
]

export default routes
