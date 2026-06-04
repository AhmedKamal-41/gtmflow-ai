import Link from "next/link";

import { Icon, type IconName } from "@/components/Icon";

const STEPS: { title: string; body: string; icon: IconName }[] = [
  {
    title: "Upload",
    body: "Drop in your lead CSV. Bad rows get flagged so you can fix them.",
    icon: "upload",
  },
  {
    title: "Score",
    body: "Each lead gets a 0–100 fit score, with the reasons shown.",
    icon: "target",
  },
  {
    title: "Draft",
    body: "A short summary and a starting outreach email, written from the lead's own details.",
    icon: "sparkles",
  },
  {
    title: "Send",
    body: "Approve the good ones and they go straight to Slack, logged for later.",
    icon: "send",
  },
];

const FEATURES: { title: string; body: string; icon: IconName }[] = [
  {
    title: "Clean CSV import",
    body: "Upload a messy export and it sorts out the columns, flags bad rows, and keeps any extra fields you had.",
    icon: "file",
  },
  {
    title: "Scoring you can explain",
    body: "A simple 0–100 fit score with the breakdown shown, so you always know why a lead landed where it did.",
    icon: "target",
  },
  {
    title: "First-draft summaries & emails",
    body: "Each lead gets a short write-up and an outreach draft to start from. It's grounded in the lead's own data.",
    icon: "sparkles",
  },
  {
    title: "One-click send to Slack",
    body: "Approved hot leads post to your Slack channel, and every send is logged so nothing gets lost.",
    icon: "send",
  },
  {
    title: "See what it saved you",
    body: "A small dashboard tracks how many drafts were approved, how many got sent, and the time it saved.",
    icon: "chart",
  },
  {
    title: "Made for non-engineers",
    body: "Clear pages, plain badges, and one-click actions, so your revenue team can run the whole thing themselves.",
    icon: "users",
  },
];

const GUARDRAILS: { title: string; body: string; icon: IconName }[] = [
  {
    title: "Works without any keys",
    body: "AI and Slack both run in a built-in mock mode, so you can try the whole flow before wiring anything up.",
    icon: "shield",
  },
  {
    title: "Nothing made up",
    body: "Every draft separates what it read from the lead from what it's guessing, with no invented facts or fake metrics.",
    icon: "check",
  },
  {
    title: "Honest about the numbers",
    body: "Time saved is a rough estimate (about 5 minutes a lead), not a promise of real revenue.",
    icon: "clock",
  },
];

const STACK = ["FastAPI", "Next.js 15", "PostgreSQL", "OpenAI / mock AI", "Slack"];

