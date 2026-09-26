"""Phase 11: the Slack delivery ledger -- no duplicate dispatch across repeats,
concurrent requests, workers and restarts; uncertain outcomes are never
resent blindly; the delivery rules are unchanged. Slack is a spy on
httpx.post behind a fake webhook URL (no network)."""
from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timedelta, timezone
from uuid import UUID

import httpx
import pytest
from sqlalchemy import func, select

from app.core import config
from app.integrations import slack as slack_module
from app.jobs import runner
from app.jobs.queue import claim_next
from app.models import IntegrationPush, Lead, WorkflowEvent
from app.models.background_job import BackgroundJobItem
from app.services import integration_push as push_service
from tests.conftest import approve_current_draft
from tests.test_push_endpoints import _lead_by, _scored_setup

pytestmark = pytest.mark.usefixtures("active_seller_profile")
FAKE_WEBHOOK = "https://hooks.example.invalid/services/T000/B000/fake"


class SlackSpy:
    """Stands in for httpx.post. `behaviour` is 'ok', 'http500', 'timeout',
    'refused' or a callable; `delay` simulates a slow Slack."""

    def __init__(self, behaviour="ok", delay=0.0):
        self.behaviour, self.delay, self.calls = behaviour, delay, 0
        self.lock = threading.Lock()

    def __call__(self, url, json=None, timeout=None):
        with self.lock:
            self.calls += 1
        if self.delay:
            time.sleep(self.delay)
        request = httpx.Request("POST", url)
        if callable(self.behaviour):
            return self.behaviour(request)
        if self.behaviour == "timeout":
            raise httpx.ReadTimeout("read timed out", request=request)
        if self.behaviour == "refused":
            raise httpx.ConnectError("connection refused", request=request)
        if self.behaviour == "http500":
            return httpx.Response(500, text="internal_error", request=request)
        return httpx.Response(200, text="ok", request=request)


@pytest.fixture()
def slack(monkeypatch):
    spy = SlackSpy()
    monkeypatch.setattr(slack_module.httpx, "post", spy)
    monkeypatch.setattr(push_service, "settings",
                        config.Settings(**{**config.settings.__dict__, "slack_webhook_url": FAKE_WEBHOOK}))
    return spy


def _hot(client) -> dict:
    batch_id, leads = _scored_setup(client)  # approves every lead's current draft
    return {"batch_id": batch_id, **_lead_by(leads, "Cascade Modular")}


def _push(client, lead_id, **body):
    return client.post(f"/api/leads/{lead_id}/push", json={"integration_type": "slack", **body})


def _rows(db_session, lead_id=None):
    db_session.expire_all()
    stmt = select(IntegrationPush)
    if lead_id:
        stmt = stmt.where(IntegrationPush.lead_id == UUID(lead_id))
    return db_session.scalars(stmt.order_by(IntegrationPush.created_at)).all()


# ------------------------------------------------------------ single process

def test_a_repeat_push_is_a_replay_and_an_explicit_redeliver_is_a_new_attempt(client, db_session, slack):
    lead = _hot(client)
    first = _push(client, lead["id"]).json()
    assert first["status"] == "success" and first["attempt"] == 1 and first["delivery_mode"] == "real"
    again = _push(client, lead["id"]).json()
    assert again["replay"] is True and again["id"] == first["id"] and slack.calls == 1
    again = _push(client, lead["id"], force=True).json()  # force only bypasses the Hot threshold
    assert again["replay"] is True and slack.calls == 1
    second = _push(client, lead["id"], redeliver=True).json()
    assert second["attempt"] == 2 and second["replay"] is False and slack.calls == 2


def test_a_definite_failure_is_not_retried_automatically_but_may_be_retried_explicitly(client, db_session, slack):
    lead = _hot(client)
    slack.behaviour = "http500"
    failed = _push(client, lead["id"]).json()
    assert failed["status"] == "failed" and failed["outcome_code"] == "not_delivered" and slack.calls == 1
    slack.behaviour = "refused"
    assert _push(client, lead["id"]).json()["status"] == "failed" and slack.calls == 2  # connection refused: not sent
    slack.behaviour = "ok"
    ok = _push(client, lead["id"]).json()
    assert ok["status"] == "success" and ok["attempt"] == 3 and slack.calls == 3


