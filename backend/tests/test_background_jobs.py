"""Phase 10: durable background jobs -- enqueue, progress, bounded retries,
cancellation, graceful release, lease-expiry recovery and fencing. All AI
is the mock (or a scripted client) and Slack is the mock transport."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest
from sqlalchemy import func, select

from app.ai.client import AIProviderError
from app.jobs import runner
from app.jobs.queue import claim_next
from app.models import AIOutput, AIOutputReview, IntegrationPush, Lead, LeadFitScore, LeadScore, WorkflowEvent
from app.models.background_job import BackgroundJob, BackgroundJobItem
from app.services import ai_generation
from tests.conftest import SYNTHETIC_SELLER_PROFILE, approve_current_draft, save_and_activate
from tests.test_push_endpoints import MIXED_CSV, _upload


class Clock:
    def __init__(self) -> None:
        self.t = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += timedelta(seconds=seconds)


def _batch(client, csv: str = MIXED_CSV) -> str:
    return _upload(client, csv)["batch_id"]


def _enqueue(client, batch_id: str, job_type: str, **params) -> dict:
    response = client.post(f"/api/batches/{batch_id}/jobs", json={"job_type": job_type, "params": params})
    assert response.status_code in (200, 202), response.text
    return response.json()


def _run(factory, job_id: str, worker: str = "w1", clock=None, **kw) -> str:
    clock = clock or Clock()
    with factory() as session:
        job = claim_next(session, worker, 60, clock=clock, job_id=UUID(job_id))
        assert job is not None, "job not claimable"
    return runner.run_job(factory, UUID(job_id), worker, lease_seconds=60, clock=clock, **kw)


def _count(session, model, *where) -> int:
    return session.scalar(select(func.count()).select_from(model).where(*where)) or 0


# ------------------------------------------------------------ enqueue

def test_enqueue_validates_and_deduplicates_active_jobs(client):
    batch_id = _batch(client)
    first = client.post(f"/api/batches/{batch_id}/jobs", json={"job_type": "fit_score"})
    assert first.status_code == 202 and first.json()["status"] == "queued"
    again = client.post(f"/api/batches/{batch_id}/jobs", json={"job_type": "fit_score"})
    assert again.status_code == 200 and again.json()["deduplicated"] and again.json()["id"] == first.json()["id"]
    assert client.post(f"/api/batches/{batch_id}/jobs", json={"job_type": "fit_score", "params": {"x": True}}).status_code == 400
    assert client.post(f"/api/batches/{batch_id}/jobs", json={"job_type": "generate_outreach", "params": {"skip_existing": "yes"}}).status_code == 400
    assert client.post(f"/api/batches/{batch_id}/jobs", json={"job_type": "approve_all"}).status_code == 422
    assert client.post("/api/batches/00000000-0000-0000-0000-000000000000/jobs", json={"job_type": "fit_score"}).status_code == 404
    assert client.get(f"/api/jobs?batch_id={batch_id}").json()["total"] == 1


# ------------------------------------------------------------ job types

def test_fit_score_job_reports_progress_and_skips_unchanged_on_rerun(client, db_session_factory, db_session):
    batch_id = _batch(client)
    job = _enqueue(client, batch_id, "fit_score")
    assert _run(db_session_factory, job["id"]) == "completed"
    done = client.get(f"/api/jobs/{job['id']}").json()
    assert done["total_items"] == 3 and done["counts"]["succeeded"] == 3 and done["progress_pct"] == 100.0
    assert done["counts"]["pending"] == 0 and done["finished_at"] and done["lease_expires_at"] is None
    assert _count(db_session, LeadFitScore) == 3
    rerun = _enqueue(client, batch_id, "fit_score")  # the first finished, so this is a new job
    assert rerun["id"] != job["id"]
    assert _run(db_session_factory, rerun["id"]) == "completed"
    assert client.get(f"/api/jobs/{rerun['id']}").json()["counts"]["skipped"] == 3
    assert _count(db_session, LeadFitScore) == 3


def test_legacy_score_job_matches_the_synchronous_endpoint(client, db_session_factory, db_session):
    sync_batch, job_batch = _batch(client), _batch(client)
    sync = client.post(f"/api/batches/{sync_batch}/score").json()
    job = _enqueue(client, job_batch, "legacy_score")
    assert _run(db_session_factory, job["id"]) == "completed"
    result = client.get(f"/api/jobs/{job['id']}").json()["result"]
    assert {k: result[k] for k in ("scored_leads", "hot", "warm", "cold", "average_score")} == \
        {k: sync[k] for k in ("scored_leads", "hot", "warm", "cold", "average_score")}
    assert client.get(f"/api/batches/{job_batch}").json()["status"] == "scored"


def test_generation_job_uses_the_grounded_path_and_never_approves(client, db_session_factory, db_session):
    save_and_activate(client, SYNTHETIC_SELLER_PROFILE)
    batch_id = _batch(client)
    leads = client.get(f"/api/leads?batch_id={batch_id}").json()["items"]
    blocked = db_session.get(Lead, UUID(leads[0]["id"]))
    blocked.status = "do_not_contact"
    db_session.commit()

    job = _enqueue(client, batch_id, "generate_outreach")
    assert _run(db_session_factory, job["id"]) == "completed"
    counts = client.get(f"/api/jobs/{job['id']}").json()["counts"]
    assert counts["succeeded"] == 2 and counts["blocked"] == 1
    items = client.get(f"/api/jobs/{job['id']}/items").json()["items"]
    assert all(i["outcome"].get("quality_flags") == [] for i in items if i["status"] == "succeeded")
    assert _count(db_session, AIOutput, AIOutput.output_type == "outreach_email") == 2
    assert _count(db_session, AIOutput, AIOutput.lead_id == blocked.id) == 0
    assert _count(db_session, AIOutputReview) == 0  # nothing approved or rejected
    rows = db_session.scalars(select(AIOutput)).all()
    assert all(r.output_schema_version == "v2" and r.input_hash and r.seller_profile_id for r in rows)

    again = _enqueue(client, batch_id, "generate_outreach")  # skip_existing by default
    assert _run(db_session_factory, again["id"]) == "completed"
    assert client.get(f"/api/jobs/{again['id']}").json()["counts"]["skipped"] == 2


def test_outreach_without_an_active_seller_fails_the_job_and_saves_nothing(client, db_session_factory, db_session):
    batch_id = _batch(client)
    job = _enqueue(client, batch_id, "generate_outreach")
    assert _run(db_session_factory, job["id"]) == "failed"
    body = client.get(f"/api/jobs/{job['id']}").json()
    assert "No seller profile revision is active" in body["last_error"]
    assert _count(db_session, AIOutput) == 0


def test_provider_errors_are_retried_a_bounded_number_of_times(client, db_session_factory, db_session, monkeypatch):
    batch_id = _batch(client, MIXED_CSV.split("Northbridge")[0].rsplit("\n", 1)[0] + "\n")  # one lead
    calls = {"n": 0}
    real = ai_generation.generate_summary_for_lead

    def flaky(session, lead, **kw):
        calls["n"] += 1
        if calls["n"] < 3:
            raise AIProviderError("AI provider request failed (ConnectTimeout).")
        return real(session, lead, **kw)

    monkeypatch.setattr(ai_generation, "generate_summary_for_lead", flaky)
    job = _enqueue(client, batch_id, "generate_summary")
    assert _run(db_session_factory, job["id"]) == "completed"
    [item] = client.get(f"/api/jobs/{job['id']}/items").json()["items"]
    assert item["status"] == "succeeded" and item["attempts"] == 3 and calls["n"] == 3

    batch2 = _batch(client, MIXED_CSV.split("Northbridge")[0].rsplit("\n", 1)[0] + "\n")

    def always_down(session, lead, **kw):
        raise AIProviderError("AI provider request failed (ConnectError).")

    monkeypatch.setattr(ai_generation, "generate_summary_for_lead", always_down)
    job2 = _enqueue(client, batch2, "generate_summary")
    assert _run(db_session_factory, job2["id"]) == "completed"
    [item] = client.get(f"/api/jobs/{job2['id']}/items").json()["items"]
    assert item["status"] == "failed" and item["attempts"] == 3 and "ConnectError" in item["error"]
    assert client.get(f"/api/jobs/{job2['id']}").json()["counts"]["failed"] == 1


# ------------------------------------------------------------ control and recovery

def test_cancelling_a_queued_job_is_immediate_and_frees_the_slot(client):
    batch_id = _batch(client)
    job = _enqueue(client, batch_id, "fit_score")
    cancelled = client.post(f"/api/jobs/{job['id']}/cancel").json()
    assert cancelled["status"] == "cancelled"
    assert client.post(f"/api/jobs/{job['id']}/cancel").status_code == 409
    assert _enqueue(client, batch_id, "fit_score")["id"] != job["id"]


def test_a_running_job_stops_before_its_next_item_when_cancelled(client, db_session_factory):
    batch_id = _batch(client)
    job = _enqueue(client, batch_id, "fit_score")
    clock = Clock()
    assert _run(db_session_factory, job["id"], clock=clock, item_limit=1) == runner.PAUSED
    assert client.post(f"/api/jobs/{job['id']}/cancel").json()["cancel_requested"] is True
    assert runner.run_job(db_session_factory, UUID(job["id"]), "w1", clock=clock) == "cancelled"
    body = client.get(f"/api/jobs/{job['id']}").json()
    assert body["status"] == "cancelled" and body["counts"]["succeeded"] == 1 and body["counts"]["pending"] == 2


def test_graceful_stop_returns_the_job_to_the_queue_without_counting_an_attempt(client, db_session_factory):
    batch_id = _batch(client)
    job = _enqueue(client, batch_id, "fit_score")
    assert _run(db_session_factory, job["id"], should_stop=lambda: True) == runner.RELEASED
    body = client.get(f"/api/jobs/{job['id']}").json()
    assert body["status"] == "queued" and body["attempts"] == 0
    assert _run(db_session_factory, job["id"], worker="w2") == "completed"


def test_a_dead_workers_job_is_recovered_after_its_lease_expires_exactly_once(client, db_session_factory, db_session):
    batch_id = _batch(client)
    job = _enqueue(client, batch_id, "generate_summary")
    clock = Clock()
    # Worker 1 processes one item and then "dies" still holding the lease.
    assert _run(db_session_factory, job["id"], worker="w1", clock=clock, item_limit=1) == runner.PAUSED
    with db_session_factory() as session:
        assert claim_next(session, "w2", 60, clock=clock) is None  # lease still valid
    clock.advance(61)
    with db_session_factory() as session:
        claimed = claim_next(session, "w2", 60, clock=clock)
        assert claimed is not None and claimed.attempts == 2
    assert runner.run_job(db_session_factory, UUID(job["id"]), "w2", clock=clock) == "completed"
    assert _count(db_session, AIOutput) == 3  # one summary per lead, none duplicated
    assert db_session.scalar(select(func.count(func.distinct(AIOutput.lead_id)))) == 3
    events = [e.event_type for e in db_session.scalars(
        select(WorkflowEvent).where(WorkflowEvent.event_type.like("job_%")).order_by(WorkflowEvent.created_at))]
    assert events == ["job_enqueued", "job_recovered", "job_completed"]


def test_a_stale_worker_cannot_commit_after_its_job_was_taken_over(client, db_session_factory, db_session):
    batch_id = _batch(client)
    job = _enqueue(client, batch_id, "generate_summary")
    clock = Clock()
    assert _run(db_session_factory, job["id"], worker="w1", clock=clock, item_limit=1) == runner.PAUSED
    clock.advance(61)
    with db_session_factory() as session:
        assert claim_next(session, "w2", 60, clock=clock) is not None
    # w1 wakes up and tries to continue: the fence stops it before any commit.
    assert runner.run_job(db_session_factory, UUID(job["id"]), "w1", clock=clock) == runner.LOST
    assert _count(db_session, AIOutput) == 1
    assert runner.run_job(db_session_factory, UUID(job["id"]), "w2", clock=clock) == "completed"
    assert _count(db_session, AIOutput) == 3


def test_a_job_that_keeps_killing_its_worker_fails_as_a_crash_loop(client, db_session_factory):
    batch_id = _batch(client)
    job = _enqueue(client, batch_id, "fit_score")
    clock = Clock()
    for attempt in range(3):
        with db_session_factory() as session:
            assert claim_next(session, f"w{attempt}", 60, clock=clock) is not None
        clock.advance(61)  # the worker died without finishing
    with db_session_factory() as session:
        assert claim_next(session, "w9", 60, clock=clock) is None
    body = client.get(f"/api/jobs/{job['id']}").json()
    assert body["status"] == "failed" and body["last_error"].startswith("crash_loop")


# ------------------------------------------------------------ push

def test_push_job_delivers_only_approved_hot_leads_in_mock_mode(client, db_session_factory, db_session):
    save_and_activate(client, SYNTHETIC_SELLER_PROFILE)
    batch_id = _batch(client)
    client.post(f"/api/batches/{batch_id}/score")
    leads = client.get(f"/api/leads?batch_id={batch_id}").json()["items"]
    by_name = {lead["company_name"]: lead for lead in leads}
    assert {n: db_session.scalar(select(LeadScore.priority).where(LeadScore.lead_id == UUID(lead["id"])))
            for n, lead in by_name.items()} == {"Cascade Modular": "Hot", "Northbridge Clinics": "Hot",
                                                 "Vault Outfitters": "Cold"}
    approve_current_draft(client, by_name["Cascade Modular"]["id"])  # Northbridge stays unapproved

    job = _enqueue(client, batch_id, "push_hot")
    assert _run(db_session_factory, job["id"]) == "completed"
    items = {i["lead_id"]: i for i in client.get(f"/api/jobs/{job['id']}/items").json()["items"]}
    assert items[by_name["Cascade Modular"]["id"]]["outcome"]["push_status"] == "mock_success"
    assert items[by_name["Northbridge Clinics"]["id"]]["status"] == "blocked"
    assert items[by_name["Northbridge Clinics"]["id"]]["outcome"]["reason"] == "not_approved"
    assert by_name["Vault Outfitters"]["id"] not in items  # Cold: not part of the job
    assert _count(db_session, IntegrationPush) == 1

    rerun = _enqueue(client, batch_id, "push_hot")
    assert _run(db_session_factory, rerun["id"]) == "completed"
    assert client.get(f"/api/jobs/{rerun['id']}").json()["counts"]["skipped"] == 1
    assert _count(db_session, IntegrationPush) == 1


def test_push_job_respects_blocked_statuses(client, db_session_factory, db_session):
    save_and_activate(client, SYNTHETIC_SELLER_PROFILE)
    batch_id = _batch(client)
    client.post(f"/api/batches/{batch_id}/score")
    leads = {lead["company_name"]: lead for lead in client.get(f"/api/leads?batch_id={batch_id}").json()["items"]}
    target = leads["Cascade Modular"]["id"]
    approve_current_draft(client, target)
    row = db_session.get(Lead, UUID(target))
    row.status = "unsubscribed"
    db_session.commit()
    job = _enqueue(client, batch_id, "push_hot")
    assert _run(db_session_factory, job["id"]) == "completed"
    items = {i["lead_id"]: i for i in client.get(f"/api/jobs/{job['id']}/items").json()["items"]}
    assert items[target]["status"] == "blocked" and items[target]["outcome"]["reason"] == "lead_status:unsubscribed"
    assert _count(db_session, IntegrationPush) == 0


def test_push_job_on_a_partial_import_fails_before_any_item(client, db_session_factory, db_session):
    from app.models import LeadBatch

    batch_id = _batch(client)
    client.post(f"/api/batches/{batch_id}/score")
    batch = db_session.get(LeadBatch, UUID(batch_id))
    batch.status = "partial"
    db_session.commit()
    job = _enqueue(client, batch_id, "push_hot")
    assert _run(db_session_factory, job["id"]) == "failed"
    assert "partially imported" in client.get(f"/api/jobs/{job['id']}").json()["last_error"]
    assert _count(db_session, BackgroundJobItem) == 0 and _count(db_session, IntegrationPush) == 0
    assert _count(db_session, BackgroundJob) == 1


# ------------------------------------------------------------ Postgres only
# These need real, separate database connections (the in-memory SQLite
# fixture shares one connection between sessions). Run with
# TEST_DATABASE_URL pointing at a disposable Postgres database.

import os
import signal
import subprocess
import sys
import time

postgres_only = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL", "").startswith("postgresql"),
    reason="needs TEST_DATABASE_URL (disposable Postgres)")


@postgres_only
def test_a_takeover_during_an_item_rolls_back_the_stale_workers_commit(client, db_session_factory, db_session, monkeypatch):
    batch_id = _batch(client)
    job = _enqueue(client, batch_id, "generate_summary")
    clock = Clock()
    with db_session_factory() as session:
        assert claim_next(session, "w1", 60, clock=clock) is not None
    real = ai_generation.generate_summary_for_lead
    state = {"taken": False}

    def slow_then_taken_over(session, lead, **kw):
        output = real(session, lead, **kw)  # w1 has written its output, uncommitted
        if not state["taken"]:
            state["taken"] = True
            clock.advance(61)  # w1 stalled past its lease; w2 takes over meanwhile
            with db_session_factory() as other:
                assert claim_next(other, "w2", 60, clock=clock) is not None
        return output

    monkeypatch.setattr(ai_generation, "generate_summary_for_lead", slow_then_taken_over)
    assert runner.run_job(db_session_factory, UUID(job["id"]), "w1", clock=clock) == runner.LOST
    assert _count(db_session, AIOutput) == 0  # the fence rolled w1's item back
    assert runner.run_job(db_session_factory, UUID(job["id"]), "w2", clock=clock) == "completed"
    assert _count(db_session, AIOutput) == 3


@postgres_only
def test_a_killed_worker_process_is_recovered_by_another_worker(client, db_session):
    rows = "".join(f"Company {i:04d},Housing,COO,Pat Lee,p{i}@c{i}.com,c{i}.com,240,referral,scheduling\n"
                   for i in range(300))
    header = "company_name,industry,contact_title,contact_name,contact_email,website,company_size,source,notes\n"
    batch_id = _batch(client, header + rows)
    job = _enqueue(client, batch_id, "generate_summary")
    env = {**os.environ, "DATABASE_URL": os.environ["TEST_DATABASE_URL"], "USE_MOCK_AI": "true",
           "OPENAI_API_KEY": "", "SLACK_WEBHOOK_URL": "", "AI_PROVIDER": "openai"}
    cmd = [sys.executable, "-m", "app.jobs.worker", "--lease-seconds", "3", "--poll-seconds", "0.2"]
    first = subprocess.Popen(cmd + ["--worker-id", "killed"], env=env, stdout=subprocess.DEVNULL)
    deadline = time.time() + 60
    done = 0
    while time.time() < deadline:
        db_session.expire_all()
        done = _count(db_session, BackgroundJobItem, BackgroundJobItem.status == "succeeded")
        if done >= 20:
            break
        time.sleep(0.05)
    first.send_signal(signal.SIGKILL)
    first.wait()
    db_session.expire_all()
    stalled = db_session.get(BackgroundJob, UUID(job["id"]))
    assert stalled.status == "running" and stalled.lease_owner == "killed" and 0 < done < 300

    time.sleep(3.5)  # the dead worker's 3 s lease must expire before anyone may take over
    second = subprocess.run(cmd + ["--worker-id", "survivor", "--drain"], env=env, timeout=300,
                            capture_output=True, text=True)
    assert second.returncode == 0, second.stderr[-2000:]
    db_session.expire_all()
    final = db_session.get(BackgroundJob, UUID(job["id"]))
    assert final.status == "completed" and final.counts["succeeded"] == 300 and final.attempts == 2
    assert _count(db_session, AIOutput) == 300
    assert db_session.scalar(select(func.count(func.distinct(AIOutput.lead_id)))) == 300
    assert _count(db_session, WorkflowEvent, WorkflowEvent.event_type == "job_recovered") == 1
