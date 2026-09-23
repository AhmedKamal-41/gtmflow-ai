"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { AIOutputCard } from "@/components/AIOutputCard";
import { Button } from "@/components/Button";
import { Card } from "@/components/Card";
import { ErrorMessage } from "@/components/ErrorMessage";
import { LoadingState } from "@/components/LoadingState";
import { PageHeader } from "@/components/PageHeader";
import { usePaginatedHistory } from "@/hooks/usePaginatedHistory";
import { useReviewTimer } from "@/hooks/useReviewTimer";
import {
  APIError,
  generateAnnotationCandidate,
  getAnnotationCandidate,
  getAnnotationCandidates,
  getAnnotationProvider,
  getAnnotationSummary,
  submitAnnotation,
} from "@/lib/api";
import type {
  AnnotationCandidateDetail,
  AnnotationProvider,
  AnnotationSubmit,
  AnnotationSummary,
} from "@/types/api";

const PILOT_QUEUE = "pilot-v1";
const inputClass = "mt-1 block w-full rounded-lg border border-slate-300 px-3 py-2 text-sm";

const STATUS_LABEL: Record<string, string> = {
  awaiting_generation: "Awaiting generation",
  pending_review: "Pending review",
  accepted: "Accepted",
  corrected: "Corrected",
  skipped: "Skipped",
};

function errorText(e: unknown, fallback: string): string {
  if (e instanceof APIError) return e.detail ?? e.message;
  return e instanceof Error ? e.message : fallback;
}

function newId(): string {
  return globalThis.crypto.randomUUID();
}

type Assessment = {
  factual_support: "" | "supported" | "partially_supported" | "unsupported";
  writing_quality: string;
  missing_info_handling: "" | "good" | "acceptable" | "poor";
  notes: string;
  skip_reason: string;
};

const EMPTY_ASSESSMENT: Assessment = {
  factual_support: "", writing_quality: "", missing_info_handling: "", notes: "", skip_reason: "",
};

/**
 * Training-annotation workbench (Phase 6). Separate from outreach review:
 * accepting or correcting here creates a training example only; it never
 * approves a draft for delivery, and outreach approval never creates one.
 */
export default function AnnotationPage() {
  const [summary, setSummary] = useState<AnnotationSummary | null>(null);
  const [provider, setProvider] = useState<AnnotationProvider | null>(null);
  const [pageError, setPageError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const queue = usePaginatedHistory(PILOT_QUEUE, (key, limit, offset) =>
    getAnnotationCandidates(key, { limit, offset }),
  );

  const loadSummary = useCallback(async () => {
    try {
      const [s, p] = await Promise.all([getAnnotationSummary(PILOT_QUEUE), getAnnotationProvider()]);
      setSummary(s);
      setProvider(p);
      setPageError(null);
    } catch (e) {
      setPageError(errorText(e, "Could not load the annotation queue."));
    }
  }, []);

  useEffect(() => {
    void loadSummary();
  }, [loadSummary]);

  const afterChange = useCallback(() => {
    void loadSummary();
    queue.refresh();
  }, [loadSummary, queue]);

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Training data"
        title="Annotation workbench"
        description="Review candidate summaries and outreach examples for the 100-example pilot. Training annotation is separate from approving outreach for delivery."
      />
      {pageError && (
        <div className="space-y-2">
          <ErrorMessage>{pageError}</ErrorMessage>
          <Button variant="secondary" onClick={() => void loadSummary()}>Retry</Button>
        </div>
      )}
      {summary && <SummaryBar summary={summary} provider={provider} />}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[320px_1fr]">
        <Card title="Queue" subtitle={`${PILOT_QUEUE} · training split only`}>
          {queue.loading && <LoadingState text="Loading candidates…" />}
          {!queue.loading && queue.items.length === 0 && !queue.error && (
            <p className="text-sm text-slate-500">No candidates. Create the pilot queue with the CLI (see the Phase 6 handoff).</p>
          )}
          <ul className="space-y-1" aria-label="Annotation candidates">
            {queue.items.map((c) => (
              <li key={c.id}>
                <button
                  type="button"
                  aria-current={c.id === selectedId}
                  onClick={() => setSelectedId(c.id)}
                  className={`w-full rounded-lg px-2 py-1.5 text-left text-sm ${c.id === selectedId ? "bg-brand-50 text-brand-900" : "hover:bg-slate-50"}`}
                >
                  #{c.position} {c.company_name} · {c.task === "company_summary" ? "Summary" : "Outreach"}
                  <span className="block text-xs text-slate-500">
                    {STATUS_LABEL[c.status]}{c.is_mock ? " · mock" : ""}
                  </span>
                </button>
              </li>
            ))}
          </ul>
          {queue.error && (
            <div className="mt-2 space-y-2">
              <ErrorMessage>{queue.error}</ErrorMessage>
              <Button variant="secondary" onClick={queue.reload}>Retry</Button>
            </div>
          )}
          {!queue.error && queue.hasMore && (
            <Button variant="ghost" loading={queue.loadingMore} onClick={queue.loadMore}>Load more</Button>
          )}
        </Card>
        {selectedId ? (
          <CandidatePanel candidateId={selectedId} provider={provider} onChanged={afterChange} />
        ) : (
          <Card><p className="text-sm text-slate-600">Select a candidate to review it.</p></Card>
        )}
      </div>
    </div>
  );
}

