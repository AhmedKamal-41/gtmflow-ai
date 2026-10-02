import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import MetricsPage from "./page";

/** Phase 11: the dashboard shows one approval cohort and consistent
 * mock-versus-real breakdowns. Only @/lib/api and navigation are faked. */

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

const fake = vi.hoisted(() => ({ metrics: null as unknown }));

const zeroGen = { drafts_generated: 0, drafts_approved: 0, drafts_rejected: 0, drafts_pending_review: 0, approval_rate: 0 };
const zeroDel = { attempts: 0, delivered: 0, unique_leads_delivered: 0, failed: 0, outcome_unknown: 0, pending: 0, success_rate: 0 };

function metrics(overrides: Record<string, unknown> = {}) {
  return {
    total_leads_uploaded: 10, total_leads_processed: 10, hot_leads: 2, warm_leads: 4, cold_leads: 4,
    outreach_generated: 3, outreach_approved: 1, outreach_rejected: 1, outreach_pending_review: 1,
    approval_rate: 33.33, reviewed_approval_rate: 50, approval_events: 4, rejection_events: 2,
    leads_pushed: 3, unique_leads_pushed: 2, push_success_rate: 60, failed_push_count: 1,
    push_unknown_count: 1, push_pending_count: 0, real_messages_delivered: 1, mock_messages_delivered: 2,
    estimated_time_saved_minutes: 50, estimated_time_saved_hours: 0.83, average_lead_score: 60,
    missing_data_rate: 0, automation_coverage: 100,
    fit_scored_leads: 0, fit_strong_match: 0, fit_partial_match: 0, fit_weak_match: 0, fit_insufficient_evidence: 0,
    generation_by_mode: {
      mock: { drafts_generated: 2, drafts_approved: 1, drafts_rejected: 0, drafts_pending_review: 1, approval_rate: 50 },
      real: { drafts_generated: 1, drafts_approved: 0, drafts_rejected: 1, drafts_pending_review: 0, approval_rate: 0 },
      unknown: zeroGen,
    },
    delivery_by_mode: {
      mock: { attempts: 2, delivered: 2, unique_leads_delivered: 1, failed: 0, outcome_unknown: 0, pending: 0, success_rate: 100 },
      real: { attempts: 3, delivered: 1, unique_leads_delivered: 1, failed: 1, outcome_unknown: 1, pending: 0, success_rate: 33.33 },
      unknown: zeroDel,
    },
    data_mode: "mixed",
    ...overrides,
  };
}

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, getMetricsDashboard: vi.fn(() => Promise.resolve(fake.metrics)), runDemo: vi.fn() };
});

beforeEach(() => {
  fake.metrics = metrics();
});

describe("metrics dashboard (Phase 11)", () => {
  it("shows the approval cohort, unknown deliveries and the mixed-data notice", async () => {
    render(<MetricsPage />);
    expect(await screen.findByLabelText("Data mode")).toHaveTextContent("mixes mock and real");
    expect(screen.getByText("Pending review").parentElement).toHaveTextContent("1");
    expect(screen.getByText("Approved of reviewed").parentElement).toHaveTextContent("50%");
    expect(screen.getByText("Delivery outcome unknown").parentElement).toHaveTextContent("1");
    expect(screen.getByText(/can never pass 100%/)).toBeInTheDocument();
  });

  it("breaks generation and delivery down by mode, summing to the totals", async () => {
    render(<MetricsPage />);
    const gen = await screen.findByRole("table", { name: "Generation by mode" });
    const rows = within(gen).getAllByRole("row");
    expect(rows[1]).toHaveTextContent("Mock2101");
    expect(rows[2]).toHaveTextContent("Real1010");
    const del = screen.getByRole("table", { name: "Delivery by mode" });
    const drows = within(del).getAllByRole("row");
    expect(drows[2]).toHaveTextContent("Real311110");
    expect(screen.getByText(/Real messages delivered: 1/)).toBeInTheDocument();
  });

  it("says plainly when everything is mock", async () => {
    fake.metrics = metrics({ data_mode: "mock_only" });
    render(<MetricsPage />);
    expect(await screen.findByLabelText("Data mode")).toHaveTextContent("no message left this app");
  });
});
