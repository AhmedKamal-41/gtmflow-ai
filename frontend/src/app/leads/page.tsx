"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useRef, useState } from "react";

import { draftModelLabel } from "@/components/AIStatusProvider";
import { Button } from "@/components/Button";
import { ErrorMessage } from "@/components/ErrorMessage";
import { Icon } from "@/components/Icon";
import { LoadingState } from "@/components/LoadingState";
import { PageHeader } from "@/components/PageHeader";
import { RankingBadge } from "@/components/PriorityBadge";
import { RankingBanner } from "@/components/RankingBanner";
import { STAGE_LABELS, StageBadge } from "@/components/StageBadge";
import {
  APIError,
  createBatchJob,
  getInbox,
  type InboxItem,
  type InboxPage,
  type InboxPriority,
  type LeadStage,
} from "@/lib/api";

// Leads: every imported lead, ranked against the active seller profile's
// target customers (Hot first), filtered by workflow stage. Select ranked
// leads to draft outreach for them with that same seller profile.

const PAGE_SIZE = 50;
const TABS: (LeadStage | "all")[] = ["all", "to_review", "approved", "outdated", "needs_draft", "needs_score", "rejected", "sent"];
const PRIORITIES: InboxPriority[] = ["Hot", "Warm", "Cold", "needs_data"];
type Priority = InboxPriority;
const PRIORITY_LABELS: Record<InboxPriority, string> = { Hot: "Hot", Warm: "Warm", Cold: "Cold", needs_data: "Needs data" };
// One selection job per import may run at a time, of at most 25 leads.
const MAX_SELECTED = 25;

function selectable(item: InboxItem): boolean {
  return item.band !== null && !item.blocked;
}

function tabLabel(tab: LeadStage | "all"): string {
  return tab === "all" ? "All" : STAGE_LABELS[tab];
}

