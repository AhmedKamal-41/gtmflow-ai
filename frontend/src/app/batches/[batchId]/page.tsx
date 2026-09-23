"use client";

import { useParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import { BatchSummaryCard } from "@/components/BatchSummaryCard";
import { Button } from "@/components/Button";
import { Card } from "@/components/Card";
import { ErrorMessage } from "@/components/ErrorMessage";
import { FitBandBadge } from "@/components/FitBandBadge";
import { Icon } from "@/components/Icon";
import { LeadTable } from "@/components/LeadTable";
import { LoadingState } from "@/components/LoadingState";
import { PageHeader } from "@/components/PageHeader";
import {
  APIError,
  getBatch,
  getBatchFitScores,
  getBatchFitSummary,
  getBatchReadiness,
  getBatchScores,
  getLeads,
  pushHotLeads,
  scoreBatch,
  scoreBatchFit,
} from "@/lib/api";
import type {
  BatchFitSummary,
  CurrentReadiness,
  Lead,
  LeadBatch,
  LeadFitScore,
  LeadScore,
} from "@/types/api";

const PAGE_SIZE = 50;

// Mirrors the backend's INCOMPLETE_BATCH_STATUSES: the import has not
// finished committing, so nothing in it may be routed.
const INCOMPLETE_BATCH_STATUSES = new Set(["partial", "uploading"]);

type PageExtras = {
  scores: Record<string, LeadScore | null>;
  fitScores: Record<string, LeadFitScore>;
  readiness: Record<string, CurrentReadiness>;
};

export default function BatchDetailPage() {
  const params = useParams<{ batchId: string }>();
  const batchId = params?.batchId ?? "";
  const [batch, setBatch] = useState<LeadBatch | null>(null);
  const [leads, setLeads] = useState<Lead[] | null>(null);
  const [leadTotal, setLeadTotal] = useState(0);
  const [hasMoreLeads, setHasMoreLeads] = useState(false);
  const [loadingMoreLeads, setLoadingMoreLeads] = useState(false);
  // Per-lead data for the loaded pages. Each is fetched with ONE bounded
  // request per page of leads (never one request per lead), keyed by
  // lead id so it can't be misattributed even if two pages overlap.
  const [scores, setScores] = useState<Record<string, LeadScore | null>>({});
  const [fitScores, setFitScores] = useState<Record<string, LeadFitScore>>({});
  const [readiness, setReadiness] = useState<Record<string, CurrentReadiness>>({});
  // Distinct-lead fit counts for the whole batch (latest score per lead).
  const [fitSummary, setFitSummary] = useState<BatchFitSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [actionResult, setActionResult] = useState<string | null>(null);
  const [scoring, setScoring] = useState(false);
  const [scoringFit, setScoringFit] = useState(false);
  const [pushing, setPushing] = useState(false);

  // Bumped on every batchId change; responses for an older batch are dropped.
  const generationRef = useRef(0);

  const fetchPageExtras = useCallback(
    async (leadPage: Lead[], offset: number): Promise<PageExtras> => {
      const page = { limit: leadPage.length || 1, offset };
      const [scorePage, fitPage, readinessPage] = await Promise.all([
        getBatchScores(batchId, page),
        getBatchFitScores(batchId, page),
        getBatchReadiness(batchId, page),
      ]);
      const extras: PageExtras = { scores: {}, fitScores: {}, readiness: {} };
      for (const lead of leadPage) extras.scores[lead.id] = null;
      for (const s of scorePage.items) extras.scores[s.lead_id] = s;
      for (const f of fitPage.items) extras.fitScores[f.lead_id] = f;
      for (const r of readinessPage.items) extras.readiness[r.lead_id] = r;
      return extras;
    },
    [batchId],
  );

  const load = useCallback(async () => {
    if (!batchId) return;
    const generation = generationRef.current;
    setError(null);
    try {
      const [b, leadPage, summary] = await Promise.all([
        getBatch(batchId),
        getLeads(batchId, { limit: PAGE_SIZE, offset: 0 }),
        getBatchFitSummary(batchId),
      ]);
      const extras = await fetchPageExtras(leadPage.items, 0);
      if (generationRef.current !== generation) return; // stale
      setBatch(b);
      setLeads(leadPage.items);
      setLeadTotal(leadPage.total);
      setHasMoreLeads(leadPage.has_more);
      setFitSummary(summary);
      setScores(extras.scores);
      setFitScores(extras.fitScores);
      setReadiness(extras.readiness);
    } catch (e) {
      if (generationRef.current !== generation) return;
      setError(
        e instanceof APIError ? (e.detail ?? e.message) : "Failed to load batch",
      );
    }
  }, [batchId, fetchPageExtras]);

  async function loadMoreLeads() {
    if (!leads) return;
    const generation = generationRef.current;
    setLoadingMoreLeads(true);
    try {
      const offset = leads.length;
      const leadPage = await getLeads(batchId, { limit: PAGE_SIZE, offset });
      const extras = await fetchPageExtras(leadPage.items, offset);
      if (generationRef.current !== generation) return; // stale
      setLeads((prev) => [...(prev ?? []), ...leadPage.items]);
      setHasMoreLeads(leadPage.has_more);
      setScores((prev) => ({ ...prev, ...extras.scores }));
      setFitScores((prev) => ({ ...prev, ...extras.fitScores }));
      setReadiness((prev) => ({ ...prev, ...extras.readiness }));
    } catch (e) {
      if (generationRef.current !== generation) return;
      setError(
        e instanceof APIError ? (e.detail ?? e.message) : "Failed to load more leads",
      );
    } finally {
      if (generationRef.current === generation) setLoadingMoreLeads(false);
    }
  }

  useEffect(() => {
    generationRef.current += 1;
    setBatch(null);
    setLeads(null);
    setScoring(false);
    setScoringFit(false);
    setPushing(false);
    setLoadingMoreLeads(false);
    setActionResult(null);
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [batchId]);

  async function handleScoreBatch() {
    const generation = generationRef.current;
    setScoring(true);
    setActionResult(null);
    setError(null);
    try {
      const res = await scoreBatch(batchId);
      if (generationRef.current !== generation) return;
      setActionResult(
        `Legacy v1 scoring: scored ${res.scored_leads} leads. Hot ${res.hot} · Warm ${res.warm} · Cold ${res.cold} · avg ${res.average_score}.`,
      );
      await load();
    } catch (e) {
      if (generationRef.current !== generation) return;
      setError(
        e instanceof APIError
          ? (e.detail ?? e.message)
          : "Failed to score batch",
      );
    } finally {
      if (generationRef.current === generation) setScoring(false);
    }
  }

  async function handleScoreBatchFit() {
    const generation = generationRef.current;
    setScoringFit(true);
    setActionResult(null);
    setError(null);
    try {
      const res = await scoreBatchFit(batchId);
      if (generationRef.current !== generation) return;
      setActionResult(
        `Company fit (v2 demo): ${res.newly_scored} of ${res.attempted} leads newly scored` +
          ` · ${res.skipped_unchanged} unchanged since their last score (skipped)` +
          ` · ${res.failed} failed.`,
      );
      await load();
    } catch (e) {
      if (generationRef.current !== generation) return;
      setError(
        e instanceof APIError
          ? (e.detail ?? e.message)
          : "Failed to score batch fit",
      );
    } finally {
      if (generationRef.current === generation) setScoringFit(false);
    }
  }

  async function handlePushHot() {
    const generation = generationRef.current;
    setPushing(true);
    setActionResult(null);
    setError(null);
    try {
      // Re-check the batch's current state right before acting -- the
      // backend enforces this too, but never act on a stale page load.
      const fresh = await getBatch(batchId);
      if (generationRef.current !== generation) return;
      if (INCOMPLETE_BATCH_STATUSES.has(fresh.status)) {
        setBatch(fresh);
        setError(
          `This batch's import is incomplete (status '${fresh.status}') -- ` +
            "nothing in it can be routed until the upload finishes.",
        );
        return;
      }
      const res = await pushHotLeads(batchId);
      if (generationRef.current !== generation) return;
      setActionResult(
        `Pushed ${res.pushed} of ${res.hot_leads_found} legacy-Hot leads (${res.skipped} skipped, ${res.failed} failed).`,
      );
      await load();
    } catch (e) {
      if (generationRef.current !== generation) return;
      setError(
        e instanceof APIError
          ? (e.detail ?? e.message)
          : "Failed to push Hot leads",
      );
    } finally {
      if (generationRef.current === generation) setPushing(false);
    }
  }

  if (!batch || !leads) {
    return error ? <ErrorMessage>{error}</ErrorMessage> : <LoadingState />;
  }

  const batchIncomplete = INCOMPLETE_BATCH_STATUSES.has(batch.status);

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Batch"
        title={batch.name ?? "(unnamed batch)"}
        back={{ href: "/batches", label: "Back to batches" }}
        actions={
          <>
            <Button
              variant="secondary"
              icon="target"
              loading={scoring}
              onClick={handleScoreBatch}
            >
              Score batch (legacy v1)
            </Button>
            <Button
              variant="secondary"
              icon="target"
              loading={scoringFit}
              onClick={handleScoreBatchFit}
            >
              Score company fit (v2 demo)
            </Button>
            <Button
              variant="primary"
              icon="send"
              loading={pushing}
              disabled={batchIncomplete}
              onClick={handlePushHot}
            >
              Push legacy-Hot leads
            </Button>
          </>
        }
      />

      <BatchSummaryCard batch={batch} />

      {batchIncomplete && (
        <div className="rounded-lg border border-red-200 bg-red-50 p-3.5 text-sm text-red-800">
          This batch&apos;s import is incomplete (status &apos;{batch.status}&apos;).
          Every lead in it is excluded from routing until the upload is
          resumed and finishes -- scoring does not change that.
        </div>
      )}

      {fitSummary && (
        <Card title="Company fit (v2 demo)" icon="target">
          {fitSummary.scored_leads === 0 ? (
            <p className="text-sm text-slate-600">
              No lead in this batch has been fit-scored under the current
              profile yet.
            </p>
          ) : (
            <div className="flex flex-wrap items-center gap-4 text-sm">
              <span className="flex items-center gap-1.5">
                <FitBandBadge band="strong_match" /> {fitSummary.strong_match}
              </span>
              <span className="flex items-center gap-1.5">
                <FitBandBadge band="partial_match" /> {fitSummary.partial_match}
              </span>
              <span className="flex items-center gap-1.5">
                <FitBandBadge band="weak_match" /> {fitSummary.weak_match}
              </span>
              <span className="flex items-center gap-1.5">
                <FitBandBadge band="insufficient_evidence" />{" "}
                {fitSummary.insufficient_evidence}
              </span>
              {fitSummary.average_fit_score !== null && (
                <span className="text-slate-500">
                  avg {fitSummary.average_fit_score} / 100
                </span>
              )}
            </div>
          )}
          <p className="mt-2 text-xs text-slate-500">
            {fitSummary.scored_leads.toLocaleString()} of{" "}
            {fitSummary.total_leads.toLocaleString()} leads scored
            {fitSummary.unscored_leads > 0 &&
              ` (${fitSummary.unscored_leads.toLocaleString()} not yet)`}
            ; each lead counted once, by its latest score
            {fitSummary.score_rows > fitSummary.scored_leads &&
              ` (${fitSummary.score_rows.toLocaleString()} stored score rows incl. history)`}
            . Broad demonstration profile -- exact industry/country match
            only, not a calibrated ICP, and not the same signal as legacy
            Hot/Warm/Cold. A strong match is not approval to contact.
          </p>
        </Card>
      )}

      {actionResult && (
        <div className="flex items-start gap-2.5 rounded-lg border border-brand-200 bg-brand-50 p-3.5 text-sm text-brand-900">
          <Icon name="check" className="h-4 w-4 flex-none translate-y-0.5 text-brand-600" />
          <div>{actionResult}</div>
        </div>
      )}
      {error && <ErrorMessage>{error}</ErrorMessage>}

      <div className="flex items-center justify-between">
        <div className="text-sm text-slate-500">
          Showing {leads.length.toLocaleString()} of {leadTotal.toLocaleString()} leads
        </div>
      </div>
      <LeadTable
        leads={leads}
        scores={scores}
        fitScores={fitScores}
        readiness={readiness}
      />
      {hasMoreLeads && (
        <div className="flex justify-center">
          <button
            type="button"
            onClick={loadMoreLeads}
            disabled={loadingMoreLeads}
            className="text-sm font-medium text-brand-600 hover:text-brand-700 disabled:opacity-50"
          >
            {loadingMoreLeads ? "Loading…" : "Load more leads"}
          </button>
        </div>
      )}
    </div>
  );
}
