"""Phase 7: budget-enforced paid generation, tested with fake clients only
(no provider is reachable from tests; see the conftest network guard)."""
from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from app.ai.client import AIClient, AIProviderError
from app.ai.mock_client import MockAIClient
from app.models import AnnotationCandidate, WorkflowEvent
from app.services import splits
from app.services.budgeted_generation import Budget, Ledger, run
from tests.conftest import SYNTHETIC_SELLER_PROFILE, save_and_activate
from tests.test_annotation import seed_cohort

USAGE = {"prompt_tokens": 1_000, "completion_tokens": 200, "total_tokens": 1_200}
CALL_USD = (1_000 * 0.15 + 200 * 0.60) / 1e6  # 0.00027


class FakePaidClient(AIClient):
    """Behaves like the real client (name "openai", reports usage) but
    returns mock content. `script` maps (task, call number for that
    candidate lead) -> "invalid" | "provider_error"."""

    name = "openai"
    model_revision = "gpt-4o-mini"

    def __init__(self, fail: dict | None = None):
        self._mock = MockAIClient()
        self.fail = fail or {}
        self.calls: list[str] = []
        self.last_usage = None

    def _do(self, kind, ctx):
        name = next(f["value"] for f in ctx["lead_facts"] if f["field"] == "company_name")
        key = (kind, name)
        self.calls.append(key)
        mode = self.fail.get(key)
        if isinstance(mode, list):
            mode = mode.pop(0) if mode else None
        self.last_usage = dict(USAGE)
        if mode == "provider_error":
            self.last_usage = None
            raise AIProviderError("AI provider request failed (RateLimitError).")
        if mode == "invalid":
            return {"nonsense": True}
        return self._mock.generate_company_summary(ctx) if kind == "company_summary" else self._mock.generate_outreach(ctx)

    def generate_company_summary(self, ctx):
        return self._do("company_summary", ctx)

    def generate_outreach(self, ctx):
        return self._do("outreach_email", ctx)


def budget(**kw):
    base = dict(max_attempts=220, cap_usd=0.25, input_usd_per_m=0.15, output_usd_per_m=0.60,
                worst_case_call_usd=0.011)
    base.update(kw)
    return Budget(**base)


@pytest.fixture()
def queues(client, db_session):
    seed_cohort(db_session)
    splits.freeze_manifest(db_session)
    splits.create_pilot_queue(db_session, max_examples=4)
    splits.create_queue(db_session, queue="validation-v1", split="validation", seed="v", max_examples=4)
    splits.create_queue(db_session, queue="test-v1", split="test", seed="t", max_examples=4)
    db_session.commit()
    save_and_activate(client, dict(SYNTHETIC_SELLER_PROFILE, profile_kind="demo"))
    rows = list(db_session.scalars(select(AnnotationCandidate).where(
        AnnotationCandidate.queue.in_(["validation-v1", "test-v1"])).order_by(
        AnnotationCandidate.queue, AnnotationCandidate.position)))
    return rows


def key(c):
    return (c.task, c.lead.company_name)


def test_generates_every_candidate_and_counts_cost(db_session, queues, tmp_path):
    ledger = Ledger(str(tmp_path / "l.jsonl"), budget())
    result = run(db_session, FakePaidClient(), ["validation-v1", "test-v1"], budget(), ledger)
    assert result["generated"] == len(queues) == 8 and result["unresolved"] == [] and result["stopped"] is None
    assert result["totals"]["attempts"] == 8
    assert result["totals"]["spent_usd"] == pytest.approx(8 * CALL_USD)
    assert result["totals"]["prompt_tokens"] == 8_000
    pilot = db_session.scalars(select(AnnotationCandidate).where(AnnotationCandidate.queue == "pilot-v1")).all()
    assert all(c.source_output_id is None for c in pilot)  # other queues untouched


