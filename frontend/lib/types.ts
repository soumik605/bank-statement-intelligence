// Mirrors AnalyzeResponse in backend/app/schema.py (full JSON Schema: docs/schema.json).

export type Category =
  | "Salary"
  | "Income"
  | "Refund"
  | "Rent"
  | "Utilities"
  | "Food"
  | "Travel"
  | "Shopping"
  | "Transfer"
  | "Cash"
  | "Other";

export interface Transaction {
  date: string; // YYYY-MM-DD
  description: string;
  amount: number; // always positive; direction is in `type`
  type: "credit" | "debit";
  balance: number | null;
  category: Category | null;
  category_source: "rule" | "llm" | null;
  is_unusual: boolean;
  unusual_reason: string | null;
}

export interface StatementInfo {
  bank_name: string | null;
  account_number: string | null; // masked, e.g. XXXX1234
  holder_name: string | null;
  period_start: string;
  period_end: string;
}

export interface Analytics {
  transaction_count: number;
  total_credits: number;
  total_debits: number;
  net_flow: number;
  total_spending: number;
  spending_by_category: Partial<Record<Category, number>>;
  top_spending_categories: Category[];
}

export interface AnalyzeResponse {
  analysis_id: number | null; // null if it couldn't be saved to history
  analyzed_at: string | null; // ISO 8601, UTC
  source_file: string;
  account: StatementInfo;
  transactions: Transaction[];
  summary: Analytics;
  unusual: Transaction[];
  ai_summary: { text: string; source: "llm" | "template"; unsupported_numbers: string[] };
  skipped_rows: { row: Record<string, string>; reason: string }[];
}

export interface HistoryItem {
  id: number;
  analyzed_at: string;
  source_file: string;
  file_sha256: string;
  bank_name: string | null;
  account_number: string | null;
  period_start: string;
  period_end: string;
  transaction_count: number;
  total_credits: number;
  total_debits: number;
  net_flow: number;
  unusual_count: number;
  ai_summary_source: "llm" | "template";
}

export interface HistoryPage {
  items: HistoryItem[];
  total: number;
}

export interface FailureItem {
  id: number;
  failed_at: string;
  source_file: string;
  file_sha256: string | null;
  file_size: number;
  file_kept: boolean;
  http_status: number;
  error_code: string;
  message: string;
  exception_type: string | null;
  traceback: string | null;
  password_supplied: boolean; // a protected PDF: the kept copy needs its password, which isn't stored
  retry_count: number;
  last_retry_at: string | null;
  resolved_at: string | null; // a retry succeeded
  resolved_analysis_id: number | null; // what that retry produced; null if since deleted
}

export interface FailurePage {
  items: FailureItem[];
  total: number;
}
