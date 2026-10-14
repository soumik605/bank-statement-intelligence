"use client";

import { useState } from "react";
import { formatDate, formatDateTime, inr, signedInr } from "@/lib/format";
import type { AnalyzeResponse, Analytics, StatementInfo, Transaction } from "@/lib/types";
import { CheckIcon, WarningIcon } from "./icons";
import DocumentPreview from "./DocumentPreview";
import SpendingChart from "./SpendingChart";

function Card({ title, children, className = "" }: { title?: string; children: React.ReactNode; className?: string }) {
  return (
    <section className={`rounded-xl border border-border bg-surface p-5 ${className}`}>
      {title && <h2 className="mb-4 text-sm font-medium text-ink-2">{title}</h2>}
      {children}
    </section>
  );
}

function AccountCard({ account, count }: { account: StatementInfo; count: number }) {
  const missing = <span className="text-muted">Not shown in file</span>;
  const rows: [string, React.ReactNode][] = [
    ["Bank", account.bank_name ?? missing],
    ["Account", account.account_number ?? missing],
    ["Holder", account.holder_name ?? missing],
    ["Period", `${formatDate(account.period_start)} – ${formatDate(account.period_end)}`],
    ["Transactions", count],
  ];
  return (
    <Card title="Account">
      <dl className="grid grid-cols-2 gap-x-6 gap-y-4 text-sm sm:grid-cols-3 lg:grid-cols-5">
        {rows.map(([label, value]) => (
          <div key={label}>
            <dt className="text-ink-2">{label}</dt>
            <dd className="mt-0.5 font-medium">{value}</dd>
          </div>
        ))}
      </dl>
    </Card>
  );
}

function StatTile({ label, value, note, valueClass = "" }: { label: string; value: string; note: string; valueClass?: string }) {
  return (
    <Card>
      <p className="text-sm text-ink-2">{label}</p>
      <p className={`mt-2 text-3xl font-semibold tracking-tight ${valueClass}`}>{value}</p>
      <p className="mt-1 text-sm text-muted">{note}</p>
    </Card>
  );
}

function Totals({ summary }: { summary: Analytics }) {
  const net = summary.net_flow;
  return (
    <>
      <StatTile label="Total credits" value={inr(summary.total_credits)} note="Money in" />
      <StatTile label="Total debits" value={inr(summary.total_debits)} note="Money out" />
      <StatTile
        label="Net flow"
        value={signedInr(net)}
        valueClass={net >= 0 ? "text-good-text" : ""}
        note={net >= 0 ? "More came in than went out" : "More went out than came in"}
      />
    </>
  );
}

function AiSummary({ summary }: { summary: AnalyzeResponse["ai_summary"] }) {
  return (
    <Card title="AI summary">
      <p className="whitespace-pre-line leading-relaxed">{summary.text}</p>
      <p className="mt-4 text-xs text-muted">
        {summary.source === "llm"
          ? "Written by the AI from the computed figures only; every number in it was checked against them."
          : "The AI summary wasn't available or used numbers it shouldn't have, so this one was built from a template."}
      </p>
    </Card>
  );
}

