"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import type { Page } from "@/types/api";

const PAGE_SIZE = 50;

export type HistoryState<T> = {
  items: T[];
  total: number;
  hasMore: boolean;
  loading: boolean; // initial load for the current key (e.g. leadId)
  loadingMore: boolean; // fetching an additional page
  error: string | null; // set on the load that failed; previously loaded items are kept
  reload: () => void; // retry the failed/in-flight page, keeping loaded items
  loadMore: () => void;
  refresh: () => void; // discard and reload from page 0
};

/**
 * Independent pagination state for one history panel (AI outputs, pushes),
 * keyed by `key` (typically the lead id) -- Phase 3 closeout Part D.
 *
 * - `key` changing (e.g. navigating to a different lead) resets state and
 *   reloads from page 0. A response for a STALE key (the previous lead,
 *   already superseded by a navigation) is discarded rather than applied --
 *   prevents a slow response for an old lead from clobbering the newly
 *   selected lead's state (Part D.2).
 * - A `loadMore` failure keeps every already-loaded item in `items` and
 *   only sets `error` -- never clears what's already on screen (Part D.2).
 * - `error` is distinguished from "empty": an empty successful page yields
 *   `items: [], error: null`; a failed request yields the previous items
 *   (or none, if it was the very first page) plus a non-null `error`.
 */
export function usePaginatedHistory<T>(
  key: string,
  fetcher: (key: string, limit: number, offset: number) => Promise<Page<T>>,
): HistoryState<T> {
  const [items, setItems] = useState<T[]>([]);
  const [total, setTotal] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Bumped every time `key` changes; a fetch started under an older
  // generation is ignored when it resolves, however late.
  const generationRef = useRef(0);
  const requestRef = useRef(0);
  const retryRef = useRef({ offset: 0, isInitial: true });
  // The key currently on screen. A caller holding a `reload`/`loadMore`
  // from an earlier render (e.g. an action that finished after the user
  // navigated to another lead) must not fetch the OLD key's history into
  // the new key's panel -- runFetch refuses any key that isn't this one.
  const keyRef = useRef(key);

  const runFetch = useCallback(
    (offset: number, isInitial: boolean) => {
      const generation = generationRef.current;
      const requestKey = key;
      if (requestKey !== keyRef.current) return; // stale caller
      const request = ++requestRef.current;
      retryRef.current = { offset, isInitial };
      const isCurrent = () =>
        generationRef.current === generation && requestRef.current === request;
      if (isInitial) {
        setLoading(true);
        setLoadingMore(false);
        setError(null);
      } else {
        setLoadingMore(true);
        setError(null);
      }
      fetcher(requestKey, PAGE_SIZE, offset)
        .then((page) => {
          if (!isCurrent()) return;
          setItems((prev) => (isInitial ? page.items : [...prev, ...page.items]));
          setTotal(page.total);
          setHasMore(page.has_more);
        })
        .catch((e: unknown) => {
          if (!isCurrent()) return;
          const message =
            e instanceof Error ? e.message : "Failed to load history.";
          setError(message);
          // Deliberately do NOT clear `items` here -- a load-more failure
          // must preserve everything already shown (Part D.2).
        })
        .finally(() => {
          if (!isCurrent()) return;
          setLoading(false);
          setLoadingMore(false);
        });
    },
    [key, fetcher],
  );

  useEffect(() => {
    generationRef.current += 1;
    keyRef.current = key;
    setItems([]);
    setTotal(0);
    setHasMore(false);
    setError(null);
    setLoadingMore(false);
    retryRef.current = { offset: 0, isInitial: true };
    if (!key) {
      setLoading(false);
      return;
    }
    runFetch(0, true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  const reload = useCallback(() => {
    // A refresh can fail with history still visible. Retry that request's
    // offset and replacement mode, not the length of the visible history.
    const { offset, isInitial } = retryRef.current;
    runFetch(offset, isInitial);
  }, [runFetch]);

  const loadMore = useCallback(() => {
    runFetch(items.length, false);
  }, [runFetch, items.length]);

  const refresh = useCallback(() => {
    // After something changed server-side (e.g. a new output was created):
    // start over from page 0. Not `reload` -- appending at the old offset
    // of a newest-first list would re-fetch shifted rows as duplicates.
    runFetch(0, true);
  }, [runFetch]);

  return {
    items, total, hasMore, loading, loadingMore, error, reload, loadMore, refresh,
  };
}
