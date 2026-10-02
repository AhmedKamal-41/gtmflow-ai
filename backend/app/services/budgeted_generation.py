"""Budget-enforced generation for annotation queues (Phase 7 held-out run).

Every paid attempt is written to an append-only JSONL ledger before and
after the call, so attempts and spend survive a crash or a restart and are
counted against the same allowance. Rules:

* An attempt starts only if the attempt count stays within `max_attempts`
  and `spent + worst_case_call_usd <= cap_usd`, where the reserve assumes the
  maximum output. The cap therefore holds even if one call is pathological.
* Cost is computed from provider-reported usage at the verified prices. A
  call with unknown usage (provider error, or usage missing) is charged the
  worst-case reserve.
* A candidate gets at most `max_attempts_per_candidate` attempts (1 + one
  retry). Retries run in a second pass after every candidate's first attempt.
* Successful outputs are committed immediately and never replaced.
* The run stops on any provider or config error (authentication, billing,
  rate limit, connection), and after `stop_after_consecutive_failures`
  consecutive failed attempts.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.client import AIClient, AIConfigError, AIProviderError
from app.models import AnnotationCandidate
from app.services.ai_generation import GenerationOutputInvalid, record_generation_rejected
from app.services.annotation import AnnotationError, generate_candidate

OUTCOME_SUCCESS = "success"
OUTCOME_INVALID = "invalid_output"
OUTCOME_PROVIDER_ERROR = "provider_error"
OUTCOME_IN_FLIGHT = "in_flight"


@dataclass(frozen=True)
class Budget:
    max_attempts: int
    cap_usd: float
    input_usd_per_m: float
    output_usd_per_m: float
    worst_case_call_usd: float
    max_attempts_per_candidate: int = 2
    stop_after_consecutive_failures: int = 3

    def cost(self, usage: dict[str, Any] | None) -> tuple[float, str]:
        if not usage or usage.get("prompt_tokens") is None or usage.get("completion_tokens") is None:
            return self.worst_case_call_usd, "worst_case_unknown_usage"
        return (usage["prompt_tokens"] * self.input_usd_per_m + usage["completion_tokens"] * self.output_usd_per_m) / 1e6, "reported_usage"


class Ledger:
    """Append-only JSONL. The last record per attempt number is authoritative;
    an attempt whose last record is still `in_flight` (crash mid-call) counts
    as an attempt charged at the worst-case reserve."""

    def __init__(self, path: str, budget: Budget):
        self.path = path
        self.budget = budget

    def records(self) -> list[dict[str, Any]]:
        if not os.path.exists(self.path):
            return []
        latest: dict[int, dict[str, Any]] = {}
        with open(self.path) as f:
            for line in f:
                if line.strip():
                    r = json.loads(line)
                    latest[r["attempt"]] = r
        out = []
        for n in sorted(latest):
            r = dict(latest[n])
            if r["outcome"] == OUTCOME_IN_FLIGHT:
                r["cost_usd"], r["cost_basis"] = self.budget.worst_case_call_usd, "worst_case_interrupted"
            out.append(r)
        return out

    def append(self, record: dict[str, Any]) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        with open(self.path, "a") as f:
            f.write(json.dumps(record, sort_keys=True, default=str) + "\n")
            f.flush()
            os.fsync(f.fileno())

    def totals(self) -> dict[str, Any]:
        rs = self.records()
        return {
            "attempts": len(rs),
            "spent_usd": round(sum(r["cost_usd"] for r in rs), 6),
            "prompt_tokens": sum((r.get("usage") or {}).get("prompt_tokens") or 0 for r in rs),
            "completion_tokens": sum((r.get("usage") or {}).get("completion_tokens") or 0 for r in rs),
            "by_outcome": {o: sum(1 for r in rs if r["outcome"] == o) for o in sorted({r["outcome"] for r in rs})},
        }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def run(session: Session, client: AIClient, queues: list[str], budget: Budget, ledger: Ledger,
        provider: str = "openai", max_position: int | None = None) -> dict[str, Any]:
    """`max_position` limits this run to candidates at or before that queue
    position (e.g. the first 450 of a queue with reserves after them)."""
    history = ledger.records()
    per_candidate: dict[str, list[dict[str, Any]]] = {}
    for r in history:
        per_candidate.setdefault(r["candidate_id"], []).append(r)
    candidates = list(session.scalars(
        select(AnnotationCandidate).where(AnnotationCandidate.queue.in_(queues))
        .order_by(AnnotationCandidate.queue, AnnotationCandidate.position)
    ))
    if max_position is not None:
        candidates = [c for c in candidates if c.position <= max_position]
    state = {"attempts": len(history), "spent": sum(r["cost_usd"] for r in history), "consecutive": 0}
    stop: dict[str, Any] = {}

    def attempt(c: AnnotationCandidate, pass_no: int) -> None:
        n = state["attempts"] + 1
        base = {"attempt": n, "candidate_id": str(c.id), "queue": c.queue, "position": c.position,
                "task": c.task, "pass": pass_no,
                "attempt_for_candidate": len(per_candidate.get(str(c.id), [])) + 1, "provider": provider}
        started = _now()
        ledger.append({**base, "outcome": OUTCOME_IN_FLIGHT, "started_at": started, "cost_usd": 0.0})
        state["attempts"] = n
        record = {**base, "started_at": started, "finished_at": None}
        try:
            output = generate_candidate(session, c, provider, client=client)
            session.commit()
            cost, basis = budget.cost(client.last_usage)
            record.update(outcome=OUTCOME_SUCCESS, ai_output_id=str(output.id), usage=client.last_usage)
            state["consecutive"] = 0
        except GenerationOutputInvalid as error:
            session.rollback()
            record_generation_rejected(session, c.lead_id, c.task, error.reason_codes, error.usage, error.details)
            session.commit()
            cost, basis = budget.cost(error.usage)
            record.update(outcome=OUTCOME_INVALID, usage=error.usage, reason_codes=error.reason_codes,
                          details=error.details)
            state["consecutive"] += 1
        except (AIProviderError, AIConfigError) as error:
            session.rollback()
            cost, basis = budget.cost(None)
            record.update(outcome=OUTCOME_PROVIDER_ERROR, usage=None, error=str(error))
            stop.update(reason="provider_or_config_error", detail=str(error), attempt=n)
        record.update(cost_usd=round(cost, 8), cost_basis=basis, finished_at=_now())
        ledger.append(record)
        per_candidate.setdefault(str(c.id), []).append(record)
        state["spent"] += cost
        if not stop and state["consecutive"] >= budget.stop_after_consecutive_failures:
            stop.update(reason="consecutive_failures", attempt=n)

    def may_start() -> bool:
        if state["attempts"] >= budget.max_attempts:
            stop.update(reason="attempt_limit")
            return False
        if state["spent"] + budget.worst_case_call_usd > budget.cap_usd:
            stop.update(reason="budget_cap", spent_usd=round(state["spent"], 6))
            return False
        return True

    for pass_no in (1, 2):
        for c in candidates:
            if stop:
                break
            session.refresh(c)
            if c.source_output_id is not None:
                continue
            tried = len(per_candidate.get(str(c.id), []))
            if tried >= budget.max_attempts_per_candidate:
                continue
            if (pass_no == 1 and tried > 0) or (pass_no == 2 and tried == 0):
                continue
            if not may_start():
                break
            try:
                attempt(c, pass_no)
            except AnnotationError as error:  # e.g. no active seller: nothing was called
                session.rollback()
                stop.update(reason="refused_before_call", detail=error.detail, position=c.position)
        if stop:
            break

    for c in candidates:
        session.refresh(c)
    unresolved = [
        {"queue": c.queue, "position": c.position, "task": c.task,
         "attempts": len(per_candidate.get(str(c.id), [])),
         "last_outcome": (per_candidate.get(str(c.id)) or [{}])[-1].get("outcome")}
        for c in candidates if c.source_output_id is None
    ]
    return {
        "budget": asdict(budget),
        "stopped": stop or None,
        "totals": ledger.totals(),
        "generated": sum(1 for c in candidates if c.source_output_id is not None),
        "candidates": len(candidates),
        "unresolved": unresolved,
    }
