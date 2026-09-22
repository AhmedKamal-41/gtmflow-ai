import type { ActionReadiness, Eligibility, Readiness } from "@/types/api";

import { Card } from "./Card";

/**
 * Phase 4: CURRENT outreach readiness and routing eligibility for a lead,
 * recomputed by the backend from live lead / batch / draft / review state
 * on every read. Deliberately separate from <FitScoreCard>: readiness is
 * not part of the fit score, applies to unscored leads too, and a strong
 * fit never makes a lead eligible or ready.
 */
export function CurrentReadinessCard({
  readiness,
  eligibility,
}: {
  readiness: Readiness;
  eligibility: Eligibility;
}) {
  return (
    <Card>
      {eligibility.excluded ? (
        <div className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-800">
          <div className="font-semibold">Excluded from routing</div>
          <ul className="mt-1 list-inside list-disc space-y-0.5">
            {eligibility.reasons.map((r) => (
              <li key={r}>{r}</li>
            ))}
          </ul>
          <div className="mt-1 text-xs text-red-700">
            Not overridable by fit score or force.
          </div>
        </div>
      ) : (
        <div className="rounded-lg border border-emerald-200 bg-emerald-50 p-3 text-sm text-emerald-800">
          No routing exclusion applies right now.
        </div>
      )}

      <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2">
        <ReadinessSection
          title="Outbound email readiness"
          readiness={readiness.outbound_email}
        />
        <ReadinessSection
          title="Internal Slack handoff readiness"
          readiness={readiness.internal_slack_handoff}
        />
      </div>

      <div className="mt-3 text-xs text-slate-400">
        Current state, checked {new Date(eligibility.checked_at).toLocaleString()}.
      </div>
    </Card>
  );
}

export function ReadinessSection({
  title,
  readiness,
}: {
  title: string;
  readiness: ActionReadiness;
}) {
  const ready = readiness.status === "ready";
  return (
    <div className="rounded-lg border border-slate-200 p-3">
      <div className="flex items-center justify-between">
        <span className="text-sm font-medium text-slate-700">{title}</span>
        <span
          className={`text-xs font-semibold ${ready ? "text-emerald-700" : "text-amber-700"}`}
        >
          {ready ? "Ready" : "Not ready"}
        </span>
      </div>
      {!ready && readiness.gaps.length > 0 && (
        <ul className="mt-2 space-y-1 text-xs text-slate-600">
          {readiness.gaps.map((gap) => (
            <li key={gap} className="flex gap-1.5">
              <span className="text-amber-500">•</span>
              <span>{readiness.gap_explanations[gap] ?? gap}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
