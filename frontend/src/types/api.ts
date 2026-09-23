// Shared TypeScript types mirroring the FastAPI response shapes.
// Kept permissive on unions (string instead of literal) so the UI never
// crashes if the backend introduces a new status/priority value.

// Phase 3: every list endpoint (batches, leads, AI-output history, push
// history) returns this envelope instead of a bare array. `total` is
// server-computed across the whole dataset, not len(items) -- always show
// it rather than deriving a count from the current page.
export type Page<T> = {
  items: T[];
  total: number;
  limit: number;
  offset: number;
  has_more: boolean;
};

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
  // Phase 2/3 provenance + canonical identity. NULL for CSV/demo leads.
  company_identity_id: string | null;
  source_snapshot_id: string | null;
  import_run_id: string | null;
  source_record_id: string | null;
  source_raw_data: Record<string, unknown> | null;
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

// Historical (prompt v1) content shapes. Kept so outputs generated before
// grounded prompting still render as they were produced.
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

// Grounded (output schema v2) content shapes, validated by the backend
// before saving (app/ai/grounding.py).
export type GroundedSummaryContent = {
  company_summary: string;
  evidence: { fact_id: string; statement: string }[];
  unknowns: string[];
  hypotheses: string[];
  seller_relevance: string | null;
  confidence: string;
};

export type GroundedOutreachContent = {
  subject: string;
  email_body: string;
  lead_facts_used: string[];
  capabilities_used: string[];
  claims_used: string[];
  unknowns_acknowledged: string[];
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
  // Phase 2 identity + provenance.
  parent_output_id: string | null;
  origin: string; // "generated" | "human_edited"
  input_snapshot: Record<string, unknown> | null;
  input_hash: string | null;
  output_schema_version: string | null;
  model_revision: string | null;
  adapter_revision: string | null;
  // Phase 5 seller provenance; null on pre-grounding (prompt v1) outputs.
  seller_profile_id: string | null;
  seller_profile_version: number | null;
  seller_profile_content_hash: string | null;
  seller_profile_kind: string | null;
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
  blocked: number;
  results: BatchPushResult[];
};

export type OutreachReviewResponse = {
  lead_id: string;
  ai_output_id: string;
  review_id: string;
  event_type: string; // "outreach_approved" | "outreach_rejected"
  message: string;
  idempotent_replay: boolean;
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

// --- Phase 4: v2 deterministic company-fit scorer -------------------------
// Entirely separate from the legacy `LeadScore` (Hot/Warm/Cold) above. A
// `strong_match` fit band is NOT the same claim as legacy "Hot" -- it means
// "matched this broad demonstration profile's two active criteria," not a
// calibrated purchase-probability signal. Always label these distinctly in
// the UI; never silently translate one into the other's meaning.

export type FitCriterion = {
  name: string;
  weight: number;
  active: boolean;
  source_field: string;
  raw_input: unknown;
  normalized_input: string | null;
  result: string; // "match" | "mismatch" | "unknown" | "not_configured"
  points: number;
  explanation: string;
};

export type ActionReadiness = {
  status: string; // "ready" | "not_ready"
  gaps: string[];
  gap_explanations: Record<string, string>;
};

export type Readiness = {
  outbound_email: ActionReadiness;
  internal_slack_handoff: ActionReadiness;
};

export type Eligibility = {
  excluded: boolean;
  reasons: string[];
  checked_at: string;
};

export type HistoricalAssessment = {
  readiness: Readiness;
  eligibility_excluded: boolean;
  eligibility_reasons: string[];
  computed_at: string;
};

// GET /api/leads/{id}/readiness -- live state, independent of fit scoring.
export type CurrentReadiness = {
  lead_id: string;
  readiness: Readiness;
  eligibility: Eligibility;
};

export type LeadFitScore = {
  id: string | null;
  lead_id: string;
  scorer_version: string;
  profile_id: string;
  profile_version: string;
  normalization_version: string;
  input_fingerprint: string;
  fit_score: number;
  max_fit_score: number;
  evidence_coverage_pct: number;
  band: string; // "strong_match" | "partial_match" | "weak_match" | "insufficient_evidence"
  criteria: FitCriterion[];
  // CURRENT readiness/eligibility, recomputed from live lead, batch,
  // draft and review state on every read (single-lead and bulk alike).
  readiness: Readiness;
  readiness_is_current: boolean;
  eligibility: Eligibility;
  // What readiness/eligibility were when this score row was computed --
  // historical, for audit only; never used to gate an action. null for
  // an unpersisted result.
  at_scoring: HistoricalAssessment | null;
  computed_at: string;
  computation_ms: number;
};

export type FitProfile = {
  profile_id: string;
  profile_version: string;
  scorer_version: string;
  normalization_version: string;
  description: string;
  industry_match_values: string[];
  industry_weight: number;
  country_match_values: string[];
  country_weight: number;
  size_weight: number;
  zero_weight_criteria: string[];
  max_fit_score: number;
  coverage_threshold_pct: number;
  band_strong_min: number;
  band_partial_min: number;
};

// Distinct leads, each counted once in the band of its LATEST applicable
// score. `score_rows` counts every stored row (history included).
export type BatchFitSummary = {
  batch_id: string;
  total_leads: number;
  scored_leads: number;
  unscored_leads: number;
  score_rows: number;
  strong_match: number;
  partial_match: number;
  weak_match: number;
  insufficient_evidence: number;
  average_fit_score: number | null;
};

// POST /api/batches/{id}/fit-score: this run's counts + the batch's
// distinct-lead state afterwards.
export type BatchFitScoreRunSummary = {
  batch_id: string;
  attempted: number;
  newly_scored: number;
  skipped_unchanged: number;
  failed: number;
  summary: BatchFitSummary;
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
  // v2 company fit (demo profile): distinct leads by latest band.
  fit_scored_leads: number;
  fit_strong_match: number;
  fit_partial_match: number;
  fit_weak_match: number;
  fit_insufficient_evidence: number;
};
export type SellerProfileContent = {
  profile_kind: "seller" | "demo";
  company_name: string;
  product_name: string;
  value_proposition: string;
  target_customer: string;
  capabilities: string[];
  proof_points: { claim: string; source: string }[];
  exclusions: string[];
};

export type SellerProfile = {
  id: string;
  version: number;
  status: "draft" | "active";
  profile: SellerProfileContent;
  content_hash: string;
  editor_label: string;
  created_at: string;
};

export type SellerProfileActivation = {
  id: string;
  sequence: number;
  action: "activate" | "deactivate";
  seller_profile_id: string | null;
  seller_profile_version: number | null;
  content_hash: string | null;
  reviewed_confirmation: boolean;
  demo_acknowledged: boolean;
  actor_label: string;
  created_at: string;
};

export type SellerProfileStatus = {
  state: "missing" | "draft_only" | "active";
  latest_version: number | null;
  activation_sequence: number;
  last_activation: SellerProfileActivation | null;
  active_profile: SellerProfile | null;
  latest_is_active: boolean;
};
