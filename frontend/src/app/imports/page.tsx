"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { type FormEvent, useCallback, useEffect, useState } from "react";

import { Button } from "@/components/Button";
import { Card } from "@/components/Card";
import { ErrorMessage } from "@/components/ErrorMessage";
import { Icon } from "@/components/Icon";
import { LoadingState } from "@/components/LoadingState";
import { PageHeader } from "@/components/PageHeader";
import { StatusBadge } from "@/components/StatusBadge";
import { APIError, getBatches, scoreBatch, uploadBatch } from "@/lib/api";
import { SAMPLE_CSV_URL, loadSampleLeads } from "@/lib/sampleData";
import type { LeadBatch, Page, UploadResponse } from "@/types/api";

// Imports: bring in a lead list (scored right away) and see past imports.

const PAGE_SIZE = 25;

export default function ImportsPage() {
  const router = useRouter();
  const [file, setFile] = useState<File | null>(null);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState<"upload" | "sample" | null>(null);
  const [result, setResult] = useState<UploadResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [batches, setBatches] = useState<Page<LeadBatch> | null>(null);
  const [listError, setListError] = useState<string | null>(null);

  const loadBatches = useCallback(async () => {
    setListError(null);
    try {
      setBatches(await getBatches({ limit: PAGE_SIZE, offset: 0 }));
    } catch (e) {
      setListError(e instanceof APIError ? (e.detail ?? e.message) : "Could not load imports.");
    }
  }, []);

  useEffect(() => {
    void loadBatches();
  }, [loadBatches]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!file) {
      setError("Choose a CSV file first.");
      return;
    }
    setBusy("upload");
    setError(null);
    setResult(null);
    try {
      const uploaded = await uploadBatch(file, name.trim() || undefined);
      if (uploaded.valid_rows > 0) await scoreBatch(uploaded.batch_id);
      setResult(uploaded);
      setFile(null);
      setName("");
      void loadBatches();
    } catch (e) {
      setError(e instanceof APIError ? (e.detail ?? e.message) : "The upload failed.");
    } finally {
      setBusy(null);
    }
  }

  async function trySample() {
    setBusy("sample");
    setError(null);
    try {
      await loadSampleLeads();
      router.push("/leads");
    } catch (e) {
      setError(e instanceof APIError ? (e.detail ?? e.message) : "Could not load the sample leads.");
      setBusy(null);
    }
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Imports"
        description="Bring in a CSV of companies you are working. Each lead is scored as soon as it is imported."
      />

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_18rem]">
        <Card title="Import a lead list" icon="upload">
          <form onSubmit={submit} className="space-y-4" aria-label="Import a lead list">
            <label className="flex cursor-pointer flex-col items-center gap-2 rounded-xl border-2 border-dashed border-slate-300 bg-slate-50 px-4 py-8 text-center hover:border-brand-400">
              <Icon name="file" className="h-6 w-6 text-brand-600" />
              <span className="text-sm font-medium text-slate-900">{file ? file.name : "Choose a CSV file"}</span>
              <span className="text-xs text-slate-500">Needs a company_name column; contact and industry columns help scoring.</span>
              <input type="file" accept=".csv,text/csv" className="sr-only" aria-label="CSV file"
                onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
            </label>
            <label className="block text-sm">
              <span className="font-medium text-slate-700">Name this import <span className="font-normal text-slate-400">(optional)</span></span>
              <input className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2" placeholder="Q4 target accounts"
                value={name} onChange={(e) => setName(e.target.value)} maxLength={255} />
            </label>
            {error && <ErrorMessage>{error}</ErrorMessage>}
            <Button type="submit" icon="upload" loading={busy === "upload"} disabled={!file || busy !== null}>
              Import and score
            </Button>
          </form>

          {result && (
            <div className="mt-6 space-y-3 rounded-xl border border-emerald-200 bg-emerald-50 p-4 text-sm" role="status">
              <div className="font-semibold text-emerald-900">
                Imported {result.valid_rows} of {result.total_rows} rows
                {result.batch_name ? ` into "${result.batch_name}"` : ""}.
              </div>
              {result.errors.length > 0 && (
                <ul className="ml-4 list-disc space-y-1 text-amber-900">
                  {result.errors.slice(0, 10).map((err, i) => (
                    <li key={i}>Row {err.row_number}, {err.field}: {err.message}</li>
                  ))}
                  {result.errors.length > 10 && <li>…and {result.errors.length - 10} more.</li>}
                </ul>
              )}
              <div className="flex gap-4">
                <Link href="/leads" className="font-semibold text-brand-700 hover:underline">Go to leads →</Link>
                <Link href={`/batches/${result.batch_id}`} className="text-slate-700 hover:underline">Open this import</Link>
              </div>
            </div>
          )}
        </Card>

        <Card title="New here?" icon="sparkles">
          <div className="space-y-3 text-sm text-slate-600">
            <p>Load 10 sample companies to see how GTMFlow ranks leads and drafts outreach.</p>
            <Button variant="secondary" loading={busy === "sample"} disabled={busy !== null} onClick={() => void trySample()}>
              Try with sample data
            </Button>
            <a href={SAMPLE_CSV_URL} download className="block text-xs font-medium text-brand-700 hover:underline">
              Download the CSV template
            </a>
          </div>
        </Card>
      </div>

      <section className="space-y-3">
        <h2 className="text-lg font-semibold text-slate-900">Past imports</h2>
        {listError && <ErrorMessage>{listError}</ErrorMessage>}
        {!batches && !listError && <LoadingState />}
        {batches && batches.items.length === 0 && (
          <p className="rounded-xl border border-dashed border-slate-300 bg-white p-6 text-center text-sm text-slate-600">No imports yet.</p>
        )}
        {batches && batches.items.length > 0 && (
          <div className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-card">
            <table className="min-w-full divide-y divide-slate-200 text-sm">
              <thead className="bg-slate-50 text-left text-xs font-semibold uppercase tracking-wide text-slate-500">
                <tr>
                  <th className="px-4 py-3">Import</th>
                  <th className="px-4 py-3">Status</th>
                  <th className="px-4 py-3 text-right">Leads</th>
                  <th className="hidden px-4 py-3 sm:table-cell">Imported</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {batches.items.map((batch) => (
                  <tr key={batch.id} className="hover:bg-slate-50">
                    <td className="px-4 py-3">
                      <Link href={`/batches/${batch.id}`} className="font-medium text-slate-900 hover:text-brand-700">
                        {batch.name ?? "Unnamed import"}
                      </Link>
                    </td>
                    <td className="px-4 py-3"><StatusBadge status={batch.status} /></td>
                    <td className="tabular px-4 py-3 text-right text-slate-700">{batch.total_leads}</td>
                    <td className="hidden px-4 py-3 text-slate-500 sm:table-cell">{new Date(batch.created_at).toLocaleDateString()}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {batches.total > batches.items.length && (
              <p className="border-t border-slate-100 px-4 py-2 text-xs text-slate-500">
                Showing the {batches.items.length} most recent of {batches.total} imports.
              </p>
            )}
          </div>
        )}
      </section>
    </div>
  );
}
