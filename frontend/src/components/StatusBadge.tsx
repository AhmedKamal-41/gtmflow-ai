// Maps a workflow status to a calm, consistent pill drawn from the shared
// palette only: brand (blue), emerald (success), red (danger), slate (neutral).
// Underscored statuses (outreach_approved) render as readable words.

const STYLES: Record<string, { cls: string; dot: string }> = {
  new: { cls: "bg-slate-100 text-slate-600 ring-slate-500/20", dot: "bg-slate-400" },
  uploaded: {
    cls: "bg-slate-100 text-slate-600 ring-slate-500/20",
    dot: "bg-slate-400",
  },
  scored: {
    cls: "bg-brand-50 text-brand-700 ring-brand-600/20",
    dot: "bg-brand-500",
  },
  outreach_approved: {
    cls: "bg-emerald-50 text-emerald-700 ring-emerald-600/20",
    dot: "bg-emerald-500",
  },
  outreach_rejected: {
    cls: "bg-red-50 text-red-700 ring-red-600/20",
    dot: "bg-red-500",
  },
  pushed: {
    cls: "bg-emerald-50 text-emerald-700 ring-emerald-600/20",
    dot: "bg-emerald-500",
  },
  failed: { cls: "bg-red-50 text-red-700 ring-red-600/20", dot: "bg-red-500" },
};

const FALLBACK = {
  cls: "bg-slate-100 text-slate-600 ring-slate-500/20",
  dot: "bg-slate-400",
};

export function StatusBadge({ status }: { status: string }) {
  const style = STYLES[status] ?? FALLBACK;
  const label = status.replace(/_/g, " ");
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium capitalize ring-1 ring-inset ${style.cls}`}
    >
      <span className={`h-1.5 w-1.5 rounded-full ${style.dot}`} />
      {label}
    </span>
  );
}
