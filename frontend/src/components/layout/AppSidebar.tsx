"use client";

import * as Tabs from "@radix-ui/react-tabs";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useLocalStorage } from "@/hooks/useLocalStorage";
import { useAuthContext } from "@/features/auth/AuthProvider";
import { useReconciliationContext } from "@/features/reconciliation/ReconciliationProvider";
import { useAnalysisContext } from "@/features/analysis/AnalysisProvider";
import { UploadForm } from "@/features/reconciliation/components/UploadForm";
import {
  CheckCircle2,
  Database,
  FileSearch,
  TriangleAlert,
  Copy,
  Trash2,
  Lightbulb,
  LogOut,
  ShieldCheck,
  PanelLeftClose,
  PanelLeftOpen,
  UploadCloud,
  Plus,
  Sparkles,
  Network,
  Download
} from "lucide-react";

// Premium Semantic Tab Configuration
const RESULT_TABS = [
  { 
    value: "matched", 
    label: "Matched", 
    icon: CheckCircle2, 
    color: "text-emerald-400", 
    indicator: "bg-emerald-500",
    badgeBg: "bg-emerald-500/15",
    badgeText: "text-emerald-400"
  },
  { 
    value: "missingBank", 
    label: "Missing In Bank", 
    icon: Database, 
    color: "text-sky-400", 
    indicator: "bg-sky-500",
    badgeBg: "bg-sky-500/15",
    badgeText: "text-sky-400"
  },
  { 
    value: "missingTally", 
    label: "Missing In Tally", 
    icon: FileSearch, 
    color: "text-indigo-400", 
    indicator: "bg-indigo-500",
    badgeBg: "bg-indigo-500/15",
    badgeText: "text-indigo-400"
  },
  { 
    value: "exceptions", 
    label: "Exceptions", 
    icon: TriangleAlert, 
    color: "text-amber-400", 
    indicator: "bg-amber-500",
    badgeBg: "bg-amber-500/15",
    badgeText: "text-amber-400"
  },
  { 
    value: "analysis", 
    label: "Pattern Analysis", 
    icon: Sparkles, 
    color: "text-teal-400", 
    indicator: "bg-teal-500",
    badgeBg: "bg-teal-500/15",
    badgeText: "text-teal-400"
  },
  { 
    value: "droppedRows", 
    label: "Dropped Rows", 
    icon: Trash2, 
    color: "text-rose-400", 
    indicator: "bg-rose-500",
    badgeBg: "bg-rose-500/15",
    badgeText: "text-rose-400"
  },
  { 
    value: "duplicates", 
    label: "Duplicates", 
    icon: Copy, 
    color: "text-purple-400", 
    indicator: "bg-purple-500",
    badgeBg: "bg-purple-500/15",
    badgeText: "text-purple-400"
  },
  { 
    value: "hints", 
    label: "Sign Reversal Hints", 
    icon: Lightbulb, 
    color: "text-blue-400", 
    indicator: "bg-blue-500",
    badgeBg: "bg-blue-500/15",
    badgeText: "text-blue-400" 
  },
  {
    value: "export",
    label: "Export",
    icon: Download,
    color: "text-neutral-300",
    indicator: "bg-neutral-400",
    badgeBg: "bg-neutral-500/15",
    badgeText: "text-neutral-300"
  },
];

