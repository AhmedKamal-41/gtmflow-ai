// Deliberately a different palette from PriorityBadge's Hot/Warm/Cold
// (red/amber/slate) -- a "strong_match" fit band is NOT the same claim as
// legacy "Hot" priority (see types/api.ts's LeadFitScore doc comment), and
// using a visually distinct palette keeps that from being read as the same
// signal at a glance.

const STYLES: Record<string, { cls: string; dot: string; label: string }> = {
  strong_match: {
    cls: "bg-indigo-50 text-indigo-700 ring-indigo-600/20",
    dot: "bg-indigo-500",
    label: "Strong match",
  },
  partial_match: {
    cls: "bg-sky-50 text-sky-700 ring-sky-600/20",
    dot: "bg-sky-500",
    label: "Partial match",
  },
  weak_match: {
    cls: "bg-slate-100 text-slate-600 ring-slate-500/20",
    dot: "bg-slate-400",
    label: "Weak match",
  },
  insufficient_evidence: {
    cls: "bg-violet-50 text-violet-700 ring-violet-600/20",
    dot: "bg-violet-400",
    label: "Insufficient evidence",
  },
};

const FALLBACK = {
  cls: "bg-slate-100 text-slate-600 ring-slate-500/20",
  dot: "bg-slate-400",
  label: "Unknown",
};

export function FitBandBadge({ band }: { band: string }) {
  const style = STYLES[band] ?? { ...FALLBACK, label: band.replace(/_/g, " ") };
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-semibold ring-1 ring-inset ${style.cls}`}
    >
      <span className={`h-1.5 w-1.5 rounded-full ${style.dot}`} />
      {style.label}
    </span>
  );
}
