"use client";

import type { ModelOption } from "@/lib/types";

// Which AI model categorizes what the rules can't, and writes the summary.
export default function ModelPicker({
  models,
  value,
  onChange,
}: {
  models: ModelOption[];
  value: string | null;
  onChange: (key: string) => void;
}) {
  if (models.length < 2 || value === null) return null;
  const current = models.find((m) => m.key === value);
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor="ai-model" className="text-sm font-medium">
        AI model
      </label>
      <select
        id="ai-model"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        aria-describedby="ai-model-note"
        className="h-11 min-w-0 rounded-lg border border-border bg-surface px-3 text-sm"
      >
        {models.map((m) => (
          <option key={m.key} value={m.key} disabled={!m.available}>
            {m.available ? m.label : `${m.label} (no API key)`}
          </option>
        ))}
      </select>
      <p id="ai-model-note" className="text-xs text-muted">
        {current?.note}. Used for categories the rules can&apos;t place, and for the summary; totals never depend on it.
      </p>
    </div>
  );
}
