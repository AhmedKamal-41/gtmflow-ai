import type { IntegrationPush } from "@/types/api";

import { Card } from "./Card";

const STATUS_STYLES: Record<string, string> = {
  success: "bg-emerald-50 text-emerald-700 ring-emerald-600/20",
  mock_success: "bg-brand-50 text-brand-700 ring-brand-600/20",
  failed: "bg-red-50 text-red-700 ring-red-600/20",
};

const FALLBACK = "bg-slate-100 text-slate-600 ring-slate-500/20";

export function PushHistory({ pushes }: { pushes: IntegrationPush[] }) {
  return (
    <Card padding="none">
      <ul className="divide-y divide-slate-100">
        {pushes.map((p) => {
          const style = STATUS_STYLES[p.status] ?? FALLBACK;
          return (
            <li key={p.id} className="space-y-2 px-5 py-4">
              <div className="flex items-center justify-between">
                <div className="text-sm font-semibold capitalize text-slate-900">
                  {p.integration_type}
                </div>
                <div className="flex items-center gap-2">
                  <span
                    className={`rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${style}`}
                  >
                    {p.status.replace(/_/g, " ")}
                  </span>
                  <span className="text-xs text-slate-400">
                    {new Date(p.created_at).toLocaleString()}
                  </span>
                </div>
              </div>
              {typeof p.payload.text === "string" && (
                <pre className="overflow-x-auto whitespace-pre-wrap rounded-lg border border-slate-200 bg-slate-50 p-3 font-sans text-sm text-slate-700">
                  {p.payload.text}
                </pre>
              )}
              {p.response_text && (
                <div className="text-xs text-slate-400">
                  Response: {p.response_text}
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </Card>
  );
}
