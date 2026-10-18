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
  // Phase 6: exact-content identity and review.
  content_hash: string;
  purpose: string; // "operational" | "annotation"
  author_label: string | null;
  review_status: string | null; // pending | approved | rejected | superseded
  // Phase 10: runtime quality checks (runtime-checks-v1). Flags ask for
  // review; approving a flagged draft needs an explicit acknowledgement.
  quality_checks_version?: string;
  quality_flags?: QualityFlag[];
};

export type QualityFlag = {
  code: string;
  field: string;
  match: string;
  source: string;
  severity: string;
};

// Phase 10: durable background jobs.
export type JobType =
  | "fit_score"
  | "generate_summary"
  | "generate_outreach"
  | "push_hot"
  | "assistant_outreach"
  | "selected_outreach";

export type Job = {
  id: string;
  job_type: JobType;
  batch_id: string | null;
  params: Record<string, unknown>;
  status: "queued" | "running" | "completed" | "failed" | "cancelled";
  attempts: number;
  max_attempts: number;
  max_item_attempts: number;
  total_items: number | null;
  counts: Record<string, number>;
  result: Record<string, unknown> | null;
  last_error: string | null;
  cancel_requested: boolean;
  lease_expires_at: string | null;
  heartbeat_at: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  deduplicated: boolean;
  done_items: number;
  progress_pct: number | null;
};

