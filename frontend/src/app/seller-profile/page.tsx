"use client";

import { FormEvent, useCallback, useEffect, useRef, useState } from "react";

import { Button } from "@/components/Button";
import { Card } from "@/components/Card";
import { ErrorMessage } from "@/components/ErrorMessage";
import { LoadingState } from "@/components/LoadingState";
import { PageHeader } from "@/components/PageHeader";
import { RankingBanner } from "@/components/RankingBanner";
import { usePaginatedHistory } from "@/hooks/usePaginatedHistory";
import {
  APIError,
  activateSellerProfile,
  deactivateSellerProfile,
  getSellerProfile,
  getSellerProfileDemoTemplate,
  getSellerProfileStatus,
  getSellerProfileVersions,
  saveSellerProfile,
} from "@/lib/api";
import type { SellerProfile, SellerProfileContent, SellerProfileStatus, TargetPreferences } from "@/types/api";

type SizeRange = TargetPreferences["company_sizes"][number];

const emptyTargets: TargetPreferences = { industries: [], locations: [], company_sizes: [], contact_roles: [], keywords: [] };
const emptyProfile: SellerProfileContent = {
  profile_kind: "seller", company_name: "", product_name: "",
  value_proposition: "", target_customer: "", capabilities: [], proof_points: [], exclusions: [],
  target_preferences: emptyTargets,
};
// Company-size presets (employees). Stored as ranges; any other saved range
// is kept and shown as a custom range.
const SIZE_PRESETS: SizeRange[] = [
  { min_employees: 1, max_employees: 10 },
  { min_employees: 11, max_employees: 50 },
  { min_employees: 51, max_employees: 200 },
  { min_employees: 201, max_employees: 500 },
  { min_employees: 501, max_employees: 1000 },
  { min_employees: 1001, max_employees: 5000 },
  { min_employees: 5001, max_employees: null },
];
const TARGET_LIST_FIELDS = ["industries", "locations", "contact_roles", "keywords"] as const;
type TargetListField = (typeof TARGET_LIST_FIELDS)[number];
const inputClass = "mt-1 block w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-brand-500 focus:outline-none focus:ring-2 focus:ring-brand-500/30";

function errorText(e: unknown, fallback: string): string {
  if (e instanceof APIError) return e.detail ?? e.message;
  return e instanceof Error ? e.message : fallback;
}

