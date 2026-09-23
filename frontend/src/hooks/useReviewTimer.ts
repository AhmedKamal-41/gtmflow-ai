"use client";

import { useCallback, useEffect, useRef } from "react";

import type { ReviewTiming } from "@/types/api";

/**
 * Review timing measured only from actual UI activity (Phase 6).
 *
 * - wall_ms: time since this review session started (open-to-submit).
 * - active_ms: visible time within `idleThresholdMs` of the last user
 *   interaction (pointer, click, key, input/change, scroll, focus).
 * - hidden_ms: time the tab was hidden. Idle = wall - active - hidden.
 * - flags: was_hidden, had_idle_gap, plus any the caller marks
 *   (resumed_after_failure, content_changed_during_session).
 *
 * Nothing is inferred from creation timestamps. A session restarts when
 * `sessionKey` changes (a different candidate or output).
 */

export type TimerState = {
  startedAt: number;
  lastTick: number;
  lastActivity: number;
  activeMs: number;
  hiddenMs: number;
  interactions: number;
  hidden: boolean;
  flags: Set<ReviewTiming["flags"][number]>;
};

export function startTimer(now: number, hidden = false): TimerState {
  return {
    startedAt: now, lastTick: now, lastActivity: now, activeMs: 0, hiddenMs: 0,
    interactions: 0, hidden, flags: new Set(hidden ? ["was_hidden"] : []),
  };
}

/** Attribute the time since the last tick to hidden, active or idle. */
export function advance(state: TimerState, now: number, idleThresholdMs: number): void {
  const elapsed = now - state.lastTick;
  if (elapsed <= 0) return;
  const from = state.lastTick;
  state.lastTick = now;
  if (state.hidden) {
    state.hiddenMs += elapsed;
    return;
  }
  const activeUntil = state.lastActivity + idleThresholdMs;
  const active = Math.max(0, Math.min(now, activeUntil) - from);
  state.activeMs += active;
  if (active < elapsed) state.flags.add("had_idle_gap");
}

export function recordActivity(state: TimerState, now: number, idleThresholdMs: number): void {
  advance(state, now, idleThresholdMs);
  state.lastActivity = now;
  state.interactions += 1;
}

export function setHidden(state: TimerState, hidden: boolean, now: number, idleThresholdMs: number): void {
  advance(state, now, idleThresholdMs);
  state.hidden = hidden;
  if (hidden) state.flags.add("was_hidden");
  else state.lastActivity = now; // returning to the tab counts as presence, not an interaction
}

export function snapshot(state: TimerState, now: number, idleThresholdMs: number): ReviewTiming {
  advance(state, now, idleThresholdMs);
  const wall = Math.max(0, Math.round(now - state.startedAt));
  const active = Math.min(wall, Math.round(state.activeMs));
  const hiddenMs = Math.min(wall - active, Math.round(state.hiddenMs));
  return {
    active_ms: active,
    wall_ms: wall,
    hidden_ms: hiddenMs,
    idle_ms: Math.max(0, wall - active - hiddenMs),
    interaction_count: state.interactions,
    idle_threshold_ms: idleThresholdMs,
    flags: [...state.flags],
  };
}

const ACTIVITY_EVENTS = ["pointerdown", "click", "keydown", "input", "change", "wheel", "focusin"] as const;

export function useReviewTimer(
  sessionKey: string | null,
  { idleThresholdMs = 60_000, clock = () => performance.now() }: { idleThresholdMs?: number; clock?: () => number } = {},
) {
  const state = useRef<TimerState | null>(null);
  const clockRef = useRef(clock);
  clockRef.current = clock;

  useEffect(() => {
    if (!sessionKey) {
      state.current = null;
      return;
    }
    const now = () => clockRef.current();
    state.current = startTimer(now(), typeof document !== "undefined" && document.visibilityState === "hidden");
    const onActivity = () => state.current && recordActivity(state.current, now(), idleThresholdMs);
    const onVisibility = () =>
      state.current && setHidden(state.current, document.visibilityState === "hidden", now(), idleThresholdMs);
    for (const name of ACTIVITY_EVENTS) window.addEventListener(name, onActivity, true);
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      for (const name of ACTIVITY_EVENTS) window.removeEventListener(name, onActivity, true);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [sessionKey, idleThresholdMs]);

  const read = useCallback((): ReviewTiming | undefined => {
    if (!state.current) return undefined;
    return snapshot(state.current, clockRef.current(), idleThresholdMs);
  }, [idleThresholdMs]);

  const flag = useCallback((name: ReviewTiming["flags"][number]) => {
    state.current?.flags.add(name);
  }, []);

  return { read, flag };
}
