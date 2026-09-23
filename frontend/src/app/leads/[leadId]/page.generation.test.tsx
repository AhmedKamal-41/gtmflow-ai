import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import LeadDetailPage from "./page";

/**
 * Phase 5: rendered generation behavior on the lead workspace -- seller
 * status, disabled/enabled generation, provenance of new outputs,
 * historical outputs, errors with retry, and stale seller-status responses.
 * Only the network layer (@/lib/api) and navigation params are faked.
 */

const { useParamsMock } = vi.hoisted(() => ({ useParamsMock: vi.fn() }));
vi.mock("next/navigation", () => ({ useParams: useParamsMock }));

const fake = vi.hoisted(() => {
  const state = {
    sellerStatus: null as unknown,
    statusQueue: [] as Array<{ resolve: (v: unknown) => void; reject: (e: unknown) => void }>,
    holdStatus: false,
    draft: null as Record<string, unknown> | null,
    generateOutreach: null as null | (() => Promise<unknown>),
  };
  return { state };
});

const LEAD = {
  id: "lead-1", batch_id: "batch-1", company_name: "Cascade Modular", website: null,
  industry: "Housing", contact_name: null, contact_email: null, contact_title: null,
  company_size: null, location: null, source: "csv", status: "new", cleaned_data: null,
  created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z",
  company_identity_id: null, source_snapshot_id: null, import_run_id: null,
  source_record_id: null, source_raw_data: null,
};

function profileRow(version: number, kind: "seller" | "demo" = "seller") {
  return {
    id: `profile-${version}`, version, status: "active", content_hash: `c0ffee${version}0000000000000000`,
    editor_label: "local-demo-unauthenticated", created_at: "2026-09-23T00:00:00Z",
    profile: {
      profile_kind: kind, company_name: kind === "demo" ? "GTMFlow (demonstration)" : "Synthetic Seller Co",
      product_name: "Scheduler", value_proposition: "x", target_customer: "y",
      capabilities: [], proof_points: [], exclusions: [],
    },
  };
}

function activeStatus(version = 2, kind: "seller" | "demo" = "seller") {
  return {
    state: "active", latest_version: version, activation_sequence: 1, last_activation: null,
    active_profile: profileRow(version, kind), latest_is_active: true,
  };
}

const NONE_ACTIVE = {
  state: "draft_only", latest_version: 1, activation_sequence: 0, last_activation: null,
  active_profile: null, latest_is_active: false,
};

function groundedDraft(overrides: Record<string, unknown> = {}) {
  return {
    id: "draft-grounded", lead_id: "lead-1", output_type: "outreach_email",
    content: {
      subject: "Scheduler for Cascade Modular", email_body: "Hi there,\n\nGrounded body.",
      lead_facts_used: ["fact-company_name", "fact-industry"], capabilities_used: [], claims_used: [],
      unknowns_acknowledged: ["buying_intent", "budget"], call_note: "Confirm relevance.", confidence: "low",
    },
    model_used: "mock", prompt_version: "grounded-v2", created_at: "2026-09-23T10:00:00Z",
    parent_output_id: null, origin: "generated", input_snapshot: {}, input_hash: "ab12cd34ef56ab12cd34",
    output_schema_version: "v2", model_revision: "mock-deterministic-v2-grounded", adapter_revision: null,
    seller_profile_id: "profile-2", seller_profile_version: 2,
    seller_profile_content_hash: "c0ffee20000000000000000", seller_profile_kind: "seller",
    ...overrides,
  };
}

