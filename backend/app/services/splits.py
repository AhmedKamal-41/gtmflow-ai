"""Phase 6: frozen company-group split manifest and pilot selection.

Split isolation works on *groups*, not rows. Leads are joined into one group
when they share any of these keys (union-find):

* the same company identity;
* the same website domain (identities are NOT merged -- two businesses can
  share a domain -- but for leakage purposes they are kept on one side);
* the same LinkedIn URL;
* the same normalized company name (lowercase alphanumerics, common legal
  suffixes removed), a conservative alias rule.

Over-grouping is the safe failure: it can only keep more companies together.
Each group's split is a pure function of the seed and a group key derived
from its members' source record ids, so recomputing the manifest from the
same cohort gives the same assignment regardless of row order or database
UUIDs. A manifest version is written once and never rewritten.
"""
from __future__ import annotations

import hashlib
import re
from collections import Counter, defaultdict
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    AnnotationCandidate,
    CompanyIdentity,
    CompanySplitAssignment,
    Lead,
    SplitManifest,
    WorkflowEvent,
)
from app.models.annotation import SPLIT_TEST, SPLIT_TRAIN, SPLIT_VALIDATION, TASK_OUTREACH, TASK_SUMMARY
from app.scoring import fit

MANIFEST_VERSION = "company-groups-v1"
SEED = "gtmflow-phase6-split-2026-09-23"
ALGORITHM = "union-find(identity,domain,linkedin,normalized-name)+sha256(seed:group)%6"
# 4:1:1 matches the experiment target of 400 / 100 / 100 examples.
BUCKETS = [SPLIT_TRAIN] * 4 + [SPLIT_VALIDATION, SPLIT_TEST]
RATIOS = {SPLIT_TRAIN: 4, SPLIT_VALIDATION: 1, SPLIT_TEST: 1}
EXPERIMENT_TARGETS = {SPLIT_TRAIN: 400, SPLIT_VALIDATION: 100, SPLIT_TEST: 100}

PILOT_QUEUE = "pilot-v1"
PILOT_SEED = "gtmflow-phase6-pilot-2026-09-23"
PILOT_TASKS = (TASK_SUMMARY, TASK_OUTREACH)
PILOT_MAX_EXAMPLES = 100

_LEGAL_SUFFIXES = re.compile(
    r"\b(inc|incorporated|llc|l\.l\.c|ltd|limited|corp|corporation|co|company|pllc|pc|p\.c|lp|llp)\b\.?",
    re.IGNORECASE,
)


class ManifestError(RuntimeError):
    pass


def normalize_name(name: str | None) -> str | None:
    if not name:
        return None
    stripped = _LEGAL_SUFFIXES.sub(" ", name.lower())
    key = re.sub(r"[^a-z0-9]", "", stripped)
    return key or None


def normalize_domain(value: str | None) -> str | None:
    if not value:
        return None
    value = value.strip().lower()
    value = re.sub(r"^[a-z]+://", "", value)
    value = value.split("/")[0].split("?")[0]
    value = value.removeprefix("www.")
    return value or None


def normalize_linkedin(value: str | None) -> str | None:
    if not value:
        return None
    value = re.sub(r"^[a-z]+://", "", value.strip().lower()).removeprefix("www.")
    return value.rstrip("/") or None


def _lead_keys(lead: Lead, identity: CompanyIdentity | None) -> list[str]:
    raw = lead.source_raw_data if isinstance(lead.source_raw_data, dict) else {}
    cleaned = lead.cleaned_data if isinstance(lead.cleaned_data, dict) else {}
    keys = []
    if lead.company_identity_id:
        keys.append(f"identity:{lead.company_identity_id}")
    domain = normalize_domain(identity.website_domain if identity else None) or normalize_domain(lead.website)
    if domain:
        keys.append(f"domain:{domain}")
    linkedin = normalize_linkedin(raw.get("linkedin_url") or cleaned.get("linkedin_url"))
    if linkedin:
        keys.append(f"linkedin:{linkedin}")
    name = normalize_name(lead.company_name)
    if name:
        keys.append(f"name:{name}")
    return keys


def _stable_member_id(lead: Lead) -> str:
    """Stable across databases: the provider's record id, not a row UUID.
    The same record id in a later snapshot joins the same group, which is
    the conservative direction for leakage."""
    return f"record:{lead.source_record_id}" if lead.source_record_id else f"lead:{lead.id}"


