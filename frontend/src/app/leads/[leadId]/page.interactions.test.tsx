import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import LeadDetailPage from "./page";

/**
 * Phase 4 closeout: rendered-page interaction checks for the lead workspace
 * (jsdom + React Testing Library, driving real clicks through the real page
 * component; only the network layer, @/lib/api, is faked).
 *
 * The fake server keeps per-lead state. Adding `server.key(name, leadId)`
 * (or a page-specific key like "getPushes:lead-a@0") to
 * `server.state.holds` holds that call until `release(...)`, so tests
 * construct out-of-order arrivals deliberately instead of hoping for them;
 * `server.state.failNext[key]` makes a call fail once.
 */

const { useParamsMock } = vi.hoisted(() => ({ useParamsMock: vi.fn() }));
vi.mock("next/navigation", () => ({ useParams: useParamsMock }));

const server = vi.hoisted(() => {
  type Held = { resolve: (v: unknown) => void; reject: (e: unknown) => void; run: () => Promise<unknown> };
  const state = {
    leads: {} as Record<string, Record<string, unknown>>,
    drafts: {} as Record<string, Record<string, unknown> | null>,
    outputs: {} as Record<string, Record<string, unknown>[]>,
    pushes: {} as Record<string, Record<string, unknown>[]>,
    fit: {} as Record<string, Record<string, unknown> | null>,
    scores: {} as Record<string, Record<string, unknown> | null>,
    readiness: {} as Record<string, Record<string, unknown>>,
    holds: new Set<string>(),
    held: {} as Record<string, Held[]>,
    failNext: {} as Record<string, number>,
    calls: [] as string[],
  };

  function key(name: string, leadId: string, extra = "") {
    return `${name}:${leadId}${extra}`;
  }

  // Wrap a handler so it can be held (deferred) or made to fail once.
  function call<T>(name: string, leadId: string, run: () => Promise<T>, extra = ""): Promise<T> {
    const k = key(name, leadId, extra);
    state.calls.push(k);
    if ((state.failNext[k] ?? 0) > 0) {
      state.failNext[k] -= 1;
      return Promise.reject(new Error(`${name} failed (simulated)`));
    }
    if (state.holds.has(k) || state.holds.has(key(name, leadId))) {
      return new Promise<T>((resolve, reject) => {
        const heldKey = state.holds.has(k) ? k : key(name, leadId);
        (state.held[heldKey] ??= []).push({
          resolve: resolve as (v: unknown) => void,
          reject,
          run: run as () => Promise<unknown>,
        });
      });
    }
    return run();
  }

  return { state, call, key };
});

function page<T>(all: T[], limit = 50, offset = 0) {
  const items = all.slice(offset, offset + limit);
  return { items, total: all.length, limit, offset, has_more: offset + items.length < all.length };
}

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  const { state, call } = server;
  const nf = () => Promise.reject(new actual.APIError(404, "not found"));
  return {
    ...actual,
    getLead: vi.fn((id: string) => call("getLead", id, () => Promise.resolve(state.leads[id]))),
    getLeadScore: vi.fn((id: string) =>
      call("getLeadScore", id, () => (state.scores[id] ? Promise.resolve(state.scores[id]) : nf())),
    ),
    getLeadFitScore: vi.fn((id: string) =>
      call("getLeadFitScore", id, () => (state.fit[id] ? Promise.resolve(state.fit[id]) : nf())),
    ),
    getSellerProfileStatus: vi.fn(() =>
      Promise.resolve({
        state: "missing", latest_version: null, activation_sequence: 0,
        last_activation: null, active_profile: null, latest_is_active: false,
      }),
    ),
    getReviewState: vi.fn((leadId: string) =>
      Promise.resolve({
        lead_id: leadId, status: "approved", draft_id: null, draft_content_hash: null,
        draft_origin: null, draft_parent_output_id: null, approval_applicable: true,
        delivery_blockers: [], email_blockers: [], blocker_explanations: {}, latest_review: null,
        source: { batch_source: "pdl_import", provider: null, source_snapshot_id: null,
          reported_acquisition_date: null, retrieved_at: null, license: null, freshness_note: "fixture" },
      }),
    ),
    getLeadReadiness: vi.fn((id: string) =>
      call("getLeadReadiness", id, () => Promise.resolve(state.readiness[id])),
    ),
    getLatestAIOutput: vi.fn((id: string, type: string) =>
      call("getLatestAIOutput", id, () =>
        type === "outreach_email" && state.drafts[id] ? Promise.resolve(state.drafts[id]) : nf(),
      ),
    ),
    getAIOutputs: vi.fn((id: string, p?: { limit?: number; offset?: number }) =>
      call(
        "getAIOutputs", id,
        () => Promise.resolve(page(state.outputs[id] ?? [], p?.limit, p?.offset)),
        `@${p?.offset ?? 0}`,
      ),
    ),
    getPushes: vi.fn((id: string, p?: { limit?: number; offset?: number }) =>
      call(
        "getPushes", id,
        () => Promise.resolve(page(state.pushes[id] ?? [], p?.limit, p?.offset)),
        `@${p?.offset ?? 0}`,
      ),
    ),
    approveOutreach: vi.fn((id: string, outputId: string) =>
      call("approveOutreach", id, () =>
        Promise.resolve({ lead_id: id, ai_output_id: outputId, status: "outreach_approved" }),
      ),
    ),
    rejectOutreach: vi.fn(() => Promise.resolve({})),
    pushLead: vi.fn((id: string) => call("pushLead", id, () => Promise.resolve({ id: "push" }))),
    generateSummary: vi.fn((id: string) =>
      call("generateSummary", id, () => {
        const list = state.outputs[id] ?? [];
        state.outputs[id] = [summary(id, `new-${list.length}`), ...list];
        return Promise.resolve(state.outputs[id][0]);
      }),
    ),
  };
});

