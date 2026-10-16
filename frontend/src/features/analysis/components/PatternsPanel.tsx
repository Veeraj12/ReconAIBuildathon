"use client";

import { useMemo, useState } from "react";
import { Sparkles, Layers, TrendingUp, CircleOff, Loader2, RotateCw, Search } from "lucide-react";
import { MetricCard } from "@/components/ui/MetricCard";
import { EmptyState } from "@/components/ui/EmptyState";
import { useReconciliationContext } from "@/features/reconciliation/ReconciliationProvider";
import { useAuthContext } from "@/features/auth/AuthProvider";
import { useAnalysisContext } from "../AnalysisProvider";
import { buildExceptionResolver } from "../resolveExceptions";
import type { ResolvedEntry } from "../resolveExceptions";
import { PatternCard } from "./PatternCard";
import type { Pattern } from "../types";

type SortKey = "combined_score" | "frequency_score" | "impact_score";

const SORT_OPTIONS: { key: SortKey; label: string }[] = [
  { key: "combined_score", label: "Overall priority" },
  { key: "frequency_score", label: "Most frequent" },
  { key: "impact_score", label: "Highest impact" },
];

export function PatternsPanel() {
  const { data } = useReconciliationContext();
  const { user } = useAuthContext();
  const {
    result, isAnalyzing, analyzeError, hasAnalyzed, analyze, explanations, explainPattern,
    causeFeedback, submitCauseFeedback,
  } = useAnalysisContext();
  const canSubmitFeedback = user?.role === "accountant" || user?.role === "admin";
  const [sortKey, setSortKey] = useState<SortKey>("combined_score");

  const totalAvailable = data
    ? data.exceptions.amountMismatches.length +
      data.exceptions.typeMismatches.length +
      data.exceptions.groupedMatches.length +
      data.missingInBank.length +
      data.missingInTally.length +
      data.duplicates.bank.length +
      data.duplicates.tally.length +
      data.signReversalHints.length
    : 0;

  const resolveEntries = useMemo(() => {
    if (!data) return (_pattern: Pattern) => [] as ResolvedEntry[];
    return buildExceptionResolver(data).resolve;
  }, [data]);

  const sortedPatterns: Pattern[] = useMemo(() => {
    if (!result) return [];
    return [...result.patterns].sort((a, b) => b[sortKey] - a[sortKey]);
  }, [result, sortKey]);

  if (!data) return null;

  if (totalAvailable === 0) {
    return (
      <EmptyState
        icon={Sparkles}
        title="Nothing to analyze"
        description="This reconciliation run has no exceptions — every transaction matched cleanly, so there are no patterns to discover."
        tone="success"
      />
    );
  }

  if (!hasAnalyzed) {
    return (
      <div className="animate-fade-in-up flex flex-col items-center justify-center gap-4 rounded-xl border border-dashed border-border-app bg-bg-panel px-6 py-14 text-center">
        <span className="flex h-14 w-14 items-center justify-center rounded-full bg-accent-soft text-accent-dark">
          <Search className="h-6 w-6" strokeWidth={1.75} />
        </span>
        <div>
          <p className="text-[15px] font-semibold text-text-primary">
            {totalAvailable.toLocaleString()} exceptions ready to analyze
          </p>
          <p className="mx-auto mt-1.5 max-w-sm text-xs leading-relaxed text-text-secondary">
            Discover recurring patterns — fixed-amount differences, posting delays, duplicates, and
            more — grouped together with financial impact and, if you want it, an AI explanation for
            each one.
          </p>
        </div>
        <button
          type="button"
          onClick={analyze}
          disabled={isAnalyzing}
          className="flex items-center gap-2 rounded-lg bg-accent px-5 py-2.5 text-sm font-semibold text-white shadow-sm transition-all duration-150 hover:bg-accent-dark active:scale-95 disabled:cursor-not-allowed disabled:opacity-60"
        >
          {isAnalyzing ? (
            <>
              <Loader2 className="h-4 w-4 animate-spin-slow" />
              Analyzing exceptions…
            </>
          ) : (
            <>
              <Sparkles className="h-4 w-4" />
              Analyze Exceptions
            </>
          )}
        </button>
        {analyzeError && <p className="text-xs font-medium text-danger">{analyzeError}</p>}
      </div>
    );
  }

  return (
    <div className="animate-stagger space-y-4">
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        <MetricCard icon={Layers} label="Exceptions Analyzed" value={result!.totalExceptions.toLocaleString()} tone="neutral" />
        <MetricCard icon={TrendingUp} label="Patterns Found" value={result!.patternsFound.toLocaleString()} tone="accent" />
        <MetricCard icon={CircleOff} label="Unclassified Outliers" value={result!.outliers.toLocaleString()} tone={result!.outliers > 0 ? "danger" : "success"} />
      </div>

      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="inline-flex gap-1 rounded-lg border border-border-app bg-row-alt p-1">
          {SORT_OPTIONS.map((opt) => (
            <button
              key={opt.key}
              type="button"
              onClick={() => setSortKey(opt.key)}
              className={`rounded-md px-3 py-1.5 text-xs font-semibold transition-all duration-150 ${
                sortKey === opt.key
                  ? "bg-accent text-white shadow-sm"
                  : "text-text-secondary hover:text-text-primary"
              }`}
            >
              {opt.label}
            </button>
          ))}
        </div>

        <button
          type="button"
          onClick={analyze}
          disabled={isAnalyzing}
          className="flex items-center gap-1.5 rounded-md border border-border-app px-3 py-1.5 text-xs font-medium text-text-secondary transition-all duration-150 hover:border-accent hover:text-accent-dark active:scale-95 disabled:opacity-50"
        >
          <RotateCw className={`h-3.5 w-3.5 ${isAnalyzing ? "animate-spin-slow" : ""}`} />
          Re-analyze
        </button>
      </div>

      {sortedPatterns.length === 0 ? (
        <EmptyState
          icon={CircleOff}
          title="No recurring patterns found"
          description="Every exception was too dissimilar from the others to form a meaningful pattern — see the outlier count above."
        />
      ) : (
        <div className="space-y-3">
          {sortedPatterns.map((pattern, i) => (
            <PatternCard
              key={pattern.pattern_id}
              pattern={pattern}
              rank={i + 1}
              explanationState={explanations[pattern.pattern_id] ?? { status: "idle" }}
              onExplain={() => explainPattern(pattern)}
              onRegenerate={() => explainPattern(pattern, { regenerate: true })}
              resolveEntries={resolveEntries}
              canSubmitFeedback={canSubmitFeedback}
              causeFeedback={causeFeedback}
              onSubmitCauseFeedback={(causeId, confirmed) =>
                submitCauseFeedback(pattern.pattern_id, causeId, confirmed)
              }
            />
          ))}
        </div>
      )}

      {result!.outliers > 0 && (
        <p className="rounded-lg border border-dashed border-border-app px-3.5 py-2.5 text-center text-[11px] text-text-secondary">
          {result!.outliers.toLocaleString()} exception{result!.outliers === 1 ? "" : "s"} didn&apos;t fit a
          recurring pattern closely enough to be grouped — shown here as unclassified rather than
          forced into a misleading pattern.
        </p>
      )}
    </div>
  );
}
