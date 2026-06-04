// Typed fetch wrappers for the FastAPI backend.
// One source of truth for the base URL and error shape.

import type {
  AIOutput,
  BatchPushResponse,
  BatchScoreResponse,
  DemoRunResponse,
  IntegrationPush,
  Lead,
  LeadBatch,
  LeadScore,
  MetricsDashboard,
  OutreachReviewResponse,
  UploadResponse,
} from "@/types/api";

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

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const url = `${API_BASE}${path}`;
  const headers: Record<string, string> = {
    ...(init?.headers as Record<string, string> | undefined),
  };
  if (
    init?.body &&
    !(init.body instanceof FormData) &&
    !("Content-Type" in headers)
  ) {
    headers["Content-Type"] = "application/json";
  }

  let response: Response;
  try {
    response = await fetch(url, { ...init, headers });
  } catch (e) {
    const message = e instanceof Error ? e.message : String(e);
    throw new APIError(0, "Network error", message);
  }

  if (!response.ok) {
    let detail: string | undefined;
    try {
      const body = (await response.json()) as { detail?: unknown };
      if (typeof body?.detail === "string") detail = body.detail;
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

export function getBatches(): Promise<LeadBatch[]> {
  return request<LeadBatch[]>("/api/batches");
}

export function getBatch(batchId: string): Promise<LeadBatch> {
  return request<LeadBatch>(`/api/batches/${batchId}`);
}

export function getLeads(batchId?: string): Promise<Lead[]> {
  const q = batchId ? `?batch_id=${encodeURIComponent(batchId)}` : "";
  return request<Lead[]>(`/api/leads${q}`);
}

export function getLead(leadId: string): Promise<Lead> {
  return request<Lead>(`/api/leads/${leadId}`);
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

export function getAIOutputs(leadId: string): Promise<AIOutput[]> {
  return request<AIOutput[]>(`/api/leads/${leadId}/ai-outputs`);
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

export function pushHotLeads(
  batchId: string,
  force = false,
): Promise<BatchPushResponse> {
  return request<BatchPushResponse>(`/api/batches/${batchId}/push-hot`, {
    method: "POST",
    body: JSON.stringify({ integration_type: "slack", force }),
  });
}

export function getPushes(leadId: string): Promise<IntegrationPush[]> {
  return request<IntegrationPush[]>(`/api/leads/${leadId}/pushes`);
}

export function approveOutreach(
  leadId: string,
): Promise<OutreachReviewResponse> {
  return request<OutreachReviewResponse>(
    `/api/leads/${leadId}/approve-outreach`,
    { method: "POST" },
  );
}

export function rejectOutreach(
  leadId: string,
  reason?: string,
): Promise<OutreachReviewResponse> {
  return request<OutreachReviewResponse>(
    `/api/leads/${leadId}/reject-outreach`,
    {
      method: "POST",
      body: JSON.stringify({ reason: reason ?? null }),
    },
  );
}

export function getMetricsDashboard(): Promise<MetricsDashboard> {
  return request<MetricsDashboard>("/api/metrics/dashboard");
}

export function runDemo(): Promise<DemoRunResponse> {
  return request<DemoRunResponse>("/api/demo/run", { method: "POST" });
}