export default function SellerProfilePage() {
  const [profile, setProfile] = useState<SellerProfileContent>(emptyProfile);
  const [capabilities, setCapabilities] = useState("");
  const [exclusions, setExclusions] = useState("");
  // Target lists are edited as text, one entry per line.
  const [targetText, setTargetText] = useState<Record<TargetListField, string>>(
    { industries: "", locations: "", contact_roles: "", keywords: "" });
  const [sizes, setSizes] = useState<SizeRange[]>([]);
  const [bannerKey, setBannerKey] = useState(0);
  const [version, setVersion] = useState(0);
  const [loaded, setLoaded] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [conflict, setConflict] = useState(false);
  const requestId = useRef(0);

  // Activation state is loaded and guarded separately from the editor, so
  // a slow status response can never overwrite a newer one.
  const [status, setStatus] = useState<SellerProfileStatus | undefined>(undefined);
  const [statusError, setStatusError] = useState<string | null>(null);
  const [activating, setActivating] = useState<string | null>(null);
  const [activationError, setActivationError] = useState<string | null>(null);
  const [activationMessage, setActivationMessage] = useState<string | null>(null);
  const statusRequest = useRef(0);

  const history = usePaginatedHistory("seller-profile", (_key, limit, offset) =>
    getSellerProfileVersions({ limit, offset }),
  );

  function applyProfile(value: SellerProfileContent) {
    const targets = { ...emptyTargets, ...(value.target_preferences ?? {}) };
    setProfile({ ...value, target_preferences: targets });
    setCapabilities(value.capabilities.join("\n"));
    setExclusions(value.exclusions.join("\n"));
    setTargetText({
      industries: targets.industries.join("\n"),
      locations: targets.locations.join("\n"),
      contact_roles: targets.contact_roles.join("\n"),
      keywords: targets.keywords.join("\n"),
    });
    setSizes(targets.company_sizes);
  }

  const loadStatus = useCallback(async () => {
    const request = ++statusRequest.current;
    setStatusError(null);
    try {
      const value = await getSellerProfileStatus();
      if (request !== statusRequest.current) return;
      setStatus(value);
    } catch (e) {
      if (request !== statusRequest.current) return;
      setStatus(undefined);
      setStatusError(errorText(e, "Could not check which seller profile is active."));
    }
  }, []);

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
    void loadStatus();
    return () => {
      requestId.current += 1;
      statusRequest.current += 1;
    };
  }, [load, loadStatus]);

  function edit(field: keyof SellerProfileContent, value: string) {
    setProfile((previous) => ({ ...previous, [field]: value }));
    setMessage(null);
  }

  async function loadDemoTemplate() {
    const request = ++requestId.current;
    setError(null);
    setMessage(null);
    try {
      const template = await getSellerProfileDemoTemplate();
      if (request !== requestId.current) return;
      applyProfile(template);
      setMessage("GTMFlow demonstration template loaded into the form. Nothing is saved or activated until you save a draft and activate it.");
    } catch (e) {
      if (request !== requestId.current) return;
      setError(errorText(e, "Could not load the demonstration template."));
    }
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
      target_preferences: {
        industries: lines(targetText.industries),
        locations: lines(targetText.locations),
        company_sizes: sizes,
        contact_roles: lines(targetText.contact_roles),
        keywords: lines(targetText.keywords),
      },
    };
    try {
      const row = await saveSellerProfile(payload, version);
      if (request !== requestId.current) return;
      applyProfile(row.profile);
      setVersion(row.version);
      const hasTargets = Object.values(row.profile.target_preferences ?? emptyTargets).some((v) => v.length > 0);
      setMessage(
        `Draft version ${row.version} saved. It is not active for outreach or ranking until you activate it.` +
          (hasTargets ? "" : " It has no target customers, so leads cannot be ranked with it."),
      );
      history.refresh();
      void loadStatus();
    } catch (e) {
      if (request !== requestId.current) return;
      setConflict(e instanceof APIError && e.status === 409);
      setError(e instanceof APIError ? (e.detail ?? e.message) : "Could not save. Your entries are still here; try again.");
    } finally {
      if (request === requestId.current) setSaving(false);
    }
  }

  async function runActivation(label: string, action: () => Promise<unknown>, done: string) {
    if (activating) return;
    setActivating(label);
    setActivationError(null);
    setActivationMessage(null);
    try {
      await action();
      setActivationMessage(done);
      setBannerKey((k) => k + 1);
    } catch (e) {
      setActivationError(
        e instanceof APIError && e.status === 409
          ? `${errorText(e, "")} The current state has been reloaded; review it and try again.`
          : errorText(e, "Activation failed. Nothing changed; try again."),
      );
    } finally {
      setActivating(null);
      // Success or failure, show what is actually active now.
      void loadStatus();
      history.refresh();
    }
  }

  function activate(row: SellerProfile, acknowledgeDemo: boolean) {
    if (!status) return;
    void runActivation(
      row.id,
      () => activateSellerProfile(row.id, status.activation_sequence, acknowledgeDemo),
      `Version ${row.version} is now active. New outreach drafts and lead rankings use it.`,
    );
  }

  function deactivate() {
    if (!status) return;
    void runActivation(
      "deactivate",
      () => deactivateSellerProfile(status.activation_sequence),
      "No seller profile is active. Outreach generation is unavailable until you activate a revision.",
    );
  }

  const activeId = status?.active_profile?.id ?? null;

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <PageHeader title="Seller profile" back={{ href: "/settings", label: "Settings" }} description="Describe what you sell and who you want as customers. Leads are ranked against your target customers, and drafts may only use what you write here." />
      <ActivationBanner status={status} error={statusError} onRetry={() => void loadStatus()} onDeactivate={deactivate} busy={activating !== null} />
      <RankingBanner refreshKey={bannerKey} onSellerProfilePage />
      {activationMessage && <p role="status" className="text-sm text-emerald-700">{activationMessage}</p>}
      {activationError && <ErrorMessage>{activationError}</ErrorMessage>}
      {loading && <LoadingState text="Loading seller profile…" />}
      {error && <ErrorMessage>{error}</ErrorMessage>}
      {!loading && !loaded && <Button variant="secondary" onClick={() => void load()}>Retry loading</Button>}
      {conflict && <div className="space-y-2 text-sm text-slate-600">
        <p>Your entries are preserved. Copy any changes you want to keep before loading the latest saved version.</p>
        <Button variant="secondary" onClick={() => void load()}>Load latest and replace this form</Button>
      </div>}
      {message && <p role="status" className="text-sm text-emerald-700">{message}</p>}
      {loaded && !loading && <Card title={version ? `Edit draft version ${version}` : "Create a seller draft"} subtitle="Each changed save keeps the previous version in history.">
        <p className="mb-4 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
          Draft only. Saving creates a new draft revision; it never changes which revision is active. Activate a reviewed revision under Saved versions to use it for outreach and lead ranking.
        </p>
        <form onSubmit={save} className="space-y-5">
          <fieldset disabled={saving || conflict} className="space-y-5 disabled:opacity-60">
            <label className="block text-sm font-medium text-slate-700">
              Profile purpose
              <select className={inputClass} value={profile.profile_kind} onChange={(e) => edit("profile_kind", e.target.value)}>
                <option value="seller">Actual seller</option>
                <option value="demo">Demonstration only</option>
              </select>
            </label>
            <SectionHeading title="What you sell" text="Your offer, what it can do, and the claims you can back up. Drafts may describe only these capabilities and make only these claims." />
            <Field label="Company name" value={profile.company_name} maxLength={160} onChange={(value) => edit("company_name", value)} />
            <Field label="Product or service" value={profile.product_name} maxLength={160} onChange={(value) => edit("product_name", value)} />
            <Field label="Value proposition" value={profile.value_proposition} maxLength={2000} multiline hint="Explain the offer in your own words. Put figures or results in a sourced proof point; generated drafts reject figures that no proof point supports." onChange={(value) => edit("value_proposition", value)} />
            <Field label="Capabilities (optional)" value={capabilities} maxLength={6011} multiline required={false} hint="One capability per line, up to 12. Describe only features you actually offer." onChange={(value) => { setCapabilities(value); setMessage(null); }} />
            <section aria-label="Proof points" className="space-y-3">
              <div>
                <h2 className="text-sm font-semibold text-slate-900">Proof points (optional)</h2>
                <p className="text-sm text-slate-500">Add a source or reference for each claim. Leave empty if none are available. Generated drafts may only make these claims.</p>
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
            <Field label="Exclusions (optional)" value={exclusions} maxLength={6011} multiline required={false} hint="One do-not-target rule per line, up to 12. They are passed to generation as seller instructions; they are not routing rules." onChange={(value) => { setExclusions(value); setMessage(null); }} />

            <SectionHeading title="Who you want to sell to" text="Your target customers. Uploaded leads are ranked by how well their recorded facts match these preferences; leave a list empty for no preference. Ranking never uses a model and never treats a match as proof that a company needs your product." />
            <Field label="Target customers, in your words" value={profile.target_customer} maxLength={3000} multiline hint="A short description passed to drafting as context. It does not affect ranking; use the lists below for that." onChange={(value) => edit("target_customer", value)} />
            <TargetList label="Industries" field="industries" value={targetText.industries} placeholder={"Healthcare\nDental clinics"} hint="One per line. Matched as whole words against the lead's industry: “Hospital” does not match “Hospitality”, “Clinic” matches “Clinics”." onChange={(field, value) => { setTargetText((t) => ({ ...t, [field]: value })); setMessage(null); }} />
            <TargetList label="Locations" field="locations" value={targetText.locations} placeholder={"United States\nCanada"} hint="Countries, states or cities, one per line, matched by name against the lead's location (USA, US and United States are treated alike). A city is not resolved to its country." onChange={(field, value) => { setTargetText((t) => ({ ...t, [field]: value })); setMessage(null); }} />
            <CompanySizes sizes={sizes} onChange={(next) => { setSizes(next); setMessage(null); }} />
            <TargetList label="Contact roles" field="contact_roles" value={targetText.contact_roles} placeholder={"Practice Manager\nVP Operations"} hint="One per line, matched against the contact's title (VP and Vice President are treated alike)." onChange={(field, value) => { setTargetText((t) => ({ ...t, [field]: value })); setMessage(null); }} />
            <TargetList label="Relevant keywords (optional)" field="keywords" value={targetText.keywords} placeholder={"appointment scheduling\npatient intake"} hint="One per line, searched in the lead's notes and description columns." onChange={(field, value) => { setTargetText((t) => ({ ...t, [field]: value })); setMessage(null); }} />
            <RankingRules />
            <div className="flex flex-wrap gap-2">
              <Button type="submit" loading={saving}>Save draft</Button>
              <Button variant="ghost" onClick={() => void loadDemoTemplate()}>Load GTMFlow demonstration template</Button>
            </div>
          </fieldset>
        </form>
      </Card>}
      <Card title="Saved versions" subtitle="Historical revisions are read-only. Activating one does not change or delete the others.">
        {history.loading && <LoadingState text="Loading versions…" />}
        {!history.loading && history.items.length === 0 && !history.error && <p className="text-sm text-slate-500">No saved versions yet.</p>}
        <div className="space-y-3">
          {history.items.map((row) => <details key={row.id} className="rounded-lg border border-slate-200 p-3">
            <summary className="cursor-pointer text-sm font-medium">
              Version {row.version} · {row.profile.company_name} · {versionLabel(row, activeId)}
            </summary>
            <div className="mt-3 space-y-2 whitespace-pre-wrap text-sm text-slate-600">
              <p><strong>Product:</strong> {row.profile.product_name}</p>
              <p><strong>Offer:</strong> {row.profile.value_proposition}</p>
              <p><strong>Customers:</strong> {row.profile.target_customer}</p>
              <p><strong>Ranks leads by:</strong> {describeTargets(row.profile.target_preferences)}</p>
              <p><strong>Capabilities:</strong> {row.profile.capabilities.join("; ") || "None supplied"}</p>
              <p><strong>Exclusions:</strong> {row.profile.exclusions.join("; ") || "None supplied"}</p>
              {row.profile.proof_points.map((point, index) => <p key={index}>{point.claim} — Source: {point.source}</p>)}
              <p className="font-mono text-xs text-slate-400">Content hash {row.content_hash}</p>
            </div>
            {row.id !== activeId && status && <ActivationForm
              row={row}
              busy={activating !== null}
              pending={activating === row.id}
              onActivate={(acknowledgeDemo) => activate(row, acknowledgeDemo)}
            />}
          </details>)}
          {history.error && <div className="space-y-2"><ErrorMessage>{history.error}</ErrorMessage><Button variant="secondary" onClick={history.reload}>Retry versions</Button></div>}
          {!history.error && history.hasMore && <Button variant="secondary" loading={history.loadingMore} disabled={history.loading} onClick={history.loadMore}>Load older versions</Button>}
        </div>
      </Card>
    </div>
  );
}