// ------------------------------------------------------------ fixtures

function lead(id: string, name: string) {
  return {
    id, batch_id: "batch-1", company_name: name, website: null, industry: "Real Estate",
    contact_name: null, contact_email: null, contact_title: null, company_size: null,
    location: null, source: "pdl_import", status: "new", cleaned_data: null,
    created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z",
    company_identity_id: null, source_snapshot_id: null, import_run_id: null,
    source_record_id: null, source_raw_data: null,
  };
}

function base(id: string, leadId: string, type: string) {
  return {
    id, lead_id: leadId, output_type: type, model_used: "mock", prompt_version: "v1",
    created_at: "2026-01-01T00:00:00Z", parent_output_id: null, origin: "generated",
    input_snapshot: null, input_hash: null, output_schema_version: "v1",
    model_revision: "mock-deterministic-v1", adapter_revision: null,
    seller_profile_id: null, seller_profile_version: null, seller_profile_content_hash: null, seller_profile_kind: null,
    content_hash: `hash-${id}`, purpose: "operational", author_label: null, review_status: null,
  };
}

function summary(leadId: string, n: number | string) {
  return {
    ...base(`${leadId}-summary-${n}`, leadId, "company_summary"),
    content: {
      company_summary: `${leadId} summary ${n}`, detected_pain_points: [], fit_reasoning: "",
      evidence: [], inferences: [], confidence: "high",
    },
  };
}

function draft(leadId: string, subject: string) {
  return {
    ...base(`${leadId}-draft`, leadId, "outreach_email"),
    content: {
      subject, email_body: `${subject} -- body`, personalization_points: [],
      call_note: "", confidence: "high",
    },
  };
}

function push(leadId: string, n: number) {
  return {
    id: `${leadId}-push-${n}`, lead_id: leadId, integration_type: "slack",
    payload: { text: `${leadId} push ${n}` }, status: "mock_success", response_text: null,
    created_at: "2026-01-01T00:00:00Z",
  };
}

function readiness(excluded: boolean, reasons: string[] = []) {
  const gap = { status: "not_ready", gaps: ["no_seller_profile_configured"], gap_explanations: {
    no_seller_profile_configured: "No seller profile." } };
  return {
    lead_id: "x",
    readiness: {
      outbound_email: gap,
      internal_slack_handoff: excluded
        ? { status: "not_ready", gaps: ["lead_excluded_from_routing"], gap_explanations: {} }
        : { status: "ready", gaps: [], gap_explanations: {} },
    },
    eligibility: { excluded, reasons, checked_at: "2026-09-22T00:00:00Z" },
  };
}

function fitRow(leadId: string, band: string, score: number) {
  return {
    id: `${leadId}-fit`, lead_id: leadId, scorer_version: "fit-scorer-v1",
    profile_id: "demo-us-sectors-v1", profile_version: "1", normalization_version: "fit-norm-v1",
    input_fingerprint: "f", fit_score: score, max_fit_score: 100, evidence_coverage_pct: 100,
    band, criteria: [], readiness: readiness(false).readiness, readiness_is_current: true,
    eligibility: readiness(false).eligibility, at_scoring: null,
    computed_at: "2026-09-22T00:00:00Z", computation_ms: 0.1,
  };
}

