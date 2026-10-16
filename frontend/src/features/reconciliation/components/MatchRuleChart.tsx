"use client";

import { useMemo, useRef } from "react";
import { Chart as ChartJS, CategoryScale, LinearScale, BarElement, Tooltip } from "chart.js";
import { Bar } from "react-chartjs-2";
import { BarChart3 } from "lucide-react";
import { ChartCard } from "@/components/ui/ChartCard";
import type { DerivedStatistics } from "../computeStatistics";

ChartJS.register(CategoryScale, LinearScale, BarElement, Tooltip);

// Fixed label set so the axis stays stable across reconciliations,
// even when a rule has zero matches for a given run — same rationale
// as the original chart.
const RULE_LABELS: Record<string, string> = {
  REFERENCE: "Reference",
  REFERENCE_DATE_TOLERANCE: "Reference (Date Tolerance)",
  AMOUNT_DATE: "Amount / Date",
};

// Stable module-level key list — the axis keys never change, so the
// memo below can key on `stats.ruleCounts` alone without pulling the
// per-render array literals into its dependency semantics.
const RULE_KEYS = Object.keys(RULE_LABELS);

export function MatchRuleChart({ stats }: { stats: DerivedStatistics }) {
  const chartRef = useRef<ChartJS<"bar">>(null);
  const ruleKeys = RULE_KEYS;
  const values = ruleKeys.map((key) => stats.ruleCounts[key as keyof typeof stats.ruleCounts] ?? 0);
  const total = values.reduce((a, b) => a + b, 0) || 1;
  const { ruleCounts } = stats;

  // Same memo intent as before — recompute the chart payload only when
  // the underlying counts change — but with a dependency list the hook
  // linter can statically verify (previously `[values.join(",")]`).
  const data = useMemo(
    () => ({
      labels: RULE_KEYS.map((key) => RULE_LABELS[key]),
      datasets: [
        {
          label: "Matched Transactions",
          data: RULE_KEYS.map((key) => ruleCounts[key as keyof typeof ruleCounts] ?? 0),
          backgroundColor: "#0d9488",
          hoverBackgroundColor: "#0f766e",
          borderRadius: 8,
          borderSkipped: false,
          maxBarThickness: 56,
        },
      ],
    }),
    [ruleCounts]
  );

  return (
    <ChartCard icon={BarChart3} title="Match Rules" subtitle="Which rule reconciled each transaction">
      <div className="relative h-56">
        <Bar
          ref={chartRef}
          data={data}
          options={{
            responsive: true,
            maintainAspectRatio: false,
            animation: { duration: 600, easing: "easeOutQuart" },
            plugins: {
              legend: { display: false },
              tooltip: {
                backgroundColor: "#2b241f",
                padding: 10,
                cornerRadius: 8,
                callbacks: {
                  label: (ctx) => {
                    const v = ctx.parsed.y as number;
                    const pct = Math.round((v / total) * 100);
                    return `${v.toLocaleString()} matches (${pct}%)`;
                  },
                },
              },
            },
            scales: {
              x: {
                grid: { display: false },
                ticks: { font: { size: 10.5 }, color: "#8a7c67" },
              },
              y: {
                beginAtZero: true,
                ticks: { precision: 0, font: { size: 10.5 }, color: "#8a7c67" },
                grid: { color: "#e6dcc6" },
              },
            },
          }}
        />
      </div>
      <div className="mt-3 grid grid-cols-3 gap-2">
        {ruleKeys.map((key, i) => (
          <div key={key} className="rounded-lg bg-row-alt px-2 py-1.5 text-center">
            <p className="tabular-data text-sm font-bold text-text-primary">{values[i]}</p>
            <p className="mt-0.5 text-[9.5px] font-medium leading-tight text-text-secondary">
              {RULE_LABELS[key]}
            </p>
          </div>
        ))}
      </div>
    </ChartCard>
  );
}