def test_an_unknown_outcome_blocks_every_send_until_an_operator_resolves_it(client, db_session, slack, db_session_factory):
    lead = _hot(client)
    slack.behaviour = "timeout"
    unknown = _push(client, lead["id"]).json()
    assert unknown["status"] == "unknown" and "may have reached Slack" in unknown["response_text"]
    slack.behaviour = "ok"
    for body in ({}, {"force": True}, {"redeliver": True}):
        blocked = _push(client, lead["id"], **body)
        assert blocked.status_code == 409 and blocked.json()["detail"]["push_id"] == unknown["id"]
    assert slack.calls == 1  # nothing was resent
    batch = client.post(f"/api/batches/{lead['batch_id']}/push-hot", json={"integration_type": "slack"}).json()
    mine = next(r for r in batch["results"] if r["lead_id"] == lead["id"])
    assert batch["uncertain"] == 1 and mine["status"] == "outcome_unknown"
    assert len(_rows(db_session, lead["id"])) == 1  # the batch sent this lead nothing (the other Hot lead was delivered)
    sent_before_resolution = slack.calls

    assert client.post(f"/api/pushes/{unknown['id']}/resolve", json={"resolution": "maybe"}).status_code == 422
    resolved = client.post(f"/api/pushes/{unknown['id']}/resolve",
                           json={"resolution": "confirmed_not_delivered", "note": "not in #sales"}).json()
    assert resolved["status"] == "failed" and resolved["resolution"] == "confirmed_not_delivered"
    assert client.post(f"/api/pushes/{unknown['id']}/resolve",
                       json={"resolution": "confirmed_delivered"}).status_code == 409  # only unknown rows
    retry = _push(client, lead["id"]).json()
    assert retry["status"] == "success" and retry["attempt"] == 2 and slack.calls == sent_before_resolution + 1


def test_resolving_as_delivered_counts_as_delivered_and_later_pushes_replay(client, db_session, slack):
    lead = _hot(client)
    slack.behaviour = "timeout"
    unknown = _push(client, lead["id"]).json()
    resolved = client.post(f"/api/pushes/{unknown['id']}/resolve", json={"resolution": "confirmed_delivered"}).json()
    assert resolved["status"] == "success" and resolved["outcome_code"] == "outcome_unknown"  # original kept
    slack.behaviour = "ok"
    assert _push(client, lead["id"]).json()["replay"] is True and slack.calls == 1
    assert client.get(f"/api/leads/{lead['id']}").json()["status"] == "pushed"
    events = [e.event_type for e in db_session.scalars(select(WorkflowEvent).where(WorkflowEvent.lead_id == UUID(lead["id"])))]
    assert "push_outcome_resolved" in events


def test_a_claim_left_by_a_dead_process_becomes_unknown_never_a_resend(client, db_session, slack):
    lead = _hot(client)
    # A process claimed the attempt and died before recording the outcome.
    _push(client, lead["id"])
    row = _rows(db_session, lead["id"])[0]
    row.status, row.completed_at, row.outcome_code = "pending", None, None
    row.claimed_at = datetime.now(timezone.utc) - timedelta(seconds=push_service.CLAIM_STALE_SECONDS - 30)
    db_session.commit()
    young = _push(client, lead["id"])
    assert young.status_code == 409 and "in progress" in young.json()["detail"]["message"]
    row.claimed_at = datetime.now(timezone.utc) - timedelta(seconds=push_service.CLAIM_STALE_SECONDS + 30)
    db_session.commit()
    old = _push(client, lead["id"])
    assert old.status_code == 409 and old.json()["detail"]["status"] == "unknown"
    assert _rows(db_session, lead["id"])[0].outcome_code == "claim_expired"
    assert slack.calls == 1  # only the original send


def test_a_late_outcome_never_overwrites_a_claim_already_declared_unknown(client, db_session, slack, db_session_factory):
    lead = _hot(client)

    def declared_unknown_while_sending(request):
        with db_session_factory() as other:
            other.query(IntegrationPush).filter(IntegrationPush.status == "pending").update(
                {"status": "unknown", "outcome_code": "claim_expired"})
            other.commit()
        return httpx.Response(200, text="ok", request=request)

    slack.behaviour = declared_unknown_while_sending
    body = _push(client, lead["id"]).json()
    assert body["status"] == "unknown" and body["outcome_code"] == "claim_expired"
    late = db_session.scalar(select(WorkflowEvent).where(WorkflowEvent.event_type == "push_late_outcome"))
    assert late is not None and late.event_data["status"] == "success"


# ------------------------------------------------------------ rules preserved

