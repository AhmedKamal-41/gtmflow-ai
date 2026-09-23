import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  APIError,
  generateAnnotationCandidate,
  getAnnotationCandidate,
  getAnnotationCandidates,
  getAnnotationProvider,
  getAnnotationSummary,
  submitAnnotation,
} from "@/lib/api";
import type { AnnotationCandidateDetail, AnnotationSubmit } from "@/types/api";
import AnnotationPage from "./page";

/**
 * Phase 6: rendered annotation workbench (jsdom + Testing Library; only
 * @/lib/api is faked). Covers accept, correction, skip, failure/retry with
 * the same submission id, provider-explicit generation, timing, and stale
 * responses when switching candidates.
 */

vi.mock("@/lib/api", async () => ({
  ...await vi.importActual<typeof import("@/lib/api")>("@/lib/api"),
  getAnnotationSummary: vi.fn(),
  getAnnotationProvider: vi.fn(),
  getAnnotationCandidates: vi.fn(),
  getAnnotationCandidate: vi.fn(),
  generateAnnotationCandidate: vi.fn(),
  submitAnnotation: vi.fn(),
}));

function output(id: string, task: string) {
  return {
    id, lead_id: "lead-1", output_type: task, model_used: "mock", prompt_version: "grounded-v2",
    created_at: "2026-09-23T00:00:00Z", parent_output_id: null, origin: "generated",
    input_snapshot: {
      lead_facts: [{ id: "fact-company_name", field: "company_name", value: "Acme Dental", kind: "structured_field" }],
      unknowns: ["buying_intent", "budget"],
    },
    input_hash: "inputhash000000", output_schema_version: "v2", model_revision: "mock-deterministic-v2-grounded",
    adapter_revision: null, seller_profile_id: null, seller_profile_version: null,
    seller_profile_content_hash: null, seller_profile_kind: null,
    content_hash: `hash-${id}`, purpose: "annotation", author_label: null, review_status: null,
    content: task === "company_summary"
      ? { company_summary: "The imported record lists Acme Dental.", evidence: [], unknowns: ["budget"], hypotheses: [], seller_relevance: null, confidence: "low" }
      : { subject: "Hello", email_body: "Original body", lead_facts_used: ["fact-company_name"], capabilities_used: [], claims_used: [], unknowns_acknowledged: [], call_note: "", confidence: "low" },
  };
}

function detail(id: string, overrides: Partial<AnnotationCandidateDetail> = {}): AnnotationCandidateDetail {
  const task = (overrides.task ?? "company_summary") as AnnotationCandidateDetail["task"];
  return {
    id, queue: "pilot-v1", position: 1, task, split: "train", group_key: "groupkey0123456789",
    lead_id: "lead-1", company_name: `Company ${id}`, status: "pending_review",
    source_output_id: `out-${id}`, is_mock: true, manifest_version: "company-groups-v1",
    lead_facts: { company_name: `Company ${id}`, contact_email: null },
    source: { batch_source: "pdl_import", provider: "people_data_labs", source_snapshot_id: null,
      reported_acquisition_date: "2025-07-28", retrieved_at: null, license: null,
      freshness_note: "Facts may be outdated." },
    source_output: output(`out-${id}`, task) as unknown as AnnotationCandidateDetail["source_output"],
    target_output: null, latest_annotation: null, annotation_count: 0,
    ...overrides,
  };
}

function summaryRow(id: string, position: number, task = "company_summary") {
  return { id, queue: "pilot-v1", position, task, split: "train", group_key: "g", lead_id: "lead-1",
    company_name: `Company ${id}`, status: "pending_review", source_output_id: `out-${id}`, is_mock: true };
}

function deferred<T>() {
  let resolve!: (v: T) => void;
  let reject!: (e: unknown) => void;
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(getAnnotationSummary).mockResolvedValue({
    queue: "pilot-v1", manifest_version: "company-groups-v1", candidates: 100, unique_companies: 50,
    generated: 2, awaiting_generation: 98, pending_review: 2, reviewed_examples: 0,
    reviewed_unique_companies: 0, accepted: 0, corrected: 0, skipped: 0, mock_candidates: 2,
    by_task: {}, by_split: { train: 100 }, experiment_targets: { train: 400, validation: 100, test: 100 },
  });
  vi.mocked(getAnnotationProvider).mockResolvedValue({
    configured_provider: "mock", model_revision: "mock-deterministic-v2-grounded", is_mock: true,
    available: true, detail: "Deterministic mock generator.",
  });
  vi.mocked(getAnnotationCandidates).mockResolvedValue({
    items: [summaryRow("c1", 1), summaryRow("c2", 2, "outreach_email")] as never,
    total: 2, limit: 50, offset: 0, has_more: false,
  });
  vi.mocked(getAnnotationCandidate).mockImplementation((id: string) =>
    Promise.resolve(detail(id, id === "c2" ? { task: "outreach_email" } : {})));
  vi.mocked(submitAnnotation).mockResolvedValue({} as never);
});

async function open(name: RegExp) {
  render(<AnnotationPage />);
  fireEvent.click(await screen.findByRole("button", { name }));
}

function assess(support = "supported") {
  fireEvent.change(screen.getByLabelText("Factual support"), { target: { value: support } });
  fireEvent.change(screen.getByLabelText("Writing quality"), { target: { value: "4" } });
  fireEvent.change(screen.getByLabelText("Missing-information handling"), { target: { value: "good" } });
}

function lastSubmit(): AnnotationSubmit {
  return vi.mocked(submitAnnotation).mock.calls.at(-1)![1];
}

