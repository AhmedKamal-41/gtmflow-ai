import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import BatchDetailPage from "./page";

/**
 * Phase 4: the batch page shows v2 company fit, evidence coverage, current
 * readiness gaps and eligibility reasons next to clearly labeled legacy v1
 * scores -- loaded with ONE bounded request per page per kind of data,
 * never one request per lead.
 */

const { useParamsMock } = vi.hoisted(() => ({ useParamsMock: vi.fn() }));
vi.mock("next/navigation", () => ({ useParams: useParamsMock }));

const fixtures = vi.hoisted(() => {
  const leads = ["a", "b", "c"].map((k) => ({
    id: `lead-${k}`, batch_id: "batch-1", company_name: `Company ${k.toUpperCase()}`,
    website: null, industry: "Real Estate", contact_name: null, contact_email: null,
    contact_title: null, company_size: null, location: null, source: "pdl_import",
    status: k === "c" ? "do_not_contact" : "new", cleaned_data: null,
    created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z",
    company_identity_id: null, source_snapshot_id: null, import_run_id: null,
    source_record_id: null, source_raw_data: null,
  }));
  const ready = (excluded: boolean, reasons: string[]) => ({
    readiness: {
      outbound_email: {
        status: "not_ready",
        gaps: ["no_seller_profile_configured", "missing_contact_email", "no_outreach_draft"],
        gap_explanations: {},
      },
      internal_slack_handoff: { status: excluded ? "not_ready" : "ready", gaps: [], gap_explanations: {} },
    },
    eligibility: { excluded, reasons, checked_at: "2026-09-22T00:00:00Z" },
  });
  return {
    batch: { status: "uploaded" },
    leads,
    ready,
  };
});

function pageOf<T>(items: T[], total = 3) {
  return { items, total, limit: 50, offset: 0, has_more: false };
}

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  const { leads, ready } = fixtures;
  return {
    ...actual,
    getBatch: vi.fn((id: string) =>
      Promise.resolve({
        id, name: id === "batch-1" ? "Real cohort" : "Other cohort", source: "pdl", total_leads: 3,
        processed_leads: 3, status: fixtures.batch.status, created_at: "2026-01-01T00:00:00Z",
      }),
    ),
    getLeads: vi.fn(() => Promise.resolve(pageOf(leads))),
    getBatchFitSummary: vi.fn(() =>
      Promise.resolve({
        batch_id: "batch-1", total_leads: 3, scored_leads: 2, unscored_leads: 1,
        score_rows: 5, strong_match: 2, partial_match: 0, weak_match: 0,
        insufficient_evidence: 0, average_fit_score: 100,
      }),
    ),
    getBatchScores: vi.fn(() =>
      Promise.resolve(
        pageOf([
          {
            lead_id: "lead-a", total_score: 38, priority: "Cold", score_breakdown: {},
            matched_signals: { industry_terms: [], title_terms: [], pain_point_terms: [] },
            reasoning: "",
          },
        ]),
      ),
    ),
    getBatchFitScores: vi.fn(() =>
      Promise.resolve(
        pageOf(
          ["lead-a", "lead-c"].map((id) => ({
            id: `${id}-fit`, lead_id: id, scorer_version: "fit-scorer-v1",
            profile_id: "demo-us-sectors-v1", profile_version: "1",
            normalization_version: "fit-norm-v1", input_fingerprint: "f", fit_score: 100,
            max_fit_score: 100, evidence_coverage_pct: 100, band: "strong_match", criteria: [],
            ...ready(id === "lead-c", id === "lead-c" ? ["lead status 'do_not_contact' blocks outreach delivery"] : []),
            readiness_is_current: true, at_scoring: null,
            computed_at: "2026-09-22T00:00:00Z", computation_ms: 0.1,
          })),
        ),
      ),
    ),
    getBatchReadiness: vi.fn(() =>
      Promise.resolve(
        pageOf(
          leads.map((lead) => ({
            lead_id: lead.id,
            ...ready(
              lead.status === "do_not_contact",
              lead.status === "do_not_contact"
                ? ["lead status 'do_not_contact' blocks outreach delivery"]
                : [],
            ),
          })),
        ),
      ),
    ),
    getLeadScore: vi.fn(() => Promise.reject(new Error("per-lead request not expected"))),
    getLeadFitScore: vi.fn(() => Promise.reject(new Error("per-lead request not expected"))),
    scoreBatch: vi.fn(),
    scoreBatchFit: vi.fn(),
    pushHotLeads: vi.fn(() => Promise.resolve({ pushed: 0, hot_leads_found: 0, skipped: 0, failed: 0 })),
  };
});

beforeEach(async () => {
  fixtures.batch.status = "uploaded";
  vi.clearAllMocks();
  useParamsMock.mockReturnValue({ batchId: "batch-1" });
});

