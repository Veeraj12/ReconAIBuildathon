"use client";

import { useState } from "react";
import { ChevronDown, CheckCircle2, AlertTriangle } from "lucide-react";
import type { CrossSourceBucket, SampleTransaction } from "../types";

function BucketRow({
  bucket,
  samples,
  totalPrimary,
}: {
  bucket: CrossSourceBucket;
  samples: SampleTransaction[];
  totalPrimary: number;
}) {
  const [expanded, setExpanded] = useState(false);
  const isClean = bucket.problemSources.length === 0;
  const pct = totalPrimary ? Math.round((bucket.count / totalPrimary) * 100) : 0;

  return (
    <div className="overflow-hidden rounded-lg border border-border-app">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left transition-colors duration-150 hover:bg-row-hover"
      >
        <div className="flex items-center gap-2.5">
          {isClean ? (
            <CheckCircle2 className="h-4 w-4 shrink-0 text-success" />
          ) : (
            <AlertTriangle className="h-4 w-4 shrink-0 text-warning" />
          )}
          <span className="text-xs font-semibold text-text-primary">{bucket.label}</span>
        </div>
        <div className="flex items-center gap-3">
          <span className="tabular-data text-xs font-bold text-text-primary">
            {bucket.count.toLocaleString()}
            <span className="ml-1 font-normal text-text-secondary">({pct}%)</span>
          </span>
          {samples.length > 0 && (
            <ChevronDown
              className={`h-3.5 w-3.5 text-text-secondary transition-transform duration-200 ${
                expanded ? "rotate-180" : ""
              }`}
            />
          )}
        </div>
      </button>

      {expanded && samples.length > 0 && (
        <div className="border-t border-border-app">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-border-app bg-row-alt text-left uppercase tracking-wide text-text-secondary">
                <th className="px-3 py-2 font-bold">Row</th>
                <th className="px-3 py-2 font-bold">Date</th>
                <th className="px-3 py-2 font-bold">Reference</th>
                <th className="px-3 py-2 font-bold">Amount</th>
                <th className="px-3 py-2 font-bold">Type</th>
              </tr>
            </thead>
            <tbody>
              {samples.map((s, i) => (
                <tr
                  key={`${s.excelRow}-${i}`}
                  className="border-b border-border-app/60 odd:bg-transparent even:bg-row-alt/50"
                >
                  <td className="px-3 py-2 tabular-data text-text-secondary">{s.excelRow}</td>
                  <td className="px-3 py-2 tabular-data text-text-secondary">{s.date ?? "—"}</td>
                  <td className="px-3 py-2 font-medium text-text-primary">{s.reference ?? "—"}</td>
                  <td className="px-3 py-2 tabular-data text-text-primary">{s.amount ?? "—"}</td>
                  <td className="px-3 py-2 text-text-secondary">{s.transactionType ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {bucket.count > samples.length && (
            <p className="px-3 py-2 text-[10px] text-text-secondary">
              Showing {samples.length} of {bucket.count.toLocaleString()} — the full set is available in each
              source&apos;s individual reconciliation below.
            </p>
          )}
        </div>
      )}
    </div>
  );
}

export function CrossSourceBreakdownList({
  breakdown,
  samples,
  totalPrimary,
}: {
  breakdown: CrossSourceBucket[];
  samples: Record<string, SampleTransaction[]>;
  totalPrimary: number;
}) {
  return (
    <div className="space-y-2">
      <h3 className="font-display text-sm font-semibold text-text-primary">Cross-Source Breakdown</h3>
      <p className="text-xs text-text-secondary">
        Every primary transaction, grouped by exactly which secondary source(s) it&apos;s missing or
        mismatched in.
      </p>
      <div className="space-y-2 pt-1">
        {breakdown.map((bucket) => (
          <BucketRow
            key={bucket.label}
            bucket={bucket}
            samples={samples[bucket.label] ?? []}
            totalPrimary={totalPrimary}
          />
        ))}
      </div>
    </div>
  );
}
