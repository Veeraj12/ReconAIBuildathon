"use client";

import { usePagination } from "@/hooks/usePagination";
import { Pager } from "@/components/ui/Pager";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { EmptyState } from "@/components/ui/EmptyState";
import { ArrowLeftRight } from "lucide-react";
import type { ExceptionPair } from "../types";

function TypePill({ type }: { type?: string | null }) {
  if (!type) return <span className="text-text-secondary">-</span>;
  return (
    <StatusBadge tone={type === "Debit" ? "danger" : "success"}>{type}</StatusBadge>
  );
}

export function TypeMismatchTable({ pairs }: { pairs: ExceptionPair[] }) {
  const { pageRows, rangeLabel, canGoPrev, canGoNext, goToPrevPage, goToNextPage } =
    usePagination(pairs);

  if (pairs.length === 0) {
    return (
      <EmptyState
        icon={ArrowLeftRight}
        title="No type mismatches"
        description="Debit/credit classification agrees on both sides for every matched reference."
        tone="success"
      />
    );
  }

  return (
    <div className="overflow-hidden rounded-lg border border-border-app">
      <table className="w-full text-xs">
        <thead>
          <tr className="sticky top-0 z-10 border-b border-border-app bg-row-alt text-left uppercase tracking-wide text-text-secondary">
            <th className="px-3 py-2.5 font-bold">Date</th>
            <th className="px-3 py-2.5 font-bold">Bank Row</th>
            <th className="px-3 py-2.5 font-bold">Tally Row</th>
            <th className="px-3 py-2.5 font-bold">Reference</th>
            <th className="px-3 py-2.5 font-bold">Amount</th>
            <th className="px-3 py-2.5 font-bold">Bank Type</th>
            <th className="px-3 py-2.5 font-bold">Tally Type</th>
          </tr>
        </thead>
        <tbody>
          {pageRows.map((pair, i) => (
            <tr
              key={i}
              className="border-b border-border-app/60 odd:bg-transparent even:bg-row-alt/50 transition-colors duration-100 hover:bg-row-hover"
            >
              <td className="px-3 py-2 tabular-data text-text-secondary">{pair.bankTransaction.date}</td>
              <td className="px-3 py-2 tabular-data text-text-secondary">{pair.bankTransaction.excelRow}</td>
              <td className="px-3 py-2 tabular-data text-text-secondary">{pair.tallyTransaction.excelRow}</td>
              <td className="px-3 py-2 font-medium text-text-primary">{pair.bankTransaction.reference}</td>
              <td className="px-3 py-2 tabular-data text-text-primary">{pair.bankTransaction.amount}</td>
              <td className="px-3 py-2"><TypePill type={pair.bankTransaction.transactionType} /></td>
              <td className="px-3 py-2"><TypePill type={pair.tallyTransaction.transactionType} /></td>
            </tr>
          ))}
        </tbody>
      </table>
      <Pager
        rangeLabel={rangeLabel}
        canGoPrev={canGoPrev}
        canGoNext={canGoNext}
        onPrev={goToPrevPage}
        onNext={goToNextPage}
      />
    </div>
  );
}
