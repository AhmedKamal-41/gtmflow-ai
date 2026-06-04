from __future__ import annotations

from pydantic import BaseModel


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
    approval_rate: float
    leads_pushed: int
    unique_leads_pushed: int
    push_success_rate: float
    failed_push_count: int
    estimated_time_saved_minutes: int
    estimated_time_saved_hours: float
    average_lead_score: float
    missing_data_rate: float
    automation_coverage: float
