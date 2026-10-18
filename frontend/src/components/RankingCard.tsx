import type { CriteriaSummary, FitCriterion, HistoricalAssessment, LeadFitScore } from "@/types/api";

import { RankingBadge } from "./PriorityBadge";

const RESULT_LABEL: Record<string, string> = {
  match: "Matches",
  partial: "Partly matches",
  mismatch: "Doesn't match",
  unknown: "No data",
  not_configured: "No preference set",
};

const RESULT_CLS: Record<string, string> = {
  match: "text-emerald-700",
  partial: "text-amber-700",
  mismatch: "text-slate-700",
  unknown: "text-violet-700",
  not_configured: "text-slate-400",
};

function label(c: FitCriterion): string {
  return c.label ?? c.name.replace(/_/g, " ");
}

/**
 * Why a lead ranks where it does: the score and priority against the active
 * seller profile's target customers, then each criterion as a match, a
 * mismatch, or missing information (kept apart -- missing data is not a
 * mismatch). A ranking describes recorded company attributes only; it never
 * says the company needs the product or intends to buy.
 */
export function RankingCard({ fit }: { fit: LeadFitScore }) {
  const configured = fit.criteria.filter((c) => c.active);
  const notConfigured = fit.criteria.filter((c) => !c.active);
  return (
    <div className="space-y-4">
      {!fit.is_current && (
        <p role="status" className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
          Outdated: this ranking was made against different target customers (seller profile version{" "}
          {fit.profile_version}). Recalculate to rank this lead against the active profile.
        </p>
      )}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-baseline gap-1">
          <span className="tabular text-4xl font-bold text-slate-900">{fit.fit_score}</span>
          <span className="text-base font-medium text-slate-400">/ {fit.max_fit_score}</span>
        </div>
        <RankingBadge priority={fit.priority} band={fit.band} />
      </div>
      <p className="text-sm text-slate-700">{fit.explanation}</p>
      <SummaryLine summary={fit.summary} />

      <ul className="divide-y divide-slate-100 rounded-lg border border-slate-200">
        {configured.map((c) => (
          <li key={c.name} className="p-3 text-sm">
            <div className="flex items-baseline justify-between gap-2">
              <span className="font-medium text-slate-900">{label(c)}</span>
              <span className={`font-medium ${RESULT_CLS[c.result] ?? "text-slate-600"}`}>
                {RESULT_LABEL[c.result] ?? c.result}
                <span className="tabular ml-2 text-xs font-normal text-slate-400" title="Weight earned of this criterion's weight">
                  {c.points}/{c.weight}
                </span>
              </span>
            </div>
            <p className="mt-0.5 text-slate-600">{c.explanation}</p>
          </li>
        ))}
      </ul>
      {notConfigured.length > 0 && (
        <p className="text-xs text-slate-500">
          Not part of this ranking (no preference in the seller profile):{" "}
          {notConfigured.map(label).join(", ").toLowerCase()}.
        </p>
      )}

      <p className="rounded-lg bg-slate-50 p-3 text-xs leading-relaxed text-slate-600">
        Score = weight earned ÷ weight of the criteria your profile sets, × 100 (evidence coverage{" "}
        {fit.evidence_coverage_pct.toFixed(0)}%). Missing information earns nothing but is not counted as a
        mismatch; under 50% coverage a lead is marked “Needs data” whatever its score. This compares recorded
        company facts with who you want to sell to — it is not evidence that the company needs your product or
        plans to buy, and it never makes a lead contactable.
      </p>

      <details className="text-xs text-slate-500">
        <summary className="cursor-pointer font-medium text-slate-600">Ranking details</summary>
        <div className="mt-2 space-y-1">
          <div>
            Seller profile version {fit.profile_version} · targets{" "}
            <span className="font-mono">{fit.targeting_hash?.slice(0, 12) ?? "none"}</span>
          </div>
          <div>
            scorer {fit.scorer_version} · normalization {fit.normalization_version} · computed{" "}
            {new Date(fit.computed_at).toLocaleString()}
          </div>
          {fit.at_scoring && <AtScoringSnapshot snapshot={fit.at_scoring} />}
        </div>
      </details>
    </div>
  );
}

function SummaryLine({ summary }: { summary: CriteriaSummary }) {
  const parts: [string, string[], string][] = [
    ["Matches", [...summary.matched, ...summary.partial.map((p) => `${p} (partly)`)], "text-emerald-700"],
    ["Doesn't match", summary.mismatched, "text-slate-700"],
    ["Missing information", summary.missing, "text-violet-700"],
  ];
  return (
    <dl className="grid gap-2 text-sm">
      {parts.map(([title, items, cls]) => (
        <div key={title} className="rounded-lg border border-slate-200 p-2">
          <dt className={`text-xs font-semibold uppercase tracking-wide ${cls}`}>{title}</dt>
          <dd className="mt-1 text-slate-700">{items.length ? items.join(", ") : "None"}</dd>
        </div>
      ))}
    </dl>
  );
}

/** Readiness/eligibility when THIS ranking was computed -- audit only. */
function AtScoringSnapshot({ snapshot }: { snapshot: HistoricalAssessment }) {
  const gaps = snapshot.readiness.outbound_email.gaps;
  return (
    <div className="rounded border border-dashed border-slate-200 p-2">
      At ranking time (historical, {new Date(snapshot.computed_at).toLocaleString()}): routing{" "}
      {snapshot.eligibility_excluded ? `excluded (${snapshot.eligibility_reasons.join("; ")})` : "not excluded"};
      outbound email {snapshot.readiness.outbound_email.status}
      {gaps.length > 0 && ` (gaps: ${gaps.join(", ")})`}. Current readiness is shown separately.
    </div>
  );
}
