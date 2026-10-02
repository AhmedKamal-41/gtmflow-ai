import type {
  AIOutput,
  GroundedOutreachContent,
  GroundedSummaryContent,
  OutreachContent,
  SummaryContent,
} from "@/types/api";

import { Card } from "./Card";
import { Icon, type IconName } from "./Icon";

const META: Record<string, { label: string; icon: IconName }> = {
  company_summary: { label: "Company summary", icon: "sparkles" },
  outreach_email: { label: "Outreach draft", icon: "send" },
};

export function AIOutputCard({
  output,
  revision,
  isLatestOfType,
  activeSellerProfileId,
}: {
  output: AIOutput;
  // 1-based position among outputs of the same output_type, oldest first.
  // Undefined when the caller hasn't computed it (falls back to no badge).
  revision?: number;
  // True when this is the current/latest draft of its type -- only this one
  // can be approved/rejected from the UI (see leads/[leadId]/page.tsx).
  isLatestOfType?: boolean;
  // The currently active seller revision (null = none active). When given,
  // an outreach draft from any other revision is flagged. Undefined = not
  // known, so nothing is flagged.
  activeSellerProfileId?: string | null;
}) {
  const created = new Date(output.created_at).toLocaleString();
  const meta = META[output.output_type] ?? {
    label: output.output_type.replace(/_/g, " "),
    icon: "file" as IconName,
  };
  const shortId = output.id.slice(0, 8);
  const grounded = isGrounded(output);
  const notFromActive =
    output.output_type === "outreach_email" &&
    activeSellerProfileId !== undefined &&
    output.seller_profile_id !== activeSellerProfileId;

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
          <span
            className="rounded-md bg-slate-100 px-1.5 py-0.5 font-mono text-[11px] text-slate-500"
            title={`Output ID: ${output.id}`}
          >
            {revision ? `rev ${revision} · ` : ""}
            {shortId}
          </span>
          {isLatestOfType === false && (
            <span className="rounded-md bg-amber-50 px-1.5 py-0.5 text-[11px] font-medium text-amber-700">
              superseded
            </span>
          )}
          {output.seller_profile_kind === "demo" && (
            <span className="rounded-md bg-amber-50 px-1.5 py-0.5 text-[11px] font-medium text-amber-700">
              demonstration
            </span>
          )}
          {notFromActive && (
            <span className="rounded-md bg-amber-50 px-1.5 py-0.5 text-[11px] font-medium text-amber-700">
              not from active seller revision
            </span>
          )}
        </div>
        <div className="flex items-center gap-2 text-xs text-slate-400">
          <span className="rounded-md bg-slate-100 px-1.5 py-0.5 font-medium text-slate-500">
            {output.model_used ?? "mock"}
          </span>
          <span>{created}</span>
        </div>
      </div>
      <div className="px-5 py-4">
        {output.output_type === "company_summary" && grounded ? (
          <GroundedSummaryView content={output.content as unknown as GroundedSummaryContent} />
        ) : output.output_type === "outreach_email" && grounded ? (
          <GroundedOutreachView content={output.content as unknown as GroundedOutreachContent} />
        ) : output.output_type === "company_summary" ? (
          <SummaryView content={output.content as unknown as SummaryContent} />
        ) : output.output_type === "outreach_email" ? (
          <OutreachView content={output.content as unknown as OutreachContent} />
        ) : (
          <pre className="overflow-x-auto rounded-lg bg-slate-50 p-3 font-mono text-xs text-slate-800">
            {JSON.stringify(output.content, null, 2)}
          </pre>
        )}
        <QualityFlags output={output} />
        <Provenance output={output} grounded={grounded} />
      </div>
    </Card>
  );
}

// Phase 10: runtime quality checks. Informational here; the lead page asks
// the reviewer to acknowledge them before approving.
function QualityFlags({ output }: { output: AIOutput }) {
  const flags = output.quality_flags ?? [];
  if (flags.length === 0) return null;
  return (
    <div
      aria-label="Quality flags"
      className="mt-4 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900"
    >
      <div className="font-semibold">
        Needs review: {flags.length} quality flag{flags.length === 1 ? "" : "s"} ({output.quality_checks_version})
      </div>
      <ul className="mt-1 list-disc space-y-0.5 pl-4">
        {flags.map((flag, i) => (
          <li key={`${flag.code}-${flag.field}-${i}`}>
            <span className="font-mono">{flag.code}</span> in {flag.field}
            {flag.match ? <> &mdash; &ldquo;{flag.match}&rdquo;</> : null}
          </li>
        ))}
      </ul>
    </div>
  );
}

// Output schema v2 is only ever written by grounded generation (Phase 5);
// older rows keep their recorded versions and are shown as historical.
function isGrounded(output: AIOutput): boolean {
  return output.output_schema_version === "v2";
}

