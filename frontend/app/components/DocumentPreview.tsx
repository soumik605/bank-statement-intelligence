"use client";

import { useEffect, useRef, useState } from "react";
import { fetchOriginalFile, originalFileUrl } from "@/lib/api";
import { parseCsv } from "@/lib/csv";
import { Spinner } from "./icons";

const MAX_CSV_ROWS = 1000;

type Loaded = { kind: "pdf"; url: string } | { kind: "csv"; rows: string[][] } | { kind: "error"; message: string };

// The uploaded statement exactly as it was, in a modal. PDFs use the browser's own
// viewer (which asks for the password of a protected PDF); CSVs show their raw cells.
export default function DocumentPreview({
  analysisId,
  fileName,
  onClose,
}: {
  analysisId: number;
  fileName: string;
  onClose: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [loaded, setLoaded] = useState<Loaded | null>(null);

  useEffect(() => {
    dialog.current?.showModal();
  }, []);

  useEffect(() => {
    const abort = new AbortController();
    let objectUrl: string | null = null;
    fetchOriginalFile(analysisId, abort.signal).then(
      async (blob) => {
        if (/\.pdf$/i.test(fileName)) {
          objectUrl = URL.createObjectURL(new Blob([blob], { type: "application/pdf" }));
          setLoaded({ kind: "pdf", url: objectUrl });
        } else {
          setLoaded({ kind: "csv", rows: parseCsv(await blob.text()) });
        }
      },
      (e) => {
        if (!abort.signal.aborted) {
          setLoaded({ kind: "error", message: e instanceof Error ? e.message : "Couldn't load the file." });
        }
      },
    );
    return () => {
      abort.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [analysisId, fileName]);

  return (
    <dialog
      ref={dialog}
      onClose={onClose}
      aria-label={`Original document: ${fileName}`}
      className="m-auto flex h-[90vh] w-[min(72rem,calc(100%-2rem))] flex-col overflow-hidden rounded-xl border border-border bg-surface p-0 text-ink backdrop:bg-black/50"
    >
      <header className="flex items-center gap-3 border-b border-hairline px-5 py-3">
        <div className="min-w-0 flex-1">
          <p className="text-xs text-ink-2">Original document</p>
          <p className="truncate font-medium">{fileName}</p>
        </div>
        <a
          href={originalFileUrl(analysisId)}
          target="_blank"
          rel="noreferrer"
          className="rounded-lg px-3 py-1.5 text-sm text-ink-2 hover:bg-wash hover:text-ink"
        >
          Open in new tab
        </a>
        <button
          type="button"
          onClick={() => dialog.current?.close()}
          autoFocus
          className="rounded-lg border border-border px-3 py-1.5 text-sm font-medium hover:bg-wash"
        >
          Close
        </button>
      </header>

      <div className="min-h-0 flex-1 bg-plane">
        {loaded === null && (
          <div className="flex h-full items-center justify-center">
            <Spinner className="size-8 text-series-1" />
          </div>
        )}
        {loaded?.kind === "error" && (
          <p className="p-6 text-sm text-ink-2">
            {loaded.message.includes("isn't available")
              ? "The original file wasn't kept for this analysis. Statements analyzed from now on are kept, so they can be previewed."
              : loaded.message}
          </p>
        )}
        {loaded?.kind === "pdf" && <iframe src={loaded.url} title={fileName} className="h-full w-full border-0" />}
        {loaded?.kind === "csv" && <CsvGrid rows={loaded.rows} />}
      </div>
    </dialog>
  );
}

function CsvGrid({ rows }: { rows: string[][] }) {
  const shown = rows.slice(0, MAX_CSV_ROWS);
  const width = Math.max(...shown.map((r) => r.length), 0);
  return (
    <div className="h-full overflow-auto p-4">
      <p className="mb-3 text-xs text-muted">
        Raw cells as they are in the file, including any lines above the table.
        {rows.length > MAX_CSV_ROWS && ` Showing the first ${MAX_CSV_ROWS} of ${rows.length} rows.`}
      </p>
      <table className="border-collapse font-mono text-xs">
        <tbody>
          {shown.map((row, i) => (
            <tr key={i}>
              <th scope="row" className="sticky left-0 border border-hairline bg-surface px-2 py-1 text-right font-normal text-muted">
                {i + 1}
              </th>
              {Array.from({ length: width }, (_, j) => (
                <td key={j} className="whitespace-pre border border-hairline bg-surface px-2 py-1">
                  {row[j] ?? ""}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
