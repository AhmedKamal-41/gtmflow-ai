import type { HistoricalAssessment, LeadFitScore } from "@/types/api";

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
 * Phase 4 Part E: renders the v2 deterministic company-fit scorer's stored
 * result (fit, evidence coverage, per-criterion explanation, versions, and
 * the historical readiness snapshot taken at scoring time) -- deliberately
 * separate from <ScoreBreakdown> (the legacy v1 Hot/Warm/Cold scorer) and
 * from <CurrentReadinessCard>. A `strong_match` band is a broad-demonstration-
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
        the legacy Hot/Warm/Cold priority, and a high fit score does not
        mean this company is contactable (see current readiness &amp;
        eligibility).
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

      {fit.at_scoring && <AtScoringSnapshot snapshot={fit.at_scoring} />}

      <div className="mt-4 flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-slate-100 pt-3 text-xs text-slate-400">
        <span>
          scorer {fit.scorer_version} · normalization {fit.normalization_version}
        </span>
        <span>·</span>
        <span>computed {new Date(fit.computed_at).toLocaleString()}</span>
      </div>
    </Card>
  );
}

/**
 * What readiness/eligibility were when THIS score row was computed. Shown
 * only as an audit trail, collapsed and labeled historical -- the current
 * values live in <CurrentReadinessCard>, and only those gate actions.
 */
function AtScoringSnapshot({ snapshot }: { snapshot: HistoricalAssessment }) {
  const gaps = snapshot.readiness.outbound_email.gaps;
  return (
    <details className="mt-5 rounded-lg border border-dashed border-slate-200 p-3 text-xs text-slate-500">
      <summary className="cursor-pointer font-medium text-slate-600">
        At scoring time (historical, {new Date(snapshot.computed_at).toLocaleString()})
      </summary>
      <div className="mt-2 space-y-1">
        <div>
          Routing:{" "}
          {snapshot.eligibility_excluded
            ? `excluded (${snapshot.eligibility_reasons.join("; ")})`
            : "not excluded"}
        </div>
        <div>
          Outbound email: {snapshot.readiness.outbound_email.status}
          {gaps.length > 0 && ` -- gaps: ${gaps.join(", ")}`}
        </div>
        <div>
          Not current: see <em>Current readiness &amp; eligibility</em> for
          what applies now.
        </div>
      </div>
    </details>
  );
}
