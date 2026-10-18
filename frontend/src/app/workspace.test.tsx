import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import ImportsPage from "@/app/imports/page";
import LeadsPage from "@/app/leads/page";
import TodayPage from "@/app/page";
import SettingsPage from "@/app/settings/page";
import { AppShell } from "@/components/AppShell";
import type { AIStatus, InboxItem, InboxPage } from "@/lib/api";

/** The rep's workspace: Today, Leads, Imports, Settings and the sidebar. */

const nav = vi.hoisted(() => ({ push: vi.fn(), replace: vi.fn(), pathname: "/", search: new URLSearchParams() }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: nav.push, replace: nav.replace }),
  usePathname: () => nav.pathname,
  useSearchParams: () => nav.search,
}));

const state = vi.hoisted(() => ({
  session: { username: "sam@example.com", role: "operator", expires_at: "", csrf_token: "c" } as Record<string, string>,
  status: null as unknown,
}));
vi.mock("@/components/AuthProvider", () => ({ useAuth: () => ({ session: state.session, signOut: vi.fn(), signOutError: null }) }));
vi.mock("@/components/AIStatusProvider", async () => {
  const actual = await vi.importActual<typeof import("@/components/AIStatusProvider")>("@/components/AIStatusProvider");
  return { ...actual, useAIStatus: () => ({ status: state.status, refresh: vi.fn() }) };
});

const api = vi.hoisted(() => ({
  inbox: null as unknown,
  batches: { items: [], total: 0, limit: 25, offset: 0, has_more: false },
  ranking: null as unknown,
  rankError: null as unknown,
}));
vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    getInbox: vi.fn(() => Promise.resolve(api.inbox)),
    getBatches: vi.fn(() => Promise.resolve(api.batches)),
    uploadBatch: vi.fn(() => Promise.resolve({ batch_id: "b1", batch_name: "Q4", total_rows: 3, valid_rows: 2, invalid_rows: 1,
      errors: [{ row_number: 3, field: "company_name", message: "required" }] })),
    scoreBatchFit: vi.fn(() => (api.rankError ? Promise.reject(api.rankError) : Promise.resolve({}))),
    getRankingStatus: vi.fn(() => Promise.resolve(api.ranking)),
    recalculateRankings: vi.fn(() => Promise.resolve({ batch_id: null, profile_id: "p", profile_version: "2",
      targeting_hash: "h", attempted: 3, newly_scored: 3, skipped_unchanged: 0, failed: 0 })),
    createBatchJob: vi.fn((batchId: string) => Promise.resolve({ id: `job-${batchId}` })),
    getSellerProfileStatus: vi.fn(() => Promise.resolve({ state: "missing", latest_version: null, activation_sequence: 0,
      last_activation: null, active_profile: null, latest_is_active: false })),
  };
});

import { APIError, createBatchJob, getInbox, recalculateRankings, scoreBatchFit, uploadBatch } from "@/lib/api";
import type { RankingStatus } from "@/types/api";

const FINE_TUNED: AIStatus = { mode: "fine_tuned", label: "GTMFlow fine-tuned model (Qwen3-4B + LoRA)",
  detail: "Drafts are written by the fine-tuned model.", fine_tuned_selected: true, fine_tuned_connected: true,
  fallback_enabled: true, base_model: "Qwen/Qwen3-4B-Instruct-2507", adapter: "phase8-qwen3-4b-lora-v1/epoch3" };
const MOCK: AIStatus = { ...FINE_TUNED, mode: "mock", label: "Demo generator (no AI model)", fine_tuned_selected: false,
  fine_tuned_connected: false, fallback_enabled: false, detail: "Drafts come from a built-in demo generator." };

function item(overrides: Partial<InboxItem>): InboxItem {
  return { id: "l1", company_name: "Cascade Modular Homes", contact_name: "Sarah Chen", contact_title: "VP Operations",
    industry: "Housing", batch_id: "b1", batch_name: "Q4", priority: "Hot", score: 94, band: "strong_match",
    evidence_coverage_pct: 100, ranking_reason: "Target-customer fit: matches industry, company size.",
    ranking_outdated: false, stage: "to_review",
    delivery_unknown: false, draft_model: "qwen3-4b-lora-v1", blocked: false, updated_at: "2026-10-02T00:00:00Z", ...overrides };
}

