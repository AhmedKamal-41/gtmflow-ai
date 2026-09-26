from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class GenerationModeCounts(BaseModel):
    """Operational outreach drafts written by one generator mode."""

    drafts_generated: int
    drafts_approved: int
    drafts_rejected: int
    drafts_pending_review: int
    approval_rate: float


class DeliveryModeCounts(BaseModel):
    """Slack delivery attempts in one delivery mode (mock webhook or real)."""

    attempts: int
    delivered: int
    unique_leads_delivered: int
    failed: int
    outcome_unknown: int
    pending: int
    success_rate: float


class MetricsDashboard(BaseModel):
    """Adoption + ROI dashboard response shape.

    ``leads_pushed`` counts successful push rows (a lead pushed twice
    contributes 2). ``unique_leads_pushed`` deduplicates by lead, so the
    same lead pushed twice still counts as 1. Both are exposed so the
    dashboard can show audit-friendly and ops-friendly views.
    """

    total_leads_uploaded: int
    total_leads_processed: int
    hot_leads: int
    warm_leads: int
    cold_leads: int
    outreach_generated: int
    outreach_approved: int
    outreach_rejected: int
    # Phase 11: one cohort -- distinct operational outreach drafts, each by its
    # latest operational review. approved + rejected + pending == generated.
    outreach_pending_review: int
    approval_rate: float           # approved drafts / drafts (never > 100%)
    reviewed_approval_rate: float  # approved / (approved + rejected)
    approval_events: int           # raw outreach_approved events (audit only)
    rejection_events: int          # raw outreach_rejected events (audit only)
    leads_pushed: int
    unique_leads_pushed: int
    push_success_rate: float
    failed_push_count: int
    # Phase 11 delivery ledger and mock-versus-real breakdowns.
    push_unknown_count: int
    push_pending_count: int
    real_messages_delivered: int
    mock_messages_delivered: int
    generation_by_mode: dict[str, GenerationModeCounts]
    delivery_by_mode: dict[str, DeliveryModeCounts]
    data_mode: Literal["empty", "mock_only", "real_only", "mixed"]
    estimated_time_saved_minutes: int
    estimated_time_saved_hours: float
    average_lead_score: float
    missing_data_rate: float
    automation_coverage: float
    # v2 company fit (demonstration profile) -- distinct leads by the band of
    # their latest applicable score; separate from hot/warm/cold above.
    fit_scored_leads: int
    fit_strong_match: int
    fit_partial_match: int
    fit_weak_match: int
    fit_insufficient_evidence: int