export function AppSidebar() {
  const [collapsed, setCollapsed] = useLocalStorage("reconai:sidebarCollapsed", false);
  const { user, logout } = useAuthContext();
  const { data, isLoading, upload, reset } = useReconciliationContext();
  const { result: analysisResult } = useAnalysisContext();
  const pathname = usePathname();
  const isMultiSource = pathname?.startsWith("/multi-source") ?? false;

  const badgeCount: Record<string, number> = data
    ? {
        exceptions:
          (data.exceptions?.amountMismatches?.length ?? 0) +
          (data.exceptions?.typeMismatches?.length ?? 0) +
          (data.exceptions?.groupedMatches?.length ?? 0),
        analysis: analysisResult?.patternsFound ?? 0,
        droppedRows:
          (data.invalidBankRowData?.length ?? 0) + (data.invalidTallyRowData?.length ?? 0),
        duplicates: (data.duplicates?.bank?.length ?? 0) + (data.duplicates?.tally?.length ?? 0),
        hints: data.signReversalHints?.length ?? 0,
      }
    : {};

  return (
    <aside
      className={`relative flex flex-col h-screen overflow-x-hidden overflow-y-auto bg-[#0E0E11] border-r border-white/[0.04] text-neutral-200 transition-all duration-300 ease-[cubic-bezier(0.16,1,0.3,1)] ${
        collapsed ? "w-[72px]" : "w-[280px]"
      }`}
    >
      {/* 1. HEADER - Minimal & Premium */}
      <div className={`flex items-center p-4 mb-2 shrink-0 ${collapsed ? "justify-center" : "justify-between"}`}>
        {!collapsed ? (
          <div className="flex items-center gap-3 overflow-hidden">
            <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-gradient-to-b from-[#14B8A6] to-[#0D9488] shadow-[0_0_12px_rgba(20,184,166,0.25)] text-sm font-bold text-white ring-1 ring-white/10">
              R
            </div>
            <div className="flex flex-col leading-tight">
              <span className="text-[13px] font-semibold tracking-tight text-white">ReconAI</span>
              <span className="text-[10px] font-medium tracking-widest text-neutral-500 uppercase">Workspace</span>
            </div>
          </div>
        ) : (
          <button
            onClick={() => setCollapsed(false)}
            title="Expand sidebar"
            className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-gradient-to-b from-[#14B8A6] to-[#0D9488] shadow-sm text-lg font-bold text-white transition-all duration-200 hover:scale-105 active:scale-95"
          >
            R
          </button>
        )}

        {!collapsed && (
          <button
            onClick={() => setCollapsed(true)}
            title="Collapse sidebar"
            className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-neutral-500 transition-all duration-200 hover:bg-white/[0.06] hover:text-neutral-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-500/50 active:scale-95"
          >
            <PanelLeftClose size={16} />
          </button>
        )}
      </div>

      {/* 1.5 MODE SWITCHER — Bank/Tally vs Multi-Source */}
      <div className="px-3 mb-5">
        {!collapsed && (
          <div className="mb-2 px-1">
            <span className="text-[10px] font-semibold tracking-widest text-neutral-500 uppercase">Mode</span>
          </div>
        )}
        <div className={`flex gap-1 rounded-lg bg-white/[0.03] p-1 ${collapsed ? "flex-col" : ""}`}>
          <Link
            href="/reconcile"
            title="Bank ↔ Tally reconciliation"
            className={`flex flex-1 items-center justify-center gap-1.5 rounded-md px-2 py-1.5 text-[11px] font-semibold transition-all duration-150 ${
              !isMultiSource ? "bg-teal-500/15 text-teal-300" : "text-neutral-500 hover:text-neutral-300"
            }`}
          >
            <FileSearch size={13} />
            {!collapsed && "Two-Source"}
          </Link>
          <Link
            href="/multi-source"
            title="Multi-source reconciliation"
            className={`flex flex-1 items-center justify-center gap-1.5 rounded-md px-2 py-1.5 text-[11px] font-semibold transition-all duration-150 ${
              isMultiSource ? "bg-teal-500/15 text-teal-300" : "text-neutral-500 hover:text-neutral-300"
            }`}
          >
            <Network size={13} />
            {!collapsed && "Multi-Source"}
          </Link>
        </div>
      </div>

      {/* 2. UPLOAD ACTION CARD */}
      {!isMultiSource && (
      <div className="px-3 mb-6">
        {!collapsed && (
          <div className="mb-2 px-1">
            <span className="text-[10px] font-semibold tracking-widest text-neutral-500 uppercase">Action</span>
          </div>
        )}

        {!collapsed && !data && (
          <div className="p-3.5 rounded-xl bg-white/[0.02] border border-white/[0.04] shadow-sm">
            <div className="flex items-center gap-2 mb-1.5">
              <UploadCloud size={16} className="text-teal-400 shrink-0" />
              <span className="text-xs font-medium text-neutral-200">Import Data</span>
            </div>
            {user?.role === "viewer" ? (
              <div className="flex items-start gap-2 rounded-lg bg-amber-500/10 border border-amber-500/20 px-2.5 py-2">
                <TriangleAlert size={14} className="text-amber-400 shrink-0 mt-0.5" />
                <p className="text-[10px] text-amber-300/90 leading-relaxed">
                  Your account has viewer access — uploading and reconciling requires an accountant
                  or admin role. Ask an admin to update your role if you need to run reconciliations.
                </p>
              </div>
            ) : (
              <>
                <p className="text-[10px] text-neutral-500 mb-3 leading-relaxed">
                  Upload Bank and Tally extracts to begin AI reconciliation.
                </p>
                <UploadForm onSubmit={upload} isLoading={isLoading} />
              </>
            )}
          </div>
        )}

        {!collapsed && data && (
          <button
            type="button"
            onClick={reset}
            className="group flex w-full items-center justify-center gap-2 rounded-lg bg-teal-500/10 border border-teal-500/20 px-4 py-2.5 text-xs font-medium text-teal-400 transition-all duration-200 hover:bg-teal-500/20 hover:text-teal-300 active:scale-[0.98] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal-500"
          >
            <Plus size={14} className="transition-transform duration-200 group-hover:rotate-90" />
            New Reconciliation
          </button>
        )}
      </div>
      )}

      {/* 3. RESULT NAVIGATION (RADIX TABS) */}
      {!isMultiSource && data && (
        <div className="px-3 mb-6 flex-1">
          {!collapsed && (
            <div className="mb-2 px-1">
              <span className="text-[10px] font-semibold tracking-widest text-neutral-500 uppercase">Results</span>
            </div>
          )}

          <Tabs.List className="flex flex-col gap-1 outline-none">
            {RESULT_TABS.map((tab) => (
              <Tabs.Trigger
                key={tab.value}
                value={tab.value}
                title={collapsed ? tab.label : undefined}
                className={`group relative flex items-center gap-3 w-full rounded-lg px-2.5 py-2 text-sm font-medium text-neutral-400 transition-all duration-150 ease-out hover:bg-white/[0.04] hover:text-neutral-200 data-[state=active]:bg-white/[0.06] data-[state=active]:text-white outline-none focus-visible:ring-2 focus-visible:ring-teal-500/50 ${
                  collapsed ? "justify-center py-3" : ""
                }`}
              >
                {/* Active Left Indicator */}
                <span 
                  className={`absolute left-0 top-[20%] bottom-[20%] w-[3px] rounded-r-full scale-y-0 opacity-0 transition-all duration-200 ease-out group-data-[state=active]:scale-y-100 group-data-[state=active]:opacity-100 ${tab.indicator}`} 
                />

                <tab.icon 
                  size={16} 
                  className={`shrink-0 transition-colors duration-200 group-data-[state=active]:${tab.color.split('text-')[1] ? tab.color : 'text-white'}`} 
                />
                
                {!collapsed && (
                  <span className="truncate">{tab.label}</span>
                )}

                {/* Notification Badge */}
                {!collapsed && badgeCount[tab.value] > 0 && (
                  <span className={`ml-auto flex items-center justify-center min-w-[20px] h-5 px-1.5 rounded-full text-[10px] font-bold transition-transform duration-200 group-hover:scale-105 ${tab.badgeBg} ${tab.badgeText}`}>
                    {badgeCount[tab.value]}
                  </span>
                )}
                
                {/* Collapsed Badge Dot */}
                {collapsed && badgeCount[tab.value] > 0 && (
                  <span className={`absolute top-1.5 right-1.5 h-2 w-2 rounded-full ring-2 ring-[#0E0E11] ${tab.indicator}`} />
                )}
              </Tabs.Trigger>
            ))}
          </Tabs.List>
        </div>
      )}

      {/* 4. FOOTER */}
      <div className="mt-auto px-3 pb-4 flex flex-col gap-3">
        {/* User Profile Card */}
        {user && (
          <div 
            className={`group flex items-center gap-3 p-2 rounded-xl transition-all duration-200 hover:bg-white/[0.04] ${collapsed ? "justify-center" : ""}`}
            title={collapsed ? `Logged in as ${user.username}` : undefined}
          >
            <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-gradient-to-tr from-neutral-800 to-neutral-700 text-xs font-semibold text-neutral-200 ring-1 ring-white/10 shadow-sm">
              {user.username.charAt(0).toUpperCase()}
            </div>
            
            {!collapsed && (
              <div className="flex flex-col flex-1 overflow-hidden">
                <span className="text-[13px] font-medium text-neutral-200 truncate">{user.username}</span>
                <span className="text-[10px] font-medium tracking-wide text-neutral-500 uppercase">
                  {user.role}
                </span>
              </div>
            )}

            {!collapsed && (
              <button
                onClick={logout}
                title="Log out"
                className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-neutral-500 opacity-0 transition-all duration-200 group-hover:opacity-100 hover:bg-white/[0.06] hover:text-rose-400 focus-visible:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-rose-500/50 active:scale-95"
              >
                <LogOut size={14} className="transition-transform duration-200 group-hover:-translate-x-0.5" />
              </button>
            )}
          </div>
        )}
      </div>
    </aside>
  );
}