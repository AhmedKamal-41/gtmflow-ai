"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { cancelJob, createBatchJob, listBatchJobs } from "@/lib/api";
import type { Job, JobType } from "@/types/api";

import { Button } from "./Button";
import { Card } from "./Card";

// Phase 10: queue batch work as durable background jobs and follow their
// progress. A separate worker process (`python -m app.jobs.worker`) runs
// them; nothing here approves, rejects or sends anything by itself -- a push
// job delivers only drafts a reviewer already approved.

const JOB_BUTTONS: { type: JobType; label: string }[] = [
  { type: "fit_score", label: "Fit-score all leads" },
  { type: "legacy_score", label: "Legacy-score all leads" },
  { type: "generate_summary", label: "Generate summaries" },
  { type: "generate_outreach", label: "Generate outreach drafts" },
  { type: "push_hot", label: "Push approved Hot leads" },
];

const LABELS: Record<string, string> = Object.fromEntries(JOB_BUTTONS.map((b) => [b.type, b.label]));
const ACTIVE = new Set(["queued", "running"]);

export function JobsPanel({
  batchId,
  batchIncomplete = false,
  pollMs = 2000,
}: {
  batchId: string;
  batchIncomplete?: boolean;
  pollMs?: number;
}) {
  const [jobs, setJobs] = useState<Job[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const seq = useRef(0);

  const load = useCallback(async () => {
    const mine = ++seq.current;
    try {
      const page = await listBatchJobs(batchId);
      if (mine === seq.current) {
        setJobs(page.items);
        setError(null);
      }
    } catch (e) {
      if (mine === seq.current) setError(e instanceof Error ? e.message : "Could not load jobs.");
    }
  }, [batchId]);

  useEffect(() => {
    setJobs(null);
    void load();
  }, [load]);

  const anyActive = (jobs ?? []).some((job) => ACTIVE.has(job.status));
  useEffect(() => {
    if (!anyActive) return undefined;
    const timer = setInterval(() => void load(), pollMs);
    return () => clearInterval(timer);
  }, [anyActive, load, pollMs]);

  async function act(key: string, action: () => Promise<Job>, message: (job: Job) => string) {
    setBusy(key);
    setNotice(null);
    try {
      const job = await action();
      setNotice(message(job));
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Request failed.");
    } finally {
      setBusy(null);
    }
  }

  return (
    <Card
      title="Background jobs"
      subtitle="Queued work runs in a separate worker process (python -m app.jobs.worker)."
    >
      <div className="flex flex-wrap gap-2">
        {JOB_BUTTONS.map(({ type, label }) => (
          <Button
            key={type}
            variant="secondary"
            size="sm"
            loading={busy === type}
            disabled={busy !== null || (type === "push_hot" && batchIncomplete)}
            onClick={() =>
              act(type, () => createBatchJob(batchId, type), (job) =>
                job.deduplicated ? `${label}: already queued or running.` : `${label}: queued.`)
            }
          >
            {label}
          </Button>
        ))}
      </div>
      {notice && <p className="mt-3 text-sm text-slate-600" role="status">{notice}</p>}
      {error && <p className="mt-3 text-sm text-red-700" role="alert">{error}</p>}
      {jobs !== null && jobs.length === 0 && (
        <p className="mt-3 text-sm text-slate-500">No jobs for this batch yet.</p>
      )}
      {jobs !== null && jobs.length > 0 && (
        <ul className="mt-4 divide-y divide-slate-100" aria-label="Jobs">
          {jobs.map((job) => (
            <li key={job.id} className="py-2.5 text-sm" aria-label={`Job ${LABELS[job.job_type] ?? job.job_type}`}>
              <div className="flex items-center justify-between gap-3">
                <span className="font-medium text-slate-800">{LABELS[job.job_type] ?? job.job_type}</span>
                <span className="text-xs text-slate-500">
                  {job.status}
                  {job.cancel_requested && ACTIVE.has(job.status) ? " (cancelling)" : ""}
                  {job.total_items !== null ? ` · ${job.done_items}/${job.total_items}` : ""}
                  {job.attempts > 1 ? ` · attempt ${job.attempts}` : ""}
                </span>
              </div>
              {job.total_items !== null && job.total_items > 0 && (
                <div
                  className="mt-1.5 h-1.5 rounded bg-slate-100"
                  role="progressbar"
                  aria-valuemin={0}
                  aria-valuemax={100}
                  aria-valuenow={job.progress_pct ?? 0}
                >
                  <div className="h-1.5 rounded bg-brand-500" style={{ width: `${job.progress_pct ?? 0}%` }} />
                </div>
              )}
              <div className="mt-1 text-xs text-slate-500">
                {["succeeded", "skipped", "blocked", "failed"]
                  .filter((k) => job.counts[k])
                  .map((k) => `${k} ${job.counts[k]}`)
                  .join(" · ")}
              </div>
              {job.last_error && <div className="mt-1 text-xs text-red-700">{job.last_error}</div>}
              {ACTIVE.has(job.status) && !job.cancel_requested && (
                <Button
                  variant="ghost"
                  size="sm"
                  loading={busy === job.id}
                  disabled={busy !== null}
                  onClick={() => act(job.id, () => cancelJob(job.id), () => "Cancellation requested.")}
                >
                  Cancel
                </Button>
              )}
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
