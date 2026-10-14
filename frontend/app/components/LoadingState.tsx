"use client";

import { useEffect, useState } from "react";
import { Spinner } from "./icons";

const STEPS = [
  "Reading the file",
  "Categorizing transactions (rules, then AI)",
  "Looking for unusual transactions",
  "Writing the summary",
];

export default function LoadingState({ fileName, onCancel }: { fileName: string; onCancel: () => void }) {
  const [seconds, setSeconds] = useState(0);
  useEffect(() => {
    const started = Date.now();
    const timer = setInterval(() => setSeconds(Math.floor((Date.now() - started) / 1000)), 500);
    return () => clearInterval(timer);
  }, []);

  return (
    <div role="status" aria-live="polite" className="flex flex-col items-center gap-5 py-10 text-center">
      <Spinner className="size-10 text-series-1" />
      <div>
        <p className="text-lg font-medium">Analyzing {fileName}…</p>
        <p className="mt-1 text-sm text-ink-2">
          Usually 5–30 seconds; the AI steps take most of it. <span className="tabular-nums">{seconds}s</span> so far.
        </p>
      </div>
      <ul className="text-left text-sm text-ink-2">
        {STEPS.map((step) => (
          <li key={step} className="list-inside list-disc">
            {step}
          </li>
        ))}
      </ul>
      <button type="button" onClick={onCancel} className="text-sm text-ink-2 underline underline-offset-4 hover:text-ink">
        Cancel
      </button>
    </div>
  );
}
