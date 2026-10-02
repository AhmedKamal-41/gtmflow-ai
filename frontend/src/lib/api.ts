// Typed fetch wrappers for the FastAPI backend.
// One source of truth for the base URL and error shape.

import type {
  AIOutput,
  BatchFitScoreRunSummary,
  BatchFitSummary,
  BatchPushResponse,
  BatchScoreResponse,
  CurrentReadiness,
  DemoRunResponse,
  FitProfile,
  IntegrationPush,
  Job,
  JobType,
  Lead,
  LeadBatch,
  LeadFitScore,
  LeadScore,
  MetricsDashboard,
  OutreachReviewResponse,
  Page,
  SellerProfile,
  SellerProfileActivation,
  SellerProfileContent,
  SellerProfileStatus,
  UploadResponse,
  ReviewState,
} from "@/types/api";

export type PageParams = { limit?: number; offset?: number };

export function getSellerProfile(): Promise<SellerProfile> {
  return request<SellerProfile>("/api/seller-profile");
}

export function saveSellerProfile(
  profile: SellerProfileContent,
  expectedVersion: number,
): Promise<SellerProfile> {
  return request<SellerProfile>("/api/seller-profile", {
    method: "POST",
    body: JSON.stringify({ profile, expected_version: expectedVersion }),
  });
}

export function getSellerProfileVersions(params?: PageParams): Promise<Page<SellerProfile>> {
  return request<Page<SellerProfile>>(`/api/seller-profile/versions${buildQuery({ ...params })}`);
}

export function getSellerProfileStatus(): Promise<SellerProfileStatus> {
  return request<SellerProfileStatus>("/api/seller-profile/status");
}

export function getSellerProfileDemoTemplate(): Promise<SellerProfileContent> {
  return request<SellerProfileContent>("/api/seller-profile/demonstration-template");
}

export function activateSellerProfile(
  sellerProfileId: string,
  expectedActivationSequence: number,
  acknowledgeDemo: boolean,
): Promise<SellerProfileActivation> {
  return request<SellerProfileActivation>("/api/seller-profile/activate", {
    method: "POST",
    body: JSON.stringify({
      seller_profile_id: sellerProfileId,
      expected_activation_sequence: expectedActivationSequence,
      confirm_reviewed: true,
      acknowledge_demo: acknowledgeDemo,
    }),
  });
}

export function deactivateSellerProfile(
  expectedActivationSequence: number,
): Promise<SellerProfileActivation> {
  return request<SellerProfileActivation>("/api/seller-profile/deactivate", {
    method: "POST",
    body: JSON.stringify({ expected_activation_sequence: expectedActivationSequence }),
  });
}

function buildQuery(params: Record<string, string | number | undefined>): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined) q.set(k, String(v));
  }
  const s = q.toString();
  return s ? `?${s}` : "";
}

const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export class APIError extends Error {
  status: number;
  detail?: string;

  constructor(status: number, message: string, detail?: string) {
    super(message);
    this.name = "APIError";
    this.status = status;
    this.detail = detail;
  }
}

// ---- Phase 12: session auth --------------------------------------------
// The session itself is an HttpOnly cookie the browser sends automatically.
// State-changing requests must also carry the session's CSRF token, which
// only this origin can read (from /api/auth/session or the login response).
let csrfToken: string | null = null;
let onUnauthorized: (() => void) | null = null;

export function setCsrfToken(token: string | null): void {
  csrfToken = token;
}

// Called once by AuthProvider: what to do when the API says "sign in".
export function setUnauthorizedHandler(handler: (() => void) | null): void {
  onUnauthorized = handler;
}

const SAFE_METHODS = new Set(["GET", "HEAD", "OPTIONS"]);

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const url = `${API_BASE}${path}`;
  const headers: Record<string, string> = {
    ...(init?.headers as Record<string, string> | undefined),
  };
  const method = (init?.method ?? "GET").toUpperCase();
  if (!SAFE_METHODS.has(method) && csrfToken) {
    headers["X-CSRF-Token"] = csrfToken;
  }
  if (
    init?.body &&
    !(init.body instanceof FormData) &&
    !("Content-Type" in headers)
  ) {
    headers["Content-Type"] = "application/json";
  }

  let response: Response;
  try {
    response = await fetch(url, { ...init, headers, credentials: "include" });
  } catch (e) {
    const message = e instanceof Error ? e.message : String(e);
    throw new APIError(0, "Network error", message);
  }

  if (!response.ok) {
    if (response.status === 401 && onUnauthorized && !path.startsWith("/api/auth/")) {
      onUnauthorized();
    }
    let detail: string | undefined;
    try {
      const body = (await response.json()) as { detail?: unknown };
      if (typeof body?.detail === "string") detail = body.detail;
      else if (
        body?.detail &&
        typeof body.detail === "object" &&
        typeof (body.detail as { message?: unknown }).message === "string"
      )
        detail = (body.detail as { message: string }).message;
      else if (body?.detail) detail = JSON.stringify(body.detail);
    } catch {
      // body wasn't JSON; leave detail undefined
    }
    throw new APIError(
      response.status,
      response.statusText || `HTTP ${response.status}`,
      detail,
    );
  }

  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export function uploadBatch(
  file: File,
  batchName?: string,
): Promise<UploadResponse> {
  const form = new FormData();
  form.append("file", file);
  if (batchName) form.append("batch_name", batchName);
  return request<UploadResponse>("/api/batches/upload", {
    method: "POST",
    body: form,
  });
}

