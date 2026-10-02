"""Phase 7 training expansion: budget-enforced paid generation for train-v2 ONLY.

Authorization (user, 2026-09-25): at most 500 paid attempts including
retries and a $0.30 cap, whichever comes first; candidates from additional
companies in the frozen training split only; start with about 450
candidates and use reserve candidates only if needed; stop generating once
400 eligible training examples are reached. At most one retry per candidate.

    python scripts/phase7_generate_train.py create-queue       # no provider call
    python scripts/phase7_generate_train.py preflight           # no provider call, no writes
    python scripts/phase7_generate_train.py execute --through-position 450

Run from backend/ with PYTHONPATH=. The key is read from settings and never
printed. The ledger carries attempts and spend across runs.
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
from app.services import splits
from app.services.ai_generation import resolve_active_seller
from app.services.budgeted_generation import Budget, Ledger, run

QUEUE, SEED = splits.TRAIN_EXPANSION_QUEUE
QUEUE_SIZE = 520  # 260 companies: ~450 first-wave candidates + reserves
LEDGER = "data/generation_ledgers/train-v2.jsonl"
# gpt-4o-mini standard tier, verified 2026-09-24 at developers.openai.com/api/docs/pricing
# (re-checked 2026-09-25 before this run): $0.15 / 1M input, $0.60 / 1M output.
INPUT_USD_PER_M = 0.15
OUTPUT_USD_PER_M = 0.60
WORST_CASE_CALL_USD = round((8_000 * INPUT_USD_PER_M + 16_384 * OUTPUT_USD_PER_M) / 1e6, 6)
BUDGET = Budget(max_attempts=500, cap_usd=0.30, input_usd_per_m=INPUT_USD_PER_M,
                output_usd_per_m=OUTPUT_USD_PER_M, worst_case_call_usd=WORST_CASE_CALL_USD)
EXPECTED_MODEL = "gpt-4o-mini"


def preflight(session) -> dict:
    problems = []
    if settings.use_mock_ai:
        problems.append("USE_MOCK_AI is true: the configured provider is the mock")
    client = get_ai_client()
    if client.name != "openai" or client.model_revision != EXPECTED_MODEL:
        problems.append(f"configured provider is {client.name}/{client.model_revision}")
    seller = resolve_active_seller(session)
    pilot_hashes = set(session.scalars(
        select(AIOutput.seller_profile_content_hash)
        .join(AnnotationCandidate, AnnotationCandidate.source_output_id == AIOutput.id)
        .where(AnnotationCandidate.queue == "pilot-v1", AIOutput.seller_profile_content_hash.is_not(None))))
    if seller is None or pilot_hashes != {seller.content_hash}:
        problems.append("active seller revision is missing or differs from the pilot's")
    splits_in_queue = set(session.scalars(
        select(AnnotationCandidate.split).where(AnnotationCandidate.queue == QUEUE).distinct()))
    if splits_in_queue != {"train"}:
        problems.append(f"{QUEUE} must contain only train-split candidates, has {sorted(splits_in_queue)}")
    total = session.scalar(select(func.count()).select_from(AnnotationCandidate).where(AnnotationCandidate.queue == QUEUE)) or 0
    pending = session.scalar(select(func.count()).select_from(AnnotationCandidate).where(
        AnnotationCandidate.queue == QUEUE, AnnotationCandidate.source_output_id.is_(None))) or 0
    return {"problems": problems,
            "provider": {"name": client.name, "model_revision": client.model_revision,
                         "key_present": bool(settings.openai_api_key)},
            "seller": {"version": seller.version, "kind": seller.kind} if seller else None,
            "queue": QUEUE, "candidates": total, "pending_generation": pending,
            "budget": BUDGET.__dict__, "ledger": LEDGER, "ledger_totals": Ledger(LEDGER, BUDGET).totals()}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("create-queue", "preflight", "execute"))
    parser.add_argument("--through-position", type=int)
    args = parser.parse_args()
    session = get_sessionmaker()()
    try:
        if args.command == "create-queue":
            rows = splits.create_queue(session, queue=QUEUE, split="train", seed=SEED, max_examples=QUEUE_SIZE)
            session.commit()
            print(json.dumps({"queue": QUEUE, "examples": len(rows), "companies": len({r.lead_id for r in rows})}))
            return 0
        pre = preflight(session)
        print(json.dumps({"preflight": pre}, indent=2, default=str))
        if pre["problems"]:
            return 2
        if args.command == "preflight":
            return 0
        if not args.through_position:
            print("execute needs --through-position", file=sys.stderr)
            return 2
        result = run(session, get_ai_client(), [QUEUE], BUDGET, Ledger(LEDGER, BUDGET),
                     max_position=args.through_position)
        print(json.dumps({"result": result}, indent=2, default=str))
        return 0 if not result["unresolved"] else 1
    finally:
        session.close()


if __name__ == "__main__":
    sys.exit(main())
