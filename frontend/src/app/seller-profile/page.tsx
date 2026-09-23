"use client";

import { FormEvent, useCallback, useEffect, useRef, useState } from "react";

import { Button } from "@/components/Button";
import { Card } from "@/components/Card";
import { ErrorMessage } from "@/components/ErrorMessage";
import { LoadingState } from "@/components/LoadingState";
import { PageHeader } from "@/components/PageHeader";
import { usePaginatedHistory } from "@/hooks/usePaginatedHistory";
import { APIError, getSellerProfile, getSellerProfileVersions, saveSellerProfile } from "@/lib/api";
import type { SellerProfileContent } from "@/types/api";

const emptyProfile: SellerProfileContent = {
  profile_kind: "seller", company_name: "", product_name: "",
  value_proposition: "", target_customer: "", capabilities: [], proof_points: [], exclusions: [],
};
const inputClass = "mt-1 block w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-brand-500 focus:outline-none focus:ring-2 focus:ring-brand-500/30";

export default function SellerProfilePage() {
  const [profile, setProfile] = useState<SellerProfileContent>(emptyProfile);
  const [capabilities, setCapabilities] = useState("");
  const [exclusions, setExclusions] = useState("");
  const [version, setVersion] = useState(0);
  const [loaded, setLoaded] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [conflict, setConflict] = useState(false);
  const requestId = useRef(0);
  const history = usePaginatedHistory("seller-profile", (_key, limit, offset) =>
    getSellerProfileVersions({ limit, offset }),
  );

  function applyProfile(value: SellerProfileContent) {
    setProfile(value);
    setCapabilities(value.capabilities.join("\n"));
    setExclusions(value.exclusions.join("\n"));
  }

  const load = useCallback(async () => {
    const request = ++requestId.current;
    setLoading(true);
    setLoaded(false);
    setError(null);
    setMessage(null);
    try {
      const row = await getSellerProfile();
      if (request !== requestId.current) return;
      applyProfile(row.profile);
      setVersion(row.version);
      setLoaded(true);
      setConflict(false);
    } catch (e) {
      if (request !== requestId.current) return;
      if (e instanceof APIError && e.status === 404) {
        applyProfile(emptyProfile);
        setVersion(0);
        setLoaded(true);
        setConflict(false);
      } else {
        setError(e instanceof APIError ? (e.detail ?? e.message) : "Could not load the seller profile.");
      }
    } finally {
      if (request === requestId.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
    return () => { requestId.current += 1; };
  }, [load]);

  function edit(field: keyof SellerProfileContent, value: string) {
    setProfile((previous) => ({ ...previous, [field]: value }));
    setMessage(null);
  }

  async function save(event: FormEvent) {
    event.preventDefault();
    if (!loaded || saving || conflict) return;
    const request = ++requestId.current;
    setSaving(true);
    setError(null);
    setMessage(null);
    const payload = {
      ...profile,
      capabilities: lines(capabilities),
      exclusions: lines(exclusions),
    };
    try {
      const row = await saveSellerProfile(payload, version);
      if (request !== requestId.current) return;
      applyProfile(row.profile);
      setVersion(row.version);
      setMessage(`Draft version ${row.version} saved. It is not active for outreach.`);
      history.refresh();
    } catch (e) {
      if (request !== requestId.current) return;
      setConflict(e instanceof APIError && e.status === 409);
      setError(e instanceof APIError ? (e.detail ?? e.message) : "Could not save. Your entries are still here; try again.");
    } finally {
      if (request === requestId.current) setSaving(false);
    }
  }

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <PageHeader eyebrow="Setup" title="Seller profile" description="Describe what you sell, who it helps, and which claims you can support." />
      <div className="rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">
        Draft only. Saving a profile keeps it for review. It does not change scores or generated outreach yet.
      </div>
      {loading && <LoadingState text="Loading seller profile…" />}
      {error && <ErrorMessage>{error}</ErrorMessage>}
      {!loading && !loaded && <Button variant="secondary" onClick={() => void load()}>Retry loading</Button>}
      {conflict && <div className="space-y-2 text-sm text-slate-600">
        <p>Your entries are preserved. Copy any changes you want to keep before loading the latest saved version.</p>
        <Button variant="secondary" onClick={() => void load()}>Load latest and replace this form</Button>
      </div>}
      {message && <p role="status" className="text-sm text-emerald-700">{message}</p>}
      {loaded && !loading && <Card title={version ? `Edit draft version ${version}` : "Create a seller draft"} subtitle="Each changed save keeps the previous version in history.">
        <form onSubmit={save} className="space-y-5">
          <fieldset disabled={saving || conflict} className="space-y-5 disabled:opacity-60">
            <label className="block text-sm font-medium text-slate-700">
              Profile purpose
              <select className={inputClass} value={profile.profile_kind} onChange={(e) => edit("profile_kind", e.target.value)}>
                <option value="seller">Actual seller</option>
                <option value="demo">Demonstration only</option>
              </select>
            </label>
            <Field label="Company name" value={profile.company_name} maxLength={160} onChange={(value) => edit("company_name", value)} />
            <Field label="Product or service" value={profile.product_name} maxLength={160} onChange={(value) => edit("product_name", value)} />
            <Field label="Value proposition" value={profile.value_proposition} maxLength={2000} multiline hint="Explain the offer in your own words. Avoid results you cannot support." onChange={(value) => edit("value_proposition", value)} />
            <Field label="Target customers" value={profile.target_customer} maxLength={3000} multiline hint="Include industries, geography, company size, and the roles you want to reach. State when you have no preference." onChange={(value) => edit("target_customer", value)} />
            <Field label="Capabilities (optional)" value={capabilities} maxLength={6011} multiline required={false} hint="One capability per line, up to 12. Describe only features you actually offer." onChange={(value) => { setCapabilities(value); setMessage(null); }} />
            <section aria-label="Proof points" className="space-y-3">
              <div>
                <h2 className="text-sm font-semibold text-slate-900">Proof points (optional)</h2>
                <p className="text-sm text-slate-500">Add a source or reference for each claim. Leave empty if none are available.</p>
              </div>
              {profile.proof_points.map((point, index) => <div key={index} className="space-y-3 rounded-lg border border-slate-200 p-3">
                <Field label={`Claim ${index + 1}`} value={point.claim} maxLength={500} onChange={(value) => {
                  setProfile((previous) => ({ ...previous, proof_points: previous.proof_points.map((item, i) => i === index ? { ...item, claim: value } : item) }));
                  setMessage(null);
                }} />
                <Field label={`Source or reference ${index + 1}`} value={point.source} maxLength={1000} onChange={(value) => {
                  setProfile((previous) => ({ ...previous, proof_points: previous.proof_points.map((item, i) => i === index ? { ...item, source: value } : item) }));
                  setMessage(null);
                }} />
                <Button variant="ghost" onClick={() => { setProfile((previous) => ({ ...previous, proof_points: previous.proof_points.filter((_item, i) => i !== index) })); setMessage(null); }}>Remove claim {index + 1}</Button>
              </div>)}
              <Button variant="secondary" disabled={profile.proof_points.length >= 12} onClick={() => { setProfile((previous) => ({ ...previous, proof_points: [...previous.proof_points, { claim: "", source: "" }] })); setMessage(null); }}>Add proof point</Button>
            </section>
            <Field label="Exclusions (optional)" value={exclusions} maxLength={6011} multiline required={false} hint="One do-not-target rule per line, up to 12. These are draft instructions, not active routing rules." onChange={(value) => { setExclusions(value); setMessage(null); }} />
            <Button type="submit" loading={saving}>Save draft</Button>
          </fieldset>
        </form>
      </Card>}
      <Card title="Saved versions" subtitle="Historical drafts are read-only.">
        {history.loading && <LoadingState text="Loading versions…" />}
        {!history.loading && history.items.length === 0 && !history.error && <p className="text-sm text-slate-500">No saved versions yet.</p>}
        <div className="space-y-3">
          {history.items.map((row) => <details key={row.id} className="rounded-lg border border-slate-200 p-3">
            <summary className="cursor-pointer text-sm font-medium">Version {row.version} · {row.profile.company_name} · {row.profile.profile_kind === "demo" ? "Demonstration draft" : "Seller draft"}</summary>
            <div className="mt-3 space-y-2 whitespace-pre-wrap text-sm text-slate-600">
              <p><strong>Product:</strong> {row.profile.product_name}</p>
              <p><strong>Offer:</strong> {row.profile.value_proposition}</p>
              <p><strong>Customers:</strong> {row.profile.target_customer}</p>
              <p><strong>Capabilities:</strong> {row.profile.capabilities.join("; ") || "None supplied"}</p>
              <p><strong>Exclusions:</strong> {row.profile.exclusions.join("; ") || "None supplied"}</p>
              {row.profile.proof_points.map((point, index) => <p key={index}>{point.claim} — Source: {point.source}</p>)}
            </div>
          </details>)}
          {history.error && <div className="space-y-2"><ErrorMessage>{history.error}</ErrorMessage><Button variant="secondary" onClick={history.reload}>Retry versions</Button></div>}
          {!history.error && history.hasMore && <Button variant="secondary" loading={history.loadingMore} disabled={history.loading} onClick={history.loadMore}>Load older versions</Button>}
        </div>
      </Card>
    </div>
  );
}

function lines(value: string): string[] {
  return value.split("\n").map((line) => line.trim()).filter(Boolean);
}

function Field({ label, value, onChange, maxLength, multiline = false, required = true, hint }: {
  label: string; value: string; onChange: (value: string) => void; maxLength: number;
  multiline?: boolean; required?: boolean; hint?: string;
}) {
  const props = { value, maxLength, required, className: inputClass, onChange: (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => onChange(e.target.value) };
  return <label className="block text-sm font-medium text-slate-700">
    {label}
    {multiline ? <textarea {...props} rows={3} /> : <input {...props} type="text" />}
    {hint && <span className="mt-1 block text-xs font-normal text-slate-500">{hint}</span>}
  </label>;
}
