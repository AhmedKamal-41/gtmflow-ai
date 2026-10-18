import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { APIError, getSellerProfile, getSellerProfileStatus, getSellerProfileVersions, saveSellerProfile } from "@/lib/api";
import type { Page, SellerProfile } from "@/types/api";
import SellerProfilePage from "./page";

vi.mock("@/lib/api", async () => ({
  ...await vi.importActual<typeof import("@/lib/api")>("@/lib/api"),
  getSellerProfile: vi.fn(),
  getSellerProfileVersions: vi.fn(),
  saveSellerProfile: vi.fn(),
  getSellerProfileStatus: vi.fn(),
  getSellerProfileDemoTemplate: vi.fn(),
  activateSellerProfile: vi.fn(),
  deactivateSellerProfile: vi.fn(),
  getRankingStatus: vi.fn(),
}));

function row(version = 1): SellerProfile {
  return {
    id: `profile-${version}`, version, status: "draft", content_hash: "hash",
    editor_label: "local-demo-unauthenticated", created_at: "2026-09-23T00:00:00Z",
    profile: {
      profile_kind: "demo", company_name: "Fixture Seller", product_name: "Queue tool",
      value_proposition: "Organizes requests.", target_customer: "US medical practices; office managers; no size preference.",
      capabilities: [], proof_points: [], exclusions: [],
      target_preferences: { industries: ["Medical Practice"], locations: ["United States"],
        company_sizes: [{ min_employees: 11, max_employees: 75 }], contact_roles: ["Office Manager"], keywords: [] },
    },
  };
}

function page(items: SellerProfile[], total = items.length): Page<SellerProfile> {
  return { items, total, limit: 50, offset: 0, has_more: items.length < total };
}

beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(getSellerProfile).mockRejectedValue(new APIError(404, "No seller profile"));
  vi.mocked(getSellerProfileVersions).mockResolvedValue(page([]));
  vi.mocked(saveSellerProfile).mockResolvedValue(row());
  vi.mocked(getSellerProfileStatus).mockResolvedValue({
    state: "missing", latest_version: null, activation_sequence: 0,
    last_activation: null, active_profile: null, latest_is_active: false,
  });
});