function SummaryBar({ summary, provider }: { summary: AnnotationSummary; provider: AnnotationProvider | null }) {
  return (
    <Card>
      <div aria-label="Pilot progress" className="space-y-1 text-sm text-slate-700">
        <p>
          <strong>{summary.reviewed_examples}</strong> of {summary.candidates} pilot examples human-reviewed
          ({summary.reviewed_unique_companies} unique companies) · {summary.skipped} skipped ·{" "}
          {summary.pending_review} pending review · {summary.awaiting_generation} awaiting generation.
        </p>
        <p className="text-xs text-slate-500">
          {summary.candidates} candidates from {summary.unique_companies} training companies
          {summary.manifest_version ? ` (manifest ${summary.manifest_version})` : ""}. Experiment target:{" "}
          {summary.experiment_targets.train} train / {summary.experiment_targets.validation} validation /{" "}
          {summary.experiment_targets.test} test examples. {summary.mock_candidates} candidates were generated by the mock provider.
        </p>
        {provider && (
          <p className="text-xs text-slate-500">
            Configured provider: {provider.configured_provider}
            {provider.model_revision ? ` (${provider.model_revision})` : ""}. {provider.detail}
          </p>
        )}
      </div>
    </Card>
  );
}

function CandidatePanel({
  candidateId,
  provider,
  onChanged,
}: {
  candidateId: string;
  provider: AnnotationProvider | null;
  onChanged: () => void;
}) {
  const [detail, setDetail] = useState<AnnotationCandidateDetail | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [pending, setPending] = useState<string | null>(null);
  const [assessment, setAssessment] = useState<Assessment>(EMPTY_ASSESSMENT);
  const [correcting, setCorrecting] = useState(false);
  const [correction, setCorrection] = useState<Record<string, string>>({});
  // A failed submission is retried with the same submission id so the
  // server can recognise a retry of a request that actually landed.
  const retry = useRef<{ key: string; id: string } | null>(null);
  // Two separate guards. `selection` changes only when another candidate is
  // selected: an action (generate/submit) that finishes after that must not
  // touch the new candidate's state. `loadRequest` only orders overlapping
  // loads. Sharing one counter made the post-save reload look like a
  // candidate switch, so the save never cleared its pending state.
  const selection = useRef(0);
  const loadRequest = useRef(0);
  const timer = useReviewTimer(detail?.source_output ? `${detail.id}:${detail.source_output.id}` : null);

  const load = useCallback(async () => {
    const current = selection.current;
    const request = ++loadRequest.current;
    const stale = () => request !== loadRequest.current || current !== selection.current;
    setLoadError(null);
    try {
      const value = await getAnnotationCandidate(candidateId);
      if (stale()) return; // superseded by a newer load or another candidate
      setDetail(value);
    } catch (e) {
      if (stale()) return;
      setLoadError(errorText(e, "Could not load the candidate."));
    }
  }, [candidateId]);

  useEffect(() => {
    setDetail(null);
    setAssessment(EMPTY_ASSESSMENT);
    setCorrecting(false);
    setCorrection({});
    setActionError(null);
    setMessage(null);
    setPending(null);
    retry.current = null;
    void load();
    return () => { selection.current += 1; };
  }, [load]);

  async function generate() {
    if (!provider) return;
    const current = selection.current;
    setPending("generate");
    setActionError(null);
    try {
      const value = await generateAnnotationCandidate(candidateId, provider.configured_provider);
      if (current !== selection.current) return;
      setDetail(value);
      onChanged();
    } catch (e) {
      if (current !== selection.current) return;
      setActionError(errorText(e, "Generation failed. Nothing was saved."));
    } finally {
      if (current === selection.current) setPending(null);
    }
  }

  function startCorrection() {
    const content = (detail?.source_output?.content ?? {}) as Record<string, unknown>;
    const fields: Record<string, string> = detail?.task === "company_summary"
      ? { company_summary: String(content.company_summary ?? "") }
      : {
          subject: String(content.subject ?? ""),
          email_body: String(content.email_body ?? ""),
          call_note: String(content.call_note ?? ""),
        };
    setCorrection(fields);
    setCorrecting(true);
  }

  async function submit(decision: AnnotationSubmit["decision"]) {
    if (!detail?.source_output) return;
    const source = detail.source_output;
    const body: Omit<AnnotationSubmit, "submission_id" | "timing"> = {
      source_output_id: source.id,
      source_content_hash: source.content_hash,
      decision,
      ...(decision !== "skipped" && {
        factual_support: assessment.factual_support || undefined,
        writing_quality: assessment.writing_quality ? Number(assessment.writing_quality) : undefined,
        missing_info_handling: assessment.missing_info_handling || undefined,
      }),
      ...(assessment.notes.trim() && { notes: assessment.notes.trim() }),
      ...(decision === "skipped" && { skip_reason: assessment.skip_reason.trim() }),
      ...(decision === "corrected" && {
        corrected_content: { ...(source.content as Record<string, unknown>), ...correction },
      }),
    };
    const key = JSON.stringify(body);
    let submissionId = newId();
    if (retry.current?.key === key) {
      submissionId = retry.current.id;
      timer.flag("resumed_after_failure");
    }
    retry.current = { key, id: submissionId };
    const current = selection.current;
    setPending(decision);
    setActionError(null);
    setMessage(null);
    try {
      await submitAnnotation(detail.id, { ...body, submission_id: submissionId, timing: timer.read() });
      if (current !== selection.current) return;
      retry.current = null;
      setMessage(
        decision === "skipped"
          ? "Skipped. This example will not be exported."
          : "Saved as a human-reviewed training example.",
      );
      setCorrecting(false);
      onChanged();
      await load();
    } catch (e) {
      if (current !== selection.current) return;
      setActionError(errorText(e, "Saving failed. Your entries are kept; try again."));
    } finally {
      if (current === selection.current) setPending(null);
    }
  }

  if (loadError) {
    return (
      <Card>
        <div className="space-y-2">
          <ErrorMessage>{loadError}</ErrorMessage>
          <Button variant="secondary" onClick={() => void load()}>Retry loading</Button>
        </div>
      </Card>
    );
  }
  if (!detail) return <LoadingState text="Loading candidate…" />;

  const assessed = assessment.factual_support && assessment.writing_quality && assessment.missing_info_handling;
  const busy = pending !== null;
  const snapshot = (detail.source_output?.input_snapshot ?? {}) as {
    lead_facts?: { id: string; field: string; value: string; kind: string }[];
    unknowns?: string[];
  };

  return (
    <div className="space-y-4">
      <Card
        title={`${detail.company_name} · ${detail.task === "company_summary" ? "Company summary" : "Outreach"}`}
        subtitle={`Candidate #${detail.position} · ${STATUS_LABEL[detail.status]} · split ${detail.split} · group ${detail.group_key.slice(0, 10)} · manifest ${detail.manifest_version}`}
      >
        <dl className="grid grid-cols-1 gap-x-6 gap-y-1 text-sm sm:grid-cols-2">
          {Object.entries(detail.lead_facts).map(([key, value]) => (
            <div key={key}>
              <dt className="text-xs uppercase tracking-wide text-slate-500">{key}</dt>
              <dd>{value ?? <span className="text-slate-400">not provided</span>}</dd>
            </div>
          ))}
        </dl>
        <p className="mt-3 text-xs text-slate-500" aria-label="Source freshness">{detail.source.freshness_note}</p>
        <p className="mt-1 text-xs text-slate-500">
          Reviewing a writing example is not permission or readiness to contact this company.
        </p>
      </Card>

      {actionError && <ErrorMessage>{actionError}</ErrorMessage>}
      {message && <p role="status" className="text-sm text-emerald-700">{message}</p>}

      {!detail.source_output ? (
        <Card title="No candidate output yet">
          <p className="text-sm text-slate-600">
            {provider
              ? `Generation uses the configured provider: ${provider.configured_provider}${provider.is_mock ? " (mock; the candidate stays labeled mock through review and export)" : " (a paid API call)"}.`
              : "Checking the configured provider…"}
            {detail.task === "outreach_email" && " Outreach candidates need an active seller profile."}
          </p>
          <div className="mt-3">
            <Button loading={pending === "generate"} disabled={!provider?.available || busy} onClick={() => void generate()}>
              Generate with {provider?.configured_provider ?? "…"}
            </Button>
          </div>
        </Card>
      ) : (
        <>
          <Card title="Immutable input" subtitle={`Input hash ${detail.source_output.input_hash?.slice(0, 12) ?? "none"}`}>
            <ul className="space-y-1 text-sm" aria-label="Input facts">
              {(snapshot.lead_facts ?? []).map((fact) => (
                <li key={fact.id}>
                  <span className="font-mono text-xs text-slate-500">{fact.id}</span> {fact.value}
                  {fact.kind === "free_text" && <span className="text-xs text-slate-400"> (imported free text)</span>}
                </li>
              ))}
            </ul>
            {(snapshot.unknowns ?? []).length > 0 && (
              <p className="mt-2 text-xs text-slate-500">Unknown from the data: {(snapshot.unknowns ?? []).join(", ")}</p>
            )}
          </Card>
          {detail.is_mock && (
            <p className="rounded-lg border border-amber-200 bg-amber-50 p-2 text-sm text-amber-900">
              Mock candidate: generated by the deterministic mock provider, not a model. It stays labeled mock in the export.
            </p>
          )}
          <AIOutputCard output={detail.source_output} />
          {detail.latest_annotation && (
            <p className="text-sm text-slate-600" aria-label="Latest annotation">
              Latest annotation: <strong>{detail.latest_annotation.decision}</strong> by{" "}
              {detail.latest_annotation.reviewer_label} on{" "}
              {new Date(detail.latest_annotation.created_at).toLocaleString()} ({detail.annotation_count} total).
            </p>
          )}
          <Card title="Your review" subtitle="Assess the model output above. Accept it only if every statement is supported; otherwise write a correction.">
            <fieldset disabled={busy} className="space-y-3">
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
                <label className="text-sm font-medium text-slate-700">
                  Factual support
                  <select className={inputClass} value={assessment.factual_support}
                    onChange={(e) => setAssessment((a) => ({ ...a, factual_support: e.target.value as Assessment["factual_support"] }))}>
                    <option value="">Choose…</option>
                    <option value="supported">Fully supported</option>
                    <option value="partially_supported">Partly supported</option>
                    <option value="unsupported">Unsupported</option>
                  </select>
                </label>
                <label className="text-sm font-medium text-slate-700">
                  Writing quality
                  <select className={inputClass} value={assessment.writing_quality}
                    onChange={(e) => setAssessment((a) => ({ ...a, writing_quality: e.target.value }))}>
                    <option value="">Choose…</option>
                    {[1, 2, 3, 4, 5].map((n) => <option key={n} value={n}>{n}</option>)}
                  </select>
                </label>
                <label className="text-sm font-medium text-slate-700">
                  Missing-information handling
                  <select className={inputClass} value={assessment.missing_info_handling}
                    onChange={(e) => setAssessment((a) => ({ ...a, missing_info_handling: e.target.value as Assessment["missing_info_handling"] }))}>
                    <option value="">Choose…</option>
                    <option value="good">Good</option>
                    <option value="acceptable">Acceptable</option>
                    <option value="poor">Poor</option>
                  </select>
                </label>
              </div>
              <label className="block text-sm font-medium text-slate-700">
                Notes (optional)
                <textarea className={inputClass} rows={2} maxLength={4000} value={assessment.notes}
                  onChange={(e) => setAssessment((a) => ({ ...a, notes: e.target.value }))} />
              </label>
              {correcting && (
                <div className="space-y-2 rounded-lg border border-slate-200 p-3">
                  {Object.keys(correction).map((field) => (
                    <label key={field} className="block text-sm font-medium text-slate-700">
                      Corrected {field.replace(/_/g, " ")}
                      <textarea className={inputClass} rows={field === "email_body" || field === "company_summary" ? 6 : 2}
                        value={correction[field]}
                        onChange={(e) => setCorrection((c) => ({ ...c, [field]: e.target.value }))} />
                    </label>
                  ))}
                </div>
              )}
              <div className="flex flex-wrap gap-2">
                <Button
                  loading={pending === "accepted"}
                  disabled={!assessed || assessment.factual_support !== "supported" || correcting}
                  onClick={() => void submit("accepted")}
                >
                  Accept as target
                </Button>
                {correcting ? (
                  <>
                    <Button loading={pending === "corrected"} disabled={!assessed} onClick={() => void submit("corrected")}>
                      Save correction
                    </Button>
                    <Button variant="ghost" onClick={() => setCorrecting(false)}>Cancel correction</Button>
                  </>
                ) : (
                  <Button variant="secondary" onClick={startCorrection}>Write a correction</Button>
                )}
              </div>
              <div className="flex flex-wrap items-end gap-2 border-t border-slate-100 pt-3">
                <label className="flex-1 text-sm font-medium text-slate-700">
                  Skip reason
                  <input className={inputClass} maxLength={1000} value={assessment.skip_reason}
                    onChange={(e) => setAssessment((a) => ({ ...a, skip_reason: e.target.value }))} />
                </label>
                <Button variant="ghost" loading={pending === "skipped"} disabled={!assessment.skip_reason.trim()}
                  onClick={() => void submit("skipped")}>
                  Skip as unsuitable
                </Button>
              </div>
            </fieldset>
          </Card>
        </>
      )}
    </div>
  );
}
