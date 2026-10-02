import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { JobsPanel } from "./JobsPanel";

/** Phase 10: queueing background jobs and following their progress. Only
 * @/lib/api is faked. */

const fake = vi.hoisted(() => ({ pages: [] as unknown[][], created: [] as unknown[], hold: null as null | ((v: unknown) => void) }));

function job(overrides: Record<string, unknown> = {}) {
  return {
    id: "job-1", job_type: "fit_score", batch_id: "batch-1", params: {}, status: "running", attempts: 1,
    max_attempts: 3, max_item_attempts: 3, total_items: 4, counts: { pending: 2, succeeded: 2 },
    result: null, last_error: null, cancel_requested: false, lease_expires_at: null, heartbeat_at: null,
    created_at: "2026-09-26T00:00:00Z", started_at: null, finished_at: null, deduplicated: false,
    done_items: 2, progress_pct: 50, ...overrides,
  };
}

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  const page = (items: unknown[]) => ({ items, total: items.length, limit: 10, offset: 0, has_more: false });
  return {
    ...actual,
    listBatchJobs: vi.fn((batchId: string) => {
      if (fake.hold && batchId === "slow") return new Promise((resolve) => { fake.hold = resolve; });
      return Promise.resolve(page(fake.pages.length > 1 ? fake.pages.shift()! : fake.pages[0] ?? []));
    }),
    createBatchJob: vi.fn((_b: string, type: string) => Promise.resolve(fake.created.shift() ?? job({ job_type: type }))),
    cancelJob: vi.fn(() => Promise.resolve(job({ cancel_requested: true }))),
  };
});

const api = await import("@/lib/api");

beforeEach(() => {
  vi.clearAllMocks();
  fake.pages = [[]];
  fake.created = [];
  fake.hold = null;
});
afterEach(() => vi.useRealTimers());

describe("JobsPanel", () => {
  it("queues a job and reports a deduplicated request honestly", async () => {
    fake.created = [job({ status: "queued" }), job({ deduplicated: true })];
    render(<JobsPanel batchId="batch-1" />);
    await screen.findByText("No jobs for this batch yet.");
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Fit-score all leads" })));
    expect(api.createBatchJob).toHaveBeenLastCalledWith("batch-1", "fit_score");
    expect(screen.getByRole("status")).toHaveTextContent("Fit-score all leads: queued.");
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Fit-score all leads" })));
    expect(screen.getByRole("status")).toHaveTextContent("already queued or running");
  });

  it("polls while a job is active and stops once it finishes", async () => {
    vi.useFakeTimers();
    fake.pages = [
      [job()],
      [job({ counts: { pending: 0, succeeded: 3, blocked: 1 }, done_items: 4, progress_pct: 100 })],
      [job({ status: "completed", counts: { pending: 0, succeeded: 3, blocked: 1 }, done_items: 4, progress_pct: 100 })],
    ];
    render(<JobsPanel batchId="batch-1" pollMs={1000} />);
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "50");
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "100");
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    expect(screen.getByLabelText("Job Fit-score all leads")).toHaveTextContent("completed · 4/4");
    expect(screen.getByLabelText("Job Fit-score all leads")).toHaveTextContent("succeeded 3 · blocked 1");
    const calls = vi.mocked(api.listBatchJobs).mock.calls.length;
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    expect(vi.mocked(api.listBatchJobs).mock.calls.length).toBe(calls); // no polling when idle
  });

  it("cancels an active job", async () => {
    fake.pages = [[job()]];
    render(<JobsPanel batchId="batch-1" />);
    const cancel = await screen.findByRole("button", { name: "Cancel" });
    await act(async () => fireEvent.click(cancel));
    expect(api.cancelJob).toHaveBeenCalledWith("job-1");
    expect(screen.getByRole("status")).toHaveTextContent("Cancellation requested.");
  });

  it("disables the push job for an incomplete import", async () => {
    render(<JobsPanel batchId="batch-1" batchIncomplete />);
    expect(await screen.findByRole("button", { name: "Push approved Hot leads" })).toBeDisabled();
  });

  it("ignores a late response for a batch it no longer shows", async () => {
    fake.hold = () => undefined;
    const { rerender } = render(<JobsPanel batchId="slow" />);
    fake.pages = [[job({ job_type: "generate_summary", batch_id: "batch-2" })]];
    rerender(<JobsPanel batchId="batch-2" />);
    await screen.findByLabelText("Job Generate summaries");
    await act(async () => { fake.hold?.({ items: [job({ job_type: "push_hot" })], total: 1, limit: 10, offset: 0, has_more: false }); });
    expect(screen.queryByLabelText("Job Push approved Hot leads")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Job Generate summaries")).toBeInTheDocument();
  });
});