def compute_assignments(session: Session) -> list[dict[str, Any]]:
    """Group and assign every imported (snapshot-backed) lead. Pure given the
    cohort: no randomness beyond the fixed seed, independent of row order."""
    leads = list(session.scalars(select(Lead).where(Lead.source_snapshot_id.is_not(None))))
    identities = {
        i.id: i for i in session.scalars(
            select(CompanyIdentity).where(CompanyIdentity.id.in_([l.company_identity_id for l in leads if l.company_identity_id]))
        )
    }
    parent = list(range(len(leads)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    first_with_key: dict[str, int] = {}
    for index, lead in enumerate(leads):
        for key in _lead_keys(lead, identities.get(lead.company_identity_id)):
            if key in first_with_key:
                a, b = find(index), find(first_with_key[key])
                if a != b:
                    parent[a] = b
            else:
                first_with_key[key] = index

    members: dict[int, list[Lead]] = defaultdict(list)
    for index, lead in enumerate(leads):
        members[find(index)].append(lead)

    rows = []
    for group in members.values():
        member_ids = sorted(_stable_member_id(lead) for lead in group)
        group_key = hashlib.sha256("|".join(member_ids).encode()).hexdigest()
        bucket = int(hashlib.sha256(f"{SEED}:{group_key}".encode()).hexdigest(), 16) % len(BUCKETS)
        for lead in group:
            rows.append({
                "lead_id": lead.id,
                "company_identity_id": lead.company_identity_id,
                "source_record_id": lead.source_record_id,
                "segment": (lead.cleaned_data or {}).get("candidate_segment") if isinstance(lead.cleaned_data, dict) else None,
                "group_key": group_key,
                "group_size": len(group),
                "split": BUCKETS[bucket],
            })
    rows.sort(key=lambda r: (r["group_key"], str(r["source_record_id"]), str(r["lead_id"])))
    return rows


def manifest_digest(rows: list[dict[str, Any]]) -> str:
    lines = sorted(f"{r['source_record_id']}|{r['group_key']}|{r['split']}" for r in rows)
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def _counts(rows: list[dict[str, Any]]) -> dict[str, Any]:
    leads = Counter(r["split"] for r in rows)
    groups = Counter()
    seen = set()
    for r in rows:
        if r["group_key"] not in seen:
            seen.add(r["group_key"])
            groups[r["split"]] += 1
    by_segment: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        by_segment[r["split"]][r["segment"] or "unknown"] += 1
    return {
        "leads_by_split": dict(leads),
        "groups_by_split": dict(groups),
        "leads_by_split_and_segment": {k: dict(v) for k, v in by_segment.items()},
        "multi_lead_groups": len({r["group_key"] for r in rows if r["group_size"] > 1}),
        "leads_in_multi_lead_groups": sum(1 for r in rows if r["group_size"] > 1),
        "experiment_targets_examples": EXPERIMENT_TARGETS,
    }


def freeze_manifest(session: Session, version: str = MANIFEST_VERSION) -> SplitManifest:
    """Write the manifest once. Refuses to rewrite an existing version."""
    if session.get(SplitManifest, version) is not None:
        raise ManifestError(f"Split manifest '{version}' already exists and is frozen; it is never rewritten.")
    rows = compute_assignments(session)
    if not rows:
        raise ManifestError("No imported (snapshot-backed) leads to assign.")
    manifest = SplitManifest(
        version=version,
        seed=SEED,
        algorithm=ALGORITHM,
        ratios=RATIOS,
        lead_count=len(rows),
        group_count=len({r["group_key"] for r in rows}),
        counts=_counts(rows),
        manifest_sha256=manifest_digest(rows),
    )
    session.add(manifest)
    session.flush()
    session.add_all(
        CompanySplitAssignment(
            manifest_version=version,
            lead_id=r["lead_id"],
            company_identity_id=r["company_identity_id"],
            group_key=r["group_key"],
            split=r["split"],
        )
        for r in rows
    )
    session.flush()
    session.add(WorkflowEvent(event_type="split_manifest_frozen", event_data={
        "manifest_version": version, "seed": SEED, "lead_count": manifest.lead_count,
        "group_count": manifest.group_count, "manifest_sha256": manifest.manifest_sha256,
    }))
    return manifest


def verify_manifest(session: Session, version: str = MANIFEST_VERSION) -> dict[str, Any]:
    """Recompute from the current cohort and compare with what is stored."""
    manifest = session.get(SplitManifest, version)
    if manifest is None:
        raise ManifestError(f"Split manifest '{version}' does not exist.")
    stored = {
        a.lead_id: (a.group_key, a.split)
        for a in session.scalars(select(CompanySplitAssignment).where(CompanySplitAssignment.manifest_version == version))
    }
    rows = compute_assignments(session)
    recomputed = {r["lead_id"]: (r["group_key"], r["split"]) for r in rows}
    return {
        "manifest_version": version,
        "stored_sha256": manifest.manifest_sha256,
        "recomputed_sha256": manifest_digest(rows),
        "stored_leads": len(stored),
        "recomputed_leads": len(recomputed),
        "mismatched_leads": sum(1 for k, v in recomputed.items() if stored.get(k) != v),
        "leads_missing_from_manifest": len(set(recomputed) - set(stored)),
        "groups_spanning_splits": _groups_spanning_splits(session, version),
        "matches": manifest_digest(rows) == manifest.manifest_sha256 and stored == recomputed,
    }


def _groups_spanning_splits(session: Session, version: str) -> int:
    splits_by_group: dict[str, set[str]] = defaultdict(set)
    for group_key, split in session.execute(
        select(CompanySplitAssignment.group_key, CompanySplitAssignment.split)
        .where(CompanySplitAssignment.manifest_version == version)
    ):
        splits_by_group[group_key].add(split)
    return sum(1 for s in splits_by_group.values() if len(s) > 1)


def create_pilot_queue(
    session: Session,
    version: str = MANIFEST_VERSION,
    queue: str = PILOT_QUEUE,
    max_examples: int = PILOT_MAX_EXAMPLES,
) -> list[AnnotationCandidate]:
    """Reserve up to `max_examples` (company, task) candidates from
    TRAINING groups only: one company per group, both tasks per company,
    segments alternated, groups ordered by a seeded hash. Leads currently
    excluded from routing are skipped. Refuses to rebuild an existing queue."""
    if session.get(SplitManifest, version) is None:
        raise ManifestError(f"Split manifest '{version}' does not exist. Freeze it first.")
    if session.scalar(select(AnnotationCandidate.id).where(AnnotationCandidate.queue == queue).limit(1)):
        raise ManifestError(f"Annotation queue '{queue}' already exists; it is never rebuilt.")
    companies_needed = max_examples // len(PILOT_TASKS)

    assignments = session.execute(
        select(CompanySplitAssignment, Lead)
        .join(Lead, Lead.id == CompanySplitAssignment.lead_id)
        .where(CompanySplitAssignment.manifest_version == version, CompanySplitAssignment.split == SPLIT_TRAIN)
    ).all()
    by_group: dict[str, list[Lead]] = defaultdict(list)
    for assignment, lead in assignments:
        by_group[assignment.group_key].append(lead)

    def order(key: str) -> str:
        return hashlib.sha256(f"{PILOT_SEED}:{key}".encode()).hexdigest()

    by_segment: dict[str, list[tuple[str, Lead]]] = defaultdict(list)
    for group_key in sorted(by_group, key=order):
        candidates = sorted(by_group[group_key], key=lambda l: order(_stable_member_id(l)))
        lead = next((l for l in candidates if not fit.compute_eligibility(l).excluded), None)
        if lead is None:
            continue
        segment = (lead.cleaned_data or {}).get("candidate_segment", "unknown") if isinstance(lead.cleaned_data, dict) else "unknown"
        by_segment[segment].append((group_key, lead))

    picked: list[tuple[str, Lead]] = []
    segments = sorted(by_segment)
    index = 0
    while len(picked) < companies_needed and any(by_segment[s] for s in segments):
        segment = segments[index % len(segments)]
        if by_segment[segment]:
            picked.append(by_segment[segment].pop(0))
        index += 1

    rows = []
    position = 0
    for group_key, lead in picked:
        for task in PILOT_TASKS:
            position += 1
            rows.append(AnnotationCandidate(
                queue=queue, position=position, manifest_version=version, lead_id=lead.id,
                group_key=group_key, split=SPLIT_TRAIN, task=task,
            ))
    session.add_all(rows)
    session.flush()
    session.add(WorkflowEvent(event_type="annotation_queue_created", event_data={
        "queue": queue, "manifest_version": version, "examples": len(rows),
        "unique_companies": len(picked), "pilot_seed": PILOT_SEED,
    }))
    return rows


def assignment_rows_for_export(session: Session, version: str) -> list[dict[str, Any]]:
    out = []
    for a, lead in session.execute(
        select(CompanySplitAssignment, Lead)
        .join(Lead, Lead.id == CompanySplitAssignment.lead_id)
        .where(CompanySplitAssignment.manifest_version == version)
        .order_by(CompanySplitAssignment.group_key, Lead.source_record_id)
    ):
        out.append({
            "manifest_version": version, "lead_id": str(a.lead_id),
            "company_identity_id": str(a.company_identity_id) if a.company_identity_id else None,
            "source_record_id": lead.source_record_id, "group_key": a.group_key, "split": a.split,
        })
    return out


def lead_split(session: Session, version: str, lead_id: UUID) -> str | None:
    return session.scalar(
        select(CompanySplitAssignment.split).where(
            CompanySplitAssignment.manifest_version == version,
            CompanySplitAssignment.lead_id == lead_id,
        )
    )