function versionLabel(row: SellerProfile, activeId: string | null): string {
  const demo = row.profile.profile_kind === "demo";
  if (row.id === activeId) return demo ? "Active demonstration profile" : "Active seller profile";
  return demo ? "Demonstration draft" : "Seller draft";
}

function ActivationBanner({ status, error, onRetry, onDeactivate, busy }: {
  status: SellerProfileStatus | undefined; error: string | null;
  onRetry: () => void; onDeactivate: () => void; busy: boolean;
}) {
  if (error) {
    return <div className="space-y-2">
      <ErrorMessage>{error}</ErrorMessage>
      <Button variant="secondary" onClick={onRetry}>Retry status</Button>
    </div>;
  }
  if (!status) return <LoadingState text="Checking which seller profile is active…" />;
  const active = status.active_profile;
  if (!active) {
    return <section aria-label="Seller profile status" className="rounded-lg border border-slate-200 bg-slate-50 p-4 text-sm text-slate-700">
      <div className="font-semibold">{status.state === "missing" ? "No seller profile saved" : "No seller profile is active"}</div>
      <p className="mt-1">Outreach generation is unavailable until a reviewed revision is activated. Company summaries still work and describe only the lead record.</p>
    </section>;
  }
  const demo = active.profile.profile_kind === "demo";
  return <section aria-label="Seller profile status" className={`rounded-lg border p-4 text-sm ${demo ? "border-amber-200 bg-amber-50 text-amber-900" : "border-emerald-200 bg-emerald-50 text-emerald-900"}`}>
    <div className="font-semibold">
      Active: version {active.version} · {active.profile.company_name}{demo ? " · Demonstration profile" : ""}
    </div>
    <p className="mt-1">
      New outreach drafts use exactly this revision (hash <span className="font-mono">{active.content_hash.slice(0, 12)}</span>).
      {demo && " Drafts from a demonstration profile are for demonstration only and never count as ready for outbound email."}
    </p>
    {!status.latest_is_active && <p className="mt-1">Version {status.latest_version} is a newer draft and is not active.</p>}
    <div className="mt-2"><Button variant="secondary" loading={busy} disabled={busy} onClick={onDeactivate}>Deactivate</Button></div>
  </section>;
}

