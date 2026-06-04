"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { Card } from "@/components/Card";
import { ErrorMessage } from "@/components/ErrorMessage";
import { Icon } from "@/components/Icon";
import { LoadingState } from "@/components/LoadingState";
import { PageHeader } from "@/components/PageHeader";
import { StatusBadge } from "@/components/StatusBadge";
import { APIError, getBatches } from "@/lib/api";
import type { LeadBatch } from "@/types/api";

export default function BatchesPage() {
  const [batches, setBatches] = useState<LeadBatch[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getBatches()
      .then(setBatches)
      .catch((e) =>
        setError(
          e instanceof APIError
            ? (e.detail ?? e.message)
            : "Failed to load batches",
        ),
      );
  }, []);

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Workspace"
        title="Batches"
        description="Every uploaded lead list, newest first."
        actions={
          <Link
            href="/upload"
            className="inline-flex items-center gap-2 rounded-lg bg-brand-600 px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-brand-700"
          >
            <Icon name="upload" className="h-4 w-4" />
            New upload
          </Link>
        }
      />

      {error && <ErrorMessage>{error}</ErrorMessage>}
      {batches === null && !error && <LoadingState text="Loading batches…" />}

      {batches && batches.length === 0 && (
        <Card>
          <div className="flex flex-col items-center gap-3 py-10 text-center">
            <span className="grid h-12 w-12 place-items-center rounded-xl bg-brand-50 text-brand-600">
              <Icon name="layers" className="h-6 w-6" />
            </span>
            <div className="text-lg font-semibold text-slate-900">
              No batches yet
            </div>
            <p className="max-w-sm text-sm text-slate-600">
              Upload a CSV, or run the one-click demo to seed a sample batch.
            </p>
            <div className="flex gap-2">
              <Link
                href="/upload"
                className="inline-flex items-center gap-2 rounded-lg bg-brand-600 px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-brand-700"
              >
                <Icon name="upload" className="h-4 w-4" />
                Upload a CSV
              </Link>
              <Link
                href="/demo"
                className="inline-flex items-center gap-2 rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm font-semibold text-slate-800 transition-colors hover:bg-slate-50"
              >
                <Icon name="play" className="h-4 w-4" />
                Run demo
              </Link>
            </div>
          </div>
        </Card>
      )}

      {batches && batches.length > 0 && (
        <Card padding="none" className="overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="border-b border-slate-200 bg-slate-50/80 text-left text-xs font-semibold uppercase tracking-wide text-slate-500">
                <tr>
                  <th className="px-4 py-3">Name</th>
                  <th className="px-4 py-3">Source</th>
                  <th className="px-4 py-3">Status</th>
                  <th className="px-4 py-3 text-right">Processed</th>
                  <th className="px-4 py-3 text-right">Total</th>
                  <th className="px-4 py-3">Created</th>
                  <th className="px-4 py-3"></th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {batches.map((b) => (
                  <tr
                    key={b.id}
                    className="group transition-colors hover:bg-brand-50/40"
                  >
                    <td className="px-4 py-3">
                      <Link
                        href={`/batches/${b.id}`}
                        className="font-medium text-slate-900 group-hover:text-brand-700"
                      >
                        {b.name ?? "(unnamed)"}
                      </Link>
                    </td>
                    <td className="px-4 py-3 capitalize text-slate-600">
                      {b.source}
                    </td>
                    <td className="px-4 py-3">
                      <StatusBadge status={b.status} />
                    </td>
                    <td className="tabular px-4 py-3 text-right text-slate-700">
                      {b.processed_leads}
                    </td>
                    <td className="tabular px-4 py-3 text-right text-slate-700">
                      {b.total_leads}
                    </td>
                    <td className="px-4 py-3 text-xs text-slate-500">
                      {new Date(b.created_at).toLocaleDateString()}
                    </td>
                    <td className="px-4 py-3 text-right">
                      <Link
                        href={`/batches/${b.id}`}
                        aria-label={`Open ${b.name ?? "batch"}`}
                        className="inline-flex items-center gap-1 text-sm font-medium text-brand-600 opacity-0 transition-opacity hover:text-brand-700 group-hover:opacity-100"
                      >
                        Open
                        <Icon name="arrow-right" className="h-3.5 w-3.5" />
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  );
}