def test_one_retry_per_candidate_and_unresolved_reported(db_session, queues, tmp_path):
    a, b = queues[0], queues[1]
    fake = FakePaidClient({key(a): ["invalid"], key(b): ["invalid", "invalid", "invalid"]})
    b_ = budget()
    ledger = Ledger(str(tmp_path / "l.jsonl"), b_)
    result = run(db_session, fake, ["validation-v1", "test-v1"], b_, ledger)
    db_session.refresh(a)
    assert a.source_output_id is not None  # recovered by its single retry
    assert fake.calls.count(key(b)) == 2  # never a third attempt
    assert [u["position"] for u in result["unresolved"]] == [b.position]
    assert result["totals"]["attempts"] == 10
    # failed attempts are billed from reported usage and audited without content
    assert result["totals"]["spent_usd"] == pytest.approx(10 * CALL_USD)
    rejected = db_session.scalars(select(WorkflowEvent).where(WorkflowEvent.event_type == "ai_generation_rejected")).all()
    assert len(rejected) == 3 and all(e.event_data["usage"]["prompt_tokens"] == 1_000 for e in rejected)
    # retries happen after every candidate's first attempt
    passes = [r["pass"] for r in ledger.records()]
    assert passes == sorted(passes)


def test_budget_cap_stops_before_a_call_could_exceed_it(db_session, queues, tmp_path):
    b_ = budget(cap_usd=0.011 + 3 * CALL_USD + 1e-9)
    ledger = Ledger(str(tmp_path / "l.jsonl"), b_)
    result = run(db_session, FakePaidClient(), ["validation-v1", "test-v1"], b_, ledger)
    assert result["stopped"]["reason"] == "budget_cap"
    assert result["totals"]["attempts"] == 4
    assert result["totals"]["spent_usd"] <= b_.cap_usd


def test_attempt_limit(db_session, queues, tmp_path):
    b_ = budget(max_attempts=3)
    result = run(db_session, FakePaidClient(), ["validation-v1", "test-v1"], b_, Ledger(str(tmp_path / "l.jsonl"), b_))
    assert result["stopped"]["reason"] == "attempt_limit" and result["totals"]["attempts"] == 3


def test_provider_error_stops_the_run_and_is_charged_worst_case(db_session, queues, tmp_path):
    fake = FakePaidClient({key(queues[1]): ["provider_error"]})
    b_ = budget()
    result = run(db_session, fake, ["validation-v1", "test-v1"], b_, Ledger(str(tmp_path / "l.jsonl"), b_))
    assert result["stopped"]["reason"] == "provider_or_config_error"
    assert result["totals"]["attempts"] == 2 and result["generated"] == 1
    assert result["totals"]["spent_usd"] == pytest.approx(CALL_USD + 0.011)


def test_three_consecutive_failures_stop(db_session, queues, tmp_path):
    fake = FakePaidClient({key(c): ["invalid"] for c in queues[:3]})
    b_ = budget()
    result = run(db_session, fake, ["validation-v1", "test-v1"], b_, Ledger(str(tmp_path / "l.jsonl"), b_))
    assert result["stopped"]["reason"] == "consecutive_failures" and result["totals"]["attempts"] == 3


def test_resume_counts_prior_attempts_spend_and_interrupted_calls(db_session, queues, tmp_path):
    path = tmp_path / "l.jsonl"
    b_ = budget(max_attempts=5)
    first = FakePaidClient({key(queues[0]): ["invalid"]})
    run(db_session, first, ["validation-v1", "test-v1"], budget(max_attempts=2), Ledger(str(path), b_))
    # simulate a crash mid-call: an in-flight record with no completion
    with open(path, "a") as f:
        f.write(json.dumps({"attempt": 3, "candidate_id": str(queues[5].id), "queue": "test-v1", "position": 99,
                            "task": "x", "pass": 1, "attempt_for_candidate": 1, "provider": "openai",
                            "outcome": "in_flight", "started_at": "t", "cost_usd": 0.0}) + "\n")
    totals = Ledger(str(path), b_).totals()
    assert totals["attempts"] == 3 and totals["spent_usd"] == pytest.approx(2 * CALL_USD + 0.011)
    second = FakePaidClient()
    result = run(db_session, second, ["validation-v1", "test-v1"], b_, Ledger(str(path), b_))
    assert result["totals"]["attempts"] == 5 and result["stopped"]["reason"] == "attempt_limit"
    assert key(queues[1]) not in second.calls  # already generated in the first run: never regenerated
