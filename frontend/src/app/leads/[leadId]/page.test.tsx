import { act, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import LeadDetailPage from "./page";
import type { AIOutput, Page } from "@/types/api";

/**
 * Phase 4 closeout Part A.3: proves the authoritative latest-outreach
 * lookup isn't just a pointer -- the exact draft it resolves to is
 * actually rendered on screen, even when that draft is far behind 200+
 * newer summaries in the paginated history list (so a naive "search the
 * loaded page" approach would never have found it, and even the
 * authoritative id alone wouldn't be enough without also rendering it).
 */

// A mutable, reconfigurable useParams so individual tests can simulate
// navigating from one lead to another (Next.js re-renders this page
// component with new route params on navigation; useParams is what
// surfaces that here).
const { useParamsMock } = vi.hoisted(() => ({ useParamsMock: vi.fn() }));
vi.mock("next/navigation", () => ({ useParams: useParamsMock }));

beforeEach(() => {
  useParamsMock.mockReturnValue({ leadId: "lead-under-test" });
});

// vi.mock factories are hoisted above top-level const declarations, so all
// fixture data referenced inside the @/lib/api mock factory below is built
// via vi.hoisted() instead of plain module-scope consts.
const { LEAD, LEAD_B, OLD_OUTREACH_DRAFT, ALL_HISTORY } = vi.hoisted(() => {
  const lead = {
    id: "lead-under-test",
    batch_id: "batch-1",
    company_name: "Deeply Buried Draft Co",
    website: null,
    industry: "Housing",
    contact_name: null,
    contact_email: null,
    contact_title: null,
    company_size: null,
    location: null,
    source: "pdl_import",
    status: "new",
    cleaned_data: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    company_identity_id: null,
    source_snapshot_id: null,
    import_run_id: null,
    source_record_id: null,
    source_raw_data: null,
  };

  const draft = {
    id: "old-outreach-draft-id",
    lead_id: "lead-under-test",
    output_type: "outreach_email",
    content: {
      subject: "The one true current draft",
      email_body: "This exact body text must be visible on screen.",
      personalization_points: [],
      call_note: "",
      confidence: "high",
    },
    model_used: "mock",
    prompt_version: "v1",
    created_at: "2026-01-01T00:00:00Z",
    parent_output_id: null,
    origin: "generated",
    input_snapshot: null,
    input_hash: null,
    output_schema_version: "v1",
    model_revision: "mock-deterministic-v1",
    adapter_revision: null,
  };

  const summaries = Array.from({ length: 200 }, (_, i) => ({
    id: `summary-${i}`,
    lead_id: "lead-under-test",
    output_type: "company_summary",
    content: {
      company_summary: `Summary ${i}`,
      detected_pain_points: [],
      fit_reasoning: "",
      evidence: [],
      inferences: [],
      confidence: "high",
    },
    model_used: "mock",
    prompt_version: "v1",
    created_at: `2026-01-02T00:${String(i % 60).padStart(2, "0")}:00Z`, // newer than the draft
    parent_output_id: null,
    origin: "generated",
    input_snapshot: null,
    input_hash: null,
    output_schema_version: "v1",
    model_revision: "mock-deterministic-v1",
    adapter_revision: null,
  }));

  const leadB = {
    ...lead,
    id: "lead-b",
    company_name: "Second Navigated-To Co",
  };

  // newest-first, draft is LAST (page 5 at 50/page) -- 200 summaries ahead of it.
  return {
    LEAD: lead,
    LEAD_B: leadB,
    OLD_OUTREACH_DRAFT: draft,
    ALL_HISTORY: [...summaries, draft],
  };
});

// getLead never auto-resolves here -- every test must explicitly resolve
// each queued call via `resolvePendingLead`, so a test can control exactly
// when (and in what order) each lead's response actually arrives, to
// deliberately construct an out-of-order-arrival race.
const { resolvePendingLead, getLeadMock } = vi.hoisted(() => {
  const resolvers: Record<string, ((lead: unknown) => void)[]> = {};
  const mockFn = vi.fn((leadId: string) => {
    return new Promise((resolve) => {
      resolvers[leadId] = resolvers[leadId] ?? [];
      resolvers[leadId].push(resolve);
    });
  });
  function resolveOne(leadId: string, value: unknown) {
    const queue = resolvers[leadId] ?? [];
    const resolve = queue.shift();
    if (!resolve) throw new Error(`No pending getLead call for ${leadId}`);
    resolve(value);
  }
  return { resolvePendingLead: resolveOne, getLeadMock: mockFn };
});

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");

  return {
    ...actual,
    getLead: getLeadMock,
    getLeadScore: vi.fn(() =>
      Promise.reject(new actual.APIError(404, "not found")),
    ),
    getLeadFitScore: vi.fn(() =>
      Promise.reject(new actual.APIError(404, "not found")),
    ),
    getLeadReadiness: vi.fn((leadId: string) =>
      Promise.resolve({
        lead_id: leadId,
        readiness: {
          outbound_email: { status: "not_ready", gaps: [], gap_explanations: {} },
          internal_slack_handoff: { status: "ready", gaps: [], gap_explanations: {} },
        },
        eligibility: { excluded: false, reasons: [], checked_at: "2026-09-22T00:00:00Z" },
      }),
    ),
    // The authoritative endpoint correctly finds the buried draft directly.
    getLatestAIOutput: vi.fn((_leadId: string, outputType: string) => {
      if (outputType === "outreach_email") {
        return Promise.resolve(OLD_OUTREACH_DRAFT);
      }
      return Promise.reject(new actual.APIError(404, "not found"));
    }),
    getAIOutputs: vi.fn(
      (_leadId: string, params?: { limit?: number; offset?: number }) => {
        const limit = params?.limit ?? 50;
        const offset = params?.offset ?? 0;
        const items = ALL_HISTORY.slice(offset, offset + limit);
        const page: Page<AIOutput> = {
          items: items as AIOutput[],
          total: ALL_HISTORY.length,
          limit,
          offset,
          has_more: offset + items.length < ALL_HISTORY.length,
        };
        return Promise.resolve(page);
      },
    ),
    getPushes: vi.fn(() =>
      Promise.resolve({ items: [], total: 0, limit: 50, offset: 0, has_more: false }),
    ),
  };
});