const LEGACY_DRAFT = {
  id: "draft-legacy", lead_id: "lead-1", output_type: "outreach_email",
  content: {
    subject: "Quick idea for Cascade", email_body: "GTMFlow turns lead lists into prioritized outreach.",
    personalization_points: ["Pain-point hook: leasing"], call_note: "legacy", confidence: "high",
  },
  model_used: "mock", prompt_version: "v1", created_at: "2026-09-01T10:00:00Z",
  parent_output_id: null, origin: "generated", input_snapshot: null, input_hash: null,
  output_schema_version: null, model_revision: null, adapter_revision: null,
  seller_profile_id: null, seller_profile_version: null, seller_profile_content_hash: null,
  seller_profile_kind: null,
};

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
        internal_slack_handoff: { status: "ready", gaps: [], gap_explanations: {} },
      },
      eligibility: { excluded: false, reasons: [], checked_at: "2026-09-23T00:00:00Z" },
    })),
    getLatestAIOutput: vi.fn((_id: string, type: string) =>
      type === "outreach_email" && state.draft ? Promise.resolve(state.draft) : nf()),
    getAIOutputs: vi.fn(() => Promise.resolve(empty)),
    getPushes: vi.fn(() => Promise.resolve(empty)),
    getSellerProfileStatus: vi.fn(() => {
      if (state.holdStatus) {
        return new Promise((resolve, reject) => state.statusQueue.push({ resolve, reject }));
      }
      return state.sellerStatus instanceof Error
        ? Promise.reject(state.sellerStatus)
        : Promise.resolve(state.sellerStatus);
    }),
    generateOutreach: vi.fn(() => (state.generateOutreach ? state.generateOutreach() : Promise.resolve({}))),
    generateSummary: vi.fn(() => Promise.resolve({})),
  };
});

const api = await import("@/lib/api");

beforeEach(() => {
  vi.clearAllMocks();
  useParamsMock.mockReturnValue({ leadId: "lead-1" });
  fake.state.sellerStatus = NONE_ACTIVE;
  fake.state.holdStatus = false;
  fake.state.statusQueue = [];
  fake.state.draft = null;
  fake.state.generateOutreach = null;
});

