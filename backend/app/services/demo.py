"""One-click demo: seed a sample batch and run the whole workflow.

This powers ``POST /api/demo/run`` so a freshly deployed instance is never an
empty dashboard. It composes the same service-layer functions the real
endpoints use: CSV ingestion, scoring, AI generation, outreach approval, and
the Slack push, so the demo exercises real code paths, not a shortcut.

The sample leads are embedded here (not read from ``sample_data/``) so the demo
works in a deployed container where the repo's sample files aren't present. The
10 rows mirror ``sample_data/leads_sample.csv`` and are tuned to score
2 Hot / 4 Warm / 4 Cold.

Each run creates a fresh batch; it never deletes existing data.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models import Lead, LeadBatch, LeadScore, WorkflowEvent
from app.scoring.lead_scoring import score_lead
from app.services.ai_generation import (
    generate_outreach_for_lead,
    generate_summary_for_lead,
)
from app.services.csv_ingestion import parse_csv
from app.services.integration_push import SUCCESS_STATUSES, push_lead_to_slack

DEMO_BATCH_NAME = "Demo: sample leads"

# Mirrors sample_data/leads_sample.csv so the demo is self-contained in deploy.
SAMPLE_CSV = """company_name,website,industry,contact_name,contact_email,contact_title,company_size,location,source,notes,pain_points
Cascade Modular Homes,cascademodular.com,Housing,Sarah Chen,sarah.chen@cascademodular.com,VP Operations,240,USA,referral,high tenant maintenance request volume and leasing tour scheduling delays,"maintenance, leasing, scheduling"
Northbridge Clinics,northbridgehealth.com,Healthcare,Anika Rao,anika.rao@northbridgehealth.com,Practice Manager,1200,USA,webinar,"patient appointment scheduling, intake forms, and front desk call volume","appointment, intake, call volume"
Meridian Diagnostics,meridiandx.com,Healthcare,Tom Whitaker,tom@meridiandx.com,Head of Commercial,410,Canada,inbound,diagnostic order intake bottlenecks during peak weeks,intake
Bluepine Property Partners,bluepine.io,Housing,Marcus Bell,m.bell@bluepine.io,Director of Acquisitions,85,USA,referral,acquisition pipeline tracking and follow-up gaps,follow-up
Loopline Software,loopline.dev,SaaS,Daniel Park,dan@loopline.dev,Founder,22,UK,inbound,customer onboarding paperwork and forms automation,"paperwork, forms, automation"
Quanta Analytics,quanta.ai,SaaS,Priya Iyer,priya.iyer@quanta.ai,VP Sales,95,USA,inbound,support call volume and follow-up forms,"support, call volume, follow-up"
Vault Outfitters,vaultoutfitters.com,Retail,Jamie Russo,jamie.russo@vaultoutfitters.com,COO,560,USA,outbound,in-store staffing during holiday peaks,
Sunday Pantry Co,sundaypantry.co,Retail,Lena Okafor,lena@sundaypantry.co,Head of Marketing,130,USA,inbound,D2C subscription churn and email open rates,
Brightline Academy Network,brightline.edu,Education,Robert Klein,rklein@brightline.edu,Superintendent,780,USA,event,district-level budget cycles and procurement timelines,
Latitude Learning,latitudelearn.com,Education,Mei Tanaka,mei.tanaka@latitudelearn.com,Director of Partnerships,60,Singapore,outbound,cross-border partnership pipeline tracking,
"""


def _seed_batch(session: Session) -> tuple[LeadBatch, list[Lead]]:
    result = parse_csv(SAMPLE_CSV)
    batch = LeadBatch(
        name=DEMO_BATCH_NAME,
        source="csv",
        total_leads=result.total_rows,
        processed_leads=len(result.valid_leads),
        status="uploaded",
    )
    session.add(batch)
    session.flush()

    leads: list[Lead] = []
    for cleaned in result.valid_leads:
        lead = Lead(
            batch_id=batch.id,
            company_name=cleaned.company_name,
            website=cleaned.website,
            industry=cleaned.industry,
            contact_name=cleaned.contact_name,
            contact_email=cleaned.contact_email,
            contact_title=cleaned.contact_title,
            company_size=cleaned.company_size,
            location=cleaned.location,
            source=cleaned.source,
            cleaned_data=cleaned.cleaned_data,
        )
        session.add(lead)
        leads.append(lead)

    session.add(
        WorkflowEvent(
            batch_id=batch.id,
            event_type="batch_uploaded",
            event_data={
                "total_rows": result.total_rows,
                "valid_rows": len(result.valid_leads),
                "invalid_rows": len(result.errors),
                "source": "demo",
            },
        )
    )
    session.flush()
    return batch, leads


def _score(session: Session, lead: Lead) -> str:
    """Score a lead, persist the LeadScore, return its priority band."""
    result = score_lead(lead)
    persisted_breakdown = {
        **result["score_breakdown"],
        "matched_signals": result["matched_signals"],
    }
    lead.score = LeadScore(
        lead_id=lead.id,
        total_score=result["total_score"],
        priority=result["priority"],
        score_breakdown=persisted_breakdown,
        reasoning=result["reasoning"],
    )
    lead.status = "scored"
    session.add(
        WorkflowEvent(
            lead_id=lead.id,
            event_type="lead_scored",
            event_data={
                "total_score": result["total_score"],
                "priority": result["priority"],
            },
        )
    )
    return result["priority"]


def _approve(session: Session, lead: Lead, ai_output_id: Any) -> None:
    session.add(
        WorkflowEvent(
            lead_id=lead.id,
            event_type="outreach_approved",
            event_data={
                "ai_output_id": str(ai_output_id),
                "output_type": "outreach_email",
                "lead_id": str(lead.id),
            },
        )
    )
    lead.status = "outreach_approved"


def run_demo(session: Session) -> dict[str, Any]:
    """Seed a batch and run upload → score → generate → approve → push.

    Hot leads get the full AI + approval + push treatment; Warm/Cold leads are
    scored only, which keeps the metrics realistic (not everything is pushed).
    Returns a summary the frontend renders as the demo result.
    """
    batch, leads = _seed_batch(session)

    hot = warm = cold = 0
    for lead in leads:
        band = _score(session, lead)
        if band == "Hot":
            hot += 1
        elif band == "Warm":
            warm += 1
        else:
            cold += 1
    batch.status = "scored"

    generated = approved = pushed = 0
    for lead in leads:
        if lead.score is None or lead.score.priority != "Hot":
            continue
        generate_summary_for_lead(session, lead)
        outreach = generate_outreach_for_lead(session, lead)
        session.flush()
        generated += 1
        _approve(session, lead, outreach.id)
        approved += 1
        push = push_lead_to_slack(session, lead)
        session.flush()
        if push.status in SUCCESS_STATUSES:
            pushed += 1

    batch_id = batch.id
    session.commit()

    return {
        "batch_id": batch_id,
        "batch_name": DEMO_BATCH_NAME,
        "total_leads": len(leads),
        "hot": hot,
        "warm": warm,
        "cold": cold,
        "outreach_generated": generated,
        "outreach_approved": approved,
        "leads_pushed": pushed,
    }
