import { Icon, type IconName } from "./Icon";

type Props = {
  children: React.ReactNode;
  padding?: "none" | "md";
  className?: string;
  // Optional header: pass a title to render a titled card section.
  title?: React.ReactNode;
  subtitle?: React.ReactNode;
  icon?: IconName;
  actions?: React.ReactNode;
};

export function Card({
  children,
  padding = "md",
  className = "",
  title,
  subtitle,
  icon,
  actions,
}: Props) {
  const hasHeader = title != null || actions != null;
  const pad = padding === "none" ? "" : "p-5";

  return (
    <div
      className={`rounded-xl border border-slate-200 bg-white shadow-card ${className}`}
    >
      {hasHeader && (
        <div className="flex items-start justify-between gap-4 border-b border-slate-100 px-5 py-4">
          <div className="flex items-start gap-3">
            {icon && (
              <span className="mt-0.5 grid h-8 w-8 flex-none place-items-center rounded-lg bg-brand-50 text-brand-600">
                <Icon name={icon} className="h-4 w-4" />
              </span>
            )}
            <div>
              {title && (
                <h3 className="text-sm font-semibold text-slate-900">{title}</h3>
              )}
              {subtitle && (
                <p className="mt-0.5 text-sm text-slate-500">{subtitle}</p>
              )}
            </div>
          </div>
          {actions && <div className="flex-none">{actions}</div>}
        </div>
      )}
      <div className={pad}>{children}</div>
    </div>
  );
}