export function getBatches(params?: PageParams): Promise<Page<LeadBatch>> {
  return request<Page<LeadBatch>>(`/api/batches${buildQuery({ ...params })}`);
}

export function getBatch(batchId: string): Promise<LeadBatch> {
  return request<LeadBatch>(`/api/batches/${batchId}`);
}

export type LeadListParams = PageParams & {
  fit_band?: string;
  min_fit_score?: number;
  sort?: "created_at_desc" | "fit_score_desc" | "fit_score_asc";
};

export function getLeads(
  batchId?: string,
  params?: LeadListParams,
): Promise<Page<Lead>> {
  const q = buildQuery({ batch_id: batchId, ...params });
  return request<Page<Lead>>(`/api/leads${q}`);
}

export function getLead(leadId: string): Promise<Lead> {
  return request<Lead>(`/api/leads/${leadId}`);
}

// Legacy v1 scores for one page of a batch's leads (same order as
// getLeads(batchId)) -- one request per page, not one per lead.
export function getBatchScores(
  batchId: string,
  params?: PageParams,
): Promise<Page<LeadScore>> {
  return request<Page<LeadScore>>(
    `/api/batches/${batchId}/scores${buildQuery({ ...params })}`,
  );
}

export function scoreLead(leadId: string): Promise<LeadScore> {
  return request<LeadScore>(`/api/leads/${leadId}/score`, { method: "POST" });
}

export function scoreBatch(batchId: string): Promise<BatchScoreResponse> {
  return request<BatchScoreResponse>(`/api/batches/${batchId}/score`, {
    method: "POST",
  });
}

export function getLeadScore(leadId: string): Promise<LeadScore> {
  return request<LeadScore>(`/api/leads/${leadId}/score`);
}

export function generateSummary(leadId: string): Promise<AIOutput> {
  return request<AIOutput>(`/api/leads/${leadId}/generate-summary`, {
    method: "POST",
  });
}

export function generateOutreach(leadId: string): Promise<AIOutput> {
  return request<AIOutput>(`/api/leads/${leadId}/generate-outreach`, {
    method: "POST",
  });
}

export function getAIOutputs(
  leadId: string,
  params?: PageParams,
): Promise<Page<AIOutput>> {
  return request<Page<AIOutput>>(
    `/api/leads/${leadId}/ai-outputs${buildQuery({ ...params })}`,
  );
}

// Authoritative "what is the current draft of this type" lookup -- a
// dedicated backend query (newest AIOutput of output_type for this lead),
// NOT inferred by paging through ai-outputs history and searching page 1.
// Phase 3 closeout Part D.4: history is now paginated, so the first page
// may not even contain the newest row once older items page in a
// different order, and even before that, "search the loaded page" was
// already the wrong source of truth -- this endpoint always exists
// independent of how much history has been loaded client-side.
export function getLatestAIOutput(
  leadId: string,
  outputType: string,
): Promise<AIOutput> {
  return request<AIOutput>(
    `/api/leads/${leadId}/latest-ai-output${buildQuery({ output_type: outputType })}`,
  );
}

export function pushLead(
  leadId: string,
  force = false,
): Promise<IntegrationPush> {
  return request<IntegrationPush>(`/api/leads/${leadId}/push`, {
    method: "POST",
    body: JSON.stringify({ integration_type: "slack", force }),
  });
}

// Phase 11: record what an operator found in Slack for a delivery whose
// outcome is unknown. Nothing is sent.
export function resolvePush(
  pushId: string,
  resolution: "confirmed_delivered" | "confirmed_not_delivered",
): Promise<IntegrationPush> {
  return request<IntegrationPush>(`/api/pushes/${pushId}/resolve`, {
    method: "POST",
    body: JSON.stringify({ resolution }),
  });
}

