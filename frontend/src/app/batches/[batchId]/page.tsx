"use client";

import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

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
  getLeadScore,
  getLeads,
  pushHotLeads,
  scoreBatch,
  scoreBatchFit,
} from "@/lib/api";
import type {
  BatchFitScoreSummary,
  Lead,
  LeadBatch,
  LeadFitScore,
  LeadScore,
} from "@/types/api";

const PAGE_SIZE = 50;

export default function BatchDetailPage() {
  const params = useParams<{ batchId: string }>();
  const batchId = params?.batchId ?? "";
  const [batch, setBatch] = useState<LeadBatch | null>(null);
  const [leads, setLeads] = useState<Lead[] | null>(null);
  const [leadTotal, setLeadTotal] = useState(0);
  const [hasMoreLeads, setHasMoreLeads] = useState(false);
  const [loadingMoreLeads, setLoadingMoreLeads] = useState(false);
  const [scores, setScores] = useState<Record<string, LeadScore | null>>({});
  // v2 deterministic company-fit scorer (Phase 4) -- fetched via ONE
  // bounded bulk lookup per page (getBatchFitScores), not one request per
  // lead like the legacy `fetchScoresFor` below (Part E: avoid N+1).
  const [fitScores, setFitScores] = useState<Record<string, LeadFitScore>>({});
  const [fitSummary, setFitSummary] = useState<BatchFitScoreSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [actionResult, setActionResult] = useState<string | null>(null);
  const [scoring, setScoring] = useState(false);
  const [scoringFit, setScoringFit] = useState(false);
  const [pushing, setPushing] = useState(false);

  const fetchScoresFor = useCallback(async (ls: Lead[]) => {
    const scoreMap: Record<string, LeadScore | null> = {};
    await Promise.all(
      ls.map(async (lead) => {
        try {
          scoreMap[lead.id] = await getLeadScore(lead.id);
        } catch (e) {
          if (e instanceof APIError && e.status === 404) {
            scoreMap[lead.id] = null;
          } else {
            throw e;
          }
        }
      }),
    );
    setScores((prev) => ({ ...prev, ...scoreMap }));
  }, []);

  const fetchFitScoresPage = useCallback(
    async (offset: number, count: number) => {
      const page = await getBatchFitScores(batchId, { limit: count, offset });
      const map: Record<string, LeadFitScore> = {};
      for (const item of page.items) map[item.lead_id] = item;
      setFitScores((prev) => ({ ...prev, ...map }));
    },
    [batchId],
  );

  const load = useCallback(async () => {
    if (!batchId) return;
    setError(null);
    try {
      const [b, leadPage] = await Promise.all([
        getBatch(batchId),
        getLeads(batchId, { limit: PAGE_SIZE, offset: 0 }),
      ]);
      setBatch(b);
      setLeads(leadPage.items);
      setLeadTotal(leadPage.total);
      setHasMoreLeads(leadPage.has_more);
      await Promise.all([
        fetchScoresFor(leadPage.items),
        fetchFitScoresPage(0, leadPage.items.length),
      ]);
    } catch (e) {
      setError(
        e instanceof APIError ? (e.detail ?? e.message) : "Failed to load batch",
      );
    }
  }, [batchId, fetchScoresFor, fetchFitScoresPage]);

  async function loadMoreLeads() {
    if (!leads) return;
    setLoadingMoreLeads(true);
    try {
      const leadPage = await getLeads(batchId, {
        limit: PAGE_SIZE,
        offset: leads.length,
      });
      setLeads([...leads, ...leadPage.items]);
      setHasMoreLeads(leadPage.has_more);
      await Promise.all([
        fetchScoresFor(leadPage.items),
        fetchFitScoresPage(leads.length, leadPage.items.length),
      ]);
    } catch (e) {
      setError(
        e instanceof APIError ? (e.detail ?? e.message) : "Failed to load more leads",
      );
    } finally {
      setLoadingMoreLeads(false);
    }
  }

  useEffect(() => {
    void load();
  }, [load]);

  async function handleScoreBatch() {
    setScoring(true);
    setActionResult(null);
    setError(null);
    try {
      const res = await scoreBatch(batchId);
      setActionResult(
        `Scored ${res.scored_leads} leads. Hot ${res.hot} · Warm ${res.warm} · Cold ${res.cold} · avg ${res.average_score}.`,
      );
      await load();
    } catch (e) {
      setError(
        e instanceof APIError
          ? (e.detail ?? e.message)
          : "Failed to score batch",
      );
    } finally {
      setScoring(false);
    }
  }

  async function handleScoreBatchFit() {
    setScoringFit(true);
    setActionResult(null);
    setError(null);
    try {
      const res = await scoreBatchFit(batchId);
      setFitSummary(res);
      setActionResult(
        `Fit-scored ${res.scored_leads} leads (v2 demo). Strong ${res.strong_match} · ` +
          `Partial ${res.partial_match} · Weak ${res.weak_match} · ` +
          `Insufficient evidence ${res.insufficient_evidence} · avg ${res.average_fit_score}.`,
      );
      await load();
    } catch (e) {
      setError(
        e instanceof APIError
          ? (e.detail ?? e.message)
          : "Failed to score batch fit",
      );
    } finally {
      setScoringFit(false);
    }
  }

  async function handlePushHot() {
    setPushing(true);
    setActionResult(null);
    setError(null);
    try {
      const res = await pushHotLeads(batchId);
      setActionResult(
        `Pushed ${res.pushed} of ${res.hot_leads_found} Hot leads (${res.skipped} skipped, ${res.failed} failed).`,
      );
      await load();
    } catch (e) {
      setError(
        e instanceof APIError
          ? (e.detail ?? e.message)
          : "Failed to push Hot leads",
      );
    } finally {
      setPushing(false);
    }
  }

  if (!batch || !leads) {
    return error ? <ErrorMessage>{error}</ErrorMessage> : <LoadingState />;
  }

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
              Score batch
            </Button>
            <Button
              variant="secondary"
              icon="target"
              loading={scoringFit}
              onClick={handleScoreBatchFit}
            >
              Score batch fit (v2 demo)
            </Button>
            <Button
              variant="primary"
              icon="send"
              loading={pushing}
              onClick={handlePushHot}
            >
              Push all Hot leads
            </Button>
          </>
        }
      />

      <BatchSummaryCard batch={batch} />

      {fitSummary && (
        <Card title="Company fit (v2 demo) -- this run" icon="target">
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
            <span className="text-slate-500">
              avg {fitSummary.average_fit_score} / 100
            </span>
          </div>
          <p className="mt-2 text-xs text-slate-500">
            Broad demonstration profile -- exact industry/country match only,
            not a calibrated ICP. Not the same signal as legacy Hot/Warm/Cold
            priority above.
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
      <LeadTable leads={leads} scores={scores} fitScores={fitScores} />
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
