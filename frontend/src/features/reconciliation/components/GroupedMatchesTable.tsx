"use client";

import { Layers } from "lucide-react";
import { usePagination } from "@/hooks/usePagination";
import { Pager } from "@/components/ui/Pager";
import { EmptyState } from "@/components/ui/EmptyState";
import type { GroupedMatch } from "../types";

function GroupCard({ group }: { group: GroupedMatch }) {
  const isTallyMany = group.direction === "TALLY_MANY_TO_BANK_ONE";
  const oneLabel = isTallyMany ? "Bank" : "Tally";
  const manyLabel = isTallyMany ? "Tally" : "Bank";

  return (
    <div className="rounded-lg border border-border-app bg-bg-content/40 p-4 transition-all duration-180 hover:-translate-y-0.5 hover:shadow-md">
      <div className="mb-3 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="flex h-7 w-7 items-center justify-center rounded-md bg-purple-soft text-purple">
            <Layers className="h-3.5 w-3.5" />
          </span>
          <span className="text-[11px] font-semibold text-text-secondary">
            {group.date} · {group.manyTransactions.length} {manyLabel} entries → 1 {oneLabel} entry
          </span>
        </div>
        <span className="tabular-data rounded-md bg-purple-soft px-2.5 py-1 text-[11px] font-bold text-purple">
          ₹{group.amount.toLocaleString()}
        </span>
      </div>

      <div className="mb-2 flex items-center justify-between rounded-md border border-border-app bg-bg-panel px-3 py-2 text-xs">
        <span className="font-semibold text-text-primary">
          {oneLabel}: {group.oneTransaction.reference}
        </span>
        <span className="tabular-data text-text-secondary">{group.oneTransaction.amount}</span>
      </div>

      <div className="space-y-1">
        {group.manyTransactions.map((t, i) => (
          <div
            key={i}
            className="flex items-center justify-between border-l-2 border-purple/40 pl-3 text-xs text-text-secondary"
          >
            <span>{manyLabel}: {t.reference}</span>
            <span className="tabular-data">{t.amount}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

export function GroupedMatchesTable({ groups }: { groups: GroupedMatch[] }) {
  const { pageRows, rangeLabel, canGoPrev, canGoNext, goToPrevPage, goToNextPage } =
    usePagination(groups);

  return (
    <div className="overflow-hidden rounded-lg border border-border-app">
      <div className="space-y-2.5 p-2.5">
        {pageRows.length === 0 && (
          <EmptyState
            icon={Layers}
            title="No grouped matches found"
            description="No set of entries on one side sums to a single entry on the other."
            tone="success"
          />
        )}
        {pageRows.map((group, i) => (
          <GroupCard key={i} group={group} />
        ))}
      </div>
      {pageRows.length > 0 && (
        <Pager
          rangeLabel={rangeLabel}
          canGoPrev={canGoPrev}
          canGoNext={canGoNext}
          onPrev={goToPrevPage}
          onNext={goToNextPage}
        />
      )}
    </div>
  );
}