describe("seller profile drafts", () => {
  it("separates what is sold from who it is for and explains how leads are ranked", async () => {
    render(<SellerProfilePage />);
    expect(await screen.findByRole("heading", { name: "What you sell" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Who you want to sell to" })).toBeInTheDocument();
    expect(screen.getByText(/“Hospital” does not match “Hospitality”/)).toBeInTheDocument();
    expect(screen.getByText("How leads are ranked")).toBeInTheDocument();
    expect(screen.getByText(/industry 35, company size 25, location 20, contact role 15, keywords 5/)).toBeInTheDocument();
    expect(screen.getByText(/Hot is 75 or more, Warm 45–74, Cold under 45/)).toBeInTheDocument();
  });

  it("keeps a saved custom size range and warns when no target customers are set", async () => {
    vi.mocked(getSellerProfile).mockResolvedValue(row(2));
    const empty = row(3);
    empty.profile = { ...empty.profile, target_preferences: { industries: [], locations: [], company_sizes: [], contact_roles: [], keywords: [] } };
    vi.mocked(saveSellerProfile).mockResolvedValue(empty);
    render(<SellerProfilePage />);
    expect(await screen.findByText("Custom 11–75")).toBeInTheDocument();
    expect(screen.getByLabelText(/^Industries/)).toHaveValue("Medical Practice");
    fireEvent.click(screen.getByRole("button", { name: "Save draft" }));
    await waitFor(() => expect(vi.mocked(saveSellerProfile).mock.calls[0][0].target_preferences.company_sizes)
      .toEqual([{ min_employees: 11, max_employees: 75 }]));
    expect(await screen.findByText(/It has no target customers, so leads cannot be ranked with it/)).toBeInTheDocument();
  });


  it("starts empty and saves supplied content with sourced claims", async () => {
    render(<SellerProfilePage />);
    expect(await screen.findByLabelText("Company name")).toHaveValue("");
    fireEvent.change(screen.getByLabelText("Company name"), { target: { value: "Fixture Seller" } });
    fireEvent.change(screen.getByLabelText("Product or service"), { target: { value: "Queue tool" } });
    fireEvent.change(screen.getByLabelText(/^Value proposition/), { target: { value: "Organizes requests." } });
    fireEvent.change(screen.getByLabelText(/^Target customers/), { target: { value: "US medical practices" } });
    fireEvent.change(screen.getByLabelText(/^Capabilities/), { target: { value: "Shared queue\n\nRequest assignment" } });
    fireEvent.click(screen.getByRole("button", { name: "Add proof point" }));
    fireEvent.change(screen.getByLabelText("Claim 1"), { target: { value: "Supports assignment" } });
    fireEvent.change(screen.getByLabelText("Source or reference 1"), { target: { value: "Feature specification" } });
    fireEvent.change(screen.getByLabelText(/^Industries/), { target: { value: "Medical Practice\n\nDental" } });
    fireEvent.change(screen.getByLabelText(/^Locations/), { target: { value: "United States" } });
    fireEvent.click(screen.getByLabelText("1–10"));
    fireEvent.click(screen.getByLabelText("11–50"));
    fireEvent.change(screen.getByLabelText(/^Contact roles/), { target: { value: "Office Manager" } });
    fireEvent.click(screen.getByRole("button", { name: "Save draft" }));
    await screen.findByText("Draft version 1 saved. It is not active for outreach or ranking until you activate it.");
    expect(saveSellerProfile).toHaveBeenCalledWith({
      profile_kind: "seller", company_name: "Fixture Seller", product_name: "Queue tool",
      value_proposition: "Organizes requests.", target_customer: "US medical practices",
      capabilities: ["Shared queue", "Request assignment"],
      proof_points: [{ claim: "Supports assignment", source: "Feature specification" }],
      exclusions: [],
      target_preferences: {
        industries: ["Medical Practice", "Dental"], locations: ["United States"],
        company_sizes: [{ min_employees: 1, max_employees: 10 }, { min_employees: 11, max_employees: 50 }],
        contact_roles: ["Office Manager"], keywords: [],
      },
    }, 0);
    expect(screen.getByText(/Draft only/)).toBeInTheDocument();
  });

  it("does not treat a load failure as an empty profile and offers retry", async () => {
    vi.mocked(getSellerProfile).mockRejectedValueOnce(new APIError(503, "Service unavailable")).mockResolvedValue(row(3));
    render(<SellerProfilePage />);
    fireEvent.click(await screen.findByRole("button", { name: "Retry loading" }));
    expect(screen.queryByRole("button", { name: "Save draft" })).not.toBeInTheDocument();
    expect(await screen.findByLabelText("Company name")).toHaveValue("Fixture Seller");
    expect(screen.getByText("Edit draft version 3")).toBeInTheDocument();
  });

  it("preserves edits after a failed save and retries with the same version", async () => {
    vi.mocked(getSellerProfile).mockResolvedValue(row(4));
    vi.mocked(saveSellerProfile).mockRejectedValueOnce(new APIError(0, "Network error")).mockResolvedValue(row(5));
    render(<SellerProfilePage />);
    fireEvent.change(await screen.findByLabelText("Company name"), { target: { value: "Edited seller" } });
    fireEvent.click(screen.getByRole("button", { name: "Save draft" }));
    await screen.findByText("Network error");
    expect(screen.getByLabelText("Company name")).toHaveValue("Edited seller");
    fireEvent.click(screen.getByRole("button", { name: "Save draft" }));
    await screen.findByText("Draft version 5 saved. It is not active for outreach or ranking until you activate it.");
    expect(saveSellerProfile).toHaveBeenCalledTimes(2);
    expect(vi.mocked(saveSellerProfile).mock.calls[0]).toEqual(vi.mocked(saveSellerProfile).mock.calls[1]);
    expect(vi.mocked(saveSellerProfile).mock.calls[1][1]).toBe(4);
  });

  it("preserves stale edits and loads newer content only after an explicit replacement", async () => {
    vi.mocked(getSellerProfile).mockResolvedValueOnce(row(1)).mockResolvedValue(row(2));
    vi.mocked(saveSellerProfile).mockRejectedValue(new APIError(409, "Changed elsewhere"));
    render(<SellerProfilePage />);
    fireEvent.change(await screen.findByLabelText("Company name"), { target: { value: "Unsaved edit" } });
    fireEvent.click(screen.getByRole("button", { name: "Save draft" }));
    const replace = await screen.findByRole("button", { name: "Load latest and replace this form" });
    expect(screen.getByLabelText("Company name")).toHaveValue("Unsaved edit");
    expect(screen.getByRole("button", { name: "Save draft" })).toBeDisabled();
    fireEvent.click(replace);
    await screen.findByText("Edit draft version 2");
    expect(screen.getByLabelText("Company name")).toHaveValue("Fixture Seller");
    fireEvent.click(screen.getByRole("button", { name: "Save draft" }));
    await waitFor(() => expect(saveSellerProfile).toHaveBeenLastCalledWith(row(2).profile, 2));
  });

  it("disables editing during save so the response cannot erase newer edits", async () => {
    vi.mocked(getSellerProfile).mockResolvedValue(row());
    let finish!: (value: SellerProfile) => void;
    vi.mocked(saveSellerProfile).mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
    render(<SellerProfilePage />);
    await screen.findByLabelText("Company name");
    fireEvent.click(screen.getByRole("button", { name: "Save draft" }));
    expect(screen.getByLabelText("Company name")).toBeDisabled();
    expect(screen.getByRole("button", { name: "Save draft" })).toBeDisabled();
    await act(async () => { finish(row(2)); });
    expect(screen.getByLabelText("Company name")).toBeEnabled();
  });

  it("shows historical drafts and preserves them when loading older versions fails", async () => {
    vi.mocked(getSellerProfileVersions)
      .mockResolvedValueOnce(page([row(2)], 2))
      .mockRejectedValueOnce(new Error("Versions unavailable"))
      .mockResolvedValueOnce({ ...page([row(1)], 2), offset: 1, has_more: false });
    render(<SellerProfilePage />);
    fireEvent.click(await screen.findByRole("button", { name: "Load older versions" }));
    await screen.findByText("Versions unavailable");
    expect(screen.getByText("Version 2 · Fixture Seller · Demonstration draft")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Retry versions" }));
    await screen.findByText("Version 1 · Fixture Seller · Demonstration draft");
    expect(getSellerProfileVersions).toHaveBeenLastCalledWith({ limit: 50, offset: 1 });
    expect(screen.queryByRole("button", { name: "Load older versions" })).not.toBeInTheDocument();
  });
});
