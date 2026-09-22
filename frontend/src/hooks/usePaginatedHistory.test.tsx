import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { usePaginatedHistory } from "./usePaginatedHistory";
import type { Page } from "@/types/api";

/**
 * Phase 4 closeout Part A: these run entirely in this environment (jsdom +
 * Vitest, no browser/extension dependency) -- added specifically because a
 * disconnected Chrome extension in a prior session did not establish that
 * NO interaction-verification tool was available.
 */

type Item = { id: string };

function makeItems(count: number): Item[] {
  return Array.from({ length: count }, (_, i) => ({ id: `item-${i}` }));
}

function pageOf(all: Item[], limit: number, offset: number): Page<Item> {
  const items = all.slice(offset, offset + limit);
  return {
    items,
    total: all.length,
    limit,
    offset,
    has_more: offset + items.length < all.length,
  };
}

describe("usePaginatedHistory", () => {
  it("walks past 200 entries across multiple load-more calls", async () => {
    const all = makeItems(250);
    const fetcher = vi.fn((_key: string, limit: number, offset: number) =>
      Promise.resolve(pageOf(all, limit, offset)),
    );

    const { result } = renderHook(() => usePaginatedHistory("lead-1", fetcher));

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.items).toHaveLength(50); // default page size
    expect(result.current.total).toBe(250);
    expect(result.current.hasMore).toBe(true);

    // Load more repeatedly until everything is in.
    while (result.current.hasMore) {
      await act(async () => {
        result.current.loadMore();
      });
      await waitFor(() => expect(result.current.loadingMore).toBe(false));
    }

    expect(result.current.items).toHaveLength(250);
    expect(result.current.items.map((i) => i.id)).toEqual(all.map((i) => i.id));
    expect(result.current.hasMore).toBe(false);
  });

  it("preserves already-loaded items when a later page fails, and error is distinguishable from empty", async () => {
    const all = makeItems(120);
    let callCount = 0;
    const fetcher = vi.fn((_key: string, limit: number, offset: number) => {
      callCount += 1;
      if (callCount === 2) {
        return Promise.reject(new Error("simulated network failure"));
      }
      return Promise.resolve(pageOf(all, limit, offset));
    });

    const { result } = renderHook(() => usePaginatedHistory("lead-1", fetcher));
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.items).toHaveLength(50);
    expect(result.current.error).toBeNull();

    await act(async () => {
      result.current.loadMore(); // this call fails (callCount becomes 2)
    });
    await waitFor(() => expect(result.current.loadingMore).toBe(false));

    // The 50 already-loaded items must still be there -- not cleared.
    expect(result.current.items).toHaveLength(50);
    expect(result.current.error).toBe("simulated network failure");

    // Retry succeeds and appends rather than duplicating or losing the first 50.
    await act(async () => {
      result.current.reload();
    });
    await waitFor(() => expect(result.current.loadingMore || result.current.loading).toBe(false));
    expect(result.current.error).toBeNull();
    expect(result.current.items).toHaveLength(100);
    expect(new Set(result.current.items.map((i) => i.id)).size).toBe(100); // no duplicates
  });

  it("discards a stale response that arrives after the key has already changed (out-of-order network races)", async () => {
    const leadA = makeItems(5).map((i) => ({ id: `A-${i.id}` }));
    const leadB = makeItems(5).map((i) => ({ id: `B-${i.id}` }));

    // Deliberately resolve the FIRST (lead-A) request AFTER the second
    // (lead-B) request, simulating responses arriving out of order.
    let resolveA: (p: Page<Item>) => void = () => {};
    const fetcher = vi.fn((key: string, limit: number, offset: number) => {
      if (key === "lead-a") {
        return new Promise<Page<Item>>((resolve) => {
          resolveA = () => resolve(pageOf(leadA, limit, offset));
        });
      }
      return Promise.resolve(pageOf(leadB, limit, offset));
    });

    const { result, rerender } = renderHook(
      ({ leadId }: { leadId: string }) => usePaginatedHistory(leadId, fetcher),
      { initialProps: { leadId: "lead-a" } },
    );

    // Navigate to lead-b before lead-a's request has resolved.
    rerender({ leadId: "lead-b" });
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.items.map((i) => i.id)).toEqual(leadB.map((i) => i.id));

    // Now let the STALE lead-a response resolve late.
    await act(async () => {
      resolveA(pageOf(leadA, 50, 0));
      await new Promise((r) => setTimeout(r, 0));
    });

    // Must still show lead-b's data -- the late lead-a response must not
    // have clobbered it.
    expect(result.current.items.map((i) => i.id)).toEqual(leadB.map((i) => i.id));
  });

  it("distinguishes a genuinely empty result from a failed request", async () => {
    const emptyFetcher = vi.fn(() =>
      Promise.resolve(pageOf([], 50, 0)),
    );
    const { result: emptyResult } = renderHook(() =>
      usePaginatedHistory("lead-1", emptyFetcher),
    );
    await waitFor(() => expect(emptyResult.current.loading).toBe(false));
    expect(emptyResult.current.items).toHaveLength(0);
    expect(emptyResult.current.error).toBeNull();

    const failFetcher = vi.fn(() => Promise.reject(new Error("boom")));
    const { result: failResult } = renderHook(() =>
      usePaginatedHistory("lead-1", failFetcher),
    );
    await waitFor(() => expect(failResult.current.loading).toBe(false));
    expect(failResult.current.items).toHaveLength(0);
    expect(failResult.current.error).toBe("boom");
  });
});
