import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
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

const api = vi.hoisted(() => ({ inbox: null as unknown, batches: { items: [], total: 0, limit: 25, offset: 0, has_more: false } }));
vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    getInbox: vi.fn(() => Promise.resolve(api.inbox)),
    getBatches: vi.fn(() => Promise.resolve(api.batches)),
    uploadBatch: vi.fn(() => Promise.resolve({ batch_id: "b1", batch_name: "Q4", total_rows: 3, valid_rows: 2, invalid_rows: 1,
      errors: [{ row_number: 3, field: "company_name", message: "required" }] })),
    scoreBatch: vi.fn(() => Promise.resolve({})),
    getSellerProfileStatus: vi.fn(() => Promise.resolve({ state: "missing", latest_version: null, activation_sequence: 0,
      last_activation: null, active_profile: null, latest_is_active: false })),
  };
});

import { getInbox, scoreBatch, uploadBatch } from "@/lib/api";

const FINE_TUNED: AIStatus = { mode: "fine_tuned", label: "GTMFlow fine-tuned model (Qwen3-4B + LoRA)",
  detail: "Drafts are written by the fine-tuned model.", fine_tuned_selected: true, fine_tuned_connected: true,
  fallback_enabled: true, base_model: "Qwen/Qwen3-4B-Instruct-2507", adapter: "phase8-qwen3-4b-lora-v1/epoch3" };
const MOCK: AIStatus = { ...FINE_TUNED, mode: "mock", label: "Demo generator (no AI model)", fine_tuned_selected: false,
  fine_tuned_connected: false, fallback_enabled: false, detail: "Drafts come from a built-in demo generator." };

function item(overrides: Partial<InboxItem>): InboxItem {
  return { id: "l1", company_name: "Cascade Modular Homes", contact_name: "Sarah Chen", contact_title: "VP Operations",
    industry: "Housing", batch_id: "b1", batch_name: "Q4", priority: "Hot", score: 94, stage: "to_review",
    delivery_unknown: false, draft_model: "qwen3-4b-lora-v1", blocked: false, updated_at: "2026-10-02T00:00:00Z", ...overrides };
}

function page(items: InboxItem[], counts: Partial<InboxPage["counts"]> = {}): InboxPage {
  return { items, total: items.length, limit: 50, offset: 0, counts: { needs_score: 0, needs_draft: 0, to_review: 0,
    outdated: 0, rejected: 0, approved: 0, sent: 0, all: items.length, hot: 0, delivery_unknown: 0, ...counts } };
}

beforeEach(() => {
  vi.clearAllMocks();
  nav.pathname = "/";
  nav.search = new URLSearchParams();
  state.session = { username: "sam@example.com", role: "operator", expires_at: "", csrf_token: "c" };
  state.status = FINE_TUNED;
  api.inbox = page([]);
});

describe("Today", () => {
  it("offers an import or sample data when there are no leads", async () => {
    render(<TodayPage />);
    expect(await screen.findByText("Start with your lead list")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try with sample data" })).toBeInTheDocument();
    expect(screen.getByText("Download the CSV template")).toHaveAttribute("href", "/sample-leads.csv");
  });

  it("loads the sample through the normal import and scoring, then opens Leads", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(new Response("company_name\nAcme\n"))));
    try {
      render(<TodayPage />);
      const sample = await screen.findByRole("button", { name: "Try with sample data" });
      await act(async () => fireEvent.click(sample));
      expect(uploadBatch).toHaveBeenCalledWith(expect.any(File), "Sample leads");
      expect(scoreBatch).toHaveBeenCalledWith("b1");
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
});

describe("Imports", () => {
  it("imports, scores right away and reports row errors", async () => {
    render(<ImportsPage />);
    const file = new File(["company_name\nAcme\n"], "leads.csv", { type: "text/csv" });
    fireEvent.change(screen.getByLabelText("CSV file"), { target: { files: [file] } });
    fireEvent.change(screen.getByPlaceholderText("Q4 target accounts"), { target: { value: "Q4" } });
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Import and score" })));
    expect(uploadBatch).toHaveBeenCalledWith(file, "Q4");
    expect(scoreBatch).toHaveBeenCalledWith("b1");
    expect(screen.getByRole("status")).toHaveTextContent("Imported 2 of 3 rows");
    expect(screen.getByText(/Row 3, company_name: required/)).toBeInTheDocument();
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
