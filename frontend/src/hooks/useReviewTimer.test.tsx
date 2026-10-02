import { act, renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { advance, recordActivity, setHidden, snapshot, startTimer, useReviewTimer } from "./useReviewTimer";

const IDLE = 60_000;

describe("review timing accounting", () => {
  it("counts active time only within the idle threshold of the last interaction", () => {
    const s = startTimer(0);
    recordActivity(s, 10_000, IDLE);
    // 10s active before, then 60s active after the interaction, then idle.
    const t = snapshot(s, 200_000, IDLE);
    expect(t.wall_ms).toBe(200_000);
    expect(t.active_ms).toBe(70_000);
    expect(t.idle_ms).toBe(130_000);
    expect(t.hidden_ms).toBe(0);
    expect(t.interaction_count).toBe(1);
    expect(t.flags).toContain("had_idle_gap");
  });

  it("separates hidden-tab time from active and idle time", () => {
    const s = startTimer(0);
    recordActivity(s, 5_000, IDLE);
    setHidden(s, true, 20_000, IDLE);
    setHidden(s, false, 320_000, IDLE); // 5 minutes away
    recordActivity(s, 330_000, IDLE);
    const t = snapshot(s, 340_000, IDLE);
    expect(t.hidden_ms).toBe(300_000);
    expect(t.active_ms).toBe(40_000);
    expect(t.idle_ms).toBe(0);
    expect(t.flags).toContain("was_hidden");
    expect(t.flags).not.toContain("had_idle_gap");
  });

  it("reports a session with no interaction honestly", () => {
    const s = startTimer(0);
    advance(s, 30_000, IDLE);
    const t = snapshot(s, 30_000, IDLE);
    expect(t.interaction_count).toBe(0);
    expect(t.active_ms + t.idle_ms + t.hidden_ms).toBe(t.wall_ms);
  });
});

describe("useReviewTimer", () => {
  it("records real DOM activity and restarts for a new session key", () => {
    let now = 1_000;
    const clock = () => now;
    const { result, rerender } = renderHook(({ key }) => useReviewTimer(key, { clock }), {
      initialProps: { key: "candidate-a" as string | null },
    });
    now = 4_000;
    act(() => { window.dispatchEvent(new KeyboardEvent("keydown")); });
    now = 9_000;
    const first = result.current.read()!;
    expect(first.wall_ms).toBe(8_000);
    expect(first.active_ms).toBe(8_000);
    expect(first.interaction_count).toBe(1);

    rerender({ key: "candidate-b" });
    now = 10_000;
    const second = result.current.read()!;
    expect(second.wall_ms).toBe(1_000);
    expect(second.interaction_count).toBe(0);
    result.current.flag("resumed_after_failure");
    expect(result.current.read()!.flags).toContain("resumed_after_failure");
  });

  it("reads nothing without a session", () => {
    const { result } = renderHook(() => useReviewTimer(null));
    expect(result.current.read()).toBeUndefined();
  });
});
