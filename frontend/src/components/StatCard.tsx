import { Icon, type IconName } from "./Icon";

type Tone = "default" | "positive" | "warning" | "danger" | "brand";

const TONE_VALUE: Record<Tone, string> = {
  default: "text-slate-900",
  positive: "text-emerald-600",
  warning: "text-amber-600",
  danger: "text-red-600",
  brand: "text-brand-600",
};

const TONE_ICON: Record<Tone, string> = {
  default: "bg-slate-100 text-slate-500",
  positive: "bg-emerald-50 text-emerald-600",
  warning: "bg-amber-50 text-amber-600",
  danger: "bg-red-50 text-red-600",
  brand: "bg-brand-50 text-brand-600",
};

type Props = {
  label: string;
  value: number | string;
  hint?: string;
  icon?: IconName;
  tone?: Tone;
};

export function StatCard({ label, value, hint, icon, tone = "default" }: Props) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-card transition-shadow hover:shadow-card-hover">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="truncate text-xs font-medium uppercase tracking-wide text-slate-500">
            {label}
          </div>
          <div
            className={`tabular mt-1.5 text-2xl font-bold ${TONE_VALUE[tone]}`}
          >
            {value}
          </div>
          {hint && <div className="mt-1 text-xs text-slate-400">{hint}</div>}
        </div>
        {icon && (
          <span
            className={`grid h-9 w-9 flex-none place-items-center rounded-lg ${TONE_ICON[tone]}`}
          >
            <Icon name={icon} className="h-4 w-4" />
          </span>
        )}
      </div>
    </div>
  );
}
