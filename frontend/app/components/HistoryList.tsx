"use client";

import { useEffect, useState } from "react";
import { deleteAnalysis, listHistory } from "@/lib/api";
import { formatDate, formatDateTime, inr } from "@/lib/format";
import type { HistoryItem } from "@/lib/types";
import { WarningIcon } from "./icons";

const PAGE = 20;

export default function HistoryList({ onOpen }: { onOpen: (item: HistoryItem) => void }) {
  const [items, setItems] = useState<HistoryItem[] | null>(null);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState<string | null>(null);

  const failed = (e: unknown) => setError(e instanceof Error ? e.message : "Couldn't load history.");

  useEffect(() => {
    let current = true; // ignore a late reply after the list has gone away
    listHistory(0, PAGE).then((page) => {
      if (!current) return;
      setItems(page.items);
      setTotal(page.total);
    }, (e) => current && failed(e));
    return () => {
      current = false;
    };
  }, []);

  async function loadMore() {
    try {
      const page = await listHistory(items?.length ?? 0, PAGE);
      setItems((shown) => [...(shown ?? []), ...page.items]);
      setTotal(page.total);
    } catch (e) {
      failed(e);
    }
  }

  async function remove(item: HistoryItem) {
    if (!window.confirm(`Delete the saved analysis of ${item.source_file}? This can't be undone.`)) return;
    try {
      await deleteAnalysis(item.id);
      setItems((current) => current?.filter((i) => i.id !== item.id) ?? null);
      setTotal((t) => t - 1);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't delete it.");
    }
  }

  return (
    <section className="rounded-xl border border-border bg-surface p-5 sm:p-6">
      <h2 className="text-sm font-medium text-ink-2">
        History{items && total > 0 ? ` (${total})` : ""}
      </h2>

      {error && <p className="mt-3 text-sm text-ink-2">{error}</p>}
      {!error && items === null && <p className="mt-3 text-sm text-muted">Loading…</p>}
      {items?.length === 0 && (
        <p className="mt-3 text-sm text-muted">No saved analyses yet. Every statement you analyze is kept here.</p>
      )}

      {items && items.length > 0 && (
        <ul className="mt-3 divide-y divide-hairline">
          {items.map((item) => (
            <li key={item.id} className="flex flex-wrap items-center gap-x-4 gap-y-2 py-3">
              <button
                type="button"
                onClick={() => onOpen(item)}
                className="min-w-0 flex-1 text-left hover:underline hover:underline-offset-4"
              >
                <span className="block truncate font-medium">{item.source_file}</span>
                <span className="block text-sm text-ink-2">
                  {[item.bank_name, item.account_number].filter(Boolean).join(" · ") || "Account details not in file"} ·{" "}
                  {formatDate(item.period_start)} – {formatDate(item.period_end)}
                </span>
                <span className="block text-xs text-muted">
                  Analyzed {formatDateTime(item.analyzed_at)} · {item.transaction_count} transactions · in{" "}
                  {inr(item.total_credits)}, out {inr(item.total_debits)}
                </span>
              </button>
              {item.unusual_count > 0 && (
                <span className="inline-flex items-center gap-1 rounded-full bg-warning-wash px-2 py-0.5 text-xs font-medium">
                  <WarningIcon className="size-3.5" /> {item.unusual_count} unusual
                </span>
              )}
              <button
                type="button"
                onClick={() => remove(item)}
                aria-label={`Delete ${item.source_file}`}
                className="rounded-lg px-2 py-1 text-sm text-ink-2 hover:bg-wash hover:text-critical"
              >
                Delete
              </button>
            </li>
          ))}
        </ul>
      )}

      {items && items.length < total && (
        <button
          type="button"
          onClick={loadMore}
          className="mt-2 text-sm text-ink-2 underline underline-offset-4 hover:text-ink"
        >
          Show more
        </button>
      )}
    </section>
  );
}