function sellerLabel(output: AIOutput): string {
  if (output.seller_profile_content_hash === null) {
    return output.output_type === "company_summary"
      ? "none active (summary of the lead record only)"
      : "none recorded";
  }
  const hash = output.seller_profile_content_hash.slice(0, 12);
  if (output.seller_profile_id === null) {
    return `built-in GTMFlow demonstration profile · ${hash}`;
  }
  const kind = output.seller_profile_kind === "demo" ? " (demonstration)" : "";
  return `version ${output.seller_profile_version}${kind} · ${hash}`;
}

function Provenance({ output, grounded }: { output: AIOutput; grounded: boolean }) {
  return (
    <details className="mt-4 border-t border-slate-100 pt-3 text-xs text-slate-500">
      <summary className="cursor-pointer select-none font-medium text-slate-500 hover:text-slate-700">Model details</summary>
    <div
      aria-label="Generation provenance"
      className="mt-2 space-y-0.5"
    >
      {grounded ? (
        <>
          <div>Seller profile: {sellerLabel(output)}</div>
          <div>
            Prompt {output.prompt_version} · output schema {output.output_schema_version} · model{" "}
            {output.model_used}/{output.model_revision}
          </div>
          {output.adapter_revision && <div>Adapter {output.adapter_revision.slice(0, 48)}…</div>}
          {output.input_hash && <div>Input hash {output.input_hash.slice(0, 12)}</div>}
        </>
      ) : (
        <div>
          Historical output (prompt {output.prompt_version ?? "not recorded"}), generated before
          grounded prompting. No seller profile was recorded for it.
        </div>
      )}
    </div>
    </details>
  );
}

function List({ items }: { items: string[] }) {
  return (
    <ul className="mt-1 list-disc space-y-1 pl-5 text-slate-700">
      {items.map((item, i) => (
        <li key={i}>{item}</li>
      ))}
    </ul>
  );
}

function GroundedSummaryView({ content }: { content: GroundedSummaryContent }) {
  return (
    <div className="space-y-4 text-sm leading-relaxed text-slate-800">
      <p>{content.company_summary}</p>
      {content.evidence?.length > 0 && (
        <div>
          <FieldLabel>Evidence from the record</FieldLabel>
          <List items={content.evidence.map((e) => `${e.statement} (${e.fact_id})`)} />
        </div>
      )}
      {content.unknowns?.length > 0 && (
        <div>
          <FieldLabel>Not known, so not assumed</FieldLabel>
          <Chips items={content.unknowns} />
        </div>
      )}
      {content.hypotheses?.length > 0 && (
        <div>
          <FieldLabel>Unconfirmed hypotheses</FieldLabel>
          <List items={content.hypotheses} />
        </div>
      )}
      {content.seller_relevance && (
        <div>
          <FieldLabel>Seller relevance</FieldLabel>
          <p className="mt-1">{content.seller_relevance}</p>
        </div>
      )}
      {content.confidence && <Confidence value={content.confidence} />}
    </div>
  );
}

function GroundedOutreachView({ content }: { content: GroundedOutreachContent }) {
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
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        <div>
          <FieldLabel>Based on these facts</FieldLabel>
          <Chips items={content.lead_facts_used} />
        </div>
        <div>
          <FieldLabel>Capabilities mentioned</FieldLabel>
          {content.capabilities_used.length ? <Chips items={content.capabilities_used} /> : <p className="mt-1 text-slate-500">None</p>}
        </div>
        <div>
          <FieldLabel>Approved claims used</FieldLabel>
          {content.claims_used.length ? <Chips items={content.claims_used} /> : <p className="mt-1 text-slate-500">None</p>}
        </div>
      </div>
      {content.unknowns_acknowledged?.length > 0 && (
        <div>
          <FieldLabel>Not known, so not assumed</FieldLabel>
          <Chips items={content.unknowns_acknowledged} />
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

function FieldLabel({ children }: { children: React.ReactNode }) {
  return (
    <div className="text-xs font-semibold uppercase tracking-wide text-slate-400">
      {children}
    </div>
  );
}

// Readable label for a cited id ("fact-company_size" -> "company size",
// "cap-2" -> "capability 2"); the exact id stays in the tooltip.
export function readableId(id: string): string {
  const text = id
    .replace(/^fact-(extra-)?/, "")
    .replace(/^cap-(\d+)$/, "capability $1")
    .replace(/^claim-(\d+)$/, "claim $1");
  return text.replace(/[_-]+/g, " ");
}

function Chips({ items }: { items: string[] }) {
  return (
    <div className="mt-1.5 flex flex-wrap gap-1.5">
      {items.map((p) => (
        <span
          key={p}
          title={p}
          className="rounded-md bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-600"
        >
          {readableId(p)}
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
