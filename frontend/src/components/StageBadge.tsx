import type { LeadStage } from "@/lib/api";

// Where a lead is in the rep's workflow (computed by GET /api/inbox).
export const STAGE_LABELS: Record<LeadStage, string> = {
  needs_score: "Not scored",
  needs_draft: "Needs a draft",
  to_review: "To review",
  outdated: "Draft out of date",
  rejected: "Draft rejected",
  approved: "Ready to send",
  sent: "Sent",
};

const STYLES: Record<LeadStage, string> = {
  needs_score: "bg-slate-100 text-slate-600 ring-slate-500/20",
  needs_draft: "bg-slate-100 text-slate-700 ring-slate-500/20",
  to_review: "bg-amber-50 text-amber-800 ring-amber-600/20",
  outdated: "bg-orange-50 text-orange-800 ring-orange-600/20",
  rejected: "bg-red-50 text-red-700 ring-red-600/20",
  approved: "bg-emerald-50 text-emerald-700 ring-emerald-600/20",
  sent: "bg-brand-50 text-brand-700 ring-brand-600/20",
};

export function StageBadge({ stage }: { stage: LeadStage }) {
  return (
    <span className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-semibold ring-1 ring-inset ${STYLES[stage]}`}>
      {STAGE_LABELS[stage]}
    </span>
  );
}