def test_blocked_and_unapproved_leads_are_refused_before_any_claim(client, db_session, slack):
    lead = _hot(client)
    row = db_session.get(Lead, UUID(lead["id"]))
    row.status = "do_not_contact"
    db_session.commit()
    assert _push(client, lead["id"], force=True, redeliver=True).status_code == 400
    row.status = "outreach_approved"
    db_session.commit()
    client.post(f"/api/leads/{lead['id']}/generate-outreach")  # a new, unapproved draft
    assert _push(client, lead["id"]).status_code == 409
    assert _rows(db_session, lead["id"]) == [] and slack.calls == 0


def test_each_approved_draft_is_its_own_delivery(client, db_session, slack):
    lead = _hot(client)
    first = _push(client, lead["id"]).json()
    approve_current_draft(client, lead["id"])  # a new draft, approved
    second = _push(client, lead["id"]).json()
    assert second["replay"] is False and second["approved_output_id"] != first["approved_output_id"]
    assert second["attempt"] == 1 and slack.calls == 2


def test_a_restarted_push_job_item_never_sends_twice(client, db_session, slack, db_session_factory):
    lead = _hot(client)
    job = client.post(f"/api/batches/{lead['batch_id']}/jobs", json={"job_type": "push_hot"}).json()
    with db_session_factory() as s:
        claim_next(s, "w1", 60, job_id=UUID(job["id"]))
    assert runner.run_job(db_session_factory, UUID(job["id"]), "w1") == "completed"
    sent = slack.calls
    # Simulate a worker that died after the delivery but before its item
    # status committed: the items are pending again, the job is re-run.
    db_session.expire_all()
    for item in db_session.scalars(select(BackgroundJobItem).where(BackgroundJobItem.job_id == UUID(job["id"]))):
        item.status = "pending"
    rerun = client.post(f"/api/batches/{lead['batch_id']}/jobs", json={"job_type": "push_hot"}).json()
    db_session.commit()
    with db_session_factory() as s:
        claim_next(s, "w2", 60, job_id=UUID(rerun["id"]))
    assert runner.run_job(db_session_factory, UUID(rerun["id"]), "w2") == "completed"
    items = client.get(f"/api/jobs/{rerun['id']}/items").json()["items"]
    assert {i["outcome"]["reason"] for i in items if i["status"] == "skipped"} <= {"already_pushed", "already_delivered"}
    assert slack.calls == sent


# ------------------------------------------------------------ Postgres concurrency

postgres_only = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL", "").startswith("postgresql"),
    reason="needs TEST_DATABASE_URL (disposable Postgres)")


@postgres_only
def test_concurrent_api_requests_send_exactly_once(client, db_session, slack):
    lead = _hot(client)
    slack.delay = 0.3  # a slow Slack keeps the claim pending while others arrive
    barrier = threading.Barrier(8)
    codes = []

    def fire():
        barrier.wait()
        codes.append(_push(client, lead["id"]).status_code)

    threads = [threading.Thread(target=fire) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert slack.calls == 1
    assert codes.count(200) >= 1 and set(codes) <= {200, 409}
    assert len(_rows(db_session, lead["id"])) == 1


@postgres_only
def test_api_requests_and_two_workers_racing_send_exactly_once(client, db_session, slack, db_session_factory):
    lead = _hot(client)
    slack.delay = 0.3
    jobs = []
    for _ in range(2):
        job = client.post(f"/api/batches/{lead['batch_id']}/jobs", json={"job_type": "push_hot"}).json()
        with db_session_factory() as s:  # claim it so the dedupe slot frees for the next one
            claim_next(s, f"w{len(jobs)}", 60, job_id=UUID(job["id"]))
        db_session.expire_all()
        from app.models.background_job import BackgroundJob
        j = db_session.get(BackgroundJob, UUID(job["id"]))
        j.dedupe_key = None
        db_session.commit()
        jobs.append(job["id"])
    barrier = threading.Barrier(4)

    def worker(i):
        barrier.wait()
        runner.run_job(db_session_factory, UUID(jobs[i]), f"w{i}")

    def api():
        barrier.wait()
        _push(client, lead["id"])

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(2)] + [threading.Thread(target=api) for _ in range(2)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    rows = [r for r in _rows(db_session) if r.lead_id == UUID(lead["id"])]
    assert len(rows) == 1 and rows[0].status == "success"
    assert db_session.scalar(select(func.count()).select_from(IntegrationPush).where(
        IntegrationPush.lead_id == UUID(lead["id"]), IntegrationPush.status == "success")) == 1
    # Per lead, at most one send; the other Hot lead may have been sent once too.
    assert slack.calls == len({r.lead_id for r in _rows(db_session) if r.status == "success"})