describe("LeadDetailPage: current draft visibility (Part A.3)", () => {
  it("renders the exact content of a draft buried behind 200+ newer summaries, via the authoritative lookup", async () => {
    render(<LeadDetailPage />);
    await act(async () => resolvePendingLead("lead-under-test", LEAD));

    // The authoritative lookup must surface the draft's actual content --
    // not just make an id available somewhere invisible.
    await waitFor(() =>
      expect(
        screen.getByText("The one true current draft"),
      ).toBeInTheDocument(),
    );
    expect(
      screen.getByText("This exact body text must be visible on screen."),
    ).toBeInTheDocument();

    // The Approve button must be present and enabled now that a real draft
    // has resolved (not just an unresolved/failed lookup state).
    const approveButton = await screen.findByRole("button", {
      name: /approve outreach/i,
    });
    expect(approveButton).toBeEnabled();

    // The history list's page 1 (newest-first) must NOT contain the
    // outreach draft yet -- it's 200 summaries deep -- proving the card
    // above is doing real work, not just re-displaying page 1.
    const historyHeading = screen.getByText("All AI outputs (history)");
    const historySection = historyHeading.closest("section");
    expect(historySection).not.toBeNull();
    // eslint-disable-next-line testing-library/no-node-access
    expect(
      historySection!.textContent?.includes(
        "This exact body text must be visible on screen.",
      ),
    ).toBe(false);
  });

  it("loading older history does not change the approval target", async () => {
    render(<LeadDetailPage />);
    await act(async () => resolvePendingLead("lead-under-test", LEAD));
    await waitFor(() =>
      expect(
        screen.getByText("The one true current draft"),
      ).toBeInTheDocument(),
    );
    const approveButtonBefore = await screen.findByRole("button", {
      name: /approve outreach/i,
    });
    expect(approveButtonBefore).toBeEnabled();

    const loadMoreButton = await screen.findByRole("button", {
      name: /load more/i,
    });
    await act(async () => {
      loadMoreButton.click();
    });
    await waitFor(() =>
      expect(screen.queryByText(/loading…/i)).not.toBeInTheDocument(),
    );

    // The exact same draft content and a still-enabled Approve button must
    // remain -- paging the history list further must never change what
    // approve/reject would act on.
    expect(
      screen.getByText("The one true current draft"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("This exact body text must be visible on screen."),
    ).toBeInTheDocument();
    const approveButtonAfter = screen.getByRole("button", {
      name: /approve outreach/i,
    });
    expect(approveButtonAfter).toBeEnabled();
  });
});

describe("LeadDetailPage: out-of-order navigation races (Part A.2)", () => {
  it("discards a stale getLead response that resolves after navigating to a different lead", async () => {
    useParamsMock.mockReturnValue({ leadId: "lead-under-test" });
    const { rerender } = render(<LeadDetailPage />);

    // Navigate to lead-b BEFORE lead-under-test's getLead call resolves --
    // simulates a slow first request whose response arrives late.
    useParamsMock.mockReturnValue({ leadId: "lead-b" });
    rerender(<LeadDetailPage />);
    await act(async () => resolvePendingLead("lead-b", LEAD_B));
    await waitFor(() =>
      expect(
        screen.getByRole("heading", { name: "Second Navigated-To Co" }),
      ).toBeInTheDocument(),
    );

    // Now let the STALE lead-under-test response resolve late.
    await act(async () => resolvePendingLead("lead-under-test", LEAD));

    // Must still show lead-b's data -- the late, now-abandoned-generation
    // response for lead-under-test must not have clobbered it. (Both the
    // page title and the "Lead details" card's Company field legitimately
    // repeat the company name, so this checks the heading specifically
    // rather than assuming a single match.)
    expect(
      screen.getByRole("heading", { name: "Second Navigated-To Co" }),
    ).toBeInTheDocument();
    expect(screen.queryByText("Deeply Buried Draft Co")).not.toBeInTheDocument();
  });
});