function seed(leadId: string, name: string, opts: { summaries?: number; pushes?: number } = {}) {
  const s = server.state;
  s.leads[leadId] = lead(leadId, name);
  s.drafts[leadId] = draft(leadId, `${name} current draft`);
  s.outputs[leadId] = [
    ...Array.from({ length: opts.summaries ?? 0 }, (_, i) => summary(leadId, i)),
    s.drafts[leadId]!,
  ];
  s.pushes[leadId] = Array.from({ length: opts.pushes ?? 0 }, (_, i) => push(leadId, i));
  s.readiness[leadId] = readiness(false);
  s.fit[leadId] = null;
  s.scores[leadId] = null;
}

function legacyScore(leadId: string, total: number, priority: string) {
  return {
    lead_id: leadId, total_score: total, priority, score_breakdown: {},
    matched_signals: { industry_terms: [], title_terms: [], pain_point_terms: [] },
    reasoning: `${leadId} legacy reasoning`,
  };
}

async function release(name: string, leadId: string) {
  const k = server.key(name, leadId);
  const queue = server.state.held[k] ?? [];
  const next = queue.shift();
  if (!next) throw new Error(`nothing held for ${k}`);
  await act(async () => {
    try {
      next.resolve(await next.run());
    } catch (e) {
      next.reject(e);
    }
  });
}

function section(heading: string): HTMLElement {
  const el = screen.getByRole("heading", { name: heading }).closest("section");
  if (!el) throw new Error(`no section for ${heading}`);
  return el as HTMLElement;
}

async function currentDraftSection(): Promise<HTMLElement> {
  await screen.findByRole("heading", { name: "Current outreach draft" });
  return section("Current outreach draft");
}

async function loadAll(heading: string, total: number) {
  for (let guard = 0; guard < 20; guard++) {
    const sec = section(heading);
    if (within(sec).queryByText(new RegExp(`Showing ${total.toLocaleString()} of`))) return;
    const more = await within(sec).findByRole("button", { name: /^load more$/i });
    await act(async () => fireEvent.click(more));
  }
  throw new Error(`never reached ${total} in ${heading}`);
}

beforeEach(async () => {
  const s = server.state;
  for (const k of Object.keys(s) as (keyof typeof s)[]) {
    const v = s[k];
    if (v instanceof Set) v.clear();
    else if (Array.isArray(v)) v.length = 0;
    else Object.keys(v).forEach((kk) => delete (v as Record<string, unknown>)[kk]);
  }
  const api = await import("@/lib/api");
  for (const fn of [api.approveOutreach, api.rejectOutreach, api.pushLead, api.getLead]) {
    (fn as unknown as { mockClear: () => void }).mockClear();
  }
  seed("lead-a", "Alpha Co", { summaries: 230, pushes: 230 });
  seed("lead-b", "Beta Co", { summaries: 3, pushes: 2 });
  useParamsMock.mockReturnValue({ leadId: "lead-a" });
  vi.spyOn(window, "prompt").mockReturnValue("");
});

// ------------------------------------------------------------------ tests

describe("history panels", () => {
  it("both panels page past 200 entries", async () => {
    render(<LeadDetailPage />);
    await screen.findByText("Alpha Co current draft");

    await loadAll("All AI outputs (history)", 231);
    await loadAll("Push history", 230);
    expect(within(section("Push history")).getByText("lead-a push 229")).toBeInTheDocument();
    expect(
      within(section("All AI outputs (history)")).getByText("lead-a summary 229"),
    ).toBeInTheDocument();
  });

  it.each([
    { heading: "Push history", method: "getPushes", total: 230, label: "push" },
    { heading: "All AI outputs (history)", method: "getAIOutputs", total: 231, label: "summary" },
  ])("$heading preserves records and retries the failed later page", async ({ heading, method, total, label }) => {
    const request = `${method}:lead-a@50`;
    server.state.failNext[request] = 1;
    render(<LeadDetailPage />);
    await screen.findByText("Alpha Co current draft");
    const sec = () => section(heading);
    await waitFor(() => expect(within(sec()).getByText(`Showing 50 of ${total}`)).toBeInTheDocument());

    await act(async () => fireEvent.click(within(sec()).getByRole("button", { name: /^load more$/i })));
    await within(sec()).findByText(`${method} failed (simulated)`);
    expect(within(sec()).getByText(`lead-a ${label} 0`)).toBeInTheDocument();
    expect(within(sec()).getByText(`Showing 50 of ${total}`)).toBeInTheDocument();

    await act(async () => fireEvent.click(within(sec()).getByRole("button", { name: /retry/i })));
    await waitFor(() => expect(within(sec()).getByText(`Showing 100 of ${total}`)).toBeInTheDocument());
    expect(within(sec()).getByText(`lead-a ${label} 99`)).toBeInTheDocument();
    expect(within(sec()).queryByText(/simulated/)).not.toBeInTheDocument();
    expect(server.state.calls.filter((call) => call === request)).toHaveLength(2);
  });
});