function ActivationForm({ row, busy, pending, onActivate }: {
  row: SellerProfile; busy: boolean; pending: boolean; onActivate: (acknowledgeDemo: boolean) => void;
}) {
  const [reviewed, setReviewed] = useState(false);
  const [demoAcknowledged, setDemoAcknowledged] = useState(false);
  const demo = row.profile.profile_kind === "demo";
  const ready = reviewed && (!demo || demoAcknowledged);
  return <div className="mt-3 space-y-2 border-t border-slate-100 pt-3 text-sm">
    <label className="flex items-start gap-2">
      <input type="checkbox" checked={reviewed} disabled={busy} onChange={(e) => setReviewed(e.target.checked)} />
      <span>I reviewed version {row.version} and want new outreach drafts to use it.</span>
    </label>
    {demo && <label className="flex items-start gap-2">
      <input type="checkbox" checked={demoAcknowledged} disabled={busy} onChange={(e) => setDemoAcknowledged(e.target.checked)} />
      <span>I understand version {row.version} is a demonstration profile, not a real offer.</span>
    </label>}
    <Button variant="secondary" loading={pending} disabled={!ready || busy} onClick={() => onActivate(demoAcknowledged)}>
      Activate version {row.version}
    </Button>
  </div>;
}

function describeTargets(targets: TargetPreferences | undefined): string {
  if (!targets) return "No target customers set (leads cannot be ranked)";
  const parts = [
    targets.industries.length ? `industries ${targets.industries.join(", ")}` : null,
    targets.locations.length ? `locations ${targets.locations.join(", ")}` : null,
    targets.company_sizes.length ? `sizes ${targets.company_sizes.map(sizeLabel).join(", ")}` : null,
    targets.contact_roles.length ? `roles ${targets.contact_roles.join(", ")}` : null,
    targets.keywords.length ? `keywords ${targets.keywords.join(", ")}` : null,
  ].filter(Boolean);
  return parts.length ? parts.join("; ") : "No target customers set (leads cannot be ranked)";
}

