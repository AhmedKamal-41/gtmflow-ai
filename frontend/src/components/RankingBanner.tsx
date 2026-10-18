"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import { APIError, getRankingStatus, recalculateRankings } from "@/lib/api";
import type { RankingStatus } from "@/types/api";

import { Button } from "./Button";

/**
 * What leads are ranked against, and what needs doing: set up target
 * customers, or recalculate rankings made against earlier preferences.
 * Renders nothing when every lead is ranked against the active profile.
 * `onRecalculated` lets the page reload its list afterwards.
 */
export function RankingBanner({ onRecalculated, refreshKey = 0, onSellerProfilePage = false }: {
  onRecalculated?: () => void;
  refreshKey?: number;
  onSellerProfilePage?: boolean;
}) {
  const [status, setStatus] = useState<RankingStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const requestId = useRef(0);

  const load = useCallback(async () => {
    const request = ++requestId.current;
    try {
      const value = await getRankingStatus();
      if (request === requestId.current) setStatus(value);
    } catch {
      if (request === requestId.current) setStatus(null);
    }
  }, []);

  useEffect(() => {
    void load();
    return () => {
      requestId.current += 1;
    };
  }, [load, refreshKey]);

  async function recalculate() {
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      const run = await recalculateRankings();
      setMessage(
        `Ranked ${run.newly_scored} leads against seller profile version ${run.profile_version}` +
          (run.skipped_unchanged ? ` (${run.skipped_unchanged} were already current)` : "") +
          (run.failed ? `; ${run.failed} failed` : "") + ".",
      );
      onRecalculated?.();
    } catch (e) {
      setError(e instanceof APIError ? (e.detail ?? e.message) : "Could not recalculate rankings.");
    } finally {
      setBusy(false);
      void load();
    }
  }

  if (!status) return null;
  if (status.state !== "ready") {
    return (
      <section aria-label="Ranking setup" className="rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">
        <div className="font-semibold">Set up your target customers to rank leads</div>
        <p className="mt-1">{status.message}</p>
        {onSellerProfilePage ? (
          <p className="mt-1">Fill in “Who you want to sell to” below, save, and activate the version.</p>
        ) : (
          <Link href="/seller-profile" className="mt-2 inline-block font-medium text-brand-700 underline">
            Open the seller profile
          </Link>
        )}
      </section>
    );
  }
  const pending = status.outdated_rankings + status.never_ranked;
  if (pending === 0 && !message && !error) return null;
  return (
    <section aria-label="Ranking status" className="rounded-lg border border-slate-200 bg-slate-50 p-4 text-sm text-slate-700">
      {pending > 0 && (
        <div className="flex flex-wrap items-center justify-between gap-3">
          <p>
            {status.outdated_rankings > 0 && (
              <>
                <strong>{status.outdated_rankings}</strong> leads were ranked against earlier target customers.{" "}
              </>
            )}
            {status.never_ranked > 0 && (
              <>
                <strong>{status.never_ranked}</strong> leads are not ranked yet.{" "}
              </>
            )}
            Rankings use seller profile version {status.profile?.version}.
          </p>
          <Button variant="secondary" size="sm" loading={busy} disabled={busy} onClick={() => void recalculate()}>
            Recalculate rankings
          </Button>
        </div>
      )}
      {message && <p role="status" className="mt-1 text-emerald-700">{message}</p>}
      {error && <p role="alert" className="mt-1 text-red-700">{error}</p>}
    </section>
  );
}
