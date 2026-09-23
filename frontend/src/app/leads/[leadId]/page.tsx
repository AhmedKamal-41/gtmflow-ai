"use client";

import Link from "next/link";
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
  getReviewState,
  getSellerProfileStatus,
  pushLead,
  rejectOutreach,
  reviseOutput,
  scoreLead,
  scoreLeadFit,
} from "@/lib/api";
import type {
  AIOutput,
  CurrentReadiness,
  Lead,
  LeadFitScore,
  LeadScore,
  ReviewState,
  SellerProfileStatus,
} from "@/types/api";

type ActionLabel =
  | "Score"
  | "Fit"
  | "Summary"
  | "Outreach"
  | "Push"
  | "Approve"
  | "Reject"
  | "Edit"
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
  // Which seller revision outreach generation would use right now.
  // `undefined` = not resolved yet or the lookup failed (the backend still
  // decides on every generate request).
  const [sellerStatus, setSellerStatus] = useState<
    SellerProfileStatus | undefined
  >(undefined);
  const [sellerStatusError, setSellerStatusError] = useState<string | null>(
    null,
  );
  // Phase 6: the shared review state (the same one Slack delivery
  // enforces). `undefined` = not resolved yet or the lookup failed.
  const [reviewState, setReviewState] = useState<ReviewState | undefined>(undefined);
  const [reviewStateError, setReviewStateError] = useState<string | null>(null);
  const [rejecting, setRejecting] = useState(false);
  const [rejectReason, setRejectReason] = useState("");
  const [editing, setEditing] = useState(false);
  const [editFields, setEditFields] = useState({ subject: "", email_body: "", call_note: "" });
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

  const refreshSellerStatus = useCallback(async (generation: number) => {
    setSellerStatusError(null);
    try {
      const status = await getSellerProfileStatus();
      if (generationRef.current !== generation) return; // stale
      setSellerStatus(status);
    } catch (e) {
      if (generationRef.current !== generation) return; // stale
      setSellerStatus(undefined);
      setSellerStatusError(
        e instanceof APIError
          ? (e.detail ?? e.message)
          : "Could not check the active seller profile.",
      );
    }
  }, []);

  const refreshReviewState = useCallback(async (generation: number) => {
    setReviewStateError(null);
    try {
      const value = await getReviewState(leadId);
      if (generationRef.current !== generation) return; // stale
      setReviewState(value);
    } catch (e) {
      if (generationRef.current !== generation) return; // stale
      setReviewState(undefined);
      setReviewStateError(
        e instanceof APIError ? (e.detail ?? e.message) : "Could not load the review state.",
      );
    }
  }, [leadId]);

  const load = useCallback(async () => {
    if (!leadId) return;
    const generation = generationRef.current;
    setReviewState(undefined);
    void refreshReviewState(generation);
    // Independent of the lead's own data: a failure here must not hide the
    // lead, so it runs alongside the sequential loads below.
    void refreshSellerStatus(generation);
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
  }, [leadId, refreshLatestOutreach, refreshSellerStatus, refreshReviewState]);

  useEffect(() => {
    generationRef.current += 1;
    setBusy(null);
    setInfo(null);
    setRejecting(false);
    setRejectReason("");
    setEditing(false);
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [leadId]);

  async function runAction(
    label: Exclude<ActionLabel, null>,
    fn: () => Promise<unknown>,
  ): Promise<boolean> {
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
      if (generationRef.current !== generation) return false; // navigated away
      setInfo(`${label} complete.`);
      await load();
      if (generationRef.current !== generation) return true;
      outputsHistory.refresh();
      pushesHistory.refresh();
      return true;
    } catch (e) {
      if (generationRef.current !== generation) return false;
      setError(
        e instanceof APIError
          ? (e.detail ?? e.message)
          : e instanceof Error
            ? e.message
            : `${label} failed.`,
      );
      return false;
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
  // Delivery also needs a current approval of the exact draft; the backend
  // enforces it at dispatch, this only avoids offering a doomed push.
  const pushBlocked =
    readiness === undefined ||
    readiness.eligibility.excluded ||
    (reviewState !== undefined && !reviewState.approval_applicable);
  // Actions that target a specific draft are only enabled once the
  // authoritative lookup has actually resolved to a real draft -- never
  // while it's still in flight (`undefined`, not yet resolved this
  // navigation) or failed (Part A.2: "disable stale actions during
  // navigation or lookup failure").
  const canReviewOutreach = latestOutreach !== undefined && latestOutreach !== null;
  const canEditOutreach =
    canReviewOutreach && latestOutreach?.output_schema_version === "v2";

  function startEditing() {
    if (!latestOutreach) return;
    const content = latestOutreach.content as Record<string, unknown>;
    setEditFields({
      subject: String(content.subject ?? ""),
      email_body: String(content.email_body ?? ""),
      call_note: String(content.call_note ?? ""),
    });
    setRejecting(false);
    setEditing(true);
  }

  async function saveEdit() {
    if (!latestOutreach) return;
    const target = latestOutreach;
    const saved = await runAction("Edit", () =>
      reviseOutput(leadId, target.id, target.content_hash, {
        ...(target.content as Record<string, unknown>),
        ...editFields,
      }),
    );
    // On failure the editor stays open with the text intact.
    if (saved) setEditing(false);
  }

  async function confirmReject() {
    if (!latestOutreach || !rejectReason.trim()) return;
    const target = latestOutreach;
    const done = await runAction("Reject", () =>
      rejectOutreach(leadId, target.id, target.content_hash, rejectReason.trim()),
    );
    if (done) {
      setRejecting(false);
      setRejectReason("");
    }
  }
  // Known to have no active seller revision: generation would be refused,
  // so don't offer it. When the status is unknown the backend decides.
  const outreachUnavailable =
    sellerStatus !== undefined && sellerStatus.state !== "active";
  const activeSellerProfileId =
    sellerStatus === undefined ? undefined : (sellerStatus.active_profile?.id ?? null);

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
          disabled={outreachUnavailable}
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
                  approveOutreach(leadId, latestOutreach.id, latestOutreach.content_hash),
                )
              }
            >
              Approve outreach
            </Button>
            <Button
              variant="danger"
              disabled={busy !== null}
              onClick={() => {
                setEditing(false);
                setRejecting(true);
              }}
            >
              Reject outreach
            </Button>
            {canEditOutreach && (
              <Button variant="secondary" disabled={busy !== null} onClick={startEditing}>
                Edit draft
              </Button>
            )}
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

      <SellerStatusNotice
        status={sellerStatus}
        error={sellerStatusError}
        onRetry={() => void refreshSellerStatus(generationRef.current)}
      />

      {rejecting && latestOutreach && (
        <Card title="Reject this draft" subtitle={`Draft ${latestOutreach.id.slice(0, 8)} · content ${latestOutreach.content_hash.slice(0, 12)}`}>
          <label className="block text-sm font-medium text-slate-700">
            Reason for rejecting (required)
            <textarea
              className="mt-1 block w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
              rows={2}
              maxLength={2000}
              value={rejectReason}
              disabled={busy !== null}
              onChange={(e) => setRejectReason(e.target.value)}
            />
          </label>
          <div className="mt-3 flex gap-2">
            <Button
              variant="danger"
              loading={busy === "Reject"}
              disabled={!rejectReason.trim()}
              onClick={() => void confirmReject()}
            >
              Confirm rejection
            </Button>
            <Button variant="ghost" disabled={busy === "Reject"} onClick={() => setRejecting(false)}>
              Cancel
            </Button>
          </div>
        </Card>
      )}

      {editing && latestOutreach && (
        <Card
          title="Edit draft"
          subtitle="Saving creates a new revision. The original model output is kept unchanged, and the new revision needs its own review."
        >
          <div className="space-y-3">
            {(["subject", "email_body", "call_note"] as const).map((field) => (
              <label key={field} className="block text-sm font-medium text-slate-700">
                {field === "subject" ? "Subject" : field === "email_body" ? "Body" : "Call note"}
                <textarea
                  className="mt-1 block w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
                  rows={field === "email_body" ? 8 : 2}
                  value={editFields[field]}
                  disabled={busy === "Edit"}
                  onChange={(e) => setEditFields((prev) => ({ ...prev, [field]: e.target.value }))}
                />
              </label>
            ))}
            <div className="flex gap-2">
              <Button loading={busy === "Edit"} onClick={() => void saveEdit()}>
                Save as new revision
              </Button>
              <Button variant="ghost" disabled={busy === "Edit"} onClick={() => setEditing(false)}>
                Cancel
              </Button>
            </div>
          </div>
        </Card>
      )}

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
        <h2 className="mb-3 text-xl font-semibold text-slate-900">Review</h2>
        <ReviewStateCard
          state={reviewState}
          error={reviewStateError}
          onRetry={() => void refreshReviewState(generationRef.current)}
        />
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
          <AIOutputCard
            output={latestOutreach}
            isLatestOfType
            activeSellerProfileId={activeSellerProfileId}
          />
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