export default function HomePage() {
  return (
    <div className="space-y-16">
      {/* Hero */}
      <section className="relative overflow-hidden rounded-2xl border border-slate-200 bg-white p-8 shadow-card sm:p-12">
        <div
          className="pointer-events-none absolute -right-24 -top-24 h-72 w-72 rounded-full bg-brand-100/50 blur-3xl"
          aria-hidden="true"
        />
        <div className="relative max-w-2xl space-y-5">
          <span className="inline-flex items-center gap-1.5 rounded-full border border-brand-200 bg-brand-50 px-3 py-1 text-xs font-semibold text-brand-700">
            <span className="h-1.5 w-1.5 rounded-full bg-brand-500" />
            Lead list → Slack, in a few clicks
          </span>
          <h1 className="text-4xl font-bold tracking-tight text-slate-900 sm:text-5xl">
            Stop hand-sorting leads.
            <span className="text-brand-600"> Let GTMFlow do the first pass.</span>
          </h1>
          <p className="text-lg leading-relaxed text-slate-600">
            Drop in a CSV and GTMFlow scores every lead, writes a first-draft
            summary and outreach email, and sends the hottest ones to Slack once
            you&apos;ve approved them. You stay in control; it just does the
            tedious part.
          </p>
          <div className="flex flex-wrap items-center gap-3 pt-1">
            <Link
              href="/demo"
              className="inline-flex items-center gap-2 rounded-lg bg-brand-600 px-5 py-2.5 text-sm font-semibold text-white shadow-sm transition-colors hover:bg-brand-700"
            >
              <Icon name="play" className="h-4 w-4" filled />
              Run the live demo
            </Link>
            <Link
              href="/upload"
              className="inline-flex items-center gap-2 rounded-lg border border-slate-300 bg-white px-5 py-2.5 text-sm font-semibold text-slate-800 transition-colors hover:bg-slate-50"
            >
              <Icon name="upload" className="h-4 w-4" />
              Upload a lead list
            </Link>
          </div>
        </div>
      </section>

      {/* Problem & Solution */}
      <section className="grid gap-4 lg:grid-cols-2">
        <div className="rounded-2xl border border-slate-200 bg-white p-7 shadow-card">
          <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-amber-600">
            <Icon name="alert" className="h-4 w-4" />
            The problem
          </div>
          <p className="mt-3 text-base leading-relaxed text-slate-700">
            Lead lists pile up and go stale. Figuring out who&apos;s worth
            contacting, writing a decent first email, and getting the good ones
            in front of the right person eats hours every week, and afterwards
            nobody can say{" "}
            <span className="font-medium text-slate-900">
              how much of it actually got used.
            </span>
          </p>
        </div>
        <div className="rounded-2xl border border-brand-200 bg-brand-50/50 p-7 shadow-card">
          <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-brand-600">
            <Icon name="check" className="h-4 w-4" />
            The solution
          </div>
          <p className="mt-3 text-base leading-relaxed text-slate-700">
            GTMFlow takes the CSV, scores each lead, and drafts a summary and
            outreach email. You approve the ones you like, it sends them to
            Slack, and it{" "}
            <span className="font-medium text-slate-900">
              keeps track of every step
            </span>{" "}
            so you can see what the team actually used.
          </p>
        </div>
      </section>

      {/* Workflow */}
      <section>
        <SectionHeading eyebrow="How it works" title="Four steps, start to finish" />
        <div className="mt-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {STEPS.map((s, i) => (
            <div
              key={s.title}
              className="group relative rounded-xl border border-slate-200 bg-white p-5 shadow-card transition-shadow hover:shadow-card-hover"
            >
              <div className="flex items-center justify-between">
                <span className="grid h-10 w-10 place-items-center rounded-lg bg-brand-50 text-brand-600">
                  <Icon name={s.icon} className="h-5 w-5" />
                </span>
                <span className="tabular text-sm font-semibold text-slate-300">
                  0{i + 1}
                </span>
              </div>
              <div className="mt-4 text-base font-semibold text-slate-900">
                {s.title}
              </div>
              <p className="mt-1.5 text-sm leading-relaxed text-slate-600">
                {s.body}
              </p>
            </div>
          ))}
        </div>
      </section>

      {/* Capabilities */}
      <section>
        <SectionHeading eyebrow="What's inside" title="What it does" />
        <div className="mt-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {FEATURES.map((f) => (
            <div
              key={f.title}
              className="rounded-xl border border-slate-200 bg-white p-5 shadow-card transition-shadow hover:shadow-card-hover"
            >
              <span className="grid h-10 w-10 place-items-center rounded-lg bg-brand-50 text-brand-600">
                <Icon name={f.icon} className="h-5 w-5" />
              </span>
              <div className="mt-4 text-sm font-semibold text-slate-900">
                {f.title}
              </div>
              <p className="mt-1.5 text-sm leading-relaxed text-slate-600">
                {f.body}
              </p>
            </div>
          ))}
        </div>
      </section>

      {/* Honest by design */}
      <section className="rounded-2xl border border-slate-200 bg-slate-900 p-8 shadow-card sm:p-10">
        <div className="text-xs font-semibold uppercase tracking-wider text-brand-300">
          Honest by design
        </div>
        <h2 className="mt-1 text-2xl font-bold tracking-tight text-white">
          No magic tricks
        </h2>
        <div className="mt-6 grid gap-6 sm:grid-cols-3">
          {GUARDRAILS.map((g) => (
            <div key={g.title}>
              <span className="grid h-10 w-10 place-items-center rounded-lg bg-white/10 text-brand-300">
                <Icon name={g.icon} className="h-5 w-5" />
              </span>
              <div className="mt-3 text-sm font-semibold text-white">
                {g.title}
              </div>
              <p className="mt-1 text-sm leading-relaxed text-slate-300">
                {g.body}
              </p>
            </div>
          ))}
        </div>
      </section>

      {/* Stack */}
      <section>
        <SectionHeading eyebrow="Built with" title="Stack" />
        <div className="mt-4 flex flex-wrap gap-2">
          {STACK.map((s) => (
            <span
              key={s}
              className="rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 shadow-card"
            >
              {s}
            </span>
          ))}
        </div>
      </section>

      {/* Final CTA */}
      <section className="relative overflow-hidden rounded-2xl border border-brand-200 bg-gradient-to-br from-brand-600 to-brand-800 p-8 text-center shadow-card sm:p-12">
        <h2 className="text-2xl font-bold tracking-tight text-white sm:text-3xl">
          Try it with one click
        </h2>
        <p className="mx-auto mt-2 max-w-xl text-sm leading-relaxed text-brand-100">
          The demo loads a sample lead list and runs the whole thing (score,
          draft, approve, send), then shows you the numbers at the end. No setup
          needed.
        </p>
        <div className="mt-6 flex flex-wrap justify-center gap-3">
          <Link
            href="/demo"
            className="inline-flex items-center gap-2 rounded-lg bg-white px-5 py-2.5 text-sm font-semibold text-brand-700 shadow-sm transition-colors hover:bg-brand-50"
          >
            <Icon name="play" className="h-4 w-4" filled />
            Run the live demo
          </Link>
          <Link
            href="/metrics"
            className="inline-flex items-center gap-2 rounded-lg border border-white/30 bg-white/10 px-5 py-2.5 text-sm font-semibold text-white transition-colors hover:bg-white/20"
          >
            <Icon name="chart" className="h-4 w-4" />
            View metrics
          </Link>
        </div>
      </section>
    </div>
  );
}

function SectionHeading({
  eyebrow,
  title,
}: {
  eyebrow: string;
  title: string;
}) {
  return (
    <div className="space-y-1">
      <div className="text-xs font-semibold uppercase tracking-wider text-brand-600">
        {eyebrow}
      </div>
      <h2 className="text-2xl font-bold tracking-tight text-slate-900">
        {title}
      </h2>
    </div>
  );
}
