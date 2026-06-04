// Shared TypeScript types mirroring the FastAPI response shapes.
// Kept permissive on unions (string instead of literal) so the UI never
// crashes if the backend introduces a new status/priority value.

export type LeadBatch = {
  id: string;
  name: string | null;
  source: string;
  total_leads: number;
  processed_leads: number;
  status: string;
  created_at: string;
  updated_at: string;
};

export type Lead = {
  id: string;
  batch_id: string;
  company_name: string;
  website: string | null;
  industry: string | null;
  contact_name: string | null;
  contact_email: string | null;
  contact_title: string | null;
  company_size: string | null;
  location: string | null;
  source: string | null;
  status: string;
  cleaned_data: Record<string, unknown> | null;
  created_at: string;
  updated_at: string;
};

export type ScoreBreakdown = {
  industry_fit: number;
  company_size_fit: number;
  persona_title_fit: number;
  pain_point_keywords: number;
  data_completeness: number;
  source_quality: number;
  penalties: number;
};

export type MatchedSignals = {
  industry_terms: string[];
  title_terms: string[];
  pain_point_terms: string[];
};

export type LeadScore = {
  lead_id: string;
  total_score: number;
  priority: string; // "Hot" | "Warm" | "Cold" but permissive
  // Permissive at runtime in case the backend rolls in new categories or
  // omits ones the UI doesn't know about yet.
  score_breakdown: Partial<ScoreBreakdown>;
  matched_signals: MatchedSignals;
  reasoning: string;
};

export type SummaryContent = {
  company_summary: string;
  detected_pain_points: string[];
  fit_reasoning: string;
  evidence: string[];
  inferences: string[];
  confidence: string;
};

export type OutreachContent = {
  subject: string;
  email_body: string;
  personalization_points: string[];
  call_note: string;
  confidence: string;
};

export type AIOutput = {
  id: string;
  lead_id: string;
  output_type: string; // "company_summary" | "outreach_email"
  content: Record<string, unknown>;
  model_used: string | null;
  prompt_version: string | null;
  created_at: string;
};

export type IntegrationPush = {
  id: string;
  lead_id: string;
  integration_type: string;
  payload: Record<string, unknown> & { text?: string };
  status: string; // "success" | "mock_success" | "failed"
  response_text: string | null;
  created_at: string;
};

export type UploadResponseError = {
  row_number: number;
  field: string;
  message: string;
};

export type UploadResponse = {
  batch_id: string;
  batch_name: string | null;
  total_rows: number;
  valid_rows: number;
  invalid_rows: number;
  errors: UploadResponseError[];
};

export type BatchScoreResponse = {
  batch_id: string;
  scored_leads: number;
  hot: number;
  warm: number;
  cold: number;
  average_score: number;
};

export type PushResponse = IntegrationPush;

export type BatchPushResult = {
  lead_id: string;
  company_name: string;
  status: string;
  push_id: string | null;
  reason: string | null;
};

export type BatchPushResponse = {
  batch_id: string;
  hot_leads_found: number;
  pushed: number;
  skipped: number;
  failed: number;
  results: BatchPushResult[];
};

export type OutreachReviewResponse = {
  lead_id: string;
  ai_output_id: string;
  event_type: string; // "outreach_approved" | "outreach_rejected"
  message: string;
};

export type DemoRunResponse = {
  batch_id: string;
  batch_name: string;
  total_leads: number;
  hot: number;
  warm: number;
  cold: number;
  outreach_generated: number;
  outreach_approved: number;
  leads_pushed: number;
};

export type MetricsDashboard = {
  total_leads_uploaded: number;
  total_leads_processed: number;
  hot_leads: number;
  warm_leads: number;
  cold_leads: number;
  outreach_generated: number;
  outreach_approved: number;
  outreach_rejected: number;
  approval_rate: number;
  // Count of successful push rows. A lead pushed twice contributes 2.
  leads_pushed: number;
  // Distinct leads with at least one successful push.
  unique_leads_pushed: number;
  push_success_rate: number;
  failed_push_count: number;
  estimated_time_saved_minutes: number;
  estimated_time_saved_hours: number;
  average_lead_score: number;
  missing_data_rate: number;
  automation_coverage: number;
};
