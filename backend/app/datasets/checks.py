"""Phase 7: independent dataset checks. Each returns a list of problems
(empty = pass). Read-only against the database.

* schema      -- every target passes the versioned v2 validator against its
                 recorded input snapshot; hashes match content.
* provenance  -- required provenance fields present; review source is
                 human or ai; AI rows are never marked human-verified; the
                 source output, input hash and seller revision match the DB.
* duplicates  -- unique example ids, target hashes, (task, input) pairs.
* leakage     -- every example's lead is assigned in the frozen manifest to
                 the example's split and group; no company group appears in
                 more than one split across the given example sets; no
                 train example's group appears in a validation/test queue.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.grounding import AIOutputValidationError, describe_details, validate_outreach, validate_summary
from app.core.hashing import content_hash
from app.models import AIOutput, AnnotationCandidate, CompanySplitAssignment

REQUIRED = (
    "example_id", "task", "split", "manifest_version", "company_group_key", "lead_id", "source_record_id",
    "input_snapshot", "input_hash", "source_output_id", "source_content_hash", "source_output",
    "prompt_version", "output_schema_version",
    "model_used", "model_revision", "review_source", "reviewer", "review_decision", "reviewed_at",
    "target", "target_content_hash",
)
# Seller provenance is all-or-none: an output generated with no active seller
# profile (pilot #1) legitimately has none, and its input snapshot has no
# seller context. Presence must match the stored output (checked below).
SELLER_FIELDS = ("seller_profile_id", "seller_profile_version", "seller_profile_content_hash", "seller_profile_kind")


def check_schema(examples: list[dict[str, Any]]) -> list[str]:
    problems = []
    for e in examples:
        validate = validate_summary if e["task"] == "company_summary" else validate_outreach
        try:
            validated = validate(e["target"], e["input_snapshot"])
        except AIOutputValidationError as err:
            problems.append(f"#{e['position']}: target fails validation: {describe_details(err.details)}")
            continue
        if content_hash(validated) != e["target_content_hash"] or content_hash(e["target"]) != e["target_content_hash"]:
            problems.append(f"#{e['position']}: target_content_hash does not match the target")
        if content_hash(e["source_output"]) != e["source_content_hash"]:
            problems.append(f"#{e['position']}: source_content_hash does not match the source output")
    return problems


def check_provenance(session: Session, examples: list[dict[str, Any]]) -> list[str]:
    problems = []
    for e in examples:
        missing = [k for k in REQUIRED if e.get(k) in (None, "")]
        if missing:
            problems.append(f"#{e['position']}: missing provenance {missing}")
        present = [e.get(k) not in (None, "") for k in SELLER_FIELDS]
        if any(present) and not all(present):
            problems.append(f"#{e['position']}: partial seller provenance")
        if all(present) != bool(e["input_snapshot"].get("seller")):
            problems.append(f"#{e['position']}: seller provenance does not match the input snapshot")
        if e.get("review_source") not in ("human", "ai"):
            problems.append(f"#{e['position']}: unknown review_source")
        if e.get("review_source") == "ai" and e.get("human_verified") is not False:
            problems.append(f"#{e['position']}: AI review marked human-verified")
        if e.get("review_decision") not in ("accepted", "corrected"):
            problems.append(f"#{e['position']}: ineligible decision {e.get('review_decision')}")
        source = session.get(AIOutput, UUID(str(e["source_output_id"])))
        if source is None:
            problems.append(f"#{e['position']}: source output missing from DB")
            continue
        if (content_hash(source.content) != e["source_content_hash"] or source.input_hash != e["input_hash"]
                or source.seller_profile_content_hash != e["seller_profile_content_hash"]
                or source.prompt_version != e["prompt_version"]
                or source.output_schema_version != e["output_schema_version"]
                or source.model_revision != e["model_revision"]):
            problems.append(f"#{e['position']}: provenance differs from the stored source output")
        if e.get("review_decision") == "accepted" and e["target_content_hash"] != e["source_content_hash"]:
            problems.append(f"#{e['position']}: accepted target differs from the source output")
        if e.get("review_decision") == "corrected" and e["target_content_hash"] == e["source_content_hash"]:
            problems.append(f"#{e['position']}: corrected target identical to the source output")
    return problems


def check_duplicates(examples: list[dict[str, Any]]) -> list[str]:
    problems = []
    for label, key in (("example_id", lambda e: e["example_id"]),
                       ("target hash", lambda e: e["target_content_hash"]),
                       ("(task, input)", lambda e: (e["task"], e["input_hash"]))):
        counts = Counter(key(e) for e in examples)
        dupes = [k for k, n in counts.items() if n > 1]
        if dupes:
            problems.append(f"duplicate {label}: {len(dupes)}")
    return problems


def check_leakage(session: Session, example_sets: dict[str, list[dict[str, Any]]]) -> list[str]:
    """`example_sets` maps a name (e.g. "train-eligible") to examples."""
    problems = []
    all_examples = [e for rows in example_sets.values() for e in rows]
    if not all_examples:
        return problems
    manifests = {e["manifest_version"] for e in all_examples}
    assignments = {
        (a.manifest_version, str(a.lead_id)): a
        for a in session.scalars(
            select(CompanySplitAssignment).where(CompanySplitAssignment.manifest_version.in_(manifests))
        )
    }
    splits_by_group: dict[str, set[str]] = defaultdict(set)
    for e in all_examples:
        a = assignments.get((e["manifest_version"], e["lead_id"]))
        if a is None:
            problems.append(f"#{e['position']}: lead not in frozen manifest {e['manifest_version']}")
            continue
        if a.split != e["split"] or a.group_key != e["company_group_key"]:
            problems.append(f"#{e['position']}: split/group differs from the frozen manifest")
        splits_by_group[a.group_key].add(a.split)
    for group, splits in splits_by_group.items():
        if len(splits) > 1:
            problems.append(f"group {group[:12]} spans splits {sorted(splits)}")
    train_groups = {g for g, s in splits_by_group.items() if "train" in s}
    held_out = {
        c.group_key for c in session.scalars(
            select(AnnotationCandidate).where(AnnotationCandidate.split.in_(("validation", "test")))
        )
    }
    if train_groups & held_out:
        problems.append(f"{len(train_groups & held_out)} train groups also appear in a validation/test queue")
    return problems


def run_all(session: Session, example_sets: dict[str, list[dict[str, Any]]]) -> dict[str, list[str]]:
    everything = [e for rows in example_sets.values() for e in rows]
    return {
        "schema": check_schema(everything),
        "provenance": check_provenance(session, everything),
        "duplicates": check_duplicates(everything),
        "leakage": check_leakage(session, example_sets),
    }
