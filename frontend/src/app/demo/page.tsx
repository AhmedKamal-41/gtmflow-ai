"use client";

import Link from "next/link";
import { useState } from "react";

import { Button } from "@/components/Button";
import { Card } from "@/components/Card";
import { ErrorMessage } from "@/components/ErrorMessage";
import { Icon, type IconName } from "@/components/Icon";
import { PageHeader } from "@/components/PageHeader";
import { APIError, runDemo } from "@/lib/api";
import type { DemoRunResponse } from "@/types/api";

const STEPS: { title: string; body: string; icon: IconName }[] = [
  {
    title: "Upload the sample CSV",
    body: "10 fictional leads tuned to score 2 Hot / 4 Warm / 4 Cold.",
    icon: "upload",
  },
  {
    title: "Score the batch",
    body: "The deterministic 100-point model fills in priority bands.",
    icon: "target",
  },
  {
    title: "Generate AI summary + outreach",
    body: "Mock-by-default: summary (evidence vs inference), then outreach.",
    icon: "sparkles",
  },
  {
    title: "Approve the drafts",
    body: "Approving is what the metrics count, so the numbers reflect real decisions.",
    icon: "check",
  },
  {
    title: "Send hot leads to Slack",
    body: "Goes to your Slack channel (or a safe mock if no webhook is set), and every send is logged.",
    icon: "send",
  },
  {
    title: "Read the metrics",
    body: "Adoption, approval rate, push success, and estimated time saved.",
    icon: "chart",
  },
];

export default function DemoPage() {
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<DemoRunResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function handleRun() {
    setRunning(true);
    setError(null);
    setResult(null);
    try {
      setResult(await runDemo());
    } catch (e) {
      setError(
        e instanceof APIError ? (e.detail ?? e.message) : "Failed to run demo",
      );
    } finally {
      setRunning(false);
    }
  }

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Guided demo"
        title="See it work in one click"
        description="Loads a sample lead list and runs the whole thing for you: scoring, drafting, approving, and sending, then takes you to the results. Each run adds a fresh batch and leaves your existing data alone."
      />

      {/* One-click runner */}
      <Card>
        <div className="flex flex-col items-start gap-5 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex items-start gap-3">
            <span className="grid h-11 w-11 flex-none place-items-center rounded-xl bg-gradient-to-br from-brand-500 to-brand-700 text-white shadow-sm">
              <Icon name="play" className="h-5 w-5" filled />
            </span>
            <div>
              <div className="text-base font-semibold text-slate-900">
                One-click demo
              </div>
              <p className="mt-0.5 text-sm text-slate-600">
                Runs the whole thing against a sample list. Takes a second.
              </p>
            </div>
          </div>
          <Button
            size="lg"
            icon="play"
            loading={running}
            onClick={handleRun}
          >
            {running ? "Running…" : "Run the full demo"}
          </Button>
        </div>

        {error && (
          <div className="mt-4">
            <ErrorMessage>{error}</ErrorMessage>
          </div>
        )}

        {result && (
          <div className="mt-5 animate-fade-in rounded-xl border border-emerald-200 bg-emerald-50/60 p-5">
            <div className="flex items-center gap-2 text-sm font-semibold text-emerald-800">
              <Icon name="check" className="h-4 w-4" />
              Demo complete: {result.batch_name}
            </div>
            <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
              <Result label="Leads" value={result.total_leads} />
              <Result label="Hot" value={result.hot} accent="text-red-600" />
              <Result label="Approved" value={result.outreach_approved} accent="text-emerald-600" />
              <Result label="Pushed" value={result.leads_pushed} accent="text-brand-600" />
            </div>
            <div className="mt-5 flex flex-wrap gap-3">
              <Link
                href={`/batches/${result.batch_id}`}
                className="inline-flex items-center gap-2 rounded-lg bg-brand-600 px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-brand-700"
              >
                <Icon name="layers" className="h-4 w-4" />
                Open the seeded batch
              </Link>
              <Link
                href="/metrics"
                className="inline-flex items-center gap-2 rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm font-semibold text-slate-800 transition-colors hover:bg-slate-50"
              >
                <Icon name="chart" className="h-4 w-4" />
                View metrics
              </Link>
            </div>
          </div>
        )}
      </Card>

      {/* What it does, step by step */}
      <div>
        <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-500">
          What runs under the hood
        </h2>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {STEPS.map((s, i) => (
            <div
              key={s.title}
              className="flex gap-3 rounded-xl border border-slate-200 bg-white p-4 shadow-card"
            >
              <span className="grid h-9 w-9 flex-none place-items-center rounded-lg bg-brand-50 text-brand-600">
                <Icon name={s.icon} className="h-4 w-4" />
              </span>
              <div>
                <div className="flex items-center gap-1.5 text-sm font-semibold text-slate-900">
                  <span className="tabular text-slate-300">0{i + 1}</span>
                  {s.title}
                </div>
                <p className="mt-1 text-sm leading-relaxed text-slate-600">
                  {s.body}
                </p>
              </div>
            </div>
          ))}
        </div>
      </div>

      <p className="text-sm text-slate-500">
        Prefer to drive it yourself? Start at{" "}
        <Link href="/upload" className="font-medium text-brand-600 hover:underline">
          Upload
        </Link>{" "}
        with <code className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-xs">sample_data/leads_sample.csv</code>.
        Full interview script lives in <code className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-xs">docs/demo-script.md</code>.
      </p>
    </div>
  );
}

function Result({
  label,
  value,
  accent = "text-slate-900",
}: {
  label: string;
  value: number;
  accent?: string;
}) {
  return (
    <div className="rounded-lg bg-white p-3 ring-1 ring-inset ring-emerald-200/60">
      <div className="text-xs font-medium uppercase tracking-wide text-slate-500">
        {label}
      </div>
      <div className={`tabular mt-1 text-2xl font-bold ${accent}`}>{value}</div>
    </div>
  );
}