describe("out-of-order responses after navigation", () => {
  // The page loads a lead's data in sequence (lead, draft, legacy score,
  // readiness, fit), so each stage is held for lead A in turn: A's load is
  // stuck exactly there when the user navigates to B, and its late response
  // arrives after B has fully rendered.
  it.each(["getLatestAIOutput", "getLeadScore", "getLeadReadiness", "getLeadFitScore"])(
    "a late %s response for the previous lead cannot overwrite the current lead",
    async (stage) => {
      const s = server.state;
      s.scores["lead-a"] = legacyScore("lead-a", 91, "Hot");
      s.scores["lead-b"] = legacyScore("lead-b", 12, "Cold");
      s.fit["lead-a"] = fitRow("lead-a", "strong_match", 100);
      s.fit["lead-b"] = fitRow("lead-b", "weak_match", 40);
      s.readiness["lead-a"] = readiness(true, ["LEAD-A-ONLY exclusion reason"]);
      s.holds.add(server.key(stage, "lead-a"));
      s.holds.add("getAIOutputs:lead-a@0");
      s.holds.add("getPushes:lead-a@0");

      const { rerender } = render(<LeadDetailPage />);
      await screen.findByRole("heading", { name: "Alpha Co" });
      await waitFor(() => expect(s.held[server.key(stage, "lead-a")]).toHaveLength(1));

      useParamsMock.mockReturnValue({ leadId: "lead-b" });
      rerender(<LeadDetailPage />);
      await within(await currentDraftSection()).findByText("Beta Co current draft");
      await screen.findByText("Weak match");
      await screen.findByText("Cold");

      // Lead A's held responses now arrive, late and out of order.
      await release("getPushes", "lead-a@0");
      await release(stage, "lead-a");
      await release("getAIOutputs", "lead-a@0");

      expect(screen.getByRole("heading", { name: "Beta Co" })).toBeInTheDocument();
      const draftCard = section("Current outreach draft");
      expect(within(draftCard).getByText("Beta Co current draft")).toBeInTheDocument();
      expect(screen.queryByText("Alpha Co current draft")).not.toBeInTheDocument();
      expect(screen.queryByText("Hot")).not.toBeInTheDocument(); // A's legacy score
      expect(screen.getByText("Cold")).toBeInTheDocument();
      expect(screen.queryByText("Strong match")).not.toBeInTheDocument(); // A's fit
      expect(screen.getByText("Weak match")).toBeInTheDocument();
      expect(screen.queryByText(/LEAD-A-ONLY/)).not.toBeInTheDocument(); // A's readiness
      expect(screen.queryByText("lead-a summary 0")).not.toBeInTheDocument();
      expect(screen.queryByText("lead-a push 0")).not.toBeInTheDocument();
      expect(within(section("Push history")).getByText(/Showing 2 of 2/)).toBeInTheDocument();
    },
  );

  it("an action that finishes after navigating away does not repaint the old lead", async () => {
    server.state.holds.add(server.key("approveOutreach", "lead-a"));
    const { rerender } = render(<LeadDetailPage />);
    await screen.findByText("Alpha Co current draft");
    await act(async () =>
      fireEvent.click(screen.getByRole("button", { name: /approve outreach/i })),
    );

    useParamsMock.mockReturnValue({ leadId: "lead-b" });
    rerender(<LeadDetailPage />);
    await within(await currentDraftSection()).findByText("Beta Co current draft");
    expect(screen.getByRole("button", { name: /approve outreach/i })).toBeEnabled();
    const getLeadCallsBefore = server.state.calls.filter((c) => c === "getLead:lead-a").length;

    await release("approveOutreach", "lead-a");
    await act(async () => undefined);

    expect(screen.getByRole("heading", { name: "Beta Co" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /approve outreach/i })).toBeEnabled();
    expect(screen.queryByText("Alpha Co current draft")).not.toBeInTheDocument();
    expect(screen.queryByText("lead-a summary 0")).not.toBeInTheDocument();
    // No post-action refresh of the old lead was even requested.
    expect(server.state.calls.filter((c) => c === "getLead:lead-a")).toHaveLength(getLeadCallsBefore);
    expect(server.state.calls.filter((c) => c.startsWith("getAIOutputs:lead-a"))).toHaveLength(1);
  });
});