describe("annotation workbench", () => {
  it("shows progress honestly and labels mock candidates", async () => {
    await open(/#1 Company c1/);
    expect(await screen.findByLabelText("Pilot progress")).toHaveTextContent("0 of 100 pilot examples human-reviewed");
    expect(screen.getByText(/Mock candidate:/)).toBeInTheDocument();
    expect(screen.getByText(/not permission or readiness to contact/)).toBeInTheDocument();
    expect(screen.getByLabelText("Input facts")).toHaveTextContent("fact-company_name");
  });

  it("accepts only a fully supported output and sends the exact output, hash and UI timing", async () => {
    await open(/#1 Company c1/);
    const accept = await screen.findByRole("button", { name: "Accept as target" });
    expect(accept).toBeDisabled();
    assess("partially_supported");
    expect(accept).toBeDisabled();
    assess("supported");
    await act(async () => fireEvent.click(accept));
    await screen.findByText("Saved as a human-reviewed training example.");
    const body = lastSubmit();
    expect(body.decision).toBe("accepted");
    expect(body.source_output_id).toBe("out-c1");
    expect(body.source_content_hash).toBe("hash-out-c1");
    expect(body.factual_support).toBe("supported");
    expect(body.timing?.interaction_count).toBeGreaterThan(0);
    expect(body.timing?.idle_threshold_ms).toBe(60000);
    expect(body).not.toHaveProperty("reviewer_label");
  });

  it("saves a correction, keeps entries after a failure, and retries with the same submission id", async () => {
    vi.mocked(submitAnnotation)
      .mockRejectedValueOnce(new APIError(0, "Network error"))
      .mockResolvedValueOnce({} as never);
    await open(/#2 Company c2/);
    fireEvent.click(await screen.findByRole("button", { name: "Write a correction" }));
    fireEvent.change(screen.getByLabelText("Corrected email body"), { target: { value: "Corrected body text" } });
    assess("unsupported");
    fireEvent.click(screen.getByRole("button", { name: "Save correction" }));
    await screen.findByText("Network error");
    expect(screen.getByLabelText("Corrected email body")).toHaveValue("Corrected body text");
    const first = lastSubmit();

    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Save correction" })));
    await screen.findByText("Saved as a human-reviewed training example.");
    const second = lastSubmit();
    expect(second.submission_id).toBe(first.submission_id);
    expect(second.corrected_content).toMatchObject({ email_body: "Corrected body text", subject: "Hello" });
    expect(second.timing?.flags).toContain("resumed_after_failure");
  });

  it("requires a reason to skip", async () => {
    await open(/#1 Company c1/);
    const skip = await screen.findByRole("button", { name: "Skip as unsuitable" });
    expect(skip).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Skip reason"), { target: { value: "Record too sparse" } });
    await act(async () => fireEvent.click(skip));
    expect(lastSubmit()).toMatchObject({ decision: "skipped", skip_reason: "Record too sparse" });
    expect(lastSubmit().factual_support).toBeUndefined();
  });

  it("generates only with the configured provider and shows a refusal", async () => {
    vi.mocked(getAnnotationCandidate).mockResolvedValue(detail("c1", { source_output: null, source_output_id: null, status: "awaiting_generation", is_mock: null }));
    vi.mocked(generateAnnotationCandidate).mockRejectedValueOnce(
      new APIError(409, "Conflict", "Outreach candidates need an active seller profile revision."),
    ).mockResolvedValueOnce(detail("c1"));
    await open(/#1 Company c1/);
    const generate = await screen.findByRole("button", { name: "Generate with mock" });
    await act(async () => fireEvent.click(generate));
    expect(await screen.findByText(/need an active seller profile/)).toBeInTheDocument();
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Generate with mock" })));
    expect(generateAnnotationCandidate).toHaveBeenLastCalledWith("c1", "mock");
    expect(await screen.findByRole("button", { name: "Accept as target" })).toBeInTheDocument();
  });

  it("ignores a late candidate response after switching to another candidate", async () => {
    const slow = deferred<AnnotationCandidateDetail>();
    vi.mocked(getAnnotationCandidate).mockImplementation((id: string) =>
      id === "c1" ? slow.promise : Promise.resolve(detail("c2", { task: "outreach_email" })));
    render(<AnnotationPage />);
    fireEvent.click(await screen.findByRole("button", { name: /#1 Company c1/ }));
    fireEvent.click(screen.getByRole("button", { name: /#2 Company c2/ }));
    expect(await screen.findByText(/Company c2 · Outreach/)).toBeInTheDocument();
    await act(async () => slow.resolve(detail("c1")));
    expect(screen.queryByText(/Company c1 · Company summary/)).not.toBeInTheDocument();
  });

  it("a submission finishing after switching candidates does not report on the new one", async () => {
    const pending = deferred<never>();
    vi.mocked(submitAnnotation).mockReturnValueOnce(pending.promise);
    await open(/#1 Company c1/);
    const accept = await screen.findByRole("button", { name: "Accept as target" });
    assess();
    fireEvent.click(accept);
    fireEvent.click(screen.getByRole("button", { name: /#2 Company c2/ }));
    await screen.findByText(/Company c2 · Outreach/);
    await act(async () => pending.resolve(undefined as never));
    expect(screen.queryByText("Saved as a human-reviewed training example.")).not.toBeInTheDocument();
    expect(within(screen.getByRole("button", { name: "Accept as target" })).queryByRole("status")).toBeNull();
  });

  it("shows a load failure with retry", async () => {
    vi.mocked(getAnnotationCandidate)
      .mockRejectedValueOnce(new APIError(503, "Unavailable", "Candidate unavailable"))
      .mockResolvedValue(detail("c1"));
    await open(/#1 Company c1/);
    fireEvent.click(await screen.findByRole("button", { name: "Retry loading" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Accept as target" })).toBeInTheDocument());
  });
});
