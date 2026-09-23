import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { APIError, getSellerProfile, getSellerProfileVersions, saveSellerProfile } from "@/lib/api";
import type { Page, SellerProfile } from "@/types/api";
import SellerProfilePage from "./page";

vi.mock("@/lib/api", async () => ({
  ...await vi.importActual<typeof import("@/lib/api")>("@/lib/api"),
  getSellerProfile: vi.fn(),
  getSellerProfileVersions: vi.fn(),
  saveSellerProfile: vi.fn(),
}));

function row(version = 1): SellerProfile {
  return {
    id: `profile-${version}`, version, status: "draft", content_hash: "hash",
    editor_label: "local-demo-unauthenticated", created_at: "2026-09-23T00:00:00Z",
    profile: {
      profile_kind: "demo", company_name: "Fixture Seller", product_name: "Queue tool",
      value_proposition: "Organizes requests.", target_customer: "US medical practices; office managers; no size preference.",
      capabilities: [], proof_points: [], exclusions: [],
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
});

describe("seller profile drafts", () => {
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
    fireEvent.click(screen.getByRole("button", { name: "Save draft" }));
    await screen.findByText("Draft version 1 saved. It is not active for outreach.");
    expect(saveSellerProfile).toHaveBeenCalledWith({
      profile_kind: "seller", company_name: "Fixture Seller", product_name: "Queue tool",
      value_proposition: "Organizes requests.", target_customer: "US medical practices",
      capabilities: ["Shared queue", "Request assignment"],
      proof_points: [{ claim: "Supports assignment", source: "Feature specification" }],
      exclusions: [],
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
    await screen.findByText("Draft version 5 saved. It is not active for outreach.");
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
