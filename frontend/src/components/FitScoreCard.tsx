import type { ActionReadiness, LeadFitScore } from "@/types/api";

import { Card } from "./Card";
import { FitBandBadge } from "./FitBandBadge";

const RESULT_LABEL: Record<string, string> = {
  match: "Match",
  mismatch: "Mismatch",
  unknown: "Unknown",
  not_configured: "Not configured",
};

const RESULT_CLS: Record<string, string> = {
  match: "text-emerald-700",
  mismatch: "text-slate-600",
  unknown: "text-amber-700",
  not_configured: "text-slate-400",
};

/**
 * Phase 4 Part E: renders the v2 deterministic company-fit scorer's full
 * output (fit, evidence coverage, readiness gaps, eligibility reasons,
 * versions) -- deliberately separate from <ScoreBreakdown> (the legacy v1
 * Hot/Warm/Cold scorer). A `strong_match` band is a broad-demonstration-
 * profile label, not a purchase-probability or "approved/contactable"
 * claim -- every render here says so explicitly rather than letting the
 * number speak for itself.
 */
export function FitScoreCard({ fit }: { fit: LeadFitScore }) {
  return (
    <Card>
      <div className="flex items-start justify-between gap-4">
        <div>
          <div className="flex items-baseline gap-1">
            <span className="tabular text-4xl font-bold text-slate-900">
              {fit.fit_score}
            </span>
            <span className="text-base font-medium text-slate-400">
              / {fit.max_fit_score}
            </span>
          </div>
          <p className="mt-1 text-sm text-slate-600">
            Evidence coverage: {fit.evidence_coverage_pct.toFixed(1)}%
          </p>
        </div>
        <FitBandBadge band={fit.band} />
      </div>

      <div className="mt-3 rounded-lg bg-slate-50 p-3 text-xs leading-relaxed text-slate-600">
        Broad demonstration profile (<code>{fit.profile_id}</code> v
        {fit.profile_version}) -- exact industry/country match only, not a
        calibrated ideal-customer profile. This is not the same signal as
        the legacy priority score above, and a high fit score does not mean
        this company is contactable (see eligibility below).
      </div>

      <div className="mt-5 overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs uppercase tracking-wide text-slate-500">
              <th className="pb-2 pr-3 font-medium">Criterion</th>
              <th className="pb-2 pr-3 font-medium">Result</th>
              <th className="pb-2 pr-3 font-medium">Points</th>
              <th className="pb-2 font-medium">Explanation</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {fit.criteria.map((c) => (
              <tr key={c.name}>
                <td className="py-2 pr-3 font-medium text-slate-900">
                  {c.name.replace(/_/g, " ")}
                </td>
                <td
                  className={`py-2 pr-3 font-medium ${RESULT_CLS[c.result] ?? "text-slate-600"}`}
                >
                  {RESULT_LABEL[c.result] ?? c.result}
                </td>
                <td className="tabular py-2 pr-3 text-slate-900">
                  {c.points} / {c.weight}
                </td>
                <td className="py-2 text-slate-500">{c.explanation}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {fit.eligibility.excluded && (
        <div className="mt-5 rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-800">
          <div className="font-semibold">Excluded from routing</div>
          <ul className="mt-1 list-inside list-disc space-y-0.5">
            {fit.eligibility.reasons.map((r) => (
              <li key={r}>{r}</li>
            ))}
          </ul>
          <div className="mt-1 text-xs text-red-700">
            Not overridable by fit score or force.
          </div>
        </div>
      )}

      <div className="mt-5 grid grid-cols-1 gap-4 sm:grid-cols-2">
        <ReadinessSection
          title="Outbound email readiness"
          readiness={fit.readiness.outbound_email}
        />
        <ReadinessSection
          title="Internal Slack handoff readiness"
          readiness={fit.readiness.internal_slack_handoff}
        />
      </div>

      <div className="mt-4 flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-slate-100 pt-3 text-xs text-slate-400">
        <span>
          scorer {fit.scorer_version} · normalization {fit.normalization_version}
        </span>
        <span>·</span>
        <span>computed {new Date(fit.computed_at).toLocaleString()}</span>
        <span>·</span>
        <span>
          readiness shown is{" "}
          {fit.readiness_is_current ? "current (live)" : "a stored snapshot from last scoring"}
        </span>
      </div>
    </Card>
  );
}

function ReadinessSection({
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