function sizeLabel(range: SizeRange): string {
  const min = range.min_employees.toLocaleString("en-US");
  if (range.max_employees === null) return `${min}+`;
  return range.max_employees === range.min_employees ? min : `${min}–${range.max_employees.toLocaleString("en-US")}`;
}

function sameRange(a: SizeRange, b: SizeRange): boolean {
  return a.min_employees === b.min_employees && a.max_employees === b.max_employees;
}

function SectionHeading({ title, text }: { title: string; text: string }) {
  return <div className="border-t border-slate-100 pt-4 first:border-0 first:pt-0">
    <h2 className="text-base font-semibold text-slate-900">{title}</h2>
    <p className="mt-1 text-sm text-slate-500">{text}</p>
  </div>;
}

function TargetList({ label, field, value, placeholder, hint, onChange }: {
  label: string; field: TargetListField; value: string; placeholder: string; hint: string;
  onChange: (field: TargetListField, value: string) => void;
}) {
  return <label className="block text-sm font-medium text-slate-700">
    {label}
    <textarea value={value} rows={3} placeholder={placeholder} className={inputClass}
      onChange={(e) => onChange(field, e.target.value)} />
    <span className="mt-1 block text-xs font-normal text-slate-500">{hint} Up to 20 entries of 80 characters.</span>
  </label>;
}