function UnusualList({ items }: { items: Transaction[] }) {
  return (
    <Card title={`Unusual transactions (${items.length})`}>
      {items.length === 0 ? (
        <p className="flex items-center gap-2 text-sm text-ink-2">
          <CheckIcon className="size-4 text-good-text" /> Nothing unusual found.
        </p>
      ) : (
        <ul className="flex flex-col gap-3">
          {items.map((t, i) => (
            <li key={i} className="rounded-lg border-l-4 border-warning bg-warning-wash px-4 py-3">
              <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
                <span className="font-medium wrap-break-word">{t.description}</span>
                <span className={`font-semibold tabular-nums ${t.type === "credit" ? "text-good-text" : ""}`}>
                  {signedInr(t.type === "credit" ? t.amount : -t.amount)}
                </span>
              </div>
              <p className="mt-1 text-xs text-muted">{formatDate(t.date)}</p>
              <p className="mt-2 flex gap-2 text-sm">
                <WarningIcon className="mt-0.5 size-4 shrink-0" />
                <span>
                  <span className="sr-only">Why: </span>
                  {t.unusual_reason}
                </span>
              </p>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

function SourceTag({ source }: { source: Transaction["category_source"] }) {
  if (!source) return null;
  const ai = source === "llm";
  return (
    <span
      title={ai ? "Categorized by the AI model" : "Categorized by a keyword rule"}
      className="rounded border border-border px-1 text-[10px] font-medium uppercase tracking-wide text-muted"
    >
      {ai ? "AI" : "rule"}
    </span>
  );
}

function TransactionsTable({ transactions, skipped }: { transactions: Transaction[]; skipped: AnalyzeResponse["skipped_rows"] }) {
  return (
    <Card title={`Transactions (${transactions.length})`}>
      <div className="max-h-[36rem] overflow-auto rounded-lg border border-hairline">
        <table className="w-full min-w-[44rem] text-sm">
          <thead className="sticky top-0 z-10 bg-surface text-left text-ink-2 shadow-[0_1px_0_var(--hairline)]">
            <tr>
              <th className="px-3 py-2 font-medium">Date</th>
              <th className="px-3 py-2 font-medium">Description</th>
              <th className="px-3 py-2 font-medium">Category</th>
              <th className="px-3 py-2 text-right font-medium">Amount</th>
              <th className="px-3 py-2 text-right font-medium">Balance</th>
            </tr>
          </thead>
          <tbody>
            {transactions.map((t, i) => (
              <tr key={i} className={`border-t border-hairline ${t.is_unusual ? "bg-warning-wash" : ""}`}>
                <td className="whitespace-nowrap px-3 py-2 tabular-nums text-ink-2">{formatDate(t.date)}</td>
                <td className="px-3 py-2">
                  <span className="wrap-break-word">{t.description}</span>
                  {t.is_unusual && (
                    <span title={t.unusual_reason ?? ""} className="ml-2 inline-flex items-center gap-1 whitespace-nowrap text-xs font-medium">
                      <WarningIcon className="size-3.5" /> Unusual
                    </span>
                  )}
                </td>
                <td className="whitespace-nowrap px-3 py-2">
                  <span className="inline-flex items-center gap-1.5">
                    <span className="rounded-full bg-wash px-2 py-0.5 text-xs font-medium">{t.category ?? "—"}</span>
                    <SourceTag source={t.category_source} />
                  </span>
                </td>
                <td className={`whitespace-nowrap px-3 py-2 text-right tabular-nums ${t.type === "credit" ? "text-good-text" : ""}`}>
                  {signedInr(t.type === "credit" ? t.amount : -t.amount, { paise: "always" })}
                </td>
                <td className="whitespace-nowrap px-3 py-2 text-right tabular-nums text-ink-2">
                  {t.balance === null ? "—" : (t.balance < 0 ? "−" : "") + inr(t.balance, { paise: "always" })}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {skipped.length > 0 && (
        <details className="mt-3 text-xs text-muted">
          <summary className="cursor-pointer">
            {skipped.length === 1 ? "1 row in the file wasn't a transaction" : `${skipped.length} rows in the file weren't transactions`}{" "}
            (totals, opening balance, footers)
          </summary>
          <ul className="mt-2 flex flex-col gap-1">
            {skipped.map((s, i) => (
              <li key={i}>
                <span className="font-medium">{s.reason}:</span> {Object.values(s.row).filter(Boolean).join(" · ")}
              </li>
            ))}
          </ul>
        </details>
      )}
    </Card>
  );
}

export default function Results({ result, onReset }: { result: AnalyzeResponse; onReset: () => void }) {
  const [previewing, setPreviewing] = useState(false);
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="text-sm text-ink-2">
            Results for <span className="font-medium text-ink">{result.source_file}</span>
          </p>
          <p className="mt-0.5 flex items-center gap-1.5 text-xs text-muted">
            {result.analysis_id !== null && result.analyzed_at ? (
              <>
                <CheckIcon className="size-3.5 text-good-text" /> Saved to history · analyzed{" "}
                {formatDateTime(result.analyzed_at)}
              </>
            ) : (
              <>
                <WarningIcon className="size-3.5" /> Couldn&apos;t be saved to history
              </>
            )}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          {result.analysis_id !== null && (
            <button
              type="button"
              onClick={() => setPreviewing(true)}
              className="h-9 rounded-lg border border-border bg-surface px-4 text-sm font-medium transition-colors hover:bg-wash"
            >
              View original document
            </button>
          )}
          <button
            type="button"
            onClick={onReset}
            className="h-9 rounded-lg border border-border bg-surface px-4 text-sm font-medium transition-colors hover:bg-wash"
          >
            Analyze another statement
          </button>
        </div>
      </div>
      {previewing && result.analysis_id !== null && (
        <DocumentPreview analysisId={result.analysis_id} fileName={result.source_file} onClose={() => setPreviewing(false)} />
      )}

      <AccountCard account={result.account} count={result.summary.transaction_count} />
      <div className="grid gap-4 sm:grid-cols-3">
        <Totals summary={result.summary} />
      </div>

      <AiSummary summary={result.ai_summary} />

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Spending by category">
          <SpendingChart analytics={result.summary} />
        </Card>
        <UnusualList items={result.unusual} />
      </div>

      <TransactionsTable transactions={result.transactions} skipped={result.skipped_rows} />
    </div>
  );
}
