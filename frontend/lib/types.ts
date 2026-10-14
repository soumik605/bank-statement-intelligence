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
  source_file: string;
  account: StatementInfo;
  transactions: Transaction[];
  summary: Analytics;
  unusual: Transaction[];
  ai_summary: { text: string; source: "llm" | "template"; unsupported_numbers: string[] };
  skipped_rows: { row: Record<string, string>; reason: string }[];
}
