import type { LeadBatch } from "@/types/api";

import { Card } from "./Card";
import { StatusBadge } from "./StatusBadge";

export function BatchSummaryCard({ batch }: { batch: LeadBatch }) {
  const pct =
    batch.total_leads > 0
      ? Math.round((batch.processed_leads / batch.total_leads) * 100)
      : 0;

  return (
    <Card>
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <Stat label="Status" value={<StatusBadge status={batch.status} />} />
        <Stat label="Source" value={<span className="capitalize">{batch.source}</span>} />
        <Stat label="Total leads" value={<span className="tabular">{batch.total_leads}</span>} />
        <Stat
          label="Processed"
          value={
            <span className="tabular">
              {batch.processed_leads}
              <span className="ml-1 text-sm font-normal text-slate-400">
                ({pct}%)
              </span>
            </span>
          }
        />
      </div>
      <div className="mt-4 border-t border-slate-100 pt-3 text-xs text-slate-400">
        Created {new Date(batch.created_at).toLocaleString()}
      </div>
    </Card>
  );
}

function Stat({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div>
      <div className="text-xs font-medium uppercase tracking-wide text-slate-500">
        {label}
      </div>
      <div className="mt-1.5 text-lg font-semibold text-slate-900">{value}</div>
    </div>
  );
}
