"""Phase 7: budget-enforced paid generation for validation-v1 and test-v1 ONLY.

Authorization (user, 2026-09-24): the 200 candidates of validation-v1 and
test-v1; at most 220 paid attempts in total and a $0.25 cap, whichever
comes first; at most one retry per candidate. Not for training queues.

    # preflight only -- no provider call, no writes:
    python scripts/phase7_generate_heldout.py preflight
    # the paid run (resumable; the ledger carries attempts and spend):
    python scripts/phase7_generate_heldout.py execute

Run from backend/. The key is read from settings (.env) and never printed.
"""
from __future__ import annotations

import argparse
import json
import sys

from sqlalchemy import func, select

from app.ai.client import get_ai_client
from app.core.config import settings
from app.core.database import get_sessionmaker
from app.models import AIOutput, AnnotationCandidate
from app.services.ai_generation import resolve_active_seller
from app.services.budgeted_generation import Budget, Ledger, run

ALLOWED_QUEUES = ["validation-v1", "test-v1"]
LEDGER = "data/generation_ledgers/heldout-v1.jsonl"
# gpt-4o-mini standard tier, verified 2026-09-24 at developers.openai.com/api/docs/pricing:
# $0.15 / 1M input, $0.60 / 1M output (cached-input discount ignored: conservative).
INPUT_USD_PER_M = 0.15
OUTPUT_USD_PER_M = 0.60
# Reserve per call: up to 8,000 prompt tokens (pilot max about 2,100) and
# gpt-4o-mini's 16,384 maximum output tokens (no max_tokens is set).
WORST_CASE_CALL_USD = round((8_000 * INPUT_USD_PER_M + 16_384 * OUTPUT_USD_PER_M) / 1e6, 6)
BUDGET = Budget(max_attempts=220, cap_usd=0.25, input_usd_per_m=INPUT_USD_PER_M,
                output_usd_per_m=OUTPUT_USD_PER_M, worst_case_call_usd=WORST_CASE_CALL_USD)
EXPECTED_MODEL = "gpt-4o-mini"


def preflight(session) -> dict:
    problems = []
    if settings.use_mock_ai:
        problems.append("USE_MOCK_AI is true: the configured provider is the mock")
    client = get_ai_client()
    if client.name != "openai" or client.model_revision != EXPECTED_MODEL:
        problems.append(f"configured provider is {client.name}/{client.model_revision}, expected openai/{EXPECTED_MODEL}")
    seller = resolve_active_seller(session)
    pilot_hashes = set(session.scalars(
        select(AIOutput.seller_profile_content_hash)
        .join(AnnotationCandidate, AnnotationCandidate.source_output_id == AIOutput.id)
        .where(AnnotationCandidate.queue == "pilot-v1", AIOutput.seller_profile_content_hash.is_not(None))
    ))
    if seller is None:
        problems.append("no active seller revision")
    elif pilot_hashes != {seller.content_hash}:
        problems.append("active seller revision differs from the one the pilot used")
    counts = dict(session.execute(
        select(AnnotationCandidate.queue, func.count()).where(AnnotationCandidate.queue.in_(ALLOWED_QUEUES))
        .group_by(AnnotationCandidate.queue)).all())
    pending = session.scalar(select(func.count()).select_from(AnnotationCandidate).where(
        AnnotationCandidate.queue.in_(ALLOWED_QUEUES), AnnotationCandidate.source_output_id.is_(None))) or 0
    return {
        "problems": problems,
        "provider": {"name": client.name, "model_revision": client.model_revision, "key_present": bool(settings.openai_api_key)},
        "seller": {"version": seller.version, "kind": seller.kind, "content_hash": seller.content_hash} if seller else None,
        "queues": counts, "pending_generation": pending,
        "budget": BUDGET.__dict__, "ledger": LEDGER, "ledger_totals": Ledger(LEDGER, BUDGET).totals(),
        "expected_cost_usd_at_pilot_average": round(pending * (1_740 * INPUT_USD_PER_M + 300 * OUTPUT_USD_PER_M) / 1e6, 4),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("preflight", "execute"))
    args = parser.parse_args()
    session = get_sessionmaker()()
    try:
        pre = preflight(session)
        print(json.dumps({"preflight": pre}, indent=2, default=str))
        if pre["problems"]:
            return 2
        if args.command == "preflight":
            return 0
        result = run(session, get_ai_client(), ALLOWED_QUEUES, BUDGET, Ledger(LEDGER, BUDGET))
        print(json.dumps({"result": result}, indent=2, default=str))
        return 0 if not result["unresolved"] else 1
    finally:
        session.close()


if __name__ == "__main__":
    sys.exit(main())
