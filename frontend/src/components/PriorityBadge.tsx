import { Icon } from "./Icon";

const STYLES: Record<string, { cls: string; dot: string }> = {
  Hot: { cls: "bg-red-50 text-red-700 ring-red-600/20", dot: "bg-red-500" },
  Warm: {
    cls: "bg-amber-50 text-amber-800 ring-amber-600/20",
    dot: "bg-amber-500",
  },
  Cold: {
    cls: "bg-slate-100 text-slate-600 ring-slate-500/20",
    dot: "bg-slate-400",
  },
};

const FALLBACK = {
  cls: "bg-slate-100 text-slate-600 ring-slate-500/20",
  dot: "bg-slate-400",
};

export function PriorityBadge({ priority }: { priority: string }) {
  const style = STYLES[priority] ?? FALLBACK;
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-semibold ring-1 ring-inset ${style.cls}`}
    >
      {priority === "Hot" ? (
        <Icon name="flame" className="h-3.5 w-3.5" filled />
      ) : (
        <span className={`h-1.5 w-1.5 rounded-full ${style.dot}`} />
      )}
      {priority}
    </span>
  );
}

/**
 * A lead's current ranking against the active seller profile: Hot / Warm /
 * Cold, "Needs data" when too little of the record could be compared, or
 * "Not ranked" (never ranked, or only against earlier target preferences).
 */
export function RankingBadge({ priority, band, outdated = false }: {
  priority: string | null | undefined;
  band: string | null | undefined;
  outdated?: boolean;
}) {
  if (priority) return <PriorityBadge priority={priority} />;
  const label = band ? "Needs data" : outdated ? "Outdated" : "Not ranked";
  const cls = band
    ? "bg-violet-50 text-violet-700 ring-violet-600/20"
    : "bg-white text-slate-500 ring-slate-300";
  return (
    <span
      title={band ? "Too little of this company's record matches what the seller profile asks about to rank it confidently." : undefined}
      className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-semibold ring-1 ring-inset ${cls}`}
    >
      {label}
    </span>
  );
}
