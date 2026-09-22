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
  reload: () => void;
  loadMore: () => void;
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

  const runFetch = useCallback(
    (offset: number, isInitial: boolean) => {
      const generation = generationRef.current;
      const requestKey = key;
      if (isInitial) {
        setLoading(true);
        setError(null);
      } else {
        setLoadingMore(true);
        setError(null);
      }
      fetcher(requestKey, PAGE_SIZE, offset)
        .then((page) => {
          if (generationRef.current !== generation) return; // stale (key changed since)
          setItems((prev) => (isInitial ? page.items : [...prev, ...page.items]));
          setTotal(page.total);
          setHasMore(page.has_more);
        })
        .catch((e: unknown) => {
          if (generationRef.current !== generation) return; // stale
          const message =
            e instanceof Error ? e.message : "Failed to load history.";
          setError(message);
          // Deliberately do NOT clear `items` here -- a load-more failure
          // must preserve everything already shown (Part D.2).
        })
        .finally(() => {
          if (generationRef.current !== generation) return;
          setLoading(false);
          setLoadingMore(false);
        });
    },
    [key, fetcher],
  );

  useEffect(() => {
    generationRef.current += 1;
    setItems([]);
    setTotal(0);
    setHasMore(false);
    setError(null);
    if (!key) {
      setLoading(false);
      return;
    }
    runFetch(0, true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  const reload = useCallback(() => {
    // Retries whichever page was in flight -- if items already exist, this
    // is a "load more" retry; otherwise it's the initial load retry. Either
    // way it re-requests at the current offset (items.length).
    runFetch(items.length, items.length === 0);
  }, [runFetch, items.length]);

  const loadMore = useCallback(() => {
    runFetch(items.length, false);
  }, [runFetch, items.length]);

  return { items, total, hasMore, loading, loadingMore, error, reload, loadMore };
}