describe("lead page grounded generation", () => {
  it("without an active seller profile, explains why and disables outreach generation", async () => {
    render(<LeadDetailPage />);
    expect(await screen.findByText(/No seller profile is active, so outreach generation is unavailable/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Review and activate a seller profile" })).toHaveAttribute("href", "/seller-profile");
    expect(screen.getByRole("button", { name: "Generate outreach" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Generate summary" })).toBeEnabled();
  });

  it("generates with the active revision and shows the new output's provenance", async () => {
    fake.state.sellerStatus = activeStatus(2);
    fake.state.generateOutreach = () => {
      fake.state.draft = groundedDraft();
      return Promise.resolve(fake.state.draft);
    };
    render(<LeadDetailPage />);
    const note = await screen.findByText(/New outreach uses seller profile version 2/);
    expect(note).toHaveTextContent("Synthetic Seller Co");
    expect(note).toHaveTextContent("c0ffee200000");

    fireEvent.click(screen.getByRole("button", { name: "Generate outreach" }));
    await screen.findByText("Outreach complete.");
    const provenance = await screen.findByLabelText("Generation provenance");
    expect(provenance).toHaveTextContent("Seller profile: version 2 · c0ffee200000");
    expect(provenance).toHaveTextContent("Prompt grounded-v2 · output schema v2 · model mock/mock-deterministic-v2-grounded");
    expect(screen.getByText("Grounded body.", { exact: false })).toBeInTheDocument();
    expect(screen.getByText("fact-company_name")).toBeInTheDocument();
    expect(screen.queryByText("not from active seller revision")).not.toBeInTheDocument();
  });

  it("shows a refused or invalid generation as an error and allows retry", async () => {
    fake.state.sellerStatus = activeStatus(2);
    let attempts = 0;
    fake.state.generateOutreach = () => {
      attempts += 1;
      if (attempts === 1) {
        return Promise.reject(new api.APIError(
          502, "Bad Gateway",
          "The AI output failed validation (unapproved_claim_reference). No output was saved.",
        ));
      }
      fake.state.draft = groundedDraft();
      return Promise.resolve(fake.state.draft);
    };
    render(<LeadDetailPage />);
    await screen.findByText(/New outreach uses seller profile version 2/);
    fireEvent.click(screen.getByRole("button", { name: "Generate outreach" }));
    expect(await screen.findByText(/unapproved_claim_reference\). No output was saved\./)).toBeInTheDocument();
    expect(screen.getByText(/No outreach draft yet/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Generate outreach" }));
    await screen.findByText("Outreach complete.");
    expect(screen.queryByText(/No output was saved/)).not.toBeInTheDocument();
    expect(await screen.findByLabelText("Generation provenance")).toHaveTextContent("version 2");
  });

  it("labels a historical draft honestly and flags it against the active revision", async () => {
    fake.state.sellerStatus = activeStatus(2);
    fake.state.draft = LEGACY_DRAFT;
    render(<LeadDetailPage />);
    const provenance = await screen.findByLabelText("Generation provenance");
    expect(provenance).toHaveTextContent("Historical output (prompt v1), generated before grounded prompting");
    expect(screen.getByText("not from active seller revision")).toBeInTheDocument();
    // Its original content still renders as produced.
    expect(screen.getByText("GTMFlow turns lead lists into prioritized outreach.")).toBeInTheDocument();
    // Review targets stay on the exact rendered draft.
    expect(screen.getByRole("button", { name: "Approve outreach" })).toBeEnabled();
  });

  it("flags a draft generated from an older revision of the profile", async () => {
    fake.state.sellerStatus = activeStatus(3);
    fake.state.draft = groundedDraft();
    render(<LeadDetailPage />);
    expect(await screen.findByText("not from active seller revision")).toBeInTheDocument();
    expect(screen.getByLabelText("Generation provenance")).toHaveTextContent("version 2");
  });

  it("labels demonstration drafts", async () => {
    fake.state.sellerStatus = activeStatus(1, "demo");
    fake.state.draft = groundedDraft({
      seller_profile_id: null, seller_profile_version: null,
      seller_profile_content_hash: "d3m0d3m0d3m0d3m0", seller_profile_kind: "demo",
    });
    render(<LeadDetailPage />);
    expect(await screen.findByText(/demonstration only/)).toBeInTheDocument();
    expect(screen.getByText("demonstration")).toBeInTheDocument();
    expect(screen.getByLabelText("Generation provenance")).toHaveTextContent(
      "built-in GTMFlow demonstration profile · d3m0d3m0d3m0",
    );
  });

  it("a seller-status failure does not hide the lead and can be retried", async () => {
    fake.state.sellerStatus = new api.APIError(503, "Unavailable", "Status service unavailable");
    render(<LeadDetailPage />);
    expect(await screen.findByText(/Seller profile status: Status service unavailable/)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Cascade Modular" })).toBeInTheDocument();
    // Unknown status: the backend decides, so generation is not pre-emptively blocked.
    expect(screen.getByRole("button", { name: "Generate outreach" })).toBeEnabled();

    fake.state.sellerStatus = activeStatus(2);
    fireEvent.click(screen.getByRole("button", { name: "Retry seller status" }));
    expect(await screen.findByText(/New outreach uses seller profile version 2/)).toBeInTheDocument();
  });

  it("ignores a seller-status response from before navigation", async () => {
    fake.state.holdStatus = true;
    const { rerender } = render(<LeadDetailPage />);
    await waitFor(() => expect(fake.state.statusQueue).toHaveLength(1));

    useParamsMock.mockReturnValue({ leadId: "lead-2" });
    rerender(<LeadDetailPage />);
    await waitFor(() => expect(fake.state.statusQueue).toHaveLength(2));

    // The new lead's status arrives first, then the old one late.
    await act(async () => { fake.state.statusQueue[1].resolve(NONE_ACTIVE); });
    expect(await screen.findByText(/No seller profile is active/)).toBeInTheDocument();
    await act(async () => { fake.state.statusQueue[0].resolve(activeStatus(9)); });
    expect(screen.queryByText(/seller profile version 9/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Generate outreach" })).toBeDisabled();
  });

  it("an outreach generation finishing after navigation does not repaint the new lead", async () => {
    fake.state.sellerStatus = activeStatus(2);
    let finish!: (v: unknown) => void;
    fake.state.generateOutreach = () => new Promise((resolve) => { finish = resolve; });
    const { rerender } = render(<LeadDetailPage />);
    await screen.findByText(/New outreach uses seller profile version 2/);
    fireEvent.click(screen.getByRole("button", { name: "Generate outreach" }));

    useParamsMock.mockReturnValue({ leadId: "lead-2" });
    rerender(<LeadDetailPage />);
    await screen.findByText(/New outreach uses seller profile version 2/);
    fake.state.draft = null;
    await act(async () => { finish(groundedDraft()); });
    expect(screen.queryByText("Outreach complete.")).not.toBeInTheDocument();
    expect(within(screen.getByRole("button", { name: "Generate outreach" })).queryByRole("status")).toBeNull();
    expect(screen.getByRole("button", { name: "Generate outreach" })).toBeEnabled();
  });
});