describe("BatchDetailPage", () => {
  it.each([
    ["scoreBatch", /rescore leads/i],
    ["scoreBatchFit", /score company fit/i],
    ["pushHotLeads", /send approved hot leads/i],
  ] as const)("a late %s action cannot replace the newly selected batch", async (method, button) => {
    const api = await import("@/lib/api");
    let finish: () => void = () => {};
    const response = {
      batch_id: "batch-1", scored_leads: 3, hot: 0, warm: 0, cold: 3, average_score: 30,
      attempted: 3, newly_scored: 3, skipped_unchanged: 0, failed: 0,
      pushed: 0, hot_leads_found: 0, skipped: 0, blocked: 0, results: [],
      summary: {
        batch_id: "batch-1", total_leads: 3, scored_leads: 3, unscored_leads: 0,
        score_rows: 3, strong_match: 3, partial_match: 0, weak_match: 0,
        insufficient_evidence: 0, average_fit_score: 100,
      },
    };
    // Only the action is held; the real page can finish loading batch 2.
    vi.mocked(api[method]).mockImplementationOnce(() => new Promise<typeof response>((resolve) => {
      finish = () => resolve(response);
    }));
    const { rerender } = render(<BatchDetailPage />);
    await screen.findByRole("heading", { name: "Real cohort" });
    // Company-fit scoring lives behind the scoring-details toggle.
    if (!screen.queryByRole("button", { name: button })) {
      fireEvent.click(screen.getByRole("button", { name: "Show scoring details" }));
    }
    await act(async () => fireEvent.click(screen.getByRole("button", { name: button })));
    useParamsMock.mockReturnValue({ batchId: "batch-2" });
    rerender(<BatchDetailPage />);
    await screen.findByRole("heading", { name: "Other cohort" });
    await act(async () => finish());
    await waitFor(() => expect(screen.getByRole("button", { name: button })).toBeEnabled());
    expect(screen.getByRole("heading", { name: "Other cohort" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Real cohort" })).not.toBeInTheDocument();
    expect(api.getBatch).toHaveBeenLastCalledWith("batch-2");
  });

  it("loads each kind of per-lead data with one bounded request, never one per lead", async () => {
    const api = await import("@/lib/api");
    render(<BatchDetailPage />);
    await screen.findByText("Company A");

    expect(api.getBatchScores).toHaveBeenCalledTimes(1);
    expect(api.getBatchFitScores).toHaveBeenCalledTimes(1);
    expect(api.getBatchReadiness).toHaveBeenCalledTimes(1);
    expect(api.getBatchScores).toHaveBeenCalledWith("batch-1", { limit: 3, offset: 0 });
    expect(api.getLeadScore).not.toHaveBeenCalled();
    expect(api.getLeadFitScore).not.toHaveBeenCalled();
  });

  it("shows v2 fit, coverage, current readiness and eligibility beside labeled legacy scores", async () => {
    render(<BatchDetailPage />);
    await screen.findByText("Company A");
    // The rep view hides scoring internals until asked.
    expect(screen.queryByText("Company fit (v2 demo)", { selector: "th" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Show scoring details" }));

    expect(screen.getByText("Legacy score (v1)")).toBeInTheDocument();
    expect(screen.getByText("Legacy priority (v1)")).toBeInTheDocument();
    expect(screen.getByText("Company fit (v2 demo)", { selector: "th" })).toBeInTheDocument();
    expect(screen.getAllByText("coverage 100%")).toHaveLength(2);
    expect(screen.getByText("Not fit-scored")).toBeInTheDocument(); // lead-b
    // Legacy Cold and v2 strong_match on the same lead, both shown, neither
    // translated into the other.
    expect(screen.getByText("Cold")).toBeInTheDocument();
    expect(screen.getAllByText("Strong match").length).toBeGreaterThanOrEqual(2);
    // Current eligibility reason and readiness gaps, for scored AND unscored leads.
    expect(
      screen.getByText(/Excluded: lead status 'do_not_contact' blocks outreach delivery/),
    ).toBeInTheDocument();
    expect(screen.getAllByText(/Not ready: no seller profile, no contact email, no draft/)).toHaveLength(3);
    // Distinct-lead summary, with history rows reported separately.
    expect(screen.getByText(/2 of 3 leads scored/)).toBeInTheDocument();
    expect(screen.getByText(/5 stored score rows incl\. history/)).toBeInTheDocument();
  });

  it("disables push and explains why when the batch import is incomplete", async () => {
    fixtures.batch.status = "uploading";
    const api = await import("@/lib/api");
    render(<BatchDetailPage />);
    await screen.findByText("Company A");
    expect(screen.getByText(/import is incomplete \(status 'uploading'\)/)).toBeInTheDocument();
    const push = screen.getByRole("button", { name: /send approved hot leads/i });
    expect(push).toBeDisabled();
    await act(async () => fireEvent.click(push));
    expect(api.pushHotLeads).not.toHaveBeenCalled();
  });

  it("re-checks the batch right before pushing", async () => {
    const api = await import("@/lib/api");
    render(<BatchDetailPage />);
    await screen.findByText("Company A");
    fixtures.batch.status = "partial"; // changed after the page loaded
    await act(async () =>
      fireEvent.click(screen.getByRole("button", { name: /send approved hot leads/i })),
    );
    // Both the refreshed banner and the action error say so.
    await waitFor(() =>
      expect(screen.getAllByText(/import is incomplete \(status 'partial'\)/)).toHaveLength(2),
    );
    expect(api.pushHotLeads).not.toHaveBeenCalled();
  });
});