export function pushHotLeads(
  batchId: string,
  force = false,
): Promise<BatchPushResponse> {
  return request<BatchPushResponse>(`/api/batches/${batchId}/push-hot`, {
    method: "POST",
    body: JSON.stringify({ integration_type: "slack", force }),
  });
}

export function getPushes(
  leadId: string,
  params?: PageParams,
): Promise<Page<IntegrationPush>> {
  return request<Page<IntegrationPush>>(
    `/api/leads/${leadId}/pushes${buildQuery({ ...params })}`,
  );
}

// ai_output_id is required: the caller must say exactly which draft it's
// approving/rejecting, so a stale/out-of-date UI can't silently act on a
// different draft than the one it's showing (docs/engineering-log/audit.md C.2).
// Phase 6: every review names the exact output AND the content hash shown.
// Phase 10: a draft with runtime quality flags is approved only with
// `acknowledgedFlags` listing exactly the flag codes that were shown.
export function approveOutreach(
  leadId: string,
  aiOutputId: string,
  contentHash: string,
  acknowledgedFlags: string[] = [],
): Promise<OutreachReviewResponse> {
  return request<OutreachReviewResponse>(
    `/api/leads/${leadId}/approve-outreach`,
    {
      method: "POST",
      body: JSON.stringify({
        ai_output_id: aiOutputId,
        content_hash: contentHash,
        acknowledged_quality_flags: acknowledgedFlags,
      }),
    },
  );
}

// ---- Phase 10: background jobs ------------------------------------------

export function createBatchJob(
  batchId: string,
  jobType: JobType,
  params: Record<string, boolean> = {},
): Promise<Job> {
  return request<Job>(`/api/batches/${batchId}/jobs`, {
    method: "POST",
    body: JSON.stringify({ job_type: jobType, params }),
  });
}

export function listBatchJobs(batchId: string, limit = 10): Promise<Page<Job>> {
  return request<Page<Job>>(`/api/jobs${buildQuery({ batch_id: batchId, limit })}`);
}

export function cancelJob(jobId: string): Promise<Job> {
  return request<Job>(`/api/jobs/${jobId}/cancel`, { method: "POST" });
}

export function rejectOutreach(
  leadId: string,
  aiOutputId: string,
  contentHash: string,
  reason: string,
): Promise<OutreachReviewResponse> {
  return request<OutreachReviewResponse>(
    `/api/leads/${leadId}/reject-outreach`,
    {
      method: "POST",
      body: JSON.stringify({ ai_output_id: aiOutputId, content_hash: contentHash, reason }),
    },
  );
}

export function getReviewState(leadId: string): Promise<ReviewState> {
  return request<ReviewState>(`/api/leads/${leadId}/review-state`);
}

export function reviseOutput(
  leadId: string,
  aiOutputId: string,
  expectedContentHash: string,
  content: Record<string, unknown>,
): Promise<AIOutput> {
  return request<AIOutput>(`/api/leads/${leadId}/ai-outputs/${aiOutputId}/revisions`, {
    method: "POST",
    body: JSON.stringify({ expected_content_hash: expectedContentHash, content }),
  });
}








export function getMetricsDashboard(): Promise<MetricsDashboard> {
  return request<MetricsDashboard>("/api/metrics/dashboard");
}

export function runDemo(): Promise<DemoRunResponse> {
  return request<DemoRunResponse>("/api/demo/run", { method: "POST" });
}

// --- Phase 4: v2 deterministic company-fit scorer --------------------
// Entirely separate from scoreLead/scoreBatch/getLeadScore above (the
// legacy v1 Hot/Warm/Cold scorer, unchanged). See types/api.ts's
// LeadFitScore doc comment for why a "strong_match" fit band is not the
// same claim as legacy "Hot".

export function scoreLeadFit(leadId: string): Promise<LeadFitScore> {
  return request<LeadFitScore>(`/api/leads/${leadId}/fit-score`, {
    method: "POST",
  });
}

export function getLeadFitScore(leadId: string): Promise<LeadFitScore> {
  return request<LeadFitScore>(`/api/leads/${leadId}/fit-score`);
}

export function scoreBatchFit(
  batchId: string,
): Promise<BatchFitScoreRunSummary> {
  return request<BatchFitScoreRunSummary>(`/api/batches/${batchId}/fit-score`, {
    method: "POST",
  });
}

export function getBatchFitSummary(batchId: string): Promise<BatchFitSummary> {
  return request<BatchFitSummary>(`/api/batches/${batchId}/fit-summary`);
}

// Current readiness + routing eligibility; works for unscored leads too.
export function getLeadReadiness(leadId: string): Promise<CurrentReadiness> {
  return request<CurrentReadiness>(`/api/leads/${leadId}/readiness`);
}

