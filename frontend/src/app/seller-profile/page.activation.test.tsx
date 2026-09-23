import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  APIError,
  activateSellerProfile,
  deactivateSellerProfile,
  getSellerProfile,
  getSellerProfileDemoTemplate,
  getSellerProfileStatus,
  getSellerProfileVersions,
  saveSellerProfile,
} from "@/lib/api";
import type { Page, SellerProfile, SellerProfileContent, SellerProfileStatus } from "@/types/api";
import SellerProfilePage from "./page";

/**
 * Phase 5: rendered activation behavior of the seller-profile page (jsdom +
 * Testing Library, real clicks; only @/lib/api is faked).
 */

vi.mock("@/lib/api", async () => ({
  ...await vi.importActual<typeof import("@/lib/api")>("@/lib/api"),
  getSellerProfile: vi.fn(),
  getSellerProfileVersions: vi.fn(),
  saveSellerProfile: vi.fn(),
  getSellerProfileStatus: vi.fn(),
  getSellerProfileDemoTemplate: vi.fn(),
  activateSellerProfile: vi.fn(),
  deactivateSellerProfile: vi.fn(),
}));

function content(kind: "seller" | "demo" = "seller"): SellerProfileContent {
  return {
    profile_kind: kind, company_name: kind === "demo" ? "Demo Seller" : "Fixture Seller",
    product_name: "Queue tool", value_proposition: "Organizes requests.",
    target_customer: "US medical practices.", capabilities: [], proof_points: [], exclusions: [],
  };
}

function row(version: number, kind: "seller" | "demo" = "seller"): SellerProfile {
  return {
    id: `profile-${version}`, version, status: "draft", content_hash: `hash-${version}-abcdefabcdef`,
    editor_label: "local-demo-unauthenticated", created_at: "2026-09-23T00:00:00Z",
    profile: content(kind),
  };
}

function status(active: SellerProfile | null, sequence: number, latest = active?.version ?? 1): SellerProfileStatus {
  return {
    state: active ? "active" : "draft_only",
    latest_version: latest,
    activation_sequence: sequence,
    last_activation: null,
    active_profile: active ? { ...active, status: "active" } : null,
    latest_is_active: active !== null && active.version === latest,
  };
}

const MISSING: SellerProfileStatus = {
  state: "missing", latest_version: null, activation_sequence: 0,
  last_activation: null, active_profile: null, latest_is_active: false,
};

function page(items: SellerProfile[]): Page<SellerProfile> {
  return { items, total: items.length, limit: 50, offset: 0, has_more: false };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((r) => { resolve = r; });
  return { promise, resolve };
}

function statusRegion() {
  return screen.getByRole("region", { name: "Seller profile status" });
}

beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(getSellerProfile).mockResolvedValue(row(1));
  vi.mocked(getSellerProfileVersions).mockResolvedValue(page([row(1)]));
  vi.mocked(getSellerProfileStatus).mockResolvedValue(status(null, 0));
});

