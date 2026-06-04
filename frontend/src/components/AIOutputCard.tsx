import type { AIOutput, OutreachContent, SummaryContent } from "@/types/api";

import { Card } from "./Card";
import { Icon, type IconName } from "./Icon";

const META: Record<string, { label: string; icon: IconName }> = {
  company_summary: { label: "Company summary", icon: "sparkles" },
  outreach_email: { label: "Outreach draft", icon: "send" },
};

export function AIOutputCard({ output }: { output: AIOutput }) {
  const created = new Date(output.created_at).toLocaleString();
  const meta = META[output.output_type] ?? {
    label: output.output_type.replace(/_/g, " "),
    icon: "file" as IconName,
  };

  return (
    <Card padding="none">
      <div className="flex items-center justify-between gap-4 border-b border-slate-100 px-5 py-3.5">
        <div className="flex items-center gap-2.5">
          <span className="grid h-8 w-8 place-items-center rounded-lg bg-brand-50 text-brand-600">
            <Icon name={meta.icon} className="h-4 w-4" />
          </span>
          <div className="text-sm font-semibold text-slate-900">
            {meta.label}
          </div>
        </div>
        <div className="flex items-center gap-2 text-xs text-slate-400">
          <span className="rounded-md bg-slate-100 px-1.5 py-0.5 font-medium text-slate-500">
            {output.model_used ?? "mock"}
          </span>
          <span>{created}</span>
        </div>
      </div>
      <div className="px-5 py-4">
        {output.output_type === "company_summary" ? (
          <SummaryView content={output.content as unknown as SummaryContent} />
        ) : output.output_type === "outreach_email" ? (
          <OutreachView content={output.content as unknown as OutreachContent} />
        ) : (
          <pre className="overflow-x-auto rounded-lg bg-slate-50 p-3 font-mono text-xs text-slate-800">
            {JSON.stringify(output.content, null, 2)}
          </pre>
        )}
      </div>
    </Card>
  );
}

function FieldLabel({ children }: { children: React.ReactNode }) {
  return (
    <div className="text-xs font-semibold uppercase tracking-wide text-slate-400">
      {children}
    </div>
  );
}

function Chips({ items }: { items: string[] }) {
  return (
    <div className="mt-1.5 flex flex-wrap gap-1.5">
      {items.map((p) => (
        <span
          key={p}
          className="rounded-md bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-600"
        >
          {p}
        </span>
      ))}
    </div>
  );
}

function SummaryView({ content }: { content: SummaryContent }) {
  return (
    <div className="space-y-4 text-sm leading-relaxed text-slate-800">
      <p>{content.company_summary}</p>
      {content.detected_pain_points?.length > 0 && (
        <div>
          <FieldLabel>Pain points</FieldLabel>
          <Chips items={content.detected_pain_points} />
        </div>
      )}
      <div>
        <FieldLabel>Fit reasoning</FieldLabel>
        <p className="mt-1">{content.fit_reasoning}</p>
      </div>
      {content.evidence?.length > 0 && (
        <div>
          <FieldLabel>Evidence</FieldLabel>
          <ul className="mt-1 list-disc space-y-1 pl-5 text-slate-700">
            {content.evidence.map((e, i) => (
              <li key={i}>{e}</li>
            ))}
          </ul>
        </div>
      )}
      {content.inferences?.length > 0 && (
        <div>
          <FieldLabel>Inferences</FieldLabel>
          <ul className="mt-1 list-disc space-y-1 pl-5 text-slate-700">
            {content.inferences.map((e, i) => (
              <li key={i}>{e}</li>
            ))}
          </ul>
        </div>
      )}
      {content.confidence && <Confidence value={content.confidence} />}
    </div>
  );
}

function OutreachView({ content }: { content: OutreachContent }) {
  return (
    <div className="space-y-4 text-sm leading-relaxed text-slate-800">
      <div>
        <FieldLabel>Subject</FieldLabel>
        <div className="mt-1 font-medium text-slate-900">{content.subject}</div>
      </div>
      <div>
        <FieldLabel>Body</FieldLabel>
        <pre className="mt-1.5 whitespace-pre-wrap rounded-lg border border-slate-200 bg-slate-50 p-3.5 font-sans text-sm text-slate-700">
          {content.email_body}
        </pre>
      </div>
      {content.personalization_points?.length > 0 && (
        <div>
          <FieldLabel>Personalization points</FieldLabel>
          <ul className="mt-1 list-disc space-y-1 pl-5">
            {content.personalization_points.map((p, i) => (
              <li key={i}>{p}</li>
            ))}
          </ul>
        </div>
      )}
      {content.call_note && (
        <div>
          <FieldLabel>Call note</FieldLabel>
          <p className="mt-1">{content.call_note}</p>
        </div>
      )}
      {content.confidence && <Confidence value={content.confidence} />}
    </div>
  );
}

function Confidence({ value }: { value: string }) {
  return (
    <div className="text-xs text-slate-400">
      Confidence: <span className="font-semibold text-slate-600">{value}</span>
    </div>
  );
}