function ranking(overrides: Partial<RankingStatus> = {}): RankingStatus {
  return { state: "ready", message: "Leads are ranked.", profile: { source: "active_revision", profile_id: "p",
    seller_profile_id: "p", version: "2", profile_kind: "seller", targeting: { industries: ["Housing"], locations: [],
      company_sizes: [], contact_roles: [], keywords: [] }, targeting_hash: "h", configured_criteria: ["industry"] },
    scorer_version: "fit-scorer-v2", normalization_version: "fit-norm-v2", weights: {}, labels: {}, hot_min: 75,
    warm_min: 45, coverage_min_pct: 50, partial_credit: 0.5, total_leads: 3, current_rankings: 3,
    outdated_rankings: 0, never_ranked: 0, ...overrides };
}

function page(items: InboxItem[], counts: Partial<InboxPage["counts"]> = {}): InboxPage {
  return { items, total: items.length, limit: 50, offset: 0, counts: { needs_score: 0, needs_draft: 0, to_review: 0,
    outdated: 0, rejected: 0, approved: 0, sent: 0, all: items.length, hot: 0, needs_data: 0, ranking_outdated: 0,
    delivery_unknown: 0, ...counts }, ranking: { state: "ready", profile_version: "2", profile_kind: "seller" } };
}

beforeEach(() => {
  vi.clearAllMocks();
  nav.pathname = "/";
  nav.search = new URLSearchParams();
  state.session = { username: "sam@example.com", role: "operator", expires_at: "", csrf_token: "c" };
  state.status = FINE_TUNED;
  api.inbox = page([]);
  api.ranking = ranking();
  api.rankError = null;
});

