import type { LeadScore, ScoreBreakdown as SB } from "@/types/api";

import { Bar } from "./charts";
import { Card } from "./Card";
import { PriorityBadge } from "./PriorityBadge";

type Row = [label: string, key: keyof SB, max: number];

const ROWS: Row[] = [
  ["Industry fit", "industry_fit", 25],
  ["Company size fit", "company_size_fit", 15],
  ["Persona / title fit", "persona_title_fit", 15],
  ["Pain-point keywords", "pain_point_keywords", 20],
  ["Data completeness", "data_completeness", 10],
  ["Source quality", "source_quality", 10],
  ["Penalties", "penalties", -15],
];

function readNumber(
  sb: Partial<SB> | null | undefined,
  key: keyof SB,
): number | null {
  if (!sb) return null;
  const raw = sb[key];
  if (typeof raw !== "number" || !Number.isFinite(raw)) return null;
  return raw;
}

function barPercent(value: number, max: number): number {
  if (!Number.isFinite(value) || !Number.isFinite(max) || max === 0) return 0;
  const pct = (Math.abs(value) / Math.abs(max)) * 100;
  if (!Number.isFinite(pct)) return 0;
  return Math.min(100, Math.max(0, pct));
}

export function ScoreBreakdown({ score }: { score: LeadScore }) {
  const sb = score.score_breakdown ?? {};
  const hasAnyCategory = ROWS.some(([, key]) => readNumber(sb, key) !== null);

  return (
    <Card>
      <div className="flex items-start justify-between gap-4">
        <div>
          <div className="flex items-baseline gap-1">
            <span className="tabular text-4xl font-bold text-slate-900">
              {score.total_score}
            </span>
            <span className="text-base font-medium text-slate-400">/ 100</span>
          </div>
          <p className="mt-2 max-w-xl text-sm leading-relaxed text-slate-600">
            {score.reasoning}
          </p>
        </div>
        <PriorityBadge priority={score.priority} />
      </div>

      {hasAnyCategory ? (
        <div className="mt-6 space-y-3.5">
          {ROWS.map(([label, key, max]) => {
            const raw = readNumber(sb, key);
            const displayValue = raw ?? 0;
            const pct = raw === null ? 0 : barPercent(raw, max);
            const isPenalty = max < 0;
            return (
              <div key={label}>
                <div className="flex items-center justify-between text-sm">
                  <span className="text-slate-700">{label}</span>
                  <span className="tabular font-semibold text-slate-900">
                    {raw === null ? (
                      <span className="font-normal text-slate-400">
                        Not available
                      </span>
                    ) : (
                      <>
                        {displayValue} / {max}
                      </>
                    )}
                  </span>
                </div>
                <div className="mt-1.5">
                  <Bar
                    percent={pct}
                    color={isPenalty ? "bg-red-400" : "bg-brand-500"}
                  />
                </div>
              </div>
            );
          })}
        </div>
      ) : (
        <div className="mt-5 text-sm text-slate-500">
          Score breakdown is not available for this lead.
        </div>
      )}

      {(score.matched_signals?.industry_terms?.length > 0 ||
        score.matched_signals?.title_terms?.length > 0 ||
        score.matched_signals?.pain_point_terms?.length > 0) && (
        <div className="mt-6 border-t border-slate-100 pt-4">
          <div className="text-xs font-semibold uppercase tracking-wide text-slate-500">
            Matched signals
          </div>
          <Signals
            label="Industry"
            items={score.matched_signals?.industry_terms ?? []}
          />
          <Signals
            label="Title"
            items={score.matched_signals?.title_terms ?? []}
          />
          <Signals
            label="Pain points"
            items={score.matched_signals?.pain_point_terms ?? []}
          />
        </div>
      )}
    </Card>
  );
}

function Signals({ label, items }: { label: string; items: string[] }) {
  if (items.length === 0) return null;
  return (
    <div className="mt-3 flex flex-wrap items-baseline gap-2">
      <div className="w-24 flex-none text-xs font-medium uppercase tracking-wide text-slate-400">
        {label}
      </div>
      <div className="flex flex-wrap gap-1.5">
        {items.map((t) => (
          <span
            key={t}
            className="rounded-md bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-600"
          >
            {t}
          </span>
        ))}
      </div>
    </div>
  );
}