describe("seller profile activation", () => {
  it("shows that nothing is saved or active", async () => {
    vi.mocked(getSellerProfile).mockRejectedValue(new APIError(404, "none"));
    vi.mocked(getSellerProfileVersions).mockResolvedValue(page([]));
    vi.mocked(getSellerProfileStatus).mockResolvedValue(MISSING);
    render(<SellerProfilePage />);
    expect(await screen.findByText("No seller profile saved")).toBeInTheDocument();
    expect(screen.getByText(/Outreach generation is unavailable/)).toBeInTheDocument();
  });

  it("activates a reviewed revision only after explicit confirmation", async () => {
    vi.mocked(activateSellerProfile).mockResolvedValue({} as never);
    render(<SellerProfilePage />);
    expect(await screen.findByText("No seller profile is active")).toBeInTheDocument();

    const activate = await screen.findByRole("button", { name: "Activate version 1" });
    expect(activate).toBeDisabled();
    fireEvent.click(screen.getByLabelText("I reviewed version 1 and want new outreach drafts to use it."));
    expect(activate).toBeEnabled();

    vi.mocked(getSellerProfileStatus).mockResolvedValue(status(row(1), 1));
    fireEvent.click(activate);
    await screen.findByText("Version 1 is now active. New outreach drafts will use it.");
    expect(activateSellerProfile).toHaveBeenCalledWith("profile-1", 0, false);
    expect(within(statusRegion()).getByText("Active: version 1 · Fixture Seller")).toBeInTheDocument();
    expect(await screen.findByText("Version 1 · Fixture Seller · Active seller profile")).toBeInTheDocument();
    // The active revision no longer offers activation.
    expect(screen.queryByRole("button", { name: "Activate version 1" })).not.toBeInTheDocument();
  });

  it("requires a separate acknowledgement for a demonstration profile", async () => {
    vi.mocked(getSellerProfileVersions).mockResolvedValue(page([row(1, "demo")]));
    vi.mocked(activateSellerProfile).mockResolvedValue({} as never);
    render(<SellerProfilePage />);
    const activate = await screen.findByRole("button", { name: "Activate version 1" });
    fireEvent.click(screen.getByLabelText("I reviewed version 1 and want new outreach drafts to use it."));
    expect(activate).toBeDisabled();
    fireEvent.click(screen.getByLabelText("I understand version 1 is a demonstration profile, not a real offer."));
    expect(activate).toBeEnabled();

    vi.mocked(getSellerProfileStatus).mockResolvedValue(status(row(1, "demo"), 1));
    fireEvent.click(activate);
    await waitFor(() => expect(activateSellerProfile).toHaveBeenCalledWith("profile-1", 0, true));
    expect(await within(statusRegion()).findByText("Active: version 1 · Demo Seller · Demonstration profile")).toBeInTheDocument();
  });

  it("reports an activation conflict and reloads what is actually active", async () => {
    vi.mocked(getSellerProfileVersions).mockResolvedValue(page([row(2), row(1)]));
    vi.mocked(getSellerProfileStatus).mockResolvedValueOnce(status(null, 0, 2));
    vi.mocked(activateSellerProfile).mockRejectedValue(
      new APIError(409, "Conflict", "The active seller profile changed. Reload before activating."),
    );
    render(<SellerProfilePage />);
    const activate = await screen.findByRole("button", { name: "Activate version 2" });
    fireEvent.click(screen.getByLabelText("I reviewed version 2 and want new outreach drafts to use it."));

    // Another tab activated version 1 in the meantime.
    vi.mocked(getSellerProfileStatus).mockResolvedValue(status(row(1), 1, 2));
    fireEvent.click(activate);
    expect(await screen.findByText(/The active seller profile changed.*has been reloaded/)).toBeInTheDocument();
    expect(await within(statusRegion()).findByText("Active: version 1 · Fixture Seller")).toBeInTheDocument();
    expect(within(statusRegion()).getByText("Version 2 is a newer draft and is not active.")).toBeInTheDocument();
    // The next attempt uses the reloaded activation sequence.
    vi.mocked(activateSellerProfile).mockResolvedValue({} as never);
    fireEvent.click(screen.getByRole("button", { name: "Activate version 2" }));
    await waitFor(() => expect(activateSellerProfile).toHaveBeenLastCalledWith("profile-2", 1, false));
  });

  it("keeps state after a failed activation and retries it", async () => {
    vi.mocked(activateSellerProfile)
      .mockRejectedValueOnce(new APIError(0, "Network error"))
      .mockResolvedValueOnce({} as never);
    render(<SellerProfilePage />);
    const activate = await screen.findByRole("button", { name: "Activate version 1" });
    fireEvent.click(screen.getByLabelText("I reviewed version 1 and want new outreach drafts to use it."));
    fireEvent.click(activate);
    await screen.findByText("Network error");
    expect(within(statusRegion()).getByText("No seller profile is active")).toBeInTheDocument();
    expect(screen.getByLabelText("I reviewed version 1 and want new outreach drafts to use it.")).toBeChecked();

    vi.mocked(getSellerProfileStatus).mockResolvedValue(status(row(1), 1));
    fireEvent.click(screen.getByRole("button", { name: "Activate version 1" }));
    await screen.findByText("Version 1 is now active. New outreach drafts will use it.");
    expect(activateSellerProfile).toHaveBeenCalledTimes(2);
    expect(screen.queryByText("Network error")).not.toBeInTheDocument();
  });

  it("disables every activation control while one is pending", async () => {
    vi.mocked(getSellerProfileVersions).mockResolvedValue(page([row(2), row(1)]));
    const pending = deferred<never>();
    vi.mocked(activateSellerProfile).mockReturnValue(pending.promise);
    render(<SellerProfilePage />);
    await screen.findByRole("button", { name: "Activate version 2" });
    fireEvent.click(screen.getByLabelText("I reviewed version 2 and want new outreach drafts to use it."));
    fireEvent.click(screen.getByLabelText("I reviewed version 1 and want new outreach drafts to use it."));
    fireEvent.click(screen.getByRole("button", { name: "Activate version 2" }));
    expect(screen.getByRole("button", { name: "Activate version 1" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Activate version 1" }));
    expect(activateSellerProfile).toHaveBeenCalledTimes(1);
  });

  it("ignores a status response that arrives after a newer one", async () => {
    const initial = deferred<SellerProfileStatus>();
    vi.mocked(getSellerProfile).mockRejectedValue(new APIError(404, "none"));
    vi.mocked(getSellerProfileVersions).mockResolvedValue(page([]));
    vi.mocked(getSellerProfileStatus)
      .mockReturnValueOnce(initial.promise)
      .mockResolvedValue(status(null, 0, 1));
    vi.mocked(saveSellerProfile).mockResolvedValue(row(1));
    render(<SellerProfilePage />);
    fireEvent.change(await screen.findByLabelText("Company name"), { target: { value: "Fixture Seller" } });
    fireEvent.change(screen.getByLabelText("Product or service"), { target: { value: "Queue tool" } });
    fireEvent.change(screen.getByLabelText(/^Value proposition/), { target: { value: "Organizes requests." } });
    fireEvent.change(screen.getByLabelText(/^Target customers/), { target: { value: "US medical practices." } });
    fireEvent.click(screen.getByRole("button", { name: "Save draft" }));
    expect(await screen.findByText("No seller profile is active")).toBeInTheDocument();

    await act(async () => { initial.resolve(MISSING); });
    expect(screen.getByText("No seller profile is active")).toBeInTheDocument();
    expect(screen.queryByText("No seller profile saved")).not.toBeInTheDocument();
  });

  it("deactivates the active revision with the current sequence", async () => {
    vi.mocked(getSellerProfileStatus).mockResolvedValueOnce(status(row(1), 3));
    vi.mocked(deactivateSellerProfile).mockResolvedValue({} as never);
    render(<SellerProfilePage />);
    fireEvent.click(await within(await screen.findByRole("region", { name: "Seller profile status" })).findByRole("button", { name: "Deactivate" }));
    vi.mocked(getSellerProfileStatus).mockResolvedValue(status(null, 4));
    await screen.findByText(/No seller profile is active. Outreach generation is unavailable/);
    expect(deactivateSellerProfile).toHaveBeenCalledWith(3);
    expect(await within(statusRegion()).findByText("No seller profile is active")).toBeInTheDocument();
  });

  it("status failure is shown with retry and does not hide the editor", async () => {
    vi.mocked(getSellerProfileStatus)
      .mockRejectedValueOnce(new APIError(503, "Unavailable", "Status unavailable"))
      .mockResolvedValue(status(row(1), 1));
    render(<SellerProfilePage />);
    expect(await screen.findByText("Status unavailable")).toBeInTheDocument();
    expect(await screen.findByLabelText("Company name")).toHaveValue("Fixture Seller");
    fireEvent.click(screen.getByRole("button", { name: "Retry status" }));
    expect(await within(await screen.findByRole("region", { name: "Seller profile status" })).findByText("Active: version 1 · Fixture Seller")).toBeInTheDocument();
  });

  it("loads the demonstration template into the form without saving or activating", async () => {
    vi.mocked(getSellerProfile).mockRejectedValue(new APIError(404, "none"));
    vi.mocked(getSellerProfileVersions).mockResolvedValue(page([]));
    vi.mocked(getSellerProfileStatus).mockResolvedValue(MISSING);
    vi.mocked(getSellerProfileDemoTemplate).mockResolvedValue({
      ...content("demo"), company_name: "GTMFlow (demonstration)",
    });
    render(<SellerProfilePage />);
    fireEvent.click(await screen.findByRole("button", { name: "Load GTMFlow demonstration template" }));
    expect(await screen.findByText(/Nothing is saved or activated/)).toBeInTheDocument();
    expect(screen.getByLabelText("Company name")).toHaveValue("GTMFlow (demonstration)");
    expect(screen.getByLabelText("Profile purpose")).toHaveValue("demo");
    expect(saveSellerProfile).not.toHaveBeenCalled();
    expect(activateSellerProfile).not.toHaveBeenCalled();
  });
});
