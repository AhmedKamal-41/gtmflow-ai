import Link from "next/link";

import type { Lead, LeadFitScore, LeadScore } from "@/types/api";

import { Card } from "./Card";
import { FitBandBadge } from "./FitBandBadge";
import { Icon } from "./Icon";
import { PriorityBadge } from "./PriorityBadge";
import { StatusBadge } from "./StatusBadge";

type Props = {
  leads: Lead[];
  scores: Record<string, LeadScore | null>;
  // v2 deterministic company-fit scorer (Phase 4) -- optional and separate
  // from `scores`/`LeadScore` above (the legacy v1 Hot/Warm/Cold scorer).
  // Omitted keys render as "Not fit-scored", never inferred from `scores`.
  fitScores?: Record<string, LeadFitScore | undefined>;
};

export function LeadTable({ leads, scores, fitScores = {} }: Props) {
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
              <th className="px-4 py-3">Title</th>
              <th className="px-4 py-3">Status</th>
              <th className="px-4 py-3 text-right">Score</th>
              <th className="px-4 py-3">Priority</th>
              <th className="px-4 py-3">Fit (v2 demo)</th>
              <th className="px-4 py-3"></th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {leads.map((lead) => {
              const score = scores[lead.id];
              const fit = fitScores[lead.id];
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
                  <td className="px-4 py-3 text-slate-600">
                    {lead.contact_title ?? "-"}
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
                      <span className="inline-flex items-center gap-1.5">
                        <FitBandBadge band={fit.band} />
                        <span className="tabular text-xs text-slate-400">
                          {fit.fit_score}/{fit.max_fit_score}
                        </span>
                      </span>
                    ) : (
                      <span className="text-xs text-slate-400">Not fit-scored</span>
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
