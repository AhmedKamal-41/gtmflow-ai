"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { Button } from "@/components/Button";
import { Card } from "@/components/Card";
import { Donut, Funnel, Gauge } from "@/components/charts";
import { ErrorMessage } from "@/components/ErrorMessage";
import { Icon } from "@/components/Icon";
import { LoadingState } from "@/components/LoadingState";
import { PageHeader } from "@/components/PageHeader";
import { StatCard } from "@/components/StatCard";
import { APIError, getMetricsDashboard, runDemo } from "@/lib/api";
import type { MetricsDashboard } from "@/types/api";

export default function MetricsPage() {
  const router = useRouter();
  const [metrics, setMetrics] = useState<MetricsDashboard | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [seeding, setSeeding] = useState(false);

  const load = useCallback(async () => {
    setError(null);
    try {
      setMetrics(await getMetricsDashboard());
    } catch (e) {
      setError(
        e instanceof APIError
          ? (e.detail ?? e.message)
          : "Failed to load metrics",
      );
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function handleSeed() {
    setSeeding(true);
    setError(null);
    try {
      await runDemo();
      await load();
    } catch (e) {
      setError(
        e instanceof APIError ? (e.detail ?? e.message) : "Failed to run demo",
      );
    } finally {
      setSeeding(false);
    }
  }

  const isEmpty = metrics != null && metrics.total_leads_uploaded === 0;

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Adoption & ROI"
        title="Metrics"
        description="A quick read on how the tool is being used: how many leads got scored, how many drafts you approved, how many went to Slack, and roughly how much time that saved. The time figure is a rough estimate (about 5 minutes a lead), not real revenue."
        actions={
          metrics && !isEmpty ? (
            <Button variant="secondary" icon="refresh" onClick={load}>
              Refresh
            </Button>
          ) : undefined
        }
      />

      {error && <ErrorMessage>{error}</ErrorMessage>}
      {!metrics && !error && <LoadingState text="Loading metrics…" />}

      {isEmpty && (
        <Card>
          <div className="flex flex-col items-center gap-3 py-10 text-center">
            <span className="grid h-12 w-12 place-items-center rounded-xl bg-brand-50 text-brand-600">
              <Icon name="chart" className="h-6 w-6" />
            </span>
            <div className="text-lg font-semibold text-slate-900">
              No data yet
            </div>
            <p className="max-w-sm text-sm text-slate-600">
              Run the demo to load a sample list and fill in these numbers.
            </p>
            <Button icon="play" loading={seeding} onClick={handleSeed}>
              Run the demo
            </Button>
          </div>
        </Card>
      )}

      {metrics && !isEmpty && <Dashboard m={metrics} router={router} />}
    </div>
  );
}

function Dashboard({
  m,
  router,
}: {
  m: MetricsDashboard;
  router: ReturnType<typeof useRouter>;
}) {
  return (
    <div className="space-y-6">
      {/* Headline stats */}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatCard
          label="Total leads"
          value={m.total_leads_uploaded}
          icon="layers"
          tone="brand"
        />
        <StatCard
          label="Processed"
          value={m.total_leads_processed}
          hint={`${m.automation_coverage}% automation coverage`}
          icon="target"
        />
        <StatCard
          label="Avg lead score"
          value={m.average_lead_score}
          hint="out of 100"
          icon="chart"
        />
        <StatCard
          label="Hours saved (est.)"
          value={m.estimated_time_saved_hours}
          hint={`${m.estimated_time_saved_minutes} minutes`}
          icon="clock"
          tone="positive"
        />
      </div>

      {/* Funnel + priority donut */}
      <div className="grid gap-6 lg:grid-cols-3">
        <Card
          title="Pipeline funnel"
          subtitle="Each stage as a share of leads uploaded"
          icon="chart"
          className="lg:col-span-2"
        >
          <Funnel
            stages={[
              { label: "Uploaded", value: m.total_leads_uploaded, color: "#1d4ed8" },
              { label: "Scored", value: m.total_leads_processed, color: "#2563eb" },
              { label: "Outreach generated", value: m.outreach_generated, color: "#3b82f6" },
              { label: "Approved", value: m.outreach_approved, color: "#60a5fa" },
              { label: "Pushed (unique)", value: m.unique_leads_pushed, color: "#10b981" },
            ]}
          />
        </Card>

        <Card title="Priority split" icon="flame">
          <div className="flex justify-center py-2">
            <Donut
              centerValue={m.total_leads_processed}
              centerLabel="Scored"
              segments={[
                { label: "Hot", value: m.hot_leads, color: "#ef4444" },
                { label: "Warm", value: m.warm_leads, color: "#f59e0b" },
                { label: "Cold", value: m.cold_leads, color: "#94a3b8" },
              ]}
            />
          </div>
        </Card>
      </div>

      {/* Gauges + time saved */}
      <div className="grid gap-6 lg:grid-cols-3">
        <Card title="Quality rates" icon="check" className="lg:col-span-2">
          <div className="flex flex-wrap items-center justify-around gap-6 py-3">
            <Gauge percent={m.approval_rate} label="Approval rate" color="#10b981" />
            <Gauge
              percent={m.push_success_rate}
              label="Push success"
              color="#10b981"
            />
            <Gauge
              percent={m.automation_coverage}
              label="Automation"
              color="#2563eb"
            />
          </div>
        </Card>

        <Card title="Estimated time saved" icon="clock">
          <div className="flex h-full flex-col justify-center py-2">
            <div className="tabular text-4xl font-bold text-emerald-600">
              {m.estimated_time_saved_hours}
              <span className="ml-1.5 text-lg font-medium text-slate-400">
                hrs
              </span>
            </div>
            <div className="tabular mt-1 text-sm text-slate-500">
              {m.estimated_time_saved_minutes} minutes across{" "}
              {m.total_leads_processed} processed leads
            </div>
            <div className="mt-3 rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-800 ring-1 ring-inset ring-amber-600/20">
              Demo estimate: about 5 min per processed lead, not measured rep
              time.
            </div>
          </div>
        </Card>
      </div>

      {/* Detail stats */}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatCard label="Outreach generated" value={m.outreach_generated} icon="sparkles" />
        <StatCard
          label="Approved"
          value={m.outreach_approved}
          tone="positive"
          icon="check"
        />
        <StatCard
          label="Rejected"
          value={m.outreach_rejected}
          tone={m.outreach_rejected > 0 ? "danger" : "default"}
          icon="x"
        />
        <StatCard label="Approval rate" value={`${m.approval_rate}%`} icon="target" />
        <StatCard label="Pushed (rows)" value={m.leads_pushed} icon="send" />
        <StatCard label="Unique leads pushed" value={m.unique_leads_pushed} icon="send" />
        <StatCard
          label="Failed pushes"
          value={m.failed_push_count}
          tone={m.failed_push_count > 0 ? "danger" : "default"}
          icon="alert"
        />
        <StatCard
          label="Missing-data rate"
          value={`${m.missing_data_rate}%`}
          tone={m.missing_data_rate > 50 ? "warning" : "default"}
          icon="file"
        />
      </div>

      {/* How to read */}
      <div className="flex items-start gap-2.5 rounded-xl border border-slate-200 bg-slate-50 p-4 text-sm text-slate-600">
        <Icon name="alert" className="h-4 w-4 flex-none translate-y-0.5 text-slate-400" />
        <p>
          <span className="font-semibold text-slate-700">How to read this:</span>{" "}
          <em>Pushed (rows)</em> counts every successful push, so a lead pushed
          twice contributes 2. <em>Unique leads pushed</em> deduplicates by
          lead. Approval, push-success, and missing-data are true ratios of the
          rows in this database (divide-by-zero returns 0%). Numbers refresh on
          load and when you press Refresh.
        </p>
      </div>

      <div>
        <Button
          variant="ghost"
          icon="arrow-right"
          onClick={() => router.push("/batches")}
        >
          View batches
        </Button>
      </div>
    </div>
  );
}
