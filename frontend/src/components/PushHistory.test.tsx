import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { PushHistory } from "./PushHistory";

const base = {
  id: "push-1", lead_id: "lead-1", integration_type: "slack", payload: { text: "Cascade" },
  response_text: "Delivery outcome unknown (ReadTimeout)", created_at: "2026-09-26T00:00:00Z",
  delivery_mode: "real" as const, attempt: 1, outcome_code: "outcome_unknown",
};

describe("PushHistory (Phase 11)", () => {
  it("explains an unknown outcome and records the operator's finding without resending", () => {
    const onResolve = vi.fn();
    render(<PushHistory pushes={[{ ...base, status: "unknown" }]} onResolve={onResolve} />);
    expect(screen.getByText("outcome unknown")).toBeInTheDocument();
    expect(screen.getByLabelText("Unknown delivery outcome")).toHaveTextContent("not resent automatically");
    fireEvent.click(screen.getByRole("button", { name: "It did not arrive" }));
    expect(onResolve).toHaveBeenCalledWith("push-1", "confirmed_not_delivered");
    fireEvent.click(screen.getByRole("button", { name: "It arrived" }));
    expect(onResolve).toHaveBeenLastCalledWith("push-1", "confirmed_delivered");
  });

  it("shows the delivery mode and a recorded resolution; delivered rows offer no actions", () => {
    render(<PushHistory pushes={[{ ...base, status: "success", resolution: "confirmed_delivered",
      resolution_note: "seen in #sales" }]} onResolve={vi.fn()} />);
    expect(screen.getByText(/real webhook · attempt 1/)).toBeInTheDocument();
    expect(screen.getByText(/Resolved: confirmed delivered \(seen in #sales\)/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "It arrived" })).not.toBeInTheDocument();
  });
});
