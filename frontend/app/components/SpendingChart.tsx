"use client";

import { useMemo, useSyncExternalStore } from "react";
import { Bar, BarChart, LabelList, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { inr } from "@/lib/format";
import type { Analytics, Category } from "@/lib/types";

// SVG attributes can't take var(), so read the colour roles from CSS, and read
// them again whenever the OS switches between light and dark.
const DARK = "(prefers-color-scheme: dark)";

function subscribeToScheme(onChange: () => void) {
  const media = window.matchMedia(DARK);
  media.addEventListener("change", onChange);
  return () => media.removeEventListener("change", onChange);
}

function readColors() {
  const css = getComputedStyle(document.documentElement);
  const get = (name: string) => css.getPropertyValue(name).trim();
  return { bar: get("--series-1"), ink2: get("--ink-2"), muted: get("--muted"), wash: get("--wash") };
}

function useColors() {
  // null while rendering on the server, where there's no CSS to read.
  const dark = useSyncExternalStore(subscribeToScheme, () => window.matchMedia(DARK).matches, () => null);
  return useMemo(() => (dark === null ? null : readColors()), [dark]);
}

type Row = { category: Category; amount: number; share: number };

function ChartTooltip({ active, payload }: { active?: boolean; payload?: { payload: Row }[] }) {
  if (!active || !payload?.length) return null;
  const row = payload[0].payload;
  return (
    <div className="rounded-lg border border-border bg-surface px-3 py-2 text-sm shadow-sm">
      <div className="font-medium">{row.category}</div>
      <div className="text-ink-2">
        {inr(row.amount)} · {row.share}% of spending
      </div>
    </div>
  );
}

export default function SpendingChart({ analytics }: { analytics: Analytics }) {
  const colors = useColors();
  const total = analytics.total_spending;
  const rows: Row[] = Object.entries(analytics.spending_by_category)
    .map(([category, amount]) => ({
      category: category as Category,
      amount: amount ?? 0,
      share: total ? Math.round(((amount ?? 0) / total) * 100) : 0,
    }))
    .sort((a, b) => b.amount - a.amount);

  if (!rows.length) return <p className="text-sm text-ink-2">No spending in this statement.</p>;

  return (
    <figure>
      <div style={{ height: rows.length * 36 + 8 }}>
        {colors && (
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={rows} layout="vertical" margin={{ top: 0, right: 96, bottom: 0, left: 0 }} barCategoryGap={8}>
              <XAxis type="number" hide domain={[0, "dataMax"]} />
              <YAxis
                type="category"
                dataKey="category"
                width={84}
                tickLine={false}
                axisLine={false}
                tick={{ fill: colors.ink2, fontSize: 13 }}
              />
              <Tooltip cursor={{ fill: colors.wash }} content={<ChartTooltip />} />
              <Bar dataKey="amount" fill={colors.bar} radius={[0, 4, 4, 0]} barSize={20} isAnimationActive={false}>
                <LabelList
                  dataKey="amount"
                  position="right"
                  formatter={(value) => inr(Number(value))}
                  style={{ fill: colors.ink2, fontSize: 12, fontVariantNumeric: "tabular-nums" }}
                />
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        )}
      </div>
      <figcaption className="mt-3 text-xs text-muted">
        Debits only. Transfers (to people, your own accounts, card bills) aren&apos;t counted as spending; refunds are
        money in and don&apos;t reduce it.
      </figcaption>
    </figure>
  );
}