const STATUS_LABELS: Record<ReviewState["status"], string> = {
  no_draft: "No outreach draft",
  pending: "Pending review",
  approved: "Approved",
  rejected: "Rejected",
};

/**
 * Phase 6 review workspace summary: status of the exact current draft,
 * whether its approval (if any) still authorizes delivery and why not,
 * the latest decision, and where the company facts came from.
 */
function ReviewStateCard({
  state,
  error,
  onRetry,
}: {
  state: ReviewState | undefined;
  error: string | null;
  onRetry: () => void;
}) {
  if (error) {
    return (
      <Card>
        <div className="space-y-2">
          <ErrorMessage>{error}</ErrorMessage>
          <Button variant="secondary" onClick={onRetry}>
            Retry review state
          </Button>
        </div>
      </Card>
    );
  }
  if (!state) return <LoadingState text="Loading review state…" />;
  const review = state.latest_review;
  return (
    <Card>
      <div aria-label="Review status" className="space-y-3 text-sm text-slate-700">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-semibold text-slate-900">{STATUS_LABELS[state.status]}</span>
          {state.draft_origin === "human_edited" && (
            <span className="rounded-md bg-slate-100 px-1.5 py-0.5 text-xs">
              human-edited revision of {state.draft_parent_output_id?.slice(0, 8)}
            </span>
          )}
          {state.draft_content_hash && (
            <span className="font-mono text-xs text-slate-400">
              content {state.draft_content_hash.slice(0, 12)}
            </span>
          )}
        </div>
        {state.approval_applicable ? (
          <p className="text-emerald-700">
            The approval applies to this exact draft and its current inputs.
          </p>
        ) : (
          <div>
            <p>Not authorized for delivery:</p>
            <ul className="mt-1 list-inside list-disc">
              {state.delivery_blockers.map((code) => (
                <li key={code}>{state.blocker_explanations[code] ?? code}</li>
              ))}
            </ul>
          </div>
        )}
        {review && (
          <p>
            Last decision: <strong>{review.decision}</strong> by {review.reviewer_label} on{" "}
            {new Date(review.created_at).toLocaleString()}
            {review.reason ? ` · reason: ${review.reason}` : ""}
          </p>
        )}
        <p className="text-xs text-slate-500">
          Reviews are recorded as &quot;local-demo-unauthenticated&quot;: this app has no sign-in,
          so no personal identity is claimed.
        </p>
        <p className="text-xs text-slate-500" aria-label="Source freshness">
          Source: {state.source.provider ?? state.source.batch_source ?? "unknown"}
          {state.source.reported_acquisition_date
            ? ` · reported acquisition ${state.source.reported_acquisition_date}`
            : ""}
          . {state.source.freshness_note}
        </p>
      </div>
    </Card>
  );
}

