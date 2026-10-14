"use client";

import { useRef, useState } from "react";
import { UploadIcon } from "./icons";

export const SAMPLES = [
  { name: "meridian_q1-2026.pdf", label: "Meridian Bank · PDF, 3 months" },
  { name: "sahyadri_feb-mar2026.pdf", label: "Sahyadri Gramin Bank · PDF, 2 months" },
  { name: "harbour_q1-2026.pdf", label: "Harbour Bank · PDF, HDFC-style (no lines between rows)" },
  { name: "konark_jan2026.csv", label: "Konark Co-op Bank · CSV with Dr/Cr column" },
  { name: "northwind_jan2026.csv", label: "Northwind Bank · CSV, no account details" },
  { name: "sahyadri_feb-mar2026_locked.pdf", label: "Sahyadri · password-protected PDF (password THOM0512)" },
];

const ACCEPT = ".csv,.pdf,text/csv,application/pdf";

function isSupported(file: File) {
  return /\.(csv|pdf)$/i.test(file.name);
}

export default function UploadPanel({
  onAnalyze,
  onSample,
  error,
  failureId,
}: {
  onAnalyze: (file: File) => void;
  onSample: (name: string) => void;
  error: string | null;
  failureId: number | null; // the failed upload was kept for review under this id
}) {
  const [file, setFile] = useState<File | null>(null);
  const [pickError, setPickError] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const [sample, setSample] = useState(SAMPLES[0].name);
  const input = useRef<HTMLInputElement>(null);

  function choose(picked: File | undefined) {
    if (!picked) return;
    if (!isSupported(picked)) {
      setFile(null);
      setPickError(`${picked.name} isn't a CSV or PDF file.`);
      return;
    }
    setPickError(null);
    setFile(picked);
  }

  const shownError = pickError ?? error;

  return (
    <div className="flex flex-col gap-6">
      <label
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          choose(e.dataTransfer.files[0]);
        }}
        className={`flex cursor-pointer flex-col items-center gap-3 rounded-xl border-2 border-dashed px-6 py-10 text-center transition-colors ${
          dragging ? "border-series-1 bg-wash" : "border-border hover:bg-wash"
        }`}
      >
        <UploadIcon className="size-8 text-muted" />
        {file ? (
          <span>
            <span className="font-medium">{file.name}</span>
            <span className="text-ink-2"> · {(file.size / 1024).toFixed(0)} KB</span>
          </span>
        ) : (
          <span>
            <span className="font-medium">Choose a statement</span>
            <span className="text-ink-2"> or drag it here</span>
          </span>
        )}
        <span className="text-sm text-muted">CSV or PDF, up to 10 MB</span>
        <input
          ref={input}
          type="file"
          accept={ACCEPT}
          className="sr-only"
          onChange={(e) => choose(e.target.files?.[0])}
        />
      </label>

      {shownError && (
        <div role="alert" className="rounded-lg border border-critical/40 bg-critical/5 px-4 py-3 text-sm">
          <p>{shownError}</p>
          {!pickError && failureId !== null && (
            <p className="mt-1 text-xs text-ink-2">
              The file and error were kept (failure #{failureId}) so the parser can be improved; see Failed uploads below.
            </p>
          )}
        </div>
      )}

      <button
        type="button"
        disabled={!file}
        onClick={() => file && onAnalyze(file)}
        className="h-11 rounded-lg bg-ink px-5 font-medium text-plane transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-30"
      >
        Analyze
      </button>

      <div className="flex items-center gap-3 text-sm text-muted">
        <span className="h-px flex-1 bg-hairline" />
        no statement to hand?
        <span className="h-px flex-1 bg-hairline" />
      </div>

      <div className="flex flex-col gap-2 sm:flex-row">
        <select
          aria-label="Sample statement"
          value={sample}
          onChange={(e) => setSample(e.target.value)}
          className="h-11 min-w-0 flex-1 rounded-lg border border-border bg-surface px-3 text-sm"
        >
          {SAMPLES.map((s) => (
            <option key={s.name} value={s.name}>
              {s.label}
            </option>
          ))}
        </select>
        <button
          type="button"
          onClick={() => onSample(sample)}
          className="h-11 whitespace-nowrap rounded-lg border border-border bg-surface px-5 font-medium transition-colors hover:bg-wash"
        >
          Try a sample statement
        </button>
      </div>
      <p className="text-xs text-muted">
        Samples are synthetic: invented banks, people and account numbers, with tricky rows on purpose.
      </p>
    </div>
  );
}