export type IntegrationPush = {
  id: string;
  lead_id: string;
  integration_type: string;
  payload: Record<string, unknown> & { text?: string };
  status: string; // "success" | "mock_success" | "failed" | "unknown" | "pending"
  response_text: string | null;
  created_at: string;
  // Phase 11 delivery ledger (null on rows from before Phase 11).
  approved_output_id?: string | null;
  attempt?: number | null;
  delivery_mode?: "mock" | "real" | null;
  outcome_code?: string | null;
  resolution?: string | null;
  resolution_note?: string | null;
  resolved_at?: string | null;
  replay?: boolean;
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

// --- Ranking against the active seller profile's target customers ---------
// One deterministic ranking (app/scoring/fit.py). Bands map to priorities:
// strong_match = Hot, partial_match = Warm, weak_match = Cold, and
// insufficient_evidence = "needs data" (priority null). A ranking describes
// recorded company attributes only -- never interest, need or intent.

export type Priority = "Hot" | "Warm" | "Cold";
export type CriterionResult = "match" | "partial" | "mismatch" | "unknown" | "not_configured";

export type FitCriterion = {
  name: string;
  label: string | null;
  weight: number;
  active: boolean;
  source_field: string;
  raw_input: unknown;
  normalized_input: string | null;
  result: CriterionResult | string;
  points: number;
  explanation: string;
  targets: string[];
  matched_targets: string[];
};

export type CriteriaSummary = {
  matched: string[];
  partial: string[];
  mismatched: string[];
  missing: string[];
  not_configured: string[];
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
  seller_profile_id: string | null;
  targeting_hash: string | null;
  normalization_version: string;
  input_fingerprint: string;
  fit_score: number;
  max_fit_score: number;
  evidence_coverage_pct: number;
  band: string; // "strong_match" | "partial_match" | "weak_match" | "insufficient_evidence"
  priority: Priority | null;
  // false: ranked against different target preferences than the active
  // profile's -- shown as outdated, never used as the current ranking.
  is_current: boolean;
  explanation: string;
  summary: CriteriaSummary;
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

export type TargetPreferences = {
  industries: string[];
  locations: string[];
  company_sizes: { min_employees: number; max_employees: number | null }[];
  contact_roles: string[];
  keywords: string[];
};

export type RankingState = "ready" | "no_active_profile" | "setup_required";

// GET /api/scoring/profile
export type RankingStatus = {
  state: RankingState;
  message: string;
  profile: {
    source: string;
    profile_id: string;
    seller_profile_id: string | null;
    version: string;
    profile_kind: string;
    targeting: TargetPreferences;
    targeting_hash: string;
    configured_criteria: string[];
  } | null;
  scorer_version: string;
  normalization_version: string;
  weights: Record<string, number>;
  labels: Record<string, string>;
  hot_min: number;
  warm_min: number;
  coverage_min_pct: number;
  partial_credit: number;
  total_leads: number;
  current_rankings: number;
  outdated_rankings: number;
  never_ranked: number;
};

// POST /api/scoring/recalculate
export type RankingRunSummary = {
  batch_id: string | null;
  profile_id: string;
  profile_version: string;
  targeting_hash: string;
  attempted: number;
  newly_scored: number;
  skipped_unchanged: number;
  failed: number;
};

// Distinct leads, each counted once in the band of its latest CURRENT
// ranking. `score_rows` counts every current row (history included).
export type BatchFitSummary = {
  batch_id: string | null;
  ranking_state: RankingState;
  profile_id: string | null;
  profile_version: string | null;
  targeting_hash: string | null;
  total_leads: number;
  scored_leads: number;
  unscored_leads: number;
  outdated_leads: number;
  score_rows: number;
  strong_match: number;
  partial_match: number;
  weak_match: number;
  insufficient_evidence: number;
  hot: number;
  warm: number;
  cold: number;
  average_fit_score: number | null;
};

// POST /api/batches/{id}/fit-score: this run's counts + the batch's
// distinct-lead state afterwards.
export type BatchFitScoreRunSummary = {
  batch_id: string;
  profile_id: string;
  profile_version: string;
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
  // Phase 11: one cohort -- distinct operational outreach drafts by their
  // latest review; approved + rejected + pending = generated.
  outreach_pending_review: number;
  approval_rate: number; // approved drafts / drafts, never above 100
  reviewed_approval_rate: number; // approved / (approved + rejected)
  approval_events: number; // raw events, audit only
  rejection_events: number;
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
  // Ranking against the active seller profile: distinct leads by the band
  // of their current ranking (hot/warm/cold above are the same counts).
  ranking_state: RankingState;
  ranking_profile_version: string | null;
  fit_outdated_leads: number;
  fit_scored_leads: number;
  fit_strong_match: number;
  fit_partial_match: number;
  fit_weak_match: number;
  fit_insufficient_evidence: number;
  // Phase 11 delivery ledger and mock-versus-real breakdowns.
  push_unknown_count: number;
  push_pending_count: number;
  real_messages_delivered: number;
  mock_messages_delivered: number;
  generation_by_mode: Record<"mock" | "real" | "unknown", GenerationModeCounts>;
  delivery_by_mode: Record<"mock" | "real" | "unknown", DeliveryModeCounts>;
  data_mode: "empty" | "mock_only" | "real_only" | "mixed";
};
export type GenerationModeCounts = {
  drafts_generated: number;
  drafts_approved: number;
  drafts_rejected: number;
  drafts_pending_review: number;
  approval_rate: number;
};
export type DeliveryModeCounts = {
  attempts: number;
  delivered: number;
  unique_leads_delivered: number;
  failed: number;
  outcome_unknown: number;
  pending: number;
  success_rate: number;
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
  // Who the seller wants to sell to, used to rank leads. Empty lists mean
  // "no preference"; with all empty, leads cannot be ranked.
  target_preferences: TargetPreferences;
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

// ---------------------------------------------------------------- Phase 6

export type Review = {
  id: string;
  ai_output_id: string | null;
  decision: "approved" | "rejected";
  reason: string | null;
  reviewer_label: string;
  content_hash: string | null;
  legacy_unlinked: boolean;
  created_at: string;
};

export type SourceInfo = {
  batch_source: string | null;
  provider: string | null;
  source_snapshot_id: string | null;
  reported_acquisition_date: string | null;
  retrieved_at: string | null;
  license: string | null;
  freshness_note: string;
};

export type ReviewState = {
  lead_id: string;
  status: "no_draft" | "pending" | "approved" | "rejected";
  draft_id: string | null;
  draft_content_hash: string | null;
  draft_origin: string | null;
  draft_parent_output_id: string | null;
  approval_applicable: boolean;
  delivery_blockers: string[];
  email_blockers: string[];
  blocker_explanations: Record<string, string>;
  latest_review: Review | null;
  source: SourceInfo;
  quality_checks_version?: string;
  draft_quality_flags?: QualityFlag[];
};

export type ReviewTiming = {
  active_ms: number;
  wall_ms: number;
  hidden_ms: number;
  idle_ms: number;
  interaction_count: number;
  idle_threshold_ms: number;
  flags: ("was_hidden" | "had_idle_gap" | "resumed_after_failure" | "content_changed_during_session")[];
};

export type TrainingAnnotation = {
  id: string;
  submission_id: string;
  candidate_id: string;
  source_output_id: string;
  source_content_hash: string;
  decision: "accepted" | "corrected" | "skipped";
  target_output_id: string | null;
  target_content_hash: string | null;
  factual_support: string | null;
  writing_quality: number | null;
  missing_info_handling: string | null;
  notes: string | null;
  skip_reason: string | null;
  reviewer_label: string;
  review_mode: string;
  timing: (ReviewTiming & { source: string; incomplete: boolean }) | null;
  created_at: string;
};

export type AnnotationCandidateSummary = {
  id: string;
  queue: string;
  position: number;
  task: "company_summary" | "outreach_email";
  split: string;
  group_key: string;
  lead_id: string;
  company_name: string;
  status: "awaiting_generation" | "pending_review" | "accepted" | "corrected" | "skipped";
  source_output_id: string | null;
  is_mock: boolean | null;
};

export type AnnotationCandidateDetail = AnnotationCandidateSummary & {
  manifest_version: string;
  lead_facts: Record<string, string | null>;
  source: SourceInfo;
  source_output: AIOutput | null;
  target_output: AIOutput | null;
  latest_annotation: TrainingAnnotation | null;
  annotation_count: number;
};

export type AnnotationSubmit = {
  submission_id: string;
  source_output_id: string;
  source_content_hash: string;
  decision: "accepted" | "corrected" | "skipped";
  corrected_content?: Record<string, unknown>;
  factual_support?: "supported" | "partially_supported" | "unsupported";
  writing_quality?: number;
  missing_info_handling?: "good" | "acceptable" | "poor";
  notes?: string;
  skip_reason?: string;
  timing?: ReviewTiming;
};

export type AnnotationQueue = {
  queue: string;
  split: string;
  candidates: number;
  generated: number;
};

export type AnnotationProvider = {
  configured_provider: string;
  model_revision: string | null;
  is_mock: boolean;
  available: boolean;
  detail: string;
};

export type AnnotationSummary = {
  queue: string;
  manifest_version: string | null;
  candidates: number;
  unique_companies: number;
  generated: number;
  awaiting_generation: number;
  pending_review: number;
  reviewed_examples: number;
  reviewed_unique_companies: number;
  accepted: number;
  corrected: number;
  skipped: number;
  mock_candidates: number;
  by_task: Record<string, number>;
  by_split: Record<string, number>;
  experiment_targets: Record<string, number>;
};