// Current readiness for every lead on one page of a batch (same order as
// getLeads(batchId)).
export function getBatchReadiness(
  batchId: string,
  params?: PageParams,
): Promise<Page<CurrentReadiness>> {
  return request<Page<CurrentReadiness>>(
    `/api/batches/${batchId}/readiness${buildQuery({ ...params })}`,
  );
}

export function getBatchFitScores(
  batchId: string,
  params?: PageParams,
): Promise<Page<LeadFitScore>> {
  return request<Page<LeadFitScore>>(
    `/api/batches/${batchId}/fit-scores${buildQuery({ ...params })}`,
  );
}

export function getFitProfile(): Promise<FitProfile> {
  return request<FitProfile>("/api/scoring/fit-profile");
}

// ---- Phase 12: auth endpoints --------------------------------------------

export type SessionInfo = {
  username: string;
  role: "operator" | "viewer" | "guest";
  expires_at: string;
  csrf_token: string;
};

export async function login(username: string, password: string): Promise<SessionInfo> {
  const info = await request<SessionInfo>("/api/auth/login", {
    method: "POST",
    body: JSON.stringify({ username, password }),
  });
  setCsrfToken(info.csrf_token);
  return info;
}

export async function getSession(): Promise<SessionInfo> {
  const info = await request<SessionInfo>("/api/auth/session");
  setCsrfToken(info.csrf_token);
  return info;
}

export async function logout(): Promise<void> {
  try {
    await request<void>("/api/auth/logout", { method: "POST" });
  } catch (error) {
    // A revoked/expired session is already signed out. A network/CSRF
    // failure is not: retain the token so the operator can retry.
    if (!(error instanceof APIError && error.status === 401)) throw error;
  }
  setCsrfToken(null);
}

// ---- Sign-up with an emailed code, and guest access -----------------------

export type AuthOptions = {
  self_signup: boolean;
  guest_access: boolean;
  email_delivery: "mock" | "smtp";
  guest_can_edit: boolean;
};

export function getAuthOptions(): Promise<AuthOptions> {
  return request<AuthOptions>("/api/auth/options");
}

export function register(email: string, password: string): Promise<{ message: string }> {
  return request<{ message: string }>("/api/auth/register", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
}

export function resendCode(email: string): Promise<{ message: string }> {
  return request<{ message: string }>("/api/auth/resend-code", {
    method: "POST",
    body: JSON.stringify({ email }),
  });
}

export async function verifyEmail(email: string, code: string): Promise<SessionInfo> {
  const info = await request<SessionInfo>("/api/auth/verify-email", {
    method: "POST",
    body: JSON.stringify({ email, code }),
  });
  setCsrfToken(info.csrf_token);
  return info;
}

export async function continueAsGuest(): Promise<SessionInfo> {
  const info = await request<SessionInfo>("/api/auth/guest", { method: "POST", body: "{}" });
  setCsrfToken(info.csrf_token);
  return info;
}

// ---- The rep's workspace: lead inbox and drafting model --------------------

export type LeadStage = "needs_score" | "needs_draft" | "to_review" | "outdated" | "rejected" | "approved" | "sent";

export type InboxItem = {
  id: string;
  company_name: string;
  contact_name: string | null;
  contact_title: string | null;
  industry: string | null;
  batch_id: string;
  batch_name: string | null;
  priority: "Hot" | "Warm" | "Cold" | null;
  score: number | null;
  stage: LeadStage;
  delivery_unknown: boolean;
  draft_model: string | null;
  blocked: boolean;
  updated_at: string;
};

export type InboxPage = {
  items: InboxItem[];
  total: number;
  limit: number;
  offset: number;
  counts: Record<LeadStage | "all" | "hot" | "delivery_unknown", number>;
};

export function getInbox(params: {
  stage?: LeadStage;
  priority?: "Hot" | "Warm" | "Cold";
  q?: string;
  limit?: number;
  offset?: number;
} = {}): Promise<InboxPage> {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== "") query.set(key, String(value));
  }
  const suffix = query.toString();
  return request<InboxPage>(`/api/inbox${suffix ? `?${suffix}` : ""}`);
}

export type AIStatus = {
  mode: "mock" | "fine_tuned" | "openai" | "unavailable";
  label: string;
  detail: string;
  fine_tuned_selected: boolean;
  fine_tuned_connected: boolean;
  fallback_enabled: boolean;
  base_model: string;
  adapter: string;
};

export function getAIStatus(): Promise<AIStatus> {
  return request<AIStatus>("/api/ai/status");
}
