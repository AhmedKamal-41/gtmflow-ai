import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import LeadDetailPage from "./page";

/**
 * Phase 6: the lead page as the exact-draft review workspace -- review
 * state and blockers, rejection with a required reason, edits saved as new
 * revisions (with failure and retry), push gating, and stale review-state
 * responses after navigation. Only @/lib/api and navigation are faked.
 */

const { useParamsMock } = vi.hoisted(() => ({ useParamsMock: vi.fn() }));
vi.mock("next/navigation", () => ({ useParams: useParamsMock }));

const fake = vi.hoisted(() => ({
  state: {
    reviewState: null as unknown,
    reviewQueue: [] as Array<(v: unknown) => void>,
    holdReview: false,
    draft: null as Record<string, unknown> | null,
    revise: null as null | (() => Promise<unknown>),
  },
}));

const LEAD = {
  id: "lead-1", batch_id: "batch-1", company_name: "Cascade Modular", website: null,
  industry: "Housing", contact_name: null, contact_email: null, contact_title: null,
  company_size: null, location: null, source: "pdl", status: "new", cleaned_data: null,
  created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z",
  company_identity_id: null, source_snapshot_id: null, import_run_id: null,
  source_record_id: null, source_raw_data: null,
};

const DRAFT = {
  id: "draft-1", lead_id: "lead-1", output_type: "outreach_email",
  content: {
    subject: "Scheduler for Cascade", email_body: "Hi there,\n\nOriginal body.",
    lead_facts_used: ["fact-company_name"], capabilities_used: [], claims_used: [],
    unknowns_acknowledged: ["budget"], call_note: "Ask first.", confidence: "low",
  },
  model_used: "mock", prompt_version: "grounded-v2", created_at: "2026-09-23T10:00:00Z",
  parent_output_id: null, origin: "generated", input_snapshot: {}, input_hash: "inputhash",
  output_schema_version: "v2", model_revision: "mock-deterministic-v2-grounded", adapter_revision: null,
  seller_profile_id: "profile-1", seller_profile_version: 1, seller_profile_content_hash: "sellerhash000000",
  seller_profile_kind: "demo", content_hash: "drafthash-1", purpose: "operational", author_label: null,
  review_status: "pending",
};

function reviewState(overrides: Record<string, unknown> = {}) {
  return {
    lead_id: "lead-1", status: "pending", draft_id: "draft-1", draft_content_hash: "drafthash-1",
    draft_origin: "generated", draft_parent_output_id: null, approval_applicable: false,
    delivery_blockers: ["draft_not_reviewed"], email_blockers: ["draft_not_reviewed"],
    blocker_explanations: { draft_not_reviewed: "The current outreach draft has not been approved or rejected yet." },
    latest_review: null,
    source: { batch_source: "pdl_import", provider: "people_data_labs", source_snapshot_id: "snap",
      reported_acquisition_date: "2025-07-28", retrieved_at: null, license: "CC-BY-4.0",
      freshness_note: "Facts may be outdated and were not re-verified." },
    ...overrides,
  };
}

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  const { state } = fake;
  const nf = () => Promise.reject(new actual.APIError(404, "not found"));
  const empty = { items: [], total: 0, limit: 50, offset: 0, has_more: false };
  return {
    ...actual,
    getLead: vi.fn((id: string) => Promise.resolve({ ...LEAD, id })),
    getLeadScore: vi.fn(nf),
    getLeadFitScore: vi.fn(nf),
    getLeadReadiness: vi.fn((id: string) => Promise.resolve({
      lead_id: id,
      readiness: {
        outbound_email: { status: "not_ready", gaps: [], gap_explanations: {} },
        internal_slack_handoff: { status: "not_ready", gaps: [], gap_explanations: {} },
      },
      eligibility: { excluded: false, reasons: [], checked_at: "2026-09-23T00:00:00Z" },
    })),
    getReviewState: vi.fn(() => {
      if (state.holdReview) return new Promise((resolve) => state.reviewQueue.push(resolve));
      return Promise.resolve(state.reviewState);
    }),
    getSellerProfileStatus: vi.fn(() => Promise.resolve({
      state: "missing", latest_version: null, activation_sequence: 0, last_activation: null,
      active_profile: null, latest_is_active: false,
    })),
    getLatestAIOutput: vi.fn((_id: string, type: string) =>
      type === "outreach_email" && state.draft ? Promise.resolve(state.draft) : nf()),
    getAIOutputs: vi.fn(() => Promise.resolve(empty)),
    getPushes: vi.fn(() => Promise.resolve(empty)),
    approveOutreach: vi.fn(() => Promise.resolve({})),
    rejectOutreach: vi.fn(() => Promise.resolve({})),
    reviseOutput: vi.fn(() => (state.revise ? state.revise() : Promise.resolve({}))),
    pushLead: vi.fn(() => Promise.resolve({})),
  };
});