function CompanySizes({ sizes, onChange }: { sizes: SizeRange[]; onChange: (sizes: SizeRange[]) => void }) {
  const [min, setMin] = useState("");
  const [max, setMax] = useState("");
  const custom = sizes.filter((size) => !SIZE_PRESETS.some((preset) => sameRange(preset, size)));
  const minValue = Number.parseInt(min, 10);
  const maxValue = max.trim() === "" ? null : Number.parseInt(max, 10);
  const customValid = Number.isInteger(minValue) && minValue >= 1
    && (maxValue === null || (Number.isInteger(maxValue) && maxValue >= minValue)) && sizes.length < 8;
  function toggle(preset: SizeRange) {
    onChange(sizes.some((size) => sameRange(size, preset))
      ? sizes.filter((size) => !sameRange(size, preset))
      : [...sizes, preset]);
  }
  return <fieldset className="space-y-2">
    <legend className="text-sm font-medium text-slate-700">Company size (employees)</legend>
    <div className="flex flex-wrap gap-2">
      {SIZE_PRESETS.map((preset) => {
        const checked = sizes.some((size) => sameRange(size, preset));
        return <label key={sizeLabel(preset)} className={`flex items-center gap-1.5 rounded-full px-3 py-1 text-sm ring-1 ${checked ? "bg-brand-50 text-brand-800 ring-brand-300" : "bg-white text-slate-600 ring-slate-200"}`}>
          <input type="checkbox" checked={checked} onChange={() => toggle(preset)} />
          {sizeLabel(preset)}
        </label>;
      })}
    </div>
    {custom.length > 0 && <ul className="flex flex-wrap gap-2 text-sm">
      {custom.map((size) => <li key={sizeLabel(size)} className="flex items-center gap-1 rounded-full bg-slate-100 px-3 py-1">
        Custom {sizeLabel(size)}
        <button type="button" className="text-slate-500 hover:text-red-700" aria-label={`Remove ${sizeLabel(size)}`}
          onClick={() => onChange(sizes.filter((other) => !sameRange(other, size)))}>×</button>
      </li>)}
    </ul>}
    <div className="flex flex-wrap items-end gap-2 text-sm">
      <label className="text-xs text-slate-500">From<input aria-label="Custom size from" type="number" min={1} value={min} onChange={(e) => setMin(e.target.value)} className={`${inputClass} w-28`} /></label>
      <label className="text-xs text-slate-500">To (blank = no limit)<input aria-label="Custom size to" type="number" min={1} value={max} onChange={(e) => setMax(e.target.value)} className={`${inputClass} w-28`} /></label>
      <Button variant="secondary" size="sm" disabled={!customValid} onClick={() => {
        onChange([...sizes, { min_employees: minValue, max_employees: maxValue }]);
        setMin(""); setMax("");
      }}>Add range</Button>
    </div>
    <p className="text-xs text-slate-500">Smaller is not worse: a 10-person company ranks well when you target 1–10. A range that only partly overlaps yours earns half credit.</p>
  </fieldset>;
}

function RankingRules() {
  return <details className="rounded-lg border border-slate-200 bg-slate-50 p-3 text-sm text-slate-600">
    <summary className="cursor-pointer font-medium text-slate-800">How leads are ranked</summary>
    <ul className="mt-2 list-disc space-y-1 pl-5">
      <li>Default weights: industry 35, company size 25, location 20, contact role 15, keywords 5. Only the criteria you fill in count; their weights are scaled to 100.</li>
      <li>Each criterion is a match (full weight), a mismatch (nothing), or missing information when the lead has no value (nothing, and shown as missing, not as a mismatch).</li>
      <li>Hot is 75 or more, Warm 45–74, Cold under 45. If under half of the weight could be compared, the lead is “Needs data” instead.</li>
      <li>Contact details and do-not-contact status are checked separately; a blocked lead stays blocked at any score.</li>
      <li>Changing these preferences and activating the new version makes earlier rankings outdated (they are kept as history) until you recalculate.</li>
    </ul>
  </details>;
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