/**
 * Which seller revision generation uses right now (Phase 5). Generated
 * outputs carry their own recorded revision; this is only the current one.
 */
function SellerStatusNotice({
  status,
  error,
  onRetry,
}: {
  status: SellerProfileStatus | undefined;
  error: string | null;
  onRetry: () => void;
}) {
  if (error) {
    return (
      <div className="flex items-center gap-3 rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-800">
        <span className="flex-1">Seller profile status: {error}</span>
        <Button variant="secondary" onClick={onRetry}>
          Retry seller status
        </Button>
      </div>
    );
  }
  if (!status) return null;
  const active = status.active_profile;
  if (!active) {
    return (
      <div
        role="note"
        className="rounded-lg border border-slate-200 bg-slate-50 p-3 text-sm text-slate-700"
      >
        No seller profile is active, so outreach generation is unavailable.
        Summaries describe only the lead record.{" "}
        <Link href="/seller-profile" className="font-medium text-brand-600 hover:text-brand-700">
          Review and activate a seller profile
        </Link>
      </div>
    );
  }
  const demo = active.profile.profile_kind === "demo";
  return (
    <div
      role="note"
      className={`rounded-lg border p-3 text-sm ${demo ? "border-amber-200 bg-amber-50 text-amber-900" : "border-slate-200 bg-slate-50 text-slate-700"}`}
    >
      New outreach uses seller profile version {active.version} (
      {active.profile.company_name}
      {demo ? ", demonstration only" : ""}), hash{" "}
      <span className="font-mono">{active.content_hash.slice(0, 12)}</span>.
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
