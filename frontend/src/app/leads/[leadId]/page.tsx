"use client";

import { useParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import { AIOutputCard } from "@/components/AIOutputCard";
import { Button } from "@/components/Button";
import { Card } from "@/components/Card";
import { CurrentReadinessCard } from "@/components/CurrentReadinessCard";
import { ErrorMessage } from "@/components/ErrorMessage";
import { FitScoreCard } from "@/components/FitScoreCard";
import { Icon } from "@/components/Icon";
import { LoadingState } from "@/components/LoadingState";
import { PageHeader } from "@/components/PageHeader";
import { PushHistory } from "@/components/PushHistory";
import { ScoreBreakdown } from "@/components/ScoreBreakdown";
import { StatusBadge } from "@/components/StatusBadge";
import { usePaginatedHistory } from "@/hooks/usePaginatedHistory";
import {
  APIError,
  approveOutreach,
  generateOutreach,
  generateSummary,
  getAIOutputs,
  getLatestAIOutput,
  getLead,
  getLeadFitScore,
  getLeadReadiness,
  getLeadScore,
  getPushes,
  pushLead,
  rejectOutreach,
  scoreLead,
  scoreLeadFit,
} from "@/lib/api";
import type {
  AIOutput,
  CurrentReadiness,
  Lead,
  LeadFitScore,
  LeadScore,
} from "@/types/api";

type ActionLabel =
  | "Score"
  | "Fit"
  | "Summary"
  | "Outreach"
  | "Push"
  | "Approve"
  | "Reject"
  | "Refresh"
  | null;

export default function LeadDetailPage() {
  const params = useParams<{ leadId: string }>();
  const leadId = params?.leadId ?? "";

  const [lead, setLead] = useState<Lead | null>(null);
  const [score, setScore] = useState<LeadScore | null>(null);
  // v2 deterministic company-fit scorer (Phase 4) -- entirely separate
  // from `score`/`LeadScore` above (the legacy v1 Hot/Warm/Cold scorer).
  const [fitScore, setFitScore] = useState<LeadFitScore | null>(null);
  // CURRENT readiness + routing eligibility (live state, independent of
  // whether the lead was ever fit-scored). `undefined` = not resolved yet
  // or the lookup failed; push stays disabled until it resolves.
  const [readiness, setReadiness] = useState<CurrentReadiness | undefined>(
    undefined,
  );
  // Authoritative "what draft would approve/reject act on right now" --
  // resolved via a dedicated backend lookup (Part D.4), never by searching
  // whatever page of history happens to be loaded client-side. `null` means
  // "resolved: no outreach draft exists"; `undefined` means "not resolved
  // yet / lookup failed" -- these are deliberately distinct so the approve
  // UI never renders against an unresolved or failed lookup as if it were
  // a confirmed "no draft" state (Part D.3/D.4 of the Phase 4 closeout).
  const [latestOutreach, setLatestOutreach] = useState<
    AIOutput | null | undefined
  >(undefined);
  const [outreachLookupError, setOutreachLookupError] = useState<
    string | null
  >(null);
  const [loadingLead, setLoadingLead] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [busy, setBusy] = useState<ActionLabel>(null);

  const outputsHistory = usePaginatedHistory(leadId, (id, limit, offset) =>
    getAIOutputs(id, { limit, offset }),
  );
  const pushesHistory = usePaginatedHistory(leadId, (id, limit, offset) =>
    getPushes(id, { limit, offset }),
  );

  // Same stale-response guard as usePaginatedHistory, applied here too
  // (Part A.2 of the Phase 4 closeout): a slow response for a lead the
  // user has already navigated away from must never overwrite the newly
  // selected lead's state. Bumped on every leadId change.
  const generationRef = useRef(0);

  const refreshLatestOutreach = useCallback(
    async (generation: number) => {
      setOutreachLookupError(null);
      try {
        const output = await getLatestAIOutput(leadId, "outreach_email");
        if (generationRef.current !== generation) return; // stale
        setLatestOutreach(output);
      } catch (e) {
        if (generationRef.current !== generation) return; // stale
        if (e instanceof APIError && e.status === 404) {
          setLatestOutreach(null); // resolved: no outreach draft exists yet
        } else {
          // Lookup genuinely failed (network, 5xx, etc.) -- leave
          // latestOutreach at `undefined` so the approve/reject UI stays
          // hidden rather than silently falling back to a stale prior
          // lead's draft or rendering with nothing to act on.
          setLatestOutreach(undefined);
          setOutreachLookupError(
            e instanceof APIError
              ? (e.detail ?? e.message)
              : "Failed to look up the current outreach draft.",
          );
        }
      }
    },
    [leadId],
  );

  const load = useCallback(async () => {
    if (!leadId) return;
    const generation = generationRef.current;
    setLoadingLead(true);
    setError(null);
    setLead(null);
    setScore(null);
    setFitScore(null);
    setReadiness(undefined);
    setLatestOutreach(undefined);
    try {
      const leadData = await getLead(leadId);
      if (generationRef.current !== generation) return; // stale
      setLead(leadData);

      await refreshLatestOutreach(generation);

      try {
        const scoreData = await getLeadScore(leadId);
        if (generationRef.current !== generation) return; // stale
        setScore(scoreData);
      } catch (e) {
        if (generationRef.current !== generation) return; // stale
        if (e instanceof APIError && e.status === 404) {
          setScore(null);
        } else {
          throw e;
        }
      }

      const readinessData = await getLeadReadiness(leadId);
      if (generationRef.current !== generation) return; // stale
      setReadiness(readinessData);

      try {
        const fitData = await getLeadFitScore(leadId);
        if (generationRef.current !== generation) return; // stale
        setFitScore(fitData);
      } catch (e) {
        if (generationRef.current !== generation) return; // stale
        if (e instanceof APIError && e.status === 404) {
          setFitScore(null);
        } else {
          throw e;
        }
      }
    } catch (e) {
      if (generationRef.current !== generation) return; // stale
      setError(
        e instanceof APIError ? (e.detail ?? e.message) : "Failed to load lead",
      );
    } finally {
      if (generationRef.current === generation) setLoadingLead(false);
    }
  }, [leadId, refreshLatestOutreach]);

  useEffect(() => {
    generationRef.current += 1;
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [leadId]);

  async function runAction(
    label: Exclude<ActionLabel, null>,
    fn: () => Promise<unknown>,
  ) {
    // Everything below was captured from THIS render (this lead). If the
    // user navigates to another lead while `fn` is in flight, the
    // follow-up refresh must not run: `load`/`reload` here still point at
    // the old lead and would paint its data into the new lead's page.
    const generation = generationRef.current;
    setBusy(label);
    setError(null);
    setInfo(null);
    try {
      await fn();
      if (generationRef.current !== generation) return; // navigated away
      setInfo(`${label} complete.`);
      await load();
      if (generationRef.current !== generation) return;
      outputsHistory.refresh();
      pushesHistory.refresh();
    } catch (e) {
      if (generationRef.current !== generation) return;
      setError(
        e instanceof APIError
          ? (e.detail ?? e.message)
          : e instanceof Error
            ? e.message
            : `${label} failed.`,
      );
    } finally {
      if (generationRef.current === generation) setBusy(null);
    }
  }

  // Current restrictions are re-checked immediately before sending, not
  // taken from whatever this page loaded earlier (the backend enforces
  // the same exclusions again at dispatch).
  async function pushWithCurrentCheck() {
    const generation = generationRef.current;
    const fresh = await getLeadReadiness(leadId);
    if (generationRef.current !== generation) {
      throw new Error("Navigated to another lead before pushing; nothing sent.");
    }
    setReadiness(fresh);
    if (fresh.eligibility.excluded) {
      throw new Error(
        `Not pushed: excluded from routing (${fresh.eligibility.reasons.join("; ")}).`,
      );
    }
    return pushLead(leadId, pushForce);
  }

  if (loadingLead && !lead) {
    return error ? <ErrorMessage>{error}</ErrorMessage> : <LoadingState />;
  }
  if (!lead) {
    return error ? <ErrorMessage>{error}</ErrorMessage> : <LoadingState />;
  }

  // Legacy v1 semantics, unchanged: a lead that isn't legacy-Hot is pushed
  // with force. Force never overrides a routing exclusion (backend-enforced).
  const pushForce = score?.priority !== "Hot";
  const pushBlocked = readiness === undefined || readiness.eligibility.excluded;
  // Actions that target a specific draft are only enabled once the
  // authoritative lookup has actually resolved to a real draft -- never
  // while it's still in flight (`undefined`, not yet resolved this
  // navigation) or failed (Part A.2: "disable stale actions during
  // navigation or lookup failure").
  const canReviewOutreach = latestOutreach !== undefined && latestOutreach !== null;

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Lead workspace"
        title={lead.company_name}
        back={{ href: `/batches/${lead.batch_id}`, label: "Back to batch" }}
        description={
          <span className="flex flex-wrap items-center gap-2">
            <StatusBadge status={lead.status} />
            {lead.industry && (
              <span className="text-slate-500">· {lead.industry}</span>
            )}
            {lead.location && (
              <span className="text-slate-500">· {lead.location}</span>
            )}
          </span>
        }
      />

      <div className="flex flex-wrap gap-2">
        <Button
          loading={busy === "Score"}
          onClick={() => runAction("Score", () => scoreLead(leadId))}
        >
          Score lead (legacy v1)
        </Button>
        <Button
          variant="secondary"
          loading={busy === "Fit"}
          onClick={() => runAction("Fit", () => scoreLeadFit(leadId))}
        >
          Score company fit (v2 demo)
        </Button>
        <Button
          variant="secondary"
          loading={busy === "Summary"}
          onClick={() => runAction("Summary", () => generateSummary(leadId))}
        >
          Generate summary
        </Button>
        <Button
          variant="secondary"
          loading={busy === "Outreach"}
          onClick={() => runAction("Outreach", () => generateOutreach(leadId))}
        >
          Generate outreach
        </Button>
        {canReviewOutreach && latestOutreach && (
          <>
            <Button
              variant="secondary"
              loading={busy === "Approve"}
              onClick={() =>
                runAction("Approve", () =>
                  approveOutreach(leadId, latestOutreach.id),
                )
              }
            >
              Approve outreach
            </Button>
            <Button
              variant="danger"
              loading={busy === "Reject"}
              onClick={() => {
                const reason =
                  typeof window !== "undefined"
                    ? window.prompt("Reason for rejecting? (optional)")
                    : null;
                if (reason === null) return;
                void runAction("Reject", () =>
                  rejectOutreach(leadId, latestOutreach.id, reason || undefined),
                );
              }}
            >
              Reject outreach
            </Button>
          </>
        )}
        <Button
          variant="primary"
          loading={busy === "Push"}
          disabled={pushBlocked}
          onClick={() => runAction("Push", pushWithCurrentCheck)}
        >
          Push to Slack
        </Button>
        <Button
          variant="ghost"
          loading={busy === "Refresh"}
          onClick={() => runAction("Refresh", async () => undefined)}
        >
          Refresh
        </Button>
      </div>

      {info && (
        <div className="flex items-center gap-2.5 rounded-lg border border-brand-200 bg-brand-50 p-3.5 text-sm text-brand-900">
          <Icon name="check" className="h-4 w-4 flex-none text-brand-600" />
          {info}
        </div>
      )}
      {error && <ErrorMessage>{error}</ErrorMessage>}

      <Card title="Lead details" icon="file">
        <dl className="grid grid-cols-1 gap-x-6 gap-y-3 sm:grid-cols-2">
          <Field label="Company" value={lead.company_name} />
          <Field label="Website" value={lead.website} />
          <Field label="Industry" value={lead.industry} />
          <Field label="Contact name" value={lead.contact_name} />
          <Field label="Contact title" value={lead.contact_title} />
          <Field label="Contact email" value={lead.contact_email} />
          <Field label="Company size" value={lead.company_size} />
          <Field label="Location" value={lead.location} />
          <Field label="Source" value={lead.source} />
        </dl>
        {lead.cleaned_data &&
          Object.keys(lead.cleaned_data).length > 0 && (
            <div className="mt-4 border-t border-slate-200 pt-4">
              <div className="text-sm font-medium text-slate-700">
                Extra fields from CSV
              </div>
              <dl className="mt-2 grid grid-cols-1 gap-x-6 gap-y-2 sm:grid-cols-2">
                {Object.entries(lead.cleaned_data).map(([k, v]) => (
                  <Field key={k} label={k} value={stringify(v)} />
                ))}
              </dl>
            </div>
          )}
      </Card>

      <section>
        <h2 className="mb-3 text-xl font-semibold text-slate-900">
          Legacy priority score (v1)
        </h2>
        {score ? (
          <ScoreBreakdown score={score} />
        ) : (
          <Card>
            <div className="text-sm text-slate-600">
              Not scored yet. Run <em>Score lead (legacy v1)</em> above.
            </div>
          </Card>
        )}
      </section>

      <section>
        <h2 className="mb-3 text-xl font-semibold text-slate-900">
          Company fit (v2 demo)
        </h2>
        {fitScore ? (
          <FitScoreCard fit={fitScore} />
        ) : (
          <Card>
            <div className="text-sm text-slate-600">
              Not fit-scored yet. Run <em>Score company fit (v2 demo)</em> above.
            </div>
          </Card>
        )}
      </section>

      <section>
        <h2 className="mb-3 text-xl font-semibold text-slate-900">
          Current readiness &amp; eligibility
        </h2>
        {readiness ? (
          <CurrentReadinessCard
            readiness={readiness.readiness}
            eligibility={readiness.eligibility}
          />
        ) : (
          <LoadingState text="Checking current readiness…" />
        )}
      </section>

      <section>
        <h2 className="mb-3 text-xl font-semibold text-slate-900">
          Current outreach draft
        </h2>
        {/*
          Phase 4 closeout Part A.3: this card ALWAYS renders the exact
          content the approve/reject buttons above will act on, fetched via
          the authoritative latest-outreach lookup -- independent of the
          paginated history list below, which may have this same draft many
          pages deep if lots of summaries were generated after it. A lookup
          pointer alone (an id with nothing rendered) must never be what
          "enables" approval; the user must be able to actually read this
          exact draft on screen before clicking Approve.
        */}
        {latestOutreach === undefined ? (
          outreachLookupError ? (
            <Card>
              <div className="space-y-2">
                <ErrorMessage>{outreachLookupError}</ErrorMessage>
                <div className="text-xs text-slate-500">
                  Approve/reject are disabled until this lookup succeeds --
                  never falls back to a stale or guessed draft.
                </div>
              </div>
            </Card>
          ) : (
            <LoadingState text="Resolving the current draft…" />
          )
        ) : latestOutreach === null ? (
          <Card>
            <div className="text-sm text-slate-600">
              No outreach draft yet. Run <em>Generate outreach</em> above.
            </div>
          </Card>
        ) : (
          <AIOutputCard output={latestOutreach} isLatestOfType />
        )}
      </section>

      <section>
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-xl font-semibold text-slate-900">
            All AI outputs (history)
          </h2>
          {outputsHistory.total > 0 && (
            <span className="text-sm text-slate-500">
              Showing {outputsHistory.items.length.toLocaleString()} of{" "}
              {outputsHistory.total.toLocaleString()}
            </span>
          )}
        </div>
        <HistoryPanel
          state={outputsHistory}
          emptyLabel="No AI outputs yet. Generate a summary or outreach above."
          renderItems={(items) => (
            <div className="space-y-3">
              {items.map((o) => (
                <AIOutputCard
                  key={o.id}
                  output={o}
                  isLatestOfType={
                    o.output_type !== "outreach_email" ||
                    o.id === latestOutreach?.id
                  }
                />
              ))}
            </div>
          )}
        />
      </section>

      <section>
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-xl font-semibold text-slate-900">
            Push history
          </h2>
          {pushesHistory.total > 0 && (
            <span className="text-sm text-slate-500">
              Showing {pushesHistory.items.length.toLocaleString()} of{" "}
              {pushesHistory.total.toLocaleString()}
            </span>
          )}
        </div>
        <HistoryPanel
          state={pushesHistory}
          emptyLabel="Not pushed yet."
          renderItems={(items) => <PushHistory pushes={items} />}
        />
      </section>
    </div>
  );
}

/**
 * Shared rendering for a paginated history panel: distinguishes loading,
 * a genuinely empty result, a failed request (with a retry button and
 * whatever was already loaded still shown above it), and a "load more"
 * control -- Phase 3 closeout Part D.1/D.2.
 */
function HistoryPanel<T>({
  state,
  emptyLabel,
  renderItems,
}: {
  state: ReturnType<typeof usePaginatedHistory<T>>;
  emptyLabel: string;
  renderItems: (items: T[]) => React.ReactNode;
}) {
  if (state.loading) {
    return <LoadingState text="Loading history…" />;
  }

  if (state.items.length === 0) {
    if (state.error) {
      return (
        <Card>
          <div className="space-y-2">
            <ErrorMessage>{state.error}</ErrorMessage>
            <Button variant="secondary" onClick={state.reload}>
              Retry
            </Button>
          </div>
        </Card>
      );
    }
    return (
      <Card>
        <div className="text-sm text-slate-600">{emptyLabel}</div>
      </Card>
    );
  }

  return (
    <div className="space-y-3">
      {renderItems(state.items)}
      {state.error && (
        <div className="flex items-center gap-3 rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-800">
          <span className="flex-1">{state.error}</span>
          <Button variant="secondary" onClick={state.reload}>
            Retry
          </Button>
        </div>
      )}
      {!state.error && state.hasMore && (
        <div className="flex justify-center">
          <button
            type="button"
            onClick={state.loadMore}
            disabled={state.loadingMore}
            className="text-sm font-medium text-brand-600 hover:text-brand-700 disabled:opacity-50"
          >
            {state.loadingMore ? "Loading…" : "Load more"}
          </button>
        </div>
      )}
    </div>
  );
}

function Field({
  label,
  value,
}: {
  label: string;
  value: string | null | undefined;
}) {
  const display = value && String(value).length > 0 ? value : null;
  return (
    <div>
      <dt className="text-xs uppercase tracking-wide text-slate-500">
        {label}
      </dt>
      <dd className="text-sm text-slate-900">
        {display ?? <span className="text-slate-400">-</span>}
      </dd>
    </div>
  );
}

function stringify(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "string") return value;
  return JSON.stringify(value);
}