describe("Today", () => {
  it("offers an import or sample data when there are no leads", async () => {
    render(<TodayPage />);
    expect(await screen.findByText("Start with your lead list")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try with sample data" })).toBeInTheDocument();
    expect(screen.getByText("Download the CSV template")).toHaveAttribute("href", "/sample-leads.csv");
  });

  it("loads the sample through the normal import and ranking, then opens Leads", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(new Response("company_name\nAcme\n"))));
    try {
      render(<TodayPage />);
      const sample = await screen.findByRole("button", { name: "Try with sample data" });
      await act(async () => fireEvent.click(sample));
      expect(uploadBatch).toHaveBeenCalledWith(expect.any(File), "Sample leads");
      expect(scoreBatchFit).toHaveBeenCalledWith("b1");
      expect(nav.push).toHaveBeenCalledWith("/leads");
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("shows what needs attention, Hot first, and the drafting model", async () => {
    api.inbox = page([item({}), item({ id: "l2", company_name: "Sent Co", stage: "sent" }),
      item({ id: "l3", company_name: "Blocked Co", stage: "needs_draft", blocked: true })],
      { to_review: 1, sent: 1, needs_draft: 1, delivery_unknown: 1 });
    render(<TodayPage />);
    expect(await screen.findByText("Next up")).toBeInTheDocument();
    expect(screen.getByText("Cascade Modular Homes")).toBeInTheDocument();
    expect(screen.queryByText("Sent Co")).not.toBeInTheDocument(); // nothing to do
    expect(screen.queryByText("Blocked Co")).not.toBeInTheDocument(); // do-not-contact
    expect(screen.getByText("Drafts to review").closest("a")).toHaveAttribute("href", "/leads?stage=to_review");
    expect(screen.getByRole("alert")).toHaveTextContent("unknown outcome");
    expect(screen.getByText(FINE_TUNED.label)).toBeInTheDocument();
    expect(screen.getByText(/Good (morning|afternoon|evening), sam/)).toBeInTheDocument();
    expect(await screen.findByRole("link", { name: "Set up seller profile" })).toHaveAttribute("href", "/seller-profile");
  });
});

describe("Leads", () => {
  it("filters by the stage in the URL and links each lead", async () => {
    nav.search = new URLSearchParams("stage=approved&priority=Hot");
    api.inbox = page([item({ stage: "approved" })], { approved: 1 });
    render(<LeadsPage />);
    expect(await screen.findByRole("link", { name: "Cascade Modular Homes" })).toHaveAttribute("href", "/leads/l1");
    expect(getInbox).toHaveBeenCalledWith(expect.objectContaining({ stage: "approved", priority: "Hot" }));
    expect(screen.getByRole("tab", { name: /Ready to send/ })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByText("Fine-tuned model")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: /All/ }));
    expect(nav.replace).toHaveBeenCalledWith("/leads?priority=Hot");
  });

  it("searches after a short pause", async () => {
    api.inbox = page([item({})]);
    render(<LeadsPage />);
    await screen.findByRole("link", { name: "Cascade Modular Homes" });
    fireEvent.change(screen.getByLabelText("Search companies or contacts"), { target: { value: "casc" } });
    await waitFor(() => expect(getInbox).toHaveBeenLastCalledWith(expect.objectContaining({ q: "casc" })));
  });

  it("explains an empty workspace", async () => {
    render(<LeadsPage />);
    expect(await screen.findByText(/No leads yet/)).toBeInTheDocument();
  });

  it("shows each ranking with its reason, and needs data separately from Cold", async () => {
    api.inbox = page([item({}), item({ id: "l2", company_name: "Sparse Co", priority: null, score: 20, band: "insufficient_evidence",
      ranking_reason: "Not enough information to rank confidently: no data for company size." }),
    item({ id: "l3", company_name: "Old Co", priority: null, score: null, band: null, ranking_reason: null, ranking_outdated: true })]);
    render(<LeadsPage />);
    await screen.findByRole("link", { name: "Cascade Modular Homes" });
    const table = within(screen.getByRole("table"));
    expect(table.getByText("Needs data")).toBeInTheDocument();
    expect(table.getByText("Outdated")).toBeInTheDocument();
    expect(screen.getByText("Target-customer fit: matches industry, company size.")).toBeInTheDocument();
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "needs_data" } });
    expect(nav.replace).toHaveBeenCalledWith("/leads?priority=needs_data");
  });

  it("asks for target customers instead of ranking with a fallback", async () => {
    api.ranking = ranking({ state: "setup_required", profile: null,
      message: "The active seller profile has no target customers." });
    render(<LeadsPage />);
    const banner = await screen.findByRole("region", { name: "Ranking setup" });
    expect(banner).toHaveTextContent("no target customers");
    expect(screen.getByRole("link", { name: "Open the seller profile" })).toHaveAttribute("href", "/seller-profile");
  });

  it("offers to recalculate outdated rankings and reloads the list", async () => {
    api.ranking = ranking({ outdated_rankings: 2, current_rankings: 1 });
    api.inbox = page([item({})]);
    render(<LeadsPage />);
    const button = await screen.findByRole("button", { name: "Recalculate rankings" });
    expect(screen.getByRole("region", { name: "Ranking status" })).toHaveTextContent("2 leads were ranked against earlier target customers");
    const before = vi.mocked(getInbox).mock.calls.length;
    await act(async () => fireEvent.click(button));
    expect(recalculateRankings).toHaveBeenCalledTimes(1);
    expect(await screen.findByText(/Ranked 3 leads against seller profile version 2/)).toBeInTheDocument();
    expect(vi.mocked(getInbox).mock.calls.length).toBeGreaterThan(before);
  });

  it("drafts selected ranked leads per import and never selects blocked or unranked leads", async () => {
    api.inbox = page([
      item({ id: "a1", company_name: "Alpha", batch_id: "b1" }),
      item({ id: "a2", company_name: "Beta", batch_id: "b2", priority: "Warm", score: 60, band: "partial_match" }),
      item({ id: "a3", company_name: "Blocked Co", blocked: true }),
      item({ id: "a4", company_name: "Unranked Co", priority: null, score: null, band: null }),
    ]);
    render(<LeadsPage />);
    await screen.findByRole("link", { name: "Alpha" });
    expect(screen.getByRole("checkbox", { name: "Select Blocked Co" })).toBeDisabled();
    expect(screen.getByRole("checkbox", { name: "Select Unranked Co" })).toBeDisabled();
    fireEvent.click(screen.getByRole("checkbox", { name: "Select Alpha" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Select Beta" }));
    expect(screen.getByText("2 selected")).toBeInTheDocument();
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Draft outreach for selected" })));
    expect(createBatchJob).toHaveBeenCalledWith("b1", "selected_outreach", { lead_ids: ["a1"] });
    expect(createBatchJob).toHaveBeenCalledWith("b2", "selected_outreach", { lead_ids: ["a2"] });
    expect(screen.getByRole("status")).toHaveTextContent("nothing is approved or sent automatically");
  });
});

describe("Imports", () => {
  it("imports, ranks right away and reports row errors", async () => {
    render(<ImportsPage />);
    const file = new File(["company_name\nAcme\n"], "leads.csv", { type: "text/csv" });
    fireEvent.change(screen.getByLabelText("CSV file"), { target: { files: [file] } });
    fireEvent.change(screen.getByPlaceholderText("Q4 target accounts"), { target: { value: "Q4" } });
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Import and rank" })));
    expect(uploadBatch).toHaveBeenCalledWith(file, "Q4");
    expect(scoreBatchFit).toHaveBeenCalledWith("b1");
    expect(screen.getByRole("status")).toHaveTextContent("Imported 2 of 3 rows");
    expect(screen.getByRole("status")).toHaveTextContent("Ranked against your seller profile");
    expect(screen.getByText(/Row 3, company_name: required/)).toBeInTheDocument();
  });

  it("keeps the import when ranking needs setup and says so", async () => {
    api.rankError = new APIError(409, "Conflict", "The active seller profile has no target customers.");
    render(<ImportsPage />);
    const file = new File(["company_name\nAcme\n"], "leads.csv", { type: "text/csv" });
    fireEvent.change(screen.getByLabelText("CSV file"), { target: { files: [file] } });
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Import and rank" })));
    expect(screen.getByRole("status")).toHaveTextContent("Imported, but not ranked: The active seller profile has no target customers.");
    expect(screen.getByRole("link", { name: "Set up target customers" })).toHaveAttribute("href", "/seller-profile");
  });
});

describe("Settings and the sidebar", () => {
  it("explains the drafting model and how to connect the fine-tuned one", async () => {
    state.status = MOCK;
    render(<SettingsPage />);
    expect(screen.getByText(MOCK.label)).toBeInTheDocument();
    expect(screen.getByText("How to connect the fine-tuned model")).toBeInTheDocument();
    expect(await screen.findByText(/No seller profile is active/)).toBeInTheDocument();
  });

  it("hides connection instructions from guests", () => {
    state.status = MOCK;
    state.session = { username: "guest-1", role: "guest", expires_at: "", csrf_token: "g" };
    render(<SettingsPage />);
    expect(screen.queryByText("How to connect the fine-tuned model")).not.toBeInTheDocument();
    expect(screen.getByText("Guest (temporary)")).toBeInTheDocument();
  });

  it("marks the current section and shows the model and a guest badge", () => {
    nav.pathname = "/batches/b1";
    state.session = { username: "guest-1", role: "guest", expires_at: "", csrf_token: "g" };
    render(<AppShell><p>content</p></AppShell>);
    const main = screen.getAllByRole("navigation", { name: "Main" })[0];
    expect(main.querySelector('[aria-current="page"]')).toHaveTextContent("Imports");
    expect(screen.getByText("Fine-tuned model")).toBeInTheDocument();
    expect(screen.getByText("Guest")).toBeInTheDocument();
    expect(screen.queryByText("guest-1")).not.toBeInTheDocument();
  });

  it("shows only the logo around the sign-in page", () => {
    nav.pathname = "/login";
    render(<AppShell><p>sign-in form</p></AppShell>);
    expect(screen.queryByRole("navigation", { name: "Main" })).not.toBeInTheDocument();
    expect(screen.getByText("sign-in form")).toBeInTheDocument();
  });
});
