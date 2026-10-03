"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { useAIStatus } from "@/components/AIStatusProvider";
import { useAuth } from "@/components/AuthProvider";
import { Button } from "@/components/Button";
import { Card } from "@/components/Card";
import { ErrorMessage } from "@/components/ErrorMessage";
import { Icon, type IconName } from "@/components/Icon";
import { LoadingState } from "@/components/LoadingState";
import { PriorityBadge } from "@/components/PriorityBadge";
import { StageBadge } from "@/components/StageBadge";
import { APIError, getInbox, getSellerProfileStatus, type InboxItem, type InboxPage, type LeadStage } from "@/lib/api";
import { SAMPLE_CSV_URL, loadSampleLeads } from "@/lib/sampleData";

// Today: what needs the rep's attention, in workflow order.

const ACTIONABLE: LeadStage[] = ["to_review", "approved", "outdated", "needs_draft"];

const NEXT_STEP: Partial<Record<LeadStage, string>> = {
  to_review: "Review the draft",
  approved: "Send to Slack",
  needs_draft: "Write a draft",
  outdated: "Write a new draft",
};

function greeting(): string {
  const hour = new Date().getHours();
  return hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening";
}

export default function TodayPage() {
  const router = useRouter();
  const { session } = useAuth();
  const { status } = useAIStatus();
  const [inbox, setInbox] = useState<InboxPage | null>(null);
  const [hotNeedingDraft, setHotNeedingDraft] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [loadingSample, setLoadingSample] = useState(false);
  // undefined = unknown (lookup pending or failed): no setup prompt is shown.
  const [sellerActive, setSellerActive] = useState<boolean | undefined>(undefined);

  useEffect(() => {
    getSellerProfileStatus()
      .then((status) => setSellerActive(status.state === "active"))
      .catch(() => setSellerActive(undefined));
  }, []);

  const load = useCallback(async () => {
    setError(null);
    try {
      const [all, hot] = await Promise.all([
        getInbox({ limit: 200 }),
        getInbox({ priority: "Hot", stage: "needs_draft", limit: 1 }),
      ]);
      setInbox(all);
      setHotNeedingDraft(hot.total);
    } catch (e) {
      setError(e instanceof APIError ? (e.detail ?? e.message) : "Could not load your leads.");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function trySample() {
    setLoadingSample(true);
    setError(null);
    try {
      await loadSampleLeads();
      router.push("/leads");
    } catch (e) {
      setError(e instanceof APIError ? (e.detail ?? e.message) : "Could not load the sample leads.");
      setLoadingSample(false);
    }
  }

  const name = session?.role === "guest" ? "" : session?.username.split("@")[0];
  const header = (
    <div className="space-y-1">
      <h1 className="text-2xl font-bold tracking-tight text-slate-900 sm:text-3xl">
        {greeting()}{name ? `, ${name}` : ""}
      </h1>
      <p className="text-sm text-slate-600">Here is what needs your attention.</p>
    </div>
  );

  if (error && !inbox) return <div className="space-y-6">{header}<ErrorMessage>{error}</ErrorMessage></div>;
  if (!inbox) return <div className="space-y-6">{header}<LoadingState /></div>;

  if (inbox.counts.all === 0) {
    return (
      <div className="space-y-6">
        {header}
        <Card>
          <div className="mx-auto max-w-xl space-y-5 py-8 text-center">
            <span className="mx-auto grid h-12 w-12 place-items-center rounded-xl bg-brand-50 text-brand-600">
              <Icon name="upload" className="h-6 w-6" />
            </span>
            <div className="space-y-2">
              <h2 className="text-lg font-semibold text-slate-900">Start with your lead list</h2>
              <p className="text-sm text-slate-600">
                Import a CSV of the companies you are working. GTMFlow ranks them, then drafts outreach
                with its fine-tuned model for you to review before anything is sent.
              </p>
            </div>
            <div className="flex flex-wrap justify-center gap-2">
              <Button icon="upload" onClick={() => router.push("/imports")}>Import leads</Button>
              <Button variant="secondary" loading={loadingSample} onClick={() => void trySample()}>
                Try with sample data
              </Button>
            </div>
            <a href={SAMPLE_CSV_URL} download className="inline-block text-xs font-medium text-brand-700 hover:underline">
              Download the CSV template
            </a>
            {error && <ErrorMessage>{error}</ErrorMessage>}
          </div>
        </Card>
      </div>
    );
  }

  const counts = inbox.counts;
  const next = inbox.items.filter((item) => ACTIONABLE.includes(item.stage) && !item.blocked).slice(0, 6);
  const tiles: { label: string; value: number; href: string; icon: IconName; tone: string }[] = [
    { label: "Drafts to review", value: counts.to_review, href: "/leads?stage=to_review", icon: "file", tone: "text-amber-600 bg-amber-50" },
    { label: "Ready to send", value: counts.approved, href: "/leads?stage=approved", icon: "send", tone: "text-emerald-600 bg-emerald-50" },
    { label: "Hot leads without a draft", value: hotNeedingDraft, href: "/leads?stage=needs_draft&priority=Hot", icon: "flame", tone: "text-red-600 bg-red-50" },
    { label: "Sent to Slack", value: counts.sent, href: "/leads?stage=sent", icon: "check", tone: "text-brand-600 bg-brand-50" },
  ];

  return (
    <div className="space-y-6">
      {header}
      {sellerActive === false && (
        <div className="flex flex-wrap items-center gap-4 rounded-xl border border-brand-200 bg-brand-50 p-4">
          <span className="grid h-9 w-9 flex-none place-items-center rounded-lg bg-white text-brand-600">
            <Icon name="target" className="h-5 w-5" />
          </span>
          <div className="min-w-0 flex-1 text-sm">
            <div className="font-semibold text-slate-900">One step before drafting: tell GTMFlow what you sell</div>
            <div className="text-slate-600">Drafts may only describe your product the way your seller profile does.</div>
          </div>
          <Link href="/seller-profile" className="rounded-lg bg-brand-600 px-3 py-2 text-sm font-medium text-white hover:bg-brand-700">
            Set up seller profile
          </Link>
        </div>
      )}
      {counts.delivery_unknown > 0 && (
        <div role="alert" className="flex items-center gap-3 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
          <Icon name="alert" className="h-4 w-4 flex-none" />
          <span className="flex-1">
            {counts.delivery_unknown} Slack {counts.delivery_unknown === 1 ? "delivery has" : "deliveries have"} an
            unknown outcome. Check the channel and confirm on the lead page.
          </span>
        </div>
      )}

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {tiles.map((tile) => (
          <Link key={tile.label} href={tile.href}
            className="rounded-xl border border-slate-200 bg-white p-4 shadow-card transition-colors hover:border-brand-300">
            <span className={`grid h-8 w-8 place-items-center rounded-lg ${tile.tone}`}>
              <Icon name={tile.icon} className="h-4 w-4" />
            </span>
            <div className="mt-3 text-2xl font-bold tabular text-slate-900">{tile.value}</div>
            <div className="text-sm text-slate-600">{tile.label}</div>
          </Link>
        ))}
      </div>

      <div className="grid gap-6 lg:grid-cols-[1fr_20rem]">
        <Card title="Next up" subtitle="Highest-priority leads that need you, Hot first."
          actions={<Link href="/leads" className="text-sm font-medium text-brand-700 hover:underline">All leads</Link>}
          padding="none">
          {next.length === 0 ? (
            <p className="p-5 text-sm text-slate-600">You are all caught up.</p>
          ) : (
            <ul className="divide-y divide-slate-100">
              {next.map((item: InboxItem) => (
                <li key={item.id}>
                  <Link href={`/leads/${item.id}`} className="flex items-center gap-4 px-5 py-3 hover:bg-slate-50">
                    <div className="min-w-0 flex-1">
                      <div className="truncate font-medium text-slate-900">{item.company_name}</div>
                      <div className="truncate text-xs text-slate-500">
                        {[item.contact_name, item.contact_title].filter(Boolean).join(" · ") || item.industry || "No contact"}
                      </div>
                    </div>
                    {item.priority && <PriorityBadge priority={item.priority} />}
                    <StageBadge stage={item.stage} />
                    <span className="hidden w-32 text-right text-sm font-medium text-brand-700 sm:block">
                      {NEXT_STEP[item.stage]} →
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card title="Drafting model" icon="cpu">
          {status ? (
            <div className="space-y-2 text-sm">
              <div className="font-medium text-slate-900">{status.label}</div>
              <p className="text-slate-600">{status.detail}</p>
              <Link href="/settings" className="inline-block text-sm font-medium text-brand-700 hover:underline">
                Model settings
              </Link>
            </div>
          ) : (
            <p className="text-sm text-slate-500">Checking the model…</p>
          )}
        </Card>
      </div>
    </div>
  );
}