const api = await import("@/lib/api");

beforeEach(() => {
  vi.clearAllMocks();
  useParamsMock.mockReturnValue({ leadId: "lead-1" });
  fake.state.reviewState = reviewState();
  fake.state.holdReview = false;
  fake.state.reviewQueue = [];
  fake.state.draft = DRAFT;
  fake.state.revise = null;
});

describe("review workspace", () => {
  it("shows the review status, why delivery is blocked, source freshness and honest identity", async () => {
    render(<LeadDetailPage />);
    const status = await screen.findByLabelText("Review status");
    expect(status).toHaveTextContent("Pending review");
    expect(status).toHaveTextContent("has not been approved or rejected yet");
    expect(status).toHaveTextContent("local-demo-unauthenticated");
    expect(screen.getByLabelText("Source freshness")).toHaveTextContent("reported acquisition 2025-07-28");
    expect(screen.getByRole("button", { name: "Push to Slack" })).toBeDisabled();
  });

  it("enables push only when the approval applies to the exact draft", async () => {
    fake.state.reviewState = reviewState({
      status: "approved", approval_applicable: true, delivery_blockers: [], email_blockers: [],
      latest_review: { id: "r1", ai_output_id: "draft-1", decision: "approved", reason: null,
        reviewer_label: "local-demo-unauthenticated", content_hash: "drafthash-1", legacy_unlinked: false,
        created_at: "2026-09-23T11:00:00Z" },
    });
    render(<LeadDetailPage />);
    expect(await screen.findByText(/The approval applies to this exact draft/)).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("button", { name: "Push to Slack" })).toBeEnabled());
  });

  it("rejects only with a reason and sends the exact draft and content hash", async () => {
    render(<LeadDetailPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Reject outreach" }));
    const confirm = screen.getByRole("button", { name: "Confirm rejection" });
    expect(confirm).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Reason for rejecting (required)"), { target: { value: "   " } });
    expect(confirm).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Reason for rejecting (required)"), { target: { value: "Wrong angle" } });
    await act(async () => fireEvent.click(confirm));
    expect(api.rejectOutreach).toHaveBeenCalledWith("lead-1", "draft-1", "drafthash-1", "Wrong angle");
    await waitFor(() => expect(screen.queryByRole("button", { name: "Confirm rejection" })).not.toBeInTheDocument());
  });

  it("saves an edit as a new revision, keeping the text after a failure and retrying", async () => {
    let attempts = 0;
    fake.state.revise = () => {
      attempts += 1;
      return attempts === 1
        ? Promise.reject(new api.APIError(422, "Unprocessable", "The edited content failed validation (unsupported_figure). Nothing was saved."))
        : Promise.resolve({ ...DRAFT, id: "draft-2" });
    };
    render(<LeadDetailPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Edit draft" }));
    expect(screen.getByText(/The original model output is kept unchanged/)).toBeInTheDocument();
    const body = screen.getByLabelText("Body");
    expect(body).toHaveValue("Hi there,\n\nOriginal body.");
    fireEvent.change(body, { target: { value: "Hi there,\n\nWe cut costs by 45%." } });
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Save as new revision" })));
    expect(await screen.findByText(/unsupported_figure/)).toBeInTheDocument();
    expect(screen.getByLabelText("Body")).toHaveValue("Hi there,\n\nWe cut costs by 45%.");

    fireEvent.change(screen.getByLabelText("Body"), { target: { value: "Hi there,\n\nCorrected text." } });
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Save as new revision" })));
    expect(api.reviseOutput).toHaveBeenLastCalledWith("lead-1", "draft-1", "drafthash-1",
      expect.objectContaining({ email_body: "Hi there,\n\nCorrected text.", subject: "Scheduler for Cascade",
        lead_facts_used: ["fact-company_name"] }));
    await waitFor(() => expect(screen.queryByRole("button", { name: "Save as new revision" })).not.toBeInTheDocument());
  });

  it("offers no edit for historical (pre-grounding) drafts", async () => {
    fake.state.draft = { ...DRAFT, output_schema_version: "v1", prompt_version: "v1" };
    render(<LeadDetailPage />);
    await screen.findByRole("button", { name: "Approve outreach" });
    expect(screen.queryByRole("button", { name: "Edit draft" })).not.toBeInTheDocument();
  });

  it("ignores a review-state response from before navigation and resets open forms", async () => {
    fake.state.holdReview = true;
    const { rerender } = render(<LeadDetailPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Reject outreach" }));
    await waitFor(() => expect(fake.state.reviewQueue).toHaveLength(1));

    useParamsMock.mockReturnValue({ leadId: "lead-2" });
    rerender(<LeadDetailPage />);
    await waitFor(() => expect(fake.state.reviewQueue).toHaveLength(2));
    expect(screen.queryByLabelText("Reason for rejecting (required)")).not.toBeInTheDocument();

    await act(async () => fake.state.reviewQueue[1](reviewState({ lead_id: "lead-2" })));
    await act(async () => fake.state.reviewQueue[0](reviewState({
      status: "approved", approval_applicable: true, delivery_blockers: [], email_blockers: [],
    })));
    const status = await screen.findByLabelText("Review status");
    expect(within(status).getByText("Pending review")).toBeInTheDocument();
    expect(screen.queryByText(/The approval applies/)).not.toBeInTheDocument();
  });
});

describe("runtime quality flags (Phase 10)", () => {
  const FLAGGED = {
    ...DRAFT,
    quality_checks_version: "runtime-checks-v1",
    quality_flags: [
      { code: "commercial_opportunity_framing", field: "subject", match: "Exploring Opportunities",
        source: "phase9-blind-review", severity: "review" },
      { code: "presumed_outreach_activity", field: "email_body", match: "your current outreach strategies",
        source: "phase9-blind-review", severity: "review" },
    ],
  };

  it("shows the flags and approves only after the reviewer acknowledges exactly them", async () => {
    fake.state.draft = FLAGGED;
    render(<LeadDetailPage />);
    const flags = (await screen.findAllByLabelText("Quality flags"))[0];
    expect(flags).toHaveTextContent("Needs review: 2 quality flags (runtime-checks-v1)");
    expect(flags).toHaveTextContent("Exploring Opportunities");
    const approve = screen.getByRole("button", { name: /approve outreach/i });
    expect(approve).toBeDisabled();
    fireEvent.click(screen.getByLabelText("I reviewed the quality flags"));
    expect(approve).toBeEnabled();
    await act(async () => fireEvent.click(approve));
    expect(api.approveOutreach).toHaveBeenLastCalledWith("lead-1", "draft-1", "drafthash-1",
      ["commercial_opportunity_framing", "presumed_outreach_activity"]);
  });

  it("an unflagged draft needs no acknowledgement", async () => {
    render(<LeadDetailPage />);
    const approve = await screen.findByRole("button", { name: /approve outreach/i });
    expect(screen.queryByLabelText("I reviewed the quality flags")).not.toBeInTheDocument();
    expect(approve).toBeEnabled();
  });
});
