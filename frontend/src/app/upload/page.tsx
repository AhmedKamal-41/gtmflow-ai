"use client";

import Link from "next/link";
import { FormEvent, useState } from "react";

import { Button } from "@/components/Button";
import { Card } from "@/components/Card";
import { ErrorMessage } from "@/components/ErrorMessage";
import { Icon } from "@/components/Icon";
import { PageHeader } from "@/components/PageHeader";
import { APIError, uploadBatch } from "@/lib/api";
import type { UploadResponse } from "@/types/api";

export default function UploadPage() {
  const [file, setFile] = useState<File | null>(null);
  const [batchName, setBatchName] = useState("");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<UploadResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (!file) {
      setError("Please choose a CSV file.");
      return;
    }
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      setResult(await uploadBatch(file, batchName.trim() || undefined));
    } catch (e) {
      setError(
        e instanceof APIError
          ? (e.detail ?? e.message)
          : "Unexpected error uploading the CSV.",
      );
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Ingestion"
        title="Upload leads"
        description={
          <>
            CSV must include a{" "}
            <code className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-xs">
              company_name
            </code>{" "}
            column. Optional: website, industry, contact_name, contact_email,
            contact_title, company_size, location, source. Unknown columns (like
            notes) are kept on each lead.
          </>
        }
      />

      <Card>
        <form onSubmit={handleSubmit} className="space-y-5">
          <div>
            <label className="block text-sm font-medium text-slate-700">
              CSV file
            </label>
            <label
              htmlFor="csv-file"
              className="mt-1.5 flex cursor-pointer items-center gap-3 rounded-xl border border-dashed border-slate-300 bg-slate-50/60 px-4 py-5 transition-colors hover:border-brand-400 hover:bg-brand-50/40"
            >
              <span className="grid h-10 w-10 flex-none place-items-center rounded-lg bg-white text-brand-600 shadow-card">
                <Icon name="file" className="h-5 w-5" />
              </span>
              <span className="min-w-0">
                <span className="block truncate text-sm font-medium text-slate-900">
                  {file ? file.name : "Choose a .csv file"}
                </span>
                <span className="block text-xs text-slate-500">
                  {file
                    ? `${(file.size / 1024).toFixed(1)} KB`
                    : "Click to browse"}
                </span>
              </span>
            </label>
            <input
              id="csv-file"
              type="file"
              accept=".csv"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
              className="sr-only"
            />
          </div>

          <div>
            <label
              htmlFor="batch-name"
              className="block text-sm font-medium text-slate-700"
            >
              Batch name{" "}
              <span className="font-normal text-slate-400">(optional)</span>
            </label>
            <input
              id="batch-name"
              type="text"
              value={batchName}
              onChange={(e) => setBatchName(e.target.value)}
              placeholder="Q3 outbound list"
              className="mt-1.5 block w-full rounded-lg border border-slate-300 px-3 py-2 text-sm placeholder:text-slate-400 focus:border-brand-500 focus:outline-none focus:ring-2 focus:ring-brand-500/30"
            />
          </div>

          <Button type="submit" icon="upload" loading={loading} disabled={!file}>
            Upload
          </Button>
        </form>
      </Card>

      {error && <ErrorMessage>{error}</ErrorMessage>}

      {result && (
        <Card
          title={result.batch_name ?? "(unnamed batch)"}
          subtitle="Upload result"
          icon="check"
        >
          <div className="grid grid-cols-3 gap-4">
            <Stat label="Total rows" value={result.total_rows} />
            <Stat
              label="Valid"
              value={result.valid_rows}
              accent="text-emerald-600"
            />
            <Stat
              label="Invalid"
              value={result.invalid_rows}
              accent={result.invalid_rows > 0 ? "text-amber-600" : "text-slate-900"}
            />
          </div>

          {result.errors.length > 0 && (
            <div className="mt-4 rounded-lg border border-amber-200 bg-amber-50 p-3.5 text-sm text-amber-900">
              <div className="mb-1.5 font-semibold">Row errors</div>
              <ul className="ml-4 list-disc space-y-1">
                {result.errors.slice(0, 20).map((err, i) => (
                  <li key={i}>
                    Row {err.row_number}, {err.field}: {err.message}
                  </li>
                ))}
              </ul>
              {result.errors.length > 20 && (
                <div className="mt-2 text-xs text-amber-700">
                  …and {result.errors.length - 20} more.
                </div>
              )}
            </div>
          )}

          <div className="mt-5">
            <Link
              href={`/batches/${result.batch_id}`}
              className="inline-flex items-center gap-1.5 text-sm font-semibold text-brand-600 hover:text-brand-700"
            >
              Open batch detail
              <Icon name="arrow-right" className="h-4 w-4" />
            </Link>
          </div>
        </Card>
      )}
    </div>
  );
}

function Stat({
  label,
  value,
  accent = "text-slate-900",
}: {
  label: string;
  value: number;
  accent?: string;
}) {
  return (
    <div>
      <div className="text-xs font-medium uppercase tracking-wide text-slate-500">
        {label}
      </div>
      <div className={`tabular mt-1 text-2xl font-bold ${accent}`}>{value}</div>
    </div>
  );
}
