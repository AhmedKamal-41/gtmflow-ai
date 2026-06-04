"use client";

import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { BatchSummaryCard } from "@/components/BatchSummaryCard";
import { Button } from "@/components/Button";
import { ErrorMessage } from "@/components/ErrorMessage";
import { Icon } from "@/components/Icon";
import { LeadTable } from "@/components/LeadTable";
import { LoadingState } from "@/components/LoadingState";
import { PageHeader } from "@/components/PageHeader";
import {
  APIError,
  getBatch,
  getLeadScore,
  getLeads,
  pushHotLeads,
  scoreBatch,
} from "@/lib/api";
import type { Lead, LeadBatch, LeadScore } from "@/types/api";

export default function BatchDetailPage() {
  const params = useParams<{ batchId: string }>();
  const batchId = params?.batchId ?? "";
  const [batch, setBatch] = useState<LeadBatch | null>(null);
  const [leads, setLeads] = useState<Lead[] | null>(null);
  const [scores, setScores] = useState<Record<string, LeadScore | null>>({});
  const [error, setError] = useState<string | null>(null);
  const [actionResult, setActionResult] = useState<string | null>(null);
  const [scoring, setScoring] = useState(false);
  const [pushing, setPushing] = useState(false);

  const load = useCallback(async () => {
    if (!batchId) return;
    setError(null);
    try {
      const [b, ls] = await Promise.all([
        getBatch(batchId),
        getLeads(batchId),
      ]);
      setBatch(b);
      setLeads(ls);

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
      setScores(scoreMap);
    } catch (e) {
      setError(
        e instanceof APIError ? (e.detail ?? e.message) : "Failed to load batch",
      );
    }
  }, [batchId]);

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

      {actionResult && (
        <div className="flex items-start gap-2.5 rounded-lg border border-brand-200 bg-brand-50 p-3.5 text-sm text-brand-900">
          <Icon name="check" className="h-4 w-4 flex-none translate-y-0.5 text-brand-600" />
          <div>{actionResult}</div>
        </div>
      )}
      {error && <ErrorMessage>{error}</ErrorMessage>}

      <LeadTable leads={leads} scores={scores} />
    </div>
  );
}
