import Link from "next/link";

import type {
  CurrentReadiness,
  Lead,
  LeadFitScore,
  LeadScore,
} from "@/types/api";

import { Card } from "./Card";
import { FitBandBadge } from "./FitBandBadge";
import { Icon } from "./Icon";
import { PriorityBadge } from "./PriorityBadge";
import { StatusBadge } from "./StatusBadge";

type Props = {
  leads: Lead[];
  // Legacy v1 Hot/Warm/Cold scorer. Missing key = not loaded yet;
  // null = loaded, never scored.
  scores: Record<string, LeadScore | null>;
  // v2 deterministic company-fit scorer (Phase 4) -- separate from
  // `scores`. Omitted keys render as "Not fit-scored", never inferred
  // from the legacy score.
  fitScores?: Record<string, LeadFitScore | undefined>;
  // Current readiness/eligibility (live state, independent of scoring).
  readiness?: Record<string, CurrentReadiness | undefined>;
};

const GAP_LABEL: Record<string, string> = {
  no_seller_profile_configured: "no seller profile",
  missing_contact_email: "no contact email",
  no_outreach_draft: "no draft",
  draft_not_reviewed: "draft not reviewed",
  draft_rejected: "draft rejected",
  lead_excluded_from_routing: "excluded",
};

export function LeadTable({
  leads,
  scores,
  fitScores = {},
  readiness = {},
}: Props) {
  if (leads.length === 0) {
    return (
      <Card>
        <div className="text-sm text-slate-600">No leads in this batch.</div>
      </Card>
    );
  }

  return (
    <Card padding="none" className="overflow-hidden">
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="border-b border-slate-200 bg-slate-50/80 text-left text-xs font-semibold uppercase tracking-wide text-slate-500">
            <tr>
              <th className="px-4 py-3">Company</th>
              <th className="px-4 py-3">Industry</th>
              <th className="px-4 py-3">Status</th>
              <th className="px-4 py-3 text-right">Legacy score (v1)</th>
              <th className="px-4 py-3">Legacy priority (v1)</th>
              <th className="px-4 py-3">Company fit (v2 demo)</th>
              <th className="px-4 py-3">Routing (current)</th>
              <th className="px-4 py-3">Email readiness (current)</th>
              <th className="px-4 py-3"></th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {leads.map((lead) => {
              const score = scores[lead.id];
              const fit = fitScores[lead.id];
              const current = readiness[lead.id];
              return (
                <tr
                  key={lead.id}
                  className="group transition-colors hover:bg-brand-50/40"
                >
                  <td className="px-4 py-3">
                    <Link
                      href={`/leads/${lead.id}`}
                      className="font-medium text-slate-900 group-hover:text-brand-700"
                    >
                      {lead.company_name}
                    </Link>
                  </td>
                  <td className="px-4 py-3 text-slate-600">
                    {lead.industry ?? "-"}
                  </td>
                  <td className="px-4 py-3">
                    <StatusBadge status={lead.status} />
                  </td>
                  <td className="tabular px-4 py-3 text-right font-semibold text-slate-900">
                    {score ? (
                      score.total_score
                    ) : (
                      <span className="font-normal text-slate-300">-</span>
                    )}
                  </td>
                  <td className="px-4 py-3">
                    {score ? (
                      <PriorityBadge priority={score.priority} />
                    ) : (
                      <span className="text-xs text-slate-400">Not scored</span>
                    )}
                  </td>
                  <td className="px-4 py-3">
                    {fit ? (
                      <div className="space-y-0.5">
                        <span className="inline-flex items-center gap-1.5">
                          <FitBandBadge band={fit.band} />
                          <span className="tabular text-xs text-slate-500">
                            {fit.fit_score}/{fit.max_fit_score}
                          </span>
                        </span>
                        <div className="tabular text-xs text-slate-400">
                          coverage {fit.evidence_coverage_pct.toFixed(0)}%
                        </div>
                      </div>
                    ) : (
                      <span className="text-xs text-slate-400">Not fit-scored</span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-xs">
                    {current ? (
                      current.eligibility.excluded ? (
                        <span
                          className="font-medium text-red-700"
                          title={current.eligibility.reasons.join("\n")}
                        >
                          Excluded: {current.eligibility.reasons.join("; ")}
                        </span>
                      ) : (
                        <span className="text-emerald-700">Not excluded</span>
                      )
                    ) : (
                      <span className="text-slate-300">-</span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-xs">
                    {current ? (
                      current.readiness.outbound_email.status === "ready" ? (
                        <span className="text-emerald-700">Ready</span>
                      ) : (
                        <span className="text-amber-700">
                          Not ready:{" "}
                          {current.readiness.outbound_email.gaps
                            .map((g) => GAP_LABEL[g] ?? g)
                            .join(", ")}
                        </span>
                      )
                    ) : (
                      <span className="text-slate-300">-</span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-right">
                    <Link
                      href={`/leads/${lead.id}`}
                      aria-label={`Open ${lead.company_name}`}
                      className="inline-flex items-center gap-1 text-sm font-medium text-brand-600 opacity-0 transition-opacity hover:text-brand-700 group-hover:opacity-100"
                    >
                      Open
                      <Icon name="arrow-right" className="h-3.5 w-3.5" />
                    </Link>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </Card>
  );
}
