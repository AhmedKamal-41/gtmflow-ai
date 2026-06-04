"use client";

import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { AIOutputCard } from "@/components/AIOutputCard";
import { Button } from "@/components/Button";
import { Card } from "@/components/Card";
import { ErrorMessage } from "@/components/ErrorMessage";
import { Icon } from "@/components/Icon";
import { LoadingState } from "@/components/LoadingState";
import { PageHeader } from "@/components/PageHeader";
import { PushHistory } from "@/components/PushHistory";
import { ScoreBreakdown } from "@/components/ScoreBreakdown";
import { StatusBadge } from "@/components/StatusBadge";
import {
  APIError,
  approveOutreach,
  generateOutreach,
  generateSummary,
  getAIOutputs,
  getLead,
  getLeadScore,
  getPushes,
  pushLead,
  rejectOutreach,
  scoreLead,
} from "@/lib/api";
import type {
  AIOutput,
  IntegrationPush,
  Lead,
  LeadScore,
} from "@/types/api";

type ActionLabel =
  | "Score"
  | "Summary"
  | "Outreach"
  | "Push"
  | "Approve"
  | "Reject"
  | "Refresh"
  | null;

export default function LeadDetailPage() {
  const params = useParams<{ leadId: string }>();
  const leadId = params?.leadId ?? "";

  const [lead, setLead] = useState<Lead | null>(null);
  const [score, setScore] = useState<LeadScore | null>(null);
  const [aiOutputs, setAIOutputs] = useState<AIOutput[]>([]);
  const [pushes, setPushes] = useState<IntegrationPush[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [busy, setBusy] = useState<ActionLabel>(null);

  const load = useCallback(async () => {
    if (!leadId) return;
    setError(null);
    try {
      const [leadData, outputsData, pushesData] = await Promise.all([
        getLead(leadId),
        getAIOutputs(leadId).catch(() => []),
        getPushes(leadId).catch(() => []),
      ]);
      setLead(leadData);
      setAIOutputs(outputsData);
      setPushes(pushesData);

      try {
        setScore(await getLeadScore(leadId));
      } catch (e) {
        if (e instanceof APIError && e.status === 404) {
          setScore(null);
        } else {
          throw e;
        }
      }
    } catch (e) {
      setError(
        e instanceof APIError ? (e.detail ?? e.message) : "Failed to load lead",
      );
    }
  }, [leadId]);

  useEffect(() => {
    void load();
  }, [load]);

  async function runAction(
    label: Exclude<ActionLabel, null>,
    fn: () => Promise<unknown>,
  ) {
    setBusy(label);
    setError(null);
    setInfo(null);
    try {
      await fn();
      setInfo(`${label} complete.`);
      await load();
    } catch (e) {
      setError(
        e instanceof APIError ? (e.detail ?? e.message) : `${label} failed.`,
      );
    } finally {
      setBusy(null);
    }
  }

  if (!lead) {
    return error ? <ErrorMessage>{error}</ErrorMessage> : <LoadingState />;
  }

  const pushForce = score?.priority !== "Hot";
  const hasOutreach = aiOutputs.some((o) => o.output_type === "outreach_email");

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Lead workspace"
        title={lead.company_name}
        back={{ href: `/batches/${lead.batch_id}`, label: "Back to batch" }}
        description={
          <span className="flex flex-wrap items-center gap-2">
            <StatusBadge status={lead.status} />
            {lead.industry && (
              <span className="text-slate-500">· {lead.industry}</span>
            )}
            {lead.location && (
              <span className="text-slate-500">· {lead.location}</span>
            )}
          </span>
        }
      />

      <div className="flex flex-wrap gap-2">
        <Button
          loading={busy === "Score"}
          onClick={() => runAction("Score", () => scoreLead(leadId))}
        >
          Score lead
        </Button>
        <Button
          variant="secondary"
          loading={busy === "Summary"}
          onClick={() => runAction("Summary", () => generateSummary(leadId))}
        >
          Generate summary
        </Button>
        <Button
          variant="secondary"
          loading={busy === "Outreach"}
          onClick={() => runAction("Outreach", () => generateOutreach(leadId))}
        >
          Generate outreach
        </Button>
        {hasOutreach && (
          <>
            <Button
              variant="secondary"
              loading={busy === "Approve"}
              onClick={() =>
                runAction("Approve", () => approveOutreach(leadId))
              }
            >
              Approve outreach
            </Button>
            <Button
              variant="danger"
              loading={busy === "Reject"}
              onClick={() => {
                const reason =
                  typeof window !== "undefined"
                    ? window.prompt("Reason for rejecting? (optional)")
                    : null;
                if (reason === null) return;
                void runAction("Reject", () =>
                  rejectOutreach(leadId, reason || undefined),
                );
              }}
            >
              Reject outreach
            </Button>
          </>
        )}
        <Button
          variant="primary"
          loading={busy === "Push"}
          onClick={() => runAction("Push", () => pushLead(leadId, pushForce))}
        >
          Push to Slack
        </Button>
        <Button
          variant="ghost"
          loading={busy === "Refresh"}
          onClick={() => runAction("Refresh", async () => undefined)}
        >
          Refresh
        </Button>
      </div>

      {info && (
        <div className="flex items-center gap-2.5 rounded-lg border border-brand-200 bg-brand-50 p-3.5 text-sm text-brand-900">
          <Icon name="check" className="h-4 w-4 flex-none text-brand-600" />
          {info}
        </div>
      )}
      {error && <ErrorMessage>{error}</ErrorMessage>}

      <Card title="Lead details" icon="file">
        <dl className="grid grid-cols-1 gap-x-6 gap-y-3 sm:grid-cols-2">
          <Field label="Company" value={lead.company_name} />
          <Field label="Website" value={lead.website} />
          <Field label="Industry" value={lead.industry} />
          <Field label="Contact name" value={lead.contact_name} />
          <Field label="Contact title" value={lead.contact_title} />
          <Field label="Contact email" value={lead.contact_email} />
          <Field label="Company size" value={lead.company_size} />
          <Field label="Location" value={lead.location} />
          <Field label="Source" value={lead.source} />
        </dl>
        {lead.cleaned_data &&
          Object.keys(lead.cleaned_data).length > 0 && (
            <div className="mt-4 border-t border-slate-200 pt-4">
              <div className="text-sm font-medium text-slate-700">
                Extra fields from CSV
              </div>
              <dl className="mt-2 grid grid-cols-1 gap-x-6 gap-y-2 sm:grid-cols-2">
                {Object.entries(lead.cleaned_data).map(([k, v]) => (
                  <Field key={k} label={k} value={stringify(v)} />
                ))}
              </dl>
            </div>
          )}
      </Card>

      <section>
        <h2 className="mb-3 text-xl font-semibold text-slate-900">Score</h2>
        {score ? (
          <ScoreBreakdown score={score} />
        ) : (
          <Card>
            <div className="text-sm text-slate-600">
              Not scored yet. Run <em>Score lead</em> above.
            </div>
          </Card>
        )}
      </section>

      <section>
        <h2 className="mb-3 text-xl font-semibold text-slate-900">
          AI outputs
        </h2>
        {aiOutputs.length === 0 ? (
          <Card>
            <div className="text-sm text-slate-600">
              No AI outputs yet. Generate a summary or outreach above.
            </div>
          </Card>
        ) : (
          <div className="space-y-3">
            {aiOutputs.map((o) => (
              <AIOutputCard key={o.id} output={o} />
            ))}
          </div>
        )}
      </section>

      <section>
        <h2 className="mb-3 text-xl font-semibold text-slate-900">
          Push history
        </h2>
        {pushes.length === 0 ? (
          <Card>
            <div className="text-sm text-slate-600">Not pushed yet.</div>
          </Card>
        ) : (
          <PushHistory pushes={pushes} />
        )}
      </section>
    </div>
  );
}

function Field({
  label,
  value,
}: {
  label: string;
  value: string | null | undefined;
}) {
  const display = value && String(value).length > 0 ? value : null;
  return (
    <div>
      <dt className="text-xs uppercase tracking-wide text-slate-500">
        {label}
      </dt>
      <dd className="text-sm text-slate-900">
        {display ?? <span className="text-slate-400">-</span>}
      </dd>
    </div>
  );
}

function stringify(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "string") return value;
  return JSON.stringify(value);
}
