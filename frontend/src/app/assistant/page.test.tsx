import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import AssistantPage from "./page";
import type { AssistantResult } from "@/lib/api";
import type { Job } from "@/types/api";

vi.mock("@/components/AIStatusProvider", () => ({
  useAIStatus: () => ({ status: null }),
  ModelStatusPill: () => <span>Demo generator</span>,
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, getAssistantStatus: vi.fn(), getBatches: vi.fn(), planOutreach: vi.fn(),
    createBatchJob: vi.fn(), getJob: vi.fn(), cancelJob: vi.fn() };
});
const api = await import("@/lib/api");

const proposal: AssistantResult = {
  run_id: "r1", batch_id: "b1", mode: "mock", model: "mock-lead-assistant-v1", status: "proposed",
  message: "Selected 1 leads. Review them before preparing drafts.", duration_ms: 5, usage: {},
  leads: [{ id: "l1", company_name: "Example Clinic", industry: "Healthcare", location: "Boston",
    score: 90, priority: "Hot", evidence: ["Industry: Healthcare"], unknowns: ["Buying intent"] }],
  steps: [{ tool: "inspect_leads", status: "ok", detail: "Checked stored facts for 1 leads.", duration_ms: 1 }],
};

function job(status: Job["status"] = "queued"): Job {
  return { id: "j1", batch_id: "b1", job_type: "assistant_outreach", status, params: { lead_ids: ["l1"] },
    attempts: 0, max_attempts: 3, max_item_attempts: 1, total_items: 1, counts: { succeeded: status === "completed" ? 1 : 0 },
    result: null, last_error: null, cancel_requested: status === "cancelled", lease_expires_at: null, heartbeat_at: null,
    created_at: "2026-10-03T00:00:00Z", started_at: null, finished_at: null, deduplicated: false,
    done_items: status === "completed" ? 1 : 0, progress_pct: status === "completed" ? 100 : 0 };
}

beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(api.getAssistantStatus).mockResolvedValue({ mode: "mock", configured: true, can_run: true, max_leads: 5, max_steps: 6 });
  vi.mocked(api.getBatches).mockResolvedValue({ items: [
    { id: "b1", name: "Clinic import", total_leads: 5, processed_leads: 5, status: "scored", source: "csv", created_at: "", updated_at: "" },
    { id: "b2", name: "Other import", total_leads: 3, processed_leads: 3, status: "scored", source: "csv", created_at: "", updated_at: "" },
  ], total: 2, offset: 0, limit: 50, has_more: false });
  vi.mocked(api.planOutreach).mockResolvedValue(proposal);
  vi.mocked(api.createBatchJob).mockResolvedValue(job());
  vi.mocked(api.getJob).mockResolvedValue(job("completed"));
  vi.mocked(api.cancelJob).mockResolvedValue(job("cancelled"));
});
afterEach(() => vi.useRealTimers());

async function find() {
  await screen.findByText("Demo assistant");
  fireEvent.change(screen.getByLabelText("Import"), { target: { value: "b1" } });
  await act(async () => fireEvent.click(screen.getByRole("button", { name: "Find leads" })));
}

describe("Outreach assistant", () => {
  it("labels the mock honestly and only prepares drafts after explicit confirmation", async () => {
    render(<AssistantPage />);
    await find();
    expect(screen.getByText(/fixed demonstration, not an AI model/)).toBeInTheDocument();
    expect(api.planOutreach).toHaveBeenCalledWith("b1", "Find Hot leads for outreach.", 5);
    expect(screen.getByRole("link", { name: "Example Clinic" })).toHaveAttribute("href", "/leads/l1");
    expect(screen.getByText(/Unknown: Buying intent/)).toBeInTheDocument();
    expect(screen.getByText(/Check company facts: Checked stored facts/)).toBeInTheDocument();
    expect(api.createBatchJob).not.toHaveBeenCalled();
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Prepare 1 draft" })));
    expect(api.createBatchJob).toHaveBeenCalledExactlyOnceWith("b1", "assistant_outreach", { lead_ids: ["l1"] });
    expect(screen.getByText(/Waiting for the background worker/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Prepare 1 draft" })).toBeDisabled();
    expect(screen.queryByRole("button", { name: /Approve|Send Slack/i })).not.toBeInTheDocument();
  });

  it.each(["no_matches", "stopped"] as const)("does not offer drafting when the run is %s", async (status) => {
    vi.mocked(api.planOutreach).mockResolvedValue({ ...proposal, status, leads: [], message: "No shortlist." });
    render(<AssistantPage />);
    await find();
    expect(screen.getByRole("status")).toHaveTextContent("No shortlist.");
    expect(screen.queryByRole("button", { name: /Prepare/ })).not.toBeInTheDocument();
    expect(api.createBatchJob).not.toHaveBeenCalled();
  });

  it("disables planning for read-only accounts", async () => {
    vi.mocked(api.getAssistantStatus).mockResolvedValue({ mode: "mock", configured: true, can_run: false, max_leads: 5, max_steps: 6 });
    render(<AssistantPage />);
    await find();
    expect(screen.getByRole("button", { name: "Find leads" })).toBeDisabled();
    expect(screen.getByText(/Your account cannot run/)).toBeInTheDocument();
    expect(api.planOutreach).not.toHaveBeenCalled();
  });

  it("clears a shortlist when the selected import changes", async () => {
    render(<AssistantPage />);
    await find();
    expect(screen.getByText("Your shortlist")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Import"), { target: { value: "b2" } });
    expect(screen.queryByText("Your shortlist")).not.toBeInTheDocument();
    expect(api.createBatchJob).not.toHaveBeenCalled();
  });

  it("reports a queue error and allows an explicit retry", async () => {
    vi.mocked(api.createBatchJob).mockRejectedValueOnce(new api.APIError(409, "Another job is active."));
    render(<AssistantPage />);
    await find();
    const button = screen.getByRole("button", { name: "Prepare 1 draft" });
    await act(async () => fireEvent.click(button));
    expect(screen.getByRole("alert")).toHaveTextContent("Another job is active.");
    expect(button).not.toBeDisabled();
    await act(async () => fireEvent.click(button));
    expect(api.createBatchJob).toHaveBeenCalledTimes(2);
  });

  it("polls until completion and can cancel remaining drafts", async () => {
    vi.useFakeTimers();
    render(<AssistantPage />);
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    fireEvent.change(screen.getByLabelText("Import"), { target: { value: "b1" } });
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Find leads" })));
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Prepare 1 draft" })));
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Cancel remaining drafts" })));
    expect(api.cancelJob).toHaveBeenCalledWith("j1");
    expect(screen.getByText(/cancelled · cancellation requested/)).toBeInTheDocument();
    await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
    expect(api.getJob).not.toHaveBeenCalled();
  });

  it("discards a pending response after unmount", async () => {
    let resolve!: (result: AssistantResult) => void;
    vi.mocked(api.planOutreach).mockImplementation(() => new Promise((done) => { resolve = done; }));
    const view = render(<AssistantPage />);
    await find();
    view.unmount();
    await act(async () => resolve(proposal));
    expect(api.createBatchJob).not.toHaveBeenCalled();
  });
});