function LeadsInbox() {
  const router = useRouter();
  const params = useSearchParams();
  const stageParam = params?.get("stage");
  const stage = TABS.includes(stageParam as LeadStage) ? (stageParam as LeadStage) : "all";
  const priorityParam = params?.get("priority");
  const priority = PRIORITIES.includes(priorityParam as Priority) ? (priorityParam as Priority) : undefined;
  const [search, setSearch] = useState(params?.get("q") ?? "");
  const [query, setQuery] = useState(search);
  const [page, setPage] = useState<InboxPage | null>(null);
  const [items, setItems] = useState<InboxItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [drafting, setDrafting] = useState(false);
  const [draftMessage, setDraftMessage] = useState<string | null>(null);
  const [draftError, setDraftError] = useState<string | null>(null);
  const [bannerKey, setBannerKey] = useState(0);
  const generation = useRef(0);

  useEffect(() => {
    const timer = window.setTimeout(() => setQuery(search.trim()), 250);
    return () => window.clearTimeout(timer);
  }, [search]);

  const load = useCallback(async () => {
    const current = ++generation.current;
    setError(null);
    setPage(null);
    try {
      const result = await getInbox({ stage: stage === "all" ? undefined : stage, priority, q: query || undefined, limit: PAGE_SIZE });
      if (current !== generation.current) return;
      setPage(result);
      setItems(result.items);
      setSelected(new Set());
    } catch (e) {
      if (current !== generation.current) return;
      setError(e instanceof APIError ? (e.detail ?? e.message) : "Could not load leads.");
    }
  }, [stage, priority, query]);

  useEffect(() => {
    void load();
  }, [load]);

  async function loadMore() {
    if (!page) return;
    const current = generation.current;
    setLoadingMore(true);
    try {
      const more = await getInbox({ stage: stage === "all" ? undefined : stage, priority, q: query || undefined,
        limit: PAGE_SIZE, offset: items.length });
      if (current === generation.current) setItems((prev) => [...prev, ...more.items]);
    } catch (e) {
      setError(e instanceof APIError ? (e.detail ?? e.message) : "Could not load more leads.");
    } finally {
      setLoadingMore(false);
    }
  }

  function toggle(item: InboxItem) {
    setDraftMessage(null);
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(item.id)) next.delete(item.id);
      else if (next.size < MAX_SELECTED) next.add(item.id);
      return next;
    });
  }

  async function draftSelected() {
    const chosen = items.filter((item) => selected.has(item.id));
    const byBatch = new Map<string, string[]>();
    for (const item of chosen) byBatch.set(item.batch_id, [...(byBatch.get(item.batch_id) ?? []), item.id]);
    setDrafting(true);
    setDraftError(null);
    setDraftMessage(null);
    const failures: string[] = [];
    let queued = 0;
    for (const [batchId, leadIds] of byBatch) {
      try {
        await createBatchJob(batchId, "selected_outreach", { lead_ids: leadIds });
        queued += leadIds.length;
      } catch (e) {
        failures.push(e instanceof APIError ? (e.detail ?? e.message) : "Could not queue drafts.");
      }
    }
    setDrafting(false);
    if (queued) {
      setSelected(new Set());
      setDraftMessage(
        `Preparing drafts for ${queued} lead${queued === 1 ? "" : "s"} with your active seller profile. ` +
          "They appear under To review as they finish; nothing is approved or sent automatically.",
      );
    }
    if (failures.length) setDraftError(failures.join(" "));
  }

  function setFilter(next: { stage?: LeadStage | "all"; priority?: Priority | "" }) {
    const url = new URLSearchParams(params?.toString() ?? "");
    const nextStage = next.stage ?? stage;
    const nextPriority = next.priority === undefined ? priority : next.priority;
    if (nextStage === "all") url.delete("stage"); else url.set("stage", nextStage);
    if (!nextPriority) url.delete("priority"); else url.set("priority", nextPriority);
    const qs = url.toString();
    router.replace(qs ? `/leads?${qs}` : "/leads");
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Leads"
        description="Every lead you imported, ranked by how well it matches the customers your seller profile targets. Select leads to draft outreach, or open one to review and send."
        actions={<Button icon="upload" variant="secondary" onClick={() => router.push("/imports")}>Import leads</Button>}
      />

      <RankingBanner refreshKey={bannerKey} onRecalculated={() => { setBannerKey((k) => k + 1); void load(); }} />

      <div className="flex flex-wrap gap-1.5" role="tablist" aria-label="Stage">
        {TABS.map((tab) => {
          const count = page ? (tab === "all" ? page.counts.all : page.counts[tab]) : undefined;
          const active = tab === stage;
          return (
            <button key={tab} type="button" role="tab" aria-selected={active}
              onClick={() => setFilter({ stage: tab })}
              className={`rounded-full px-3 py-1.5 text-sm font-medium transition-colors ${
                active ? "bg-slate-900 text-white" : "bg-white text-slate-600 ring-1 ring-slate-200 hover:bg-slate-50"}`}>
              {tabLabel(tab)}
              {count !== undefined && <span className={`ml-1.5 tabular ${active ? "text-slate-300" : "text-slate-400"}`}>{count}</span>}
            </button>
          );
        })}
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <label className="relative flex-1 sm:max-w-xs">
          <span className="sr-only">Search companies or contacts</span>
          <Icon name="search" className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-slate-400" />
          <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search companies or contacts"
            className="w-full rounded-lg border border-slate-300 bg-white py-2 pl-9 pr-3 text-sm" />
        </label>
        <label className="flex items-center gap-2 text-sm text-slate-600">
          Priority
          <select value={priority ?? ""} onChange={(e) => setFilter({ priority: e.target.value as Priority | "" })}
            className="rounded-lg border border-slate-300 bg-white px-2 py-2 text-sm">
            <option value="">Any</option>
            {PRIORITIES.map((p) => <option key={p} value={p}>{PRIORITY_LABELS[p]}</option>)}
          </select>
        </label>
        {selected.size > 0 && (
          <div className="flex items-center gap-2">
            <span className="text-sm text-slate-600">{selected.size} selected</span>
            <Button size="sm" icon="sparkles" loading={drafting} disabled={drafting} onClick={() => void draftSelected()}>
              Draft outreach for selected
            </Button>
            <Button size="sm" variant="ghost" disabled={drafting} onClick={() => setSelected(new Set())}>Clear</Button>
          </div>
        )}
      </div>
      {draftMessage && <p role="status" className="text-sm text-emerald-700">{draftMessage}</p>}
      {draftError && <ErrorMessage>{draftError}</ErrorMessage>}

      {error && <ErrorMessage>{error}</ErrorMessage>}
      {!page && !error && <LoadingState />}
      {page && items.length === 0 && (
        <div className="rounded-xl border border-dashed border-slate-300 bg-white p-10 text-center text-sm text-slate-600">
          {page.counts.all === 0 ? (
            <>No leads yet. <Link href="/imports" className="font-medium text-brand-700 hover:underline">Import a list</Link> to get started.</>
          ) : "No leads match these filters."}
        </div>
      )}
      {page && items.length > 0 && (
        <div className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-card">
          <table className="min-w-full divide-y divide-slate-200 text-sm">
            <thead className="bg-slate-50 text-left text-xs font-semibold uppercase tracking-wide text-slate-500">
              <tr>
                <th className="w-10 px-4 py-3"><span className="sr-only">Select</span></th>
                <th className="px-4 py-3">Company</th>
                <th className="px-4 py-3">Ranking</th>
                <th className="px-4 py-3">Stage</th>
                <th className="hidden px-4 py-3 md:table-cell">Draft by</th>
                <th className="hidden px-4 py-3 lg:table-cell">Import</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {items.map((item) => (
                <tr key={item.id} className="cursor-pointer hover:bg-slate-50" onClick={() => router.push(`/leads/${item.id}`)}>
                  <td className="px-4 py-3" onClick={(e) => e.stopPropagation()}>
                    <input type="checkbox" aria-label={`Select ${item.company_name}`}
                      checked={selected.has(item.id)}
                      disabled={!selectable(item) || (!selected.has(item.id) && selected.size >= MAX_SELECTED)}
                      title={item.blocked ? "Do not contact" : item.band === null ? "Rank this lead first" : undefined}
                      onChange={() => toggle(item)} />
                  </td>
                  <td className="px-4 py-3">
                    <Link href={`/leads/${item.id}`} className="font-medium text-slate-900 hover:text-brand-700" onClick={(e) => e.stopPropagation()}>
                      {item.company_name}
                    </Link>
                    <div className="text-xs text-slate-500">
                      {[item.contact_name, item.contact_title].filter(Boolean).join(" · ") || item.industry || "-"}
                    </div>
                  </td>
                  <td className="px-4 py-3" title={item.ranking_reason ?? undefined}>
                    <span className="flex items-center gap-2">
                      <RankingBadge priority={item.priority} band={item.band} outdated={item.ranking_outdated} />
                      {item.score !== null && <span className="tabular text-xs text-slate-500">{item.score}</span>}
                    </span>
                    {item.ranking_reason && <div className="mt-0.5 hidden max-w-xs truncate text-xs text-slate-500 xl:block">{item.ranking_reason}</div>}
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex flex-wrap items-center gap-1.5">
                      <StageBadge stage={item.stage} />
                      {item.blocked && <span className="text-xs font-medium text-red-700">Do not contact</span>}
                      {item.delivery_unknown && <span className="text-xs font-medium text-amber-700">Delivery unknown</span>}
                    </div>
                  </td>
                  <td className="hidden px-4 py-3 text-slate-600 md:table-cell">{item.draft_model ? draftModelLabel(item.draft_model) : "-"}</td>
                  <td className="hidden px-4 py-3 text-slate-600 lg:table-cell">
                    <Link href={`/batches/${item.batch_id}`} className="hover:text-brand-700" onClick={(e) => e.stopPropagation()}>
                      {item.batch_name ?? "Unnamed import"}
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {items.length < page.total && (
            <div className="flex justify-center border-t border-slate-100 p-3">
              <Button variant="ghost" loading={loadingMore} onClick={() => void loadMore()}>
                Load more ({page.total - items.length} left)
              </Button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default function LeadsPage() {
  return (
    <Suspense fallback={<LoadingState />}>
      <LeadsInbox />
    </Suspense>
  );
}
