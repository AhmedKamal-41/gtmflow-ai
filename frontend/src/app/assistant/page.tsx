"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import { ModelStatusPill, useAIStatus } from "@/components/AIStatusProvider";
import { Button } from "@/components/Button";
import { Card } from "@/components/Card";
import { PageHeader } from "@/components/PageHeader";
import { APIError, cancelJob, createBatchJob, getAssistantStatus, getBatches, getJob, planOutreach,
  type AssistantResult, type AssistantStatus } from "@/lib/api";
import type { Job, LeadBatch, Page } from "@/types/api";

const ACTIVE = new Set(["queued", "running"]);
const STEPS: Record<string, string> = {
  search_leads: "Search records", inspect_leads: "Check company facts", propose_leads: "Choose leads",
  planner: "Assistant request", unavailable_tool: "Action refused",
};

function errorMessage(error: unknown): string {
  return error instanceof APIError ? error.message : "The request could not be completed. Please try again.";
}

export default function AssistantPage() {
  const { status: drafting } = useAIStatus();
  const [status, setStatus] = useState<AssistantStatus | null>(null);
  const [batches, setBatches] = useState<Page<LeadBatch> | null>(null);
  const [offset, setOffset] = useState(0);
  const [batchId, setBatchId] = useState("");
  const [description, setDescription] = useState("Find Hot leads for outreach.");
  const [maxLeads, setMaxLeads] = useState(5);
  const [result, setResult] = useState<AssistantResult | null>(null);
  const [job, setJob] = useState<Job | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [pollError, setPollError] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const generation = useRef(0);

  useEffect(() => () => { generation.current += 1; }, []);

  useEffect(() => {
    let active = true;
    Promise.all([getAssistantStatus(), getBatches({ limit: 50, offset })]).then(([nextStatus, page]) => {
      if (!active) return;
      setStatus(nextStatus);
      setBatches(page);
      setLoadError(null);
    }).catch((reason: unknown) => { if (active) setLoadError(errorMessage(reason)); });
    return () => { active = false; };
  }, [offset, refresh]);

  useEffect(() => {
    if (!job || !ACTIVE.has(job.status) || pollError) return;
    let active = true;
    const timer = setTimeout(() => {
      getJob(job.id).then((next) => { if (active) setJob(next); })
        .catch(() => { if (active) setPollError(true); });
    }, 2000);
    return () => { active = false; clearTimeout(timer); };
  }, [job, pollError]);

  function invalidate() {
    generation.current += 1;
    setResult(null);
    setJob(null);
    setPollError(false);
    setError(null);
    setBusy(false);
  }

  async function find() {
    const current = ++generation.current;
    setBusy(true);
    setError(null);
    setResult(null);
    setJob(null);
    setPollError(false);
    try {
      const next = await planOutreach(batchId, description.trim(), maxLeads);
      if (generation.current === current) setResult(next);
    } catch (reason) {
      if (generation.current === current) setError(errorMessage(reason));
    } finally {
      if (generation.current === current) setBusy(false);
    }
  }

  async function prepare() {
    if (!result?.leads.length) return;
    const current = ++generation.current;
    setBusy(true);
    setError(null);
    try {
      const next = await createBatchJob(result.batch_id, "assistant_outreach", {
        lead_ids: result.leads.map((lead) => lead.id),
      });
      if (generation.current === current) setJob(next);
    } catch (reason) {
      if (generation.current === current) setError(errorMessage(reason));
    } finally {
      if (generation.current === current) setBusy(false);
    }
  }

  async function stopJob() {
    if (!job) return;
    const current = generation.current;
    setBusy(true);
    try {
      const next = await cancelJob(job.id);
      if (generation.current === current) setJob(next);
    } catch (reason) {
      if (generation.current === current) setError(errorMessage(reason));
    } finally {
      if (generation.current === current) setBusy(false);
    }
  }

  const selectedBatch = batches?.items.find((batch) => batch.id === batchId);
  const incomplete = selectedBatch && ["partial", "uploading"].includes(selectedBatch.status);
  return (
    <div className="space-y-6">
      <PageHeader title="Outreach assistant" description="Find leads in an import, check the company facts, and prepare drafts for your review." />
      <Card>
        {status && <div className="mb-4 rounded-lg bg-slate-50 p-3 text-sm text-slate-600">
          <p className="font-medium text-slate-900">{status.mode === "mock" ? "Demo assistant" : "AI assistant · OpenAI"}</p>
          <p className="mt-1">{status.mode === "mock"
            ? "This is a fixed demonstration, not an AI model. It recognizes Hot/Warm/Cold and healthcare/real estate; other instructions are not interpreted."
            : "Your request and selected company facts are sent to OpenAI. Finding leads uses paid AI requests."}</p>
          {!status.can_run && <p className="mt-2">{!status.configured ? "The assistant needs configuration before it can run."
            : "Your account cannot run the assistant on this server."}</p>}
        </div>}
        <form className="space-y-4" onSubmit={(event) => { event.preventDefault(); void find(); }}>
          <div className="grid gap-4 sm:grid-cols-[1fr_10rem]">
            <label className="text-sm font-medium text-slate-700">Import
              <select aria-label="Import" value={batchId} disabled={busy} className="mt-1 block w-full rounded-lg border border-slate-300 bg-white p-2"
                onChange={(event) => { invalidate(); setBatchId(event.target.value); }}>
                <option value="">Choose an import</option>
                {batches?.items.map((batch) => <option key={batch.id} value={batch.id}>{batch.name || "Untitled import"} · {batch.total_leads} leads</option>)}
              </select>
            </label>
            <label className="text-sm font-medium text-slate-700">Maximum leads
              <select aria-label="Maximum leads" value={maxLeads} disabled={busy} className="mt-1 block w-full rounded-lg border border-slate-300 bg-white p-2"
                onChange={(event) => { invalidate(); setMaxLeads(Number(event.target.value)); }}>
                {[1, 2, 3, 4, 5].map((count) => <option key={count} value={count}>{count}</option>)}
              </select>
            </label>
          </div>
          {batches && (offset > 0 || batches.has_more) && <div className="flex gap-2">
            <Button variant="ghost" size="sm" disabled={busy || offset === 0} onClick={() => { invalidate(); setBatchId(""); setOffset(offset - 50); }}>Previous imports</Button>
            <Button variant="ghost" size="sm" disabled={busy || !batches.has_more} onClick={() => { invalidate(); setBatchId(""); setOffset(offset + 50); }}>More imports</Button>
          </div>}
          {batches?.total === 0 && <p className="text-sm text-slate-600"><Link className="text-brand-700 underline" href="/imports">Import leads</Link> to get started.</p>}
          <label className="block text-sm font-medium text-slate-700">Which leads are you looking for?
            <textarea value={description} maxLength={1000} rows={3} disabled={busy} className="mt-1 block w-full rounded-lg border border-slate-300 p-3 font-normal"
              onChange={(event) => { invalidate(); setDescription(event.target.value); }} />
          </label>
          <p className="text-sm text-slate-500">Uses stored records only. Blocked leads and leads with existing outreach drafts are excluded. Company fit does not establish buying interest.</p>
          {incomplete && <p role="alert" className="text-sm text-amber-800">Finish this import before finding leads.</p>}
          <Button type="submit" icon="search" loading={busy && !result}
            disabled={busy || !status?.can_run || !batchId || !description.trim() || Boolean(incomplete)}>Find leads</Button>
        </form>
      </Card>
      {loadError && <div role="alert" className="rounded-lg bg-red-50 p-4 text-sm text-red-800">
        <p>{loadError}</p>
        <Button variant="ghost" onClick={() => setRefresh(refresh + 1)}>Retry loading</Button>
      </div>}
      {error && <div role="alert" className="rounded-lg bg-red-50 p-4 text-sm text-red-800">
        <p>{error}</p>
      </div>}
      {result && <Card title="Your shortlist">
        <p role="status" className="text-sm text-slate-700">{result.message}</p>
        <ul className="mt-4 divide-y divide-slate-100">
          {result.leads.map((lead) => <li key={lead.id} className="py-4">
            <Link href={`/leads/${lead.id}`} className="font-semibold text-brand-700 hover:underline">{lead.company_name}</Link>
            <p className="mt-1 text-sm text-slate-600">{lead.priority ? `${lead.priority} · ${lead.score}/100` : "Not scored"}</p>
            <ul className="mt-2 space-y-1 text-sm text-slate-600">{lead.evidence.map((fact, index) => <li key={index}>{fact}</li>)}</ul>
            <p className="mt-2 text-xs text-slate-500">Unknown: {lead.unknowns.join(", ")}.</p>
          </li>)}
        </ul>
        <details className="mt-4 text-sm text-slate-600">
          <summary className="cursor-pointer font-medium">How these leads were selected</summary>
          <ol className="mt-3 list-inside list-decimal space-y-2">{result.steps.map((step, index) => <li key={index}>
            {STEPS[step.tool] || "Assistant step"}: {step.detail}
          </li>)}</ol>
          <p className="mt-3 text-xs">{(result.duration_ms / 1000).toFixed(1)} seconds · {result.usage.total_tokens ?? 0} reported tokens · {result.mode === "mock" ? "Demonstration" : result.model}</p>
        </details>
        {result.status === "proposed" && result.leads.length > 0 && <div className="mt-5 border-t border-slate-100 pt-4">
          <div className="mb-3 text-sm text-slate-600">Drafting model: <ModelStatusPill status={drafting} /></div>
          <p className="mb-3 text-sm text-slate-600">Preparing drafts uses your configured drafting model and requires an active seller profile. Every draft still needs your review and approval before sending.</p>
          <Button onClick={() => void prepare()} loading={busy} disabled={!status?.can_run || Boolean(job)}>Prepare {result.leads.length} {result.leads.length === 1 ? "draft" : "drafts"}</Button>
        </div>}
      </Card>}
      {job && <Card title="Draft preparation">
        <p role="status" className="text-sm text-slate-700">{job.status}{job.cancel_requested ? " · cancellation requested" : ""} · {job.done_items}/{job.total_items ?? "?"} processed</p>
        <p className="mt-2 text-sm text-slate-600">{job.counts.succeeded ?? 0} generated · {job.counts.skipped ?? 0} skipped · {job.counts.blocked ?? 0} blocked · {job.counts.failed ?? 0} failed</p>
        {job.status === "queued" && <p className="mt-2 text-sm text-slate-500">Waiting for the background worker. You can leave this page; progress is also available on the import.</p>}
        {job.last_error && <p role="alert" className="mt-2 text-sm text-red-700">{job.last_error}</p>}
        {pollError && <div role="alert" className="mt-3 text-sm text-amber-800">Could not refresh progress; the job may still be running.
          <Button variant="ghost" size="sm" onClick={() => setPollError(false)}>Refresh progress</Button></div>}
        <div className="mt-4 flex items-center gap-4">
          <Link className="text-sm text-brand-700 underline" href={`/batches/${job.batch_id}`}>Open import and jobs</Link>
          {ACTIVE.has(job.status) && <Button variant="secondary" size="sm" onClick={() => void stopJob()} disabled={busy || job.cancel_requested}>Cancel remaining drafts</Button>}
        </div>
      </Card>}
    </div>
  );
}
