export type Role = 'buyer' | 'bill_to';
export type Publication = { id: string; published_at_utc: string };
export type Coverage = { date_from: string | null; date_to: string | null; observed_months: string[] };
export type Kpis = {
  base_amount: string | null; extended_amount: string; units: number; invoice_count: number;
  profit_amount: string; profit_margin: string | null; line_count: number; missing_base: number;
  base_status: string; margin_status: string;
};
export type Catalogs = { publication: Publication; coverage: Coverage;
  customers: { id: number; name: string }[]; products: { id: number; name: string }[]; catalogs_truncated: boolean };
export type Ranking = { id: number; name: string; kpis: Kpis };
export type Overview = {
  publication: Publication; coverage: Coverage; kpis: Kpis;
  monthly: { period: string; kpis: Kpis | null; observed: boolean }[];
  annual: { year: number; kpis: Kpis; observed_months: number[]; comparable_kpis: Kpis | null }[];
  comparison_months: number[]; comparison_partial_dates: boolean;
  top_products: Ranking[]; top_customers: Ranking[]; customer_role: Role;
};
export type Sale = {
  invoice_line_id: number; invoice_id: number; invoice_date: string;
  customer_id: number; customer_name: string; bill_to_customer_id: number; bill_to_customer_name: string;
  stock_item_id: number; product_name: string; quantity: number; unit_price: string | null;
  base_amount: string | null; tax_amount: string; extended_amount: string; profit_amount: string; is_credit_note: boolean;
};
export type Details = { publication: Publication; page: number; page_size: number; total: number; total_pages: number; items: Sale[] };
export type Draft = { from: string; until: string; customer: string; product: string; role: Role; credit: 'all' | 'sales' | 'credits' };

export type ForecastMethod = 'ridge' | 'seasonal_naive';
export type ForecastScores = { mae: number; rmse: number };
export type ForecastEvaluation = { ridge: ForecastScores; seasonal_naive: ForecastScores;
  predictions: { period: string; origin: string; actual: string; ridge: string; seasonal_naive: string }[] };
export type ForecastModel = {
  model_version: string; trained_at_utc: string; source_publication: Publication; trained_through: string;
  target: string; unit: string; horizon: number; selected_method: ForecastMethod; ridge_alpha: number;
  history: { period: string; amount: string; line_count: number }[];
  forecast: { period: string; amount: string; ridge: string; seasonal_naive: string; is_estimate: true }[];
  split: Record<'train' | 'validation' | 'test', { from: string; to: string; months: number }>;
  metrics: { validation: ForecastEvaluation; test: ForecastEvaluation };
  limitations: string[];
};
export type ForecastResult = { status: 'NOT_TRAINED'; message: string } |
  { status: 'READY' | 'STALE'; publication: Publication; model: ForecastModel };

export type AssistantStatus = { status: 'READY' | 'NOT_CONFIGURED'; provider: string; model: string | null; message: string; request_timeout_seconds?: number };
export type AssistantEvidence = { id: string; statement: string; query_id: string; source: string; period_from: string; period_to: string };
export type AssistantAnswer = { status: 'ANSWERED' | 'INSUFFICIENT_DATA' | 'NEEDS_CLARIFICATION' | 'NOT_CONFIGURED';
  message?: string; paragraphs?: string[]; evidence?: AssistantEvidence[]; citations?: string[];
  publication?: Publication; model?: string; partial_coverage?: boolean; composition?: string;
  period_notice?: string; period_policy?: 'QUESTION_OVERRIDES_DASHBOARD' | 'DASHBOARD_OR_COVERAGE';
  requested_periods?: { date_from: string; date_to: string; label: string }[];
  effective_periods?: { date_from: string; date_to: string; label: string }[];
  effective_filters?: Record<string, string | number | boolean | null> };
export type NotificationItem = { notification_id: string; title: string; body: string; delivered_at_utc: string;
  read_at_utc: string | null; source_publication: string; is_demo: boolean; delivery_status: string;
  period: string; change_pct: string; threshold_pct: string; mode: string };
export type NotificationsResult = { status: 'READY' | 'NOT_READY'; message?: string; items: NotificationItem[]; unread_count: number };
