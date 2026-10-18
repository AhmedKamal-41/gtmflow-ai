import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import BatchDetailPage from "./page";

/**
 * The batch page shows each lead's ranking against the active seller
 * profile, evidence coverage and missing data, current readiness gaps and
 * eligibility reasons -- loaded with ONE bounded request per page per kind
 * of data, never one request per lead. No legacy score is shown.
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
        batch_id: "batch-1", ranking_state: "ready", profile_id: "p", profile_version: "4", targeting_hash: "h",
        total_leads: 3, scored_leads: 2, unscored_leads: 1, outdated_leads: 1,
        score_rows: 5, strong_match: 1, partial_match: 0, weak_match: 0,
        insufficient_evidence: 1, hot: 1, warm: 0, cold: 0, average_fit_score: 60,
      }),
    ),
    getRankingStatus: vi.fn(() => Promise.resolve({ state: "ready", outdated_rankings: 0, never_ranked: 0, profile: { version: "4" } })),
    getBatchFitScores: vi.fn(() =>
      Promise.resolve(
        pageOf(
          ["lead-a", "lead-c"].map((id) => ({
            id: `${id}-fit`, lead_id: id, scorer_version: "fit-scorer-v2",
            profile_id: "p", profile_version: "4", seller_profile_id: "p", targeting_hash: "h",
            normalization_version: "fit-norm-v2", input_fingerprint: "f",
            fit_score: id === "lead-a" ? 100 : 20, max_fit_score: 100,
            evidence_coverage_pct: id === "lead-a" ? 100 : 35,
            band: id === "lead-a" ? "strong_match" : "insufficient_evidence",
            priority: id === "lead-a" ? "Hot" : null, is_current: true,
            explanation: id === "lead-a" ? "Target-customer fit: matches industry." : "Not enough information to rank confidently.",
            summary: { matched: ["Industry"], partial: [], mismatched: [],
              missing: id === "lead-a" ? [] : ["Company size", "Location"], not_configured: [] },
            criteria: [],
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
    getLeadFitScore: vi.fn(() => Promise.reject(new Error("per-lead request not expected"))),
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
    ["scoreBatchFit", /rank leads/i],
    ["pushHotLeads", /send approved hot leads/i],
  ] as const)("a late %s action cannot replace the newly selected batch", async (method, button) => {
    const api = await import("@/lib/api");
    let finish: () => void = () => {};
    const response = {
      batch_id: "batch-1", profile_id: "p", profile_version: "4",
      attempted: 3, newly_scored: 3, skipped_unchanged: 0, failed: 0,
      pushed: 0, hot_leads_found: 0, skipped: 0, blocked: 0, results: [],
      summary: {
        batch_id: "batch-1", ranking_state: "ready", profile_id: "p", profile_version: "4", targeting_hash: "h",
        total_leads: 3, scored_leads: 3, unscored_leads: 0, outdated_leads: 0,
        score_rows: 3, strong_match: 3, partial_match: 0, weak_match: 0,
        insufficient_evidence: 0, hot: 3, warm: 0, cold: 0, average_fit_score: 100,
      },
    };
    // Only the action is held; the real page can finish loading batch 2.
    // One fixture answers both actions, so the mock is typed loosely.
    vi.mocked(api[method]).mockImplementationOnce((() => new Promise<typeof response>((resolve) => {
      finish = () => resolve(response);
    })) as never);
    const { rerender } = render(<BatchDetailPage />);
    await screen.findByRole("heading", { name: "Real cohort" });
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

    expect(api.getBatchFitScores).toHaveBeenCalledTimes(1);
    expect(api.getBatchReadiness).toHaveBeenCalledTimes(1);
    expect(api.getBatchFitScores).toHaveBeenCalledWith("batch-1", { limit: 3, offset: 0 });
    expect(api.getLeadFitScore).not.toHaveBeenCalled();
  });

  it("shows each ranking, missing data, current readiness and eligibility -- and no legacy score", async () => {
    render(<BatchDetailPage />);
    await screen.findByText("Company A");
    expect(screen.queryByText(/Legacy/)).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Ranking against seller profile version 4" })).toBeInTheDocument();
    const table = within(screen.getByRole("table"));
    expect(table.getByText("Hot")).toBeInTheDocument(); // lead-a
    expect(table.getByText("Needs data")).toBeInTheDocument(); // lead-c: too little evidence, not "Cold"
    expect(table.getByText("Not ranked")).toBeInTheDocument(); // lead-b
    expect(screen.queryByText("coverage 100%")).not.toBeInTheDocument(); // details hidden for reps
    fireEvent.click(screen.getByRole("button", { name: "Show ranking details" }));
    expect(screen.getByText("coverage 100%")).toBeInTheDocument();
    expect(screen.getByText("missing: company size, location")).toBeInTheDocument();
    // Current eligibility reason and readiness gaps, for ranked AND unranked leads.
    expect(
      screen.getByText(/Excluded: lead status 'do_not_contact' blocks outreach delivery/),
    ).toBeInTheDocument();
    expect(screen.getAllByText(/Not ready: no seller profile, no contact email, no draft/)).toHaveLength(3);
    // Distinct-lead summary, with outdated rankings and history reported separately.
    expect(screen.getByText(/2 of 3 leads ranked \(1 only against earlier target customers\)/)).toBeInTheDocument();
    expect(screen.getByText(/5 stored rows incl\. history/)).toBeInTheDocument();
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
