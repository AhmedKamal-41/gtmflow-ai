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
