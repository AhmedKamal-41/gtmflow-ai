"""Formatting helpers for held-out AI-review decision files (not a reviewer).

Every decision is made per candidate by the AI reviewer after reading the
candidate's stored input snapshot and output (scripts/phase6_ai_review.py
dump). These helpers only:
  * pin each decision to that candidate's stored output id and content hash
    (outputs are immutable and never regenerated);
  * format a corrected demonstration outreach exactly as the pilot's
    ai-review-rubric-v1 corrections did (review_helpers.outreach);
  * write batch files. The build step re-verifies every pin and validates
    every corrected target against the recorded input snapshot.
"""
from __future__ import annotations

import importlib.util
import json
import os
from datetime import datetime, timezone

from sqlalchemy import select

from app.core.database import get_sessionmaker
from app.core.hashing import content_hash
from app.models import AIOutput, AnnotationCandidate

_spec = importlib.util.spec_from_file_location(
    "review_helpers", os.path.join(os.path.dirname(__file__), "pilot-v1", "review_helpers.py"))
_rh = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_rh)
outreach = _rh.outreach

RUBRIC = "ai-review-rubric-v1"
REVIEWER = "ai:claude-opus-5-5"
NEUTRAL_RELEVANCE = ("Compared only with the demonstration profile's stated target customers; "
                     "the record shows no interest, need or budget.")
PERSON_NAME = ("The company name is a person's name; the record may describe an individual's profile "
               "rather than an organization.")


class Reviewer:
    def __init__(self, queue: str):
        self.queue = queue
        self.reviews: list[dict] = []
        session = get_sessionmaker()()
        try:
            self._pins = {}
            for c in session.scalars(select(AnnotationCandidate).where(AnnotationCandidate.queue == queue)):
                if c.source_output_id is not None:
                    o = session.get(AIOutput, c.source_output_id)
                    self._pins[c.position] = (str(o.id), content_hash(o.content), c.task)
        finally:
            session.close()

    def _base(self, pos, decision, support, writing, missing, reason, issues, uncertain):
        output_id, chash, _task = self._pins[pos]
        r = {"position": pos, "source_output_id": output_id, "source_content_hash": chash,
             "decision": decision, "factual_support": support, "writing_quality": writing,
             "missing_info_handling": missing, "reason": reason, "issues": issues or [],
             "uncertain": bool(uncertain), "uncertainty_reason": uncertain or None}
        self.reviews.append(r)
        return r

    def accept(self, pos, reason, writing=4, support="supported", missing="good", uncertain=None):
        return self._base(pos, "accepted", support, writing, missing, reason, [], uncertain)

    def correct(self, pos, fields, reason, issues, support="partially_supported", writing=3,
                missing="acceptable", uncertain=None):
        r = self._base(pos, "corrected", support, writing, missing, reason, issues, uncertain)
        r["corrected_fields"] = fields
        return r

    def fix_outreach(self, pos, name, descriptor, place, facts, reason, issues, uncertain=None,
                     support="unsupported", writing=2, missing="poor"):
        return self.correct(pos, outreach(name, descriptor, facts, place), reason, issues,
                            support=support, writing=writing, missing=missing, uncertain=uncertain)

    def write(self, batch: str) -> str:
        path = os.path.join(os.path.dirname(__file__), self.queue, "decisions", f"{batch}.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if os.path.exists(path):
            raise FileExistsError(path)
        stamp = datetime.now(timezone.utc).isoformat()
        for r in self.reviews:
            r.setdefault("reviewed_at", stamp)
        with open(path, "w") as f:
            json.dump({"reviewer": REVIEWER, "rubric": RUBRIC, "queue": self.queue, "reviews": self.reviews}, f, indent=1)
        return path
