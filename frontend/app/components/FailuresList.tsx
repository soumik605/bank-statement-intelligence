"use client";

import { useEffect, useState } from "react";
import { deleteFailure, failureFileUrl, listFailures } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import type { FailureItem } from "@/lib/types";
import { CheckIcon } from "./icons";

const PAGE = 20;

function fileSize(bytes: number) {
  return bytes < 1024 ? `${bytes} bytes` : `${(bytes / 1024).toFixed(0)} KB`;
}

// Uploads that failed, kept with their error and file so the parser can be
// improved against real statements. Hidden while there are none.
export default function FailuresList({
  onRetry,
  onOpenResult,
}: {
  onRetry: (item: FailureItem) => void;
  onOpenResult: (analysisId: number, fileName: string) => void;
}) {
  const [items, setItems] = useState<FailureItem[] | null>(null);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let current = true;
    listFailures(0, PAGE).then(
      (page) => {
        if (!current) return;
        setItems(page.items);
        setTotal(page.total);
      },
      (e) => current && setError(e instanceof Error ? e.message : "Couldn't load failed uploads."),
    );
    return () => {
      current = false;
    };
  }, []);

  async function loadMore() {
    try {
      const page = await listFailures(items?.length ?? 0, PAGE);
      setItems((shown) => [...(shown ?? []), ...page.items]);
      setTotal(page.total);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't load failed uploads.");
    }
  }

  async function remove(item: FailureItem) {
    if (!window.confirm(`Delete the failed upload of ${item.source_file} and its kept file?`)) return;
    try {
      await deleteFailure(item.id);
      setItems((shown) => shown?.filter((i) => i.id !== item.id) ?? null);
      setTotal((t) => t - 1);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't delete it.");
    }
  }

  if (error) return null; // the history list above already reports an unreachable server
  if (!items || total === 0) return null;

  return (
    <details className="group rounded-xl border border-border bg-surface p-5 sm:p-6">
      <summary className="cursor-pointer text-sm font-medium text-ink-2">
        Failed uploads ({total}) <span className="font-normal text-muted">· kept with their errors to improve the parser</span>
      </summary>
      <ul className="mt-3 divide-y divide-hairline">
        {items.map((item) => (
          <li key={item.id} className="py-3 text-sm">
            <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
              <span className="min-w-0 flex-1 truncate font-medium">{item.source_file}</span>
              {item.resolved_at ? (
                <span className="inline-flex items-center gap-1 rounded bg-wash px-1.5 py-0.5 text-xs font-medium text-good-text">
                  <CheckIcon className="size-3.5" /> Resolved
                </span>
              ) : (
                <code className="rounded bg-wash px-1.5 py-0.5 text-xs">{item.error_code}</code>
              )}
              <span className="text-xs text-muted">{formatDateTime(item.failed_at)}</span>
            </div>
            {item.resolved_at ? (
              <p className="mt-1 text-ink-2">
                A retry on {formatDateTime(item.resolved_at)} worked.{" "}
                {item.resolved_analysis_id !== null ? (
                  <button
                    type="button"
                    onClick={() => onOpenResult(item.resolved_analysis_id!, item.source_file)}
                    className="underline underline-offset-4 hover:text-ink"
                  >
                    Open the result
                  </button>
                ) : (
                  <span className="text-muted">(that analysis has since been deleted)</span>
                )}
                <span className="block text-xs text-muted">Originally failed with: {item.message}</span>
              </p>
            ) : (
              <p className="mt-1 text-ink-2">{item.message}</p>
            )}
            {item.retry_count > 0 && !item.resolved_at && item.last_retry_at && (
              <p className="mt-1 text-xs text-muted">
                Retried {item.retry_count} time{item.retry_count > 1 ? "s" : ""}, last on {formatDateTime(item.last_retry_at)}
              </p>
            )}
            {item.password_supplied && (
              <p className="mt-1 text-xs text-muted">Protected PDF: the kept copy needs its password, which isn&apos;t stored.</p>
            )}
            <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs">
              {item.file_kept && !item.resolved_at && (
                <button
                  type="button"
                  onClick={() => onRetry(item)}
                  title="Run the analysis again on the kept file, e.g. after the parser has been improved"
                  className="rounded-md border border-border px-2.5 py-1 font-medium text-ink hover:bg-wash"
                >
                  Retry
                </button>
              )}
              {item.file_kept ? (
                <a href={failureFileUrl(item.id)} download={item.source_file} className="text-ink-2 underline underline-offset-4 hover:text-ink">
                  Download file ({fileSize(item.file_size)})
                </a>
              ) : (
                <span className="text-muted">No file kept</span>
              )}
              <button type="button" onClick={() => remove(item)} className="text-ink-2 hover:text-critical">
                Delete
              </button>
              {item.traceback && (
                <details className="w-full">
                  <summary className="cursor-pointer text-ink-2">
                    Error details{item.exception_type ? ` (${item.exception_type})` : ""}
                  </summary>
                  <pre className="mt-2 max-h-64 overflow-auto rounded-lg bg-plane p-3 font-mono text-[11px] leading-relaxed text-ink-2">
                    {item.traceback}
                  </pre>
                </details>
              )}
            </div>
          </li>
        ))}
      </ul>
      {items.length < total && (
        <button type="button" onClick={loadMore} className="mt-2 text-sm text-ink-2 underline underline-offset-4 hover:text-ink">
          Show more
        </button>
      )}
    </details>
  );
}
