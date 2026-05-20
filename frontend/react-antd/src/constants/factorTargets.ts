export const DEFAULT_FACTOR_TARGET = 'next_1d_return'
export const DEFAULT_FREQUENCY = '1d'

export const FACTOR_TARGETS = [
  { value: 'next_1d_return', label: '次日收益率', frequency: '1d' },
  { value: 'next_5d_return', label: '未来5日收益率', frequency: '1d' },
  { value: 'next_10d_return', label: '未来10日收益率', frequency: '1d' },
  { value: 'next_1h_return', label: '未来1小时收益率', frequency: '60m' },
  { value: 'next_2h_return', label: '未来2小时收益率', frequency: '60m' },
  { value: 'next_4h_return', label: '未来4小时收益率', frequency: '60m' }
] as const

export const FREQUENCIES = [
  { value: '1d', label: '日频' },
  { value: '60m', label: '60分钟' }
] as const

export const getFrequencyLabel = (frequency?: string) =>
  FREQUENCIES.find(item => item.value === frequency)?.label || '日频'

export const getTargetsByFrequency = (frequency: string = DEFAULT_FREQUENCY) =>
  FACTOR_TARGETS.filter(item => item.frequency === frequency)

export const getDefaultTargetByFrequency = (frequency: string = DEFAULT_FREQUENCY) =>
  getTargetsByFrequency(frequency)[0]?.value || DEFAULT_FACTOR_TARGET

export const getTargetLabel = (target?: string) =>
  FACTOR_TARGETS.find(item => item.value === target)?.label || '次日收益率'
