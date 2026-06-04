import Link from "next/link";

import { Icon } from "./Icon";

type Props = {
  title: string;
  description?: React.ReactNode;
  eyebrow?: string;
  actions?: React.ReactNode;
  back?: { href: string; label: string };
};

export function PageHeader({
  title,
  description,
  eyebrow,
  actions,
  back,
}: Props) {
  return (
    <div className="space-y-3">
      {back && (
        <Link
          href={back.href}
          className="inline-flex items-center gap-1 text-sm font-medium text-slate-500 transition-colors hover:text-brand-600"
        >
          <Icon name="arrow-left" className="h-4 w-4" />
          {back.label}
        </Link>
      )}
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="space-y-1.5">
          {eyebrow && (
            <div className="text-xs font-semibold uppercase tracking-wider text-brand-600">
              {eyebrow}
            </div>
          )}
          <h1 className="text-2xl font-bold tracking-tight text-slate-900 sm:text-3xl">
            {title}
          </h1>
          {description && (
            <p className="max-w-2xl text-sm leading-relaxed text-slate-600">
              {description}
            </p>
          )}
        </div>
        {actions && (
          <div className="flex flex-none flex-wrap items-center gap-2">
            {actions}
          </div>
        )}
      </div>
    </div>
  );
}
