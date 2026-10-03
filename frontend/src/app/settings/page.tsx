"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { ModelStatusPill, useAIStatus } from "@/components/AIStatusProvider";
import { useAuth } from "@/components/AuthProvider";
import { Button } from "@/components/Button";
import { Card } from "@/components/Card";
import { PageHeader } from "@/components/PageHeader";
import { getSellerProfileStatus } from "@/lib/api";
import type { SellerProfileStatus } from "@/types/api";

// Settings: the drafting model, what you sell, and your account.

export default function SettingsPage() {
  const { session, signOut } = useAuth();
  const { status, refresh } = useAIStatus();
  const [seller, setSeller] = useState<SellerProfileStatus | null>(null);
  const [sellerError, setSellerError] = useState(false);

  useEffect(() => {
    getSellerProfileStatus().then(setSeller).catch(() => setSellerError(true));
  }, []);

  const active = seller?.active_profile ?? null;
  const role = session?.role === "guest" ? "Guest (temporary)" : session?.role === "viewer" ? "Viewer (read-only)" : "Operator";

  return (
    <div className="space-y-6">
      <PageHeader title="Settings" description="How drafts are written and who you are signed in as." />

      <Card title="Drafting model" icon="cpu"
        actions={<Button variant="ghost" size="sm" icon="refresh" onClick={refresh}>Check again</Button>}>
        {status ? (
          <div className="space-y-4 text-sm">
            <div className="flex flex-wrap items-center gap-3">
              <ModelStatusPill status={status} />
              <span className="font-medium text-slate-900">{status.label}</span>
            </div>
            <p className="text-slate-600">{status.detail}</p>
            <dl className="grid gap-3 rounded-lg bg-slate-50 p-4 sm:grid-cols-2">
              <div>
                <dt className="text-xs uppercase tracking-wide text-slate-500">Fine-tuned model</dt>
                <dd className="text-slate-900">{status.base_model} + LoRA adapter</dd>
              </div>
              <div>
                <dt className="text-xs uppercase tracking-wide text-slate-500">Model server</dt>
                <dd className="text-slate-900">
                  {status.fine_tuned_connected ? "Connected" : status.fine_tuned_selected ? "Not reachable" : "Not connected"}
                </dd>
              </div>
              <div className="sm:col-span-2">
                <dt className="text-xs uppercase tracking-wide text-slate-500">Adapter</dt>
                <dd className="break-all font-mono text-xs text-slate-700">{status.adapter}</dd>
              </div>
            </dl>
            <p className="text-xs text-slate-500">
              The fine-tuned model was trained on reviewed examples of grounded outreach and evaluated against its base
              model (AI-reviewed, not human-verified). Every draft, whichever model writes it, is checked against the
              lead&apos;s record and needs your approval before it can be sent.
            </p>
            {!status.fine_tuned_connected && session?.role === "operator" && (
              <details className="rounded-lg border border-slate-200 p-3 text-xs text-slate-600">
                <summary className="cursor-pointer font-medium text-slate-800">How to connect the fine-tuned model</summary>
                <p className="mt-2">Start the model server, then set these on the API and worker and restart them:</p>
                <pre className="mt-2 overflow-x-auto rounded bg-slate-900 p-3 text-slate-100">{`USE_MOCK_AI=false
AI_PROVIDER=qwen3-4b-lora-v1
LORA_INFERENCE_BASE_URL=https://<your-model-server>/v1
LORA_FALLBACK_TO_MOCK=true   # use the demo generator while it is offline`}</pre>
              </details>
            )}
          </div>
        ) : (
          <p className="text-sm text-slate-500">Checking the model…</p>
        )}
      </Card>

      <Card title="What you sell" icon="target"
        actions={<Link href="/seller-profile" className="text-sm font-medium text-brand-700 hover:underline">Edit seller profile</Link>}>
        <div className="text-sm text-slate-600">
          {sellerError ? (
            "The seller profile could not be loaded."
          ) : !seller ? (
            "Loading…"
          ) : active ? (
            <>
              Drafts describe <span className="font-medium text-slate-900">{active.profile.company_name}</span>{" "}
              (version {active.version}){active.profile.profile_kind === "demo" ? ", a demonstration profile: its drafts are never marked ready for email." : "."}
            </>
          ) : (
            <>No seller profile is active, so outreach drafts are unavailable. <Link href="/seller-profile" className="font-medium text-brand-700 hover:underline">Set one up</Link>.</>
          )}
        </div>
      </Card>

      <Card title="Account" icon="users">
        <div className="flex flex-wrap items-center justify-between gap-3 text-sm">
          <div>
            <div className="font-medium text-slate-900">{session?.role === "guest" ? "Guest" : session?.username}</div>
            <div className="text-slate-500">{role}</div>
          </div>
          <Button variant="secondary" onClick={() => void signOut()}>Sign out</Button>
        </div>
      </Card>
    </div>
  );
}
