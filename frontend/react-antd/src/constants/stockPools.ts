export const CUSTOM_STOCK_POOL = "custom";
export const DEFAULT_STOCK_POOL = "sse50";

export interface StockPoolOption {
  key: string;
  label: string;
  index_symbol?: string | null;
  source?: string;
  updated_at?: string | null;
  trade_date?: string | null;
  stock_count?: number;
  is_custom?: boolean;
  stock_codes?: string[];
}

export const FALLBACK_STOCK_POOLS: StockPoolOption[] = [
  { key: "sse50", label: "上证50", index_symbol: "000016", stock_count: 0 },
  { key: "csi300", label: "沪深300", index_symbol: "000300", stock_count: 0 },
  { key: "csi500", label: "中证500", index_symbol: "000905", stock_count: 0 },
  { key: "csi1000", label: "中证1000", index_symbol: "000852", stock_count: 0 },
  { key: CUSTOM_STOCK_POOL, label: "自定义", source: "manual", stock_count: 0, is_custom: true },
];