describe("approval target", () => {
  it.each(["approve", "reject"])("%s sends exactly the draft rendered on screen, before and after loading older history", async (decision) => {
    const api = await import("@/lib/api");
    const action = decision === "approve" ? api.approveOutreach : api.rejectOutreach;
    // Phase 6: the exact content hash displayed is sent too, and a
    // rejection carries its required reason.
    // Phase 10: an unflagged draft is approved with an empty acknowledgement.
    const args = decision === "approve"
      ? ["lead-a", "lead-a-draft", "hash-lead-a-draft", []]
      : ["lead-a", "lead-a-draft", "hash-lead-a-draft", "Off-target"];
    const act_ = async () => {
      await act(async () =>
        fireEvent.click(screen.getByRole("button", { name: new RegExp(`${decision} outreach`, "i") })),
      );
      if (decision === "reject") {
        fireEvent.change(screen.getByLabelText("Reason for rejecting (required)"), { target: { value: "Off-target" } });
        await act(async () => fireEvent.click(screen.getByRole("button", { name: "Confirm rejection" })));
      }
    };
    render(<LeadDetailPage />);
    const current = await currentDraftSection();
    await within(current).findByText("Alpha Co current draft");

    await act_();
    expect(action).toHaveBeenLastCalledWith(...args);

    await loadAll("All AI outputs (history)", 231);
    // The draft is also now visible deep in history; the target is unchanged.
    expect(within(current).getByText("Alpha Co current draft")).toBeInTheDocument();
    await act_();
    expect(action).toHaveBeenLastCalledWith(...args);
  });

  it("approve/reject are unavailable while the draft lookup is unresolved", async () => {
    server.state.holds.add(server.key("getLatestAIOutput", "lead-a"));
    render(<LeadDetailPage />);
    await screen.findByRole("heading", { name: "Alpha Co" });
    expect(screen.queryByRole("button", { name: /approve outreach/i })).not.toBeInTheDocument();
    await release("getLatestAIOutput", "lead-a");
    expect(await screen.findByRole("button", { name: /approve outreach/i })).toBeEnabled();
  });
});

describe("post-action history refresh", () => {
  it("reloads from the first page instead of appending shifted duplicates", async () => {
    render(<LeadDetailPage />);
    await screen.findByText("Alpha Co current draft");
    await act(async () =>
      fireEvent.click(screen.getByRole("button", { name: /generate summary/i })),
    );
    const hist = section("All AI outputs (history)");
    await within(hist).findByText("lead-a summary new-231");
    await waitFor(() => expect(within(hist).getByText(/Showing 50 of 232/)).toBeInTheDocument());
    expect(within(hist).getAllByText("lead-a summary 0")).toHaveLength(1);
  });
});

describe("current restrictions before push", () => {
  it("push is disabled while the lead is currently excluded", async () => {
    server.state.readiness["lead-a"] = readiness(true, ["lead status 'do_not_contact' blocks outreach delivery"]);
    render(<LeadDetailPage />);
    await screen.findByText(/Excluded from routing/);
    expect(screen.getByRole("button", { name: /push to slack/i })).toBeDisabled();
  });

  it("re-checks current eligibility at click time and does not send if it changed", async () => {
    const api = await import("@/lib/api");
    render(<LeadDetailPage />);
    const pushButton = await screen.findByRole("button", { name: /push to slack/i });
    await waitFor(() => expect(pushButton).toBeEnabled());

    // Became excluded after the page loaded (e.g. batch marked partial).
    server.state.readiness["lead-a"] = readiness(true, ["batch batch-1 is only partially imported (status='partial')"]);
    await act(async () => fireEvent.click(pushButton));

    expect(api.pushLead).not.toHaveBeenCalled();
    expect(await screen.findByText(/Not pushed: excluded from routing/)).toBeInTheDocument();
  });

  it("pushes when currently eligible", async () => {
    const api = await import("@/lib/api");
    render(<LeadDetailPage />);
    const pushButton = await screen.findByRole("button", { name: /push to slack/i });
    await waitFor(() => expect(pushButton).toBeEnabled());
    await act(async () => fireEvent.click(pushButton));
    await waitFor(() => expect(api.pushLead).toHaveBeenCalledWith("lead-a", true));
  });
});
