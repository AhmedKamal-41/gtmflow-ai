"""Phase 7: dataset build, dedup/weighting, checks, held-out queues, metrics."""
from __future__ import annotations

import argparse
import copy
import json
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from app import dataset_cli
from app.ai.mock_client import MockAIClient
from app.core.hashing import content_hash
from app.datasets import checks, dedup, pilot
from app.evaluation import metrics
from app.models import AIOutput, AnnotationCandidate, CompanySplitAssignment
from app.services import splits
from tests.conftest import SYNTHETIC_SELLER_PROFILE, save_and_activate
from tests.test_annotation import ASSESSED, seed_cohort

DEMO = dict(SYNTHETIC_SELLER_PROFILE, profile_kind="demo")
FROZEN_CRITERIA_SHA256 = "1b5d3779a94dea439135911a5219d0fe1159654142ee37d48a4250ab15ada0f6"


def _submit(client, cid, detail, **fields):
    body = {"submission_id": str(uuid4()), "source_output_id": detail["source_output"]["id"],
            "source_content_hash": detail["source_output"]["content_hash"], **fields}
    response = client.post(f"/api/annotation/candidates/{cid}/annotations", json=body)
    assert response.status_code == 201, response.text


def _ai_row(detail, position, decision, target=None, uncertain=False):
    source = detail["source_output"]
    target = target if target is not None else source["content"]
    return {
        "position": position, "candidate_id": detail["id"], "source_output_id": source["id"],
        "source_content_hash": source["content_hash"], "input_hash": source["input_hash"],
        "decision": decision, "target": target, "target_content_hash": content_hash(target),
        "reviewer_model": "claude-opus-5-5", "decision_batch": "batch-test.json",
        "reviewed_at": "2026-09-24T00:00:00+00:00", "reason": "test", "uncertain": uncertain,
        "uncertainty_reason": "record looks misclassified" if uncertain else None,
        "assessment": {"factual_support": "supported", "writing_quality": 4, "missing_info_handling": "good"},
    }


def _demo_outreach(detail):
    name = next(f["value"] for f in detail["source_output"]["input_snapshot"]["lead_facts"]
                if f["field"] == "company_name")
    content = copy.deepcopy(detail["source_output"]["content"])
    content["email_body"] = (
        f"Hi there,\n\nThe supplied company record lists {name} as an organization.\n\n"
        "GTMFlow is a portfolio demonstration. This message illustrates that workflow and is not a "
        "commercial offer.\n\nBest regards,\nGTMFlow (demonstration)")
    content["subject"] = f"GTMFlow workflow demonstration for {name}"
    content["call_note"] = ""
    return content


@pytest.fixture()
def reviewed(client, db_session):
    """Pilot of 12 candidates: #1 human accepted, #2 human skipped, #3 human
    accepted then corrected, #4/#5/#6/#7 AI reviewed (#7 uncertain), rest
    unreviewed. Returns (details by position, ai rows)."""
    seed_cohort(db_session)
    splits.freeze_manifest(db_session)
    splits.create_pilot_queue(db_session, max_examples=12)
    db_session.commit()
    save_and_activate(client, DEMO)
    items = client.get("/api/annotation/queues/pilot-v1/candidates").json()["items"]
    details = {}
    for c in items:
        details[c["position"]] = client.post(
            f"/api/annotation/candidates/{c['id']}/generate", json={"provider": "mock"}).json()
    d = details
    _submit(client, d[1]["id"], d[1], decision="accepted", **ASSESSED)
    _submit(client, d[2]["id"], d[2], decision="skipped", skip_reason="sparse")
    _submit(client, d[3]["id"], d[3], decision="accepted", **ASSESSED)
    corrected = copy.deepcopy(d[3]["source_output"]["content"])
    corrected["company_summary"] = "A human-corrected summary that restates only the record."
    _submit(client, d[3]["id"], d[3], decision="corrected", corrected_content=corrected,
            **dict(ASSESSED, factual_support="partially_supported"))
    ai = [
        _ai_row(d[4], 4, "corrected", _demo_outreach(d[4])),
        _ai_row(d[5], 5, "accepted"),
        _ai_row(d[6], 6, "corrected", _demo_outreach(d[6])),
        _ai_row(d[7], 7, "accepted", uncertain=True),
        _ai_row(d[1], 1, "corrected", _demo_outreach(d[1])),  # human wins
    ]
    return details, ai


def test_build_combines_sources_with_precedence_and_exclusions(db_session, reviewed):
    details, ai = reviewed
    built = pilot.build(db_session, "pilot-v1", ai)
    by_pos = {e["position"]: e for e in built["examples"]}
    assert sorted(by_pos) == [1, 3, 4, 5, 6, 7]
    assert by_pos[1]["review_source"] == "human" and by_pos[1]["review_decision"] == "accepted"
    # #3: the latest human decision (the correction) wins over the earlier acceptance.
    assert by_pos[3]["review_decision"] == "corrected"
    assert by_pos[3]["target"]["company_summary"] == "A human-corrected summary that restates only the record."
    assert by_pos[3]["target_output_id"] and by_pos[3]["human_verified"] is True
    assert by_pos[4]["review_source"] == "ai" and by_pos[4]["human_verified"] is False
    assert by_pos[4]["review_timing"] is None and by_pos[4]["target_origin"] == "ai_corrected"
    assert by_pos[7]["uncertain"] is True
    reasons = {(x["position"], x["review_source"]): x["reason"] for x in built["excluded"]}
    assert reasons[(2, "human")] == "skipped by the human reviewer"
    assert reasons[(1, "ai")] == "superseded by a human review of the same candidate"
    assert built["unreviewed_positions"] == list(range(8, 13))
    for e in built["examples"]:  # original output, source ids and versions preserved
        source = db_session.get(AIOutput, UUID(e["source_output_id"]))
        assert e["source_output"] == source.content and e["input_hash"] == source.input_hash
        assert e["prompt_version"] == source.prompt_version and e["seller_profile_kind"] == "demo"


def test_ai_row_not_pinned_to_the_stored_output_is_excluded(db_session, reviewed):
    details, ai = reviewed
    stale = dict(ai[1], source_content_hash="f" * 64)
    built = pilot.build(db_session, "pilot-v1", [stale])
    assert [e["position"] for e in built["examples"] if e["review_source"] == "ai"] == []
    assert built["excluded"][-1]["reason"] == "AI row is not pinned to the candidate's stored output/input"


def test_checks_pass_and_catch_tampering(db_session, reviewed):
    details, ai = reviewed
    examples = dedup.apply(pilot.build(db_session, "pilot-v1", ai)["examples"])["kept"]
    assert checks.run_all(db_session, {"all": examples}) == {"schema": [], "provenance": [], "duplicates": [], "leakage": []}

    bad = copy.deepcopy(examples)
    bad[0]["target"]["company_summary"] = "Tampered after hashing."
    assert checks.check_schema(bad)
    bad = copy.deepcopy(examples)
    next(e for e in bad if e["review_source"] == "ai")["human_verified"] = True
    assert any("human-verified" in p for p in checks.check_provenance(db_session, bad))
    bad = copy.deepcopy(examples)
    bad[0]["prompt_version"] = "grounded-v9"
    assert any("provenance differs" in p for p in checks.check_provenance(db_session, bad))
    bad = copy.deepcopy(examples)
    bad[0]["seller_profile_kind"] = None
    assert any("partial seller provenance" in p for p in checks.check_provenance(db_session, bad))
    assert checks.check_duplicates(examples + [copy.deepcopy(examples[0])])
    bad = copy.deepcopy(examples)
    bad[0]["split"] = "validation"
    assert any("differs from the frozen manifest" in p for p in checks.check_leakage(db_session, {"x": bad}))


def test_leakage_check_detects_a_group_shared_with_a_held_out_queue(db_session, reviewed):
    details, ai = reviewed
    examples = pilot.build(db_session, "pilot-v1", ai)["examples"]
    first = db_session.scalars(select(AnnotationCandidate).where(AnnotationCandidate.position == 1)).first()
    db_session.add(AnnotationCandidate(queue="validation-v1", position=1, manifest_version=first.manifest_version,
                                       lead_id=first.lead_id, group_key=first.group_key, split="validation",
                                       task="company_summary"))
    db_session.commit()
    assert any("appear in a validation/test queue" in p for p in checks.check_leakage(db_session, {"x": examples}))


def test_dedup_weights_repeated_structures_without_changing_counts(db_session, reviewed):
    details, ai = reviewed
    examples = pilot.build(db_session, "pilot-v1", ai)["examples"]
    result = dedup.apply(examples, cap=1)
    kept = {e["position"]: e for e in result["kept"]}
    assert len(kept) == len(examples)  # nothing dropped, nothing added
    # #4 and #6 differ only in the company name -> one structure group of 2.
    assert kept[4]["structure_signature"] == kept[6]["structure_signature"]
    assert kept[4]["structure_group_size"] == 2 and kept[4]["weight"] == 0.5
    # cap=1: each structure group contributes exactly one effective example.
    signatures = {e["structure_signature"] for e in kept.values()}
    assert result["report"]["effective_examples"] == pytest.approx(len(signatures))
    assert len(signatures) < len(examples)
    dup = copy.deepcopy(examples[0])
    dup["position"] = 999
    assert dedup.apply(examples + [dup])["report"]["exact_duplicates_dropped"] == 1


def test_cli_build_and_check_are_reproducible(db_session, reviewed, tmp_path):
    details, ai = reviewed
    ai_path = tmp_path / "ai.jsonl"
    ai_path.write_text("".join(json.dumps(r) + "\n" for r in ai))
    hashes = []
    for run in ("a", "b"):
        out = tmp_path / run
        args = argparse.Namespace(queue="pilot-v1", ai_export=str(ai_path), out_dir=str(out), cap=5)
        assert dataset_cli.cmd_build(args, db_session) == 0
        manifest = json.loads((out / "dataset-manifest.json").read_text())
        hashes.append(manifest["files"])
        assert dataset_cli.cmd_check(argparse.Namespace(dir=str(out)), db_session) == 0
    assert hashes[0] == hashes[1]
    counts = manifest["counts"]
    assert counts["eligible"]["examples"] == 5 and counts["flagged_uncertain"]["examples"] == 1
    assert counts["eligible"]["by_review_source"] == {"human": 2, "ai": 3}
    assert manifest["allowed_use"] == "training"
    (tmp_path / "b" / "eligible.jsonl").write_text("tampered\n{}\n")
    with pytest.raises(Exception):
        dataset_cli.cmd_check(argparse.Namespace(dir=str(tmp_path / "b")), db_session)


def test_cli_evaluate_labels_in_sample_runs(db_session, reviewed, tmp_path):
    details, ai = reviewed
    ai_path = tmp_path / "ai.jsonl"
    ai_path.write_text("".join(json.dumps(r) + "\n" for r in ai))
    out = tmp_path / "ds"
    dataset_cli.cmd_build(argparse.Namespace(queue="pilot-v1", ai_export=str(ai_path), out_dir=str(out), cap=5), db_session)
    for system in ("source", "mock"):
        result_path = tmp_path / f"eval-{system}.json"
        args = argparse.Namespace(dataset=str(out / "eligible.jsonl"), system=system, out=str(result_path))
        assert dataset_cli.cmd_evaluate(args, db_session) == 0
        result = json.loads(result_path.read_text())
        assert result["held_out"] is False and result["label"].startswith("IN-SAMPLE")
        assert result["overall"]["n"] == 5
    source = json.loads((tmp_path / "eval-source.json").read_text())
    by_pos = {r["position"]: r for r in source["per_example"]}
    assert by_pos[1]["exact_match"] is True and by_pos[3]["exact_match"] is False


def test_held_out_queues_use_only_their_own_split(db_session):
    seed_cohort(db_session)
    splits.freeze_manifest(db_session)
    pilot_rows = splits.create_pilot_queue(db_session, max_examples=10)
    val = splits.create_queue(db_session, queue="validation-v1", split="validation", seed="v", max_examples=6)
    test = splits.create_queue(db_session, queue="test-v1", split="test", seed="t", max_examples=6)
    db_session.commit()
    split_of = {a.group_key: a.split for a in db_session.scalars(select(CompanySplitAssignment))}
    assert {split_of[r.group_key] for r in val} == {"validation"} == {r.split for r in val}
    assert {split_of[r.group_key] for r in test} == {"test"}
    groups = [{r.group_key for r in rows} for rows in (pilot_rows, val, test)]
    assert not (groups[0] & groups[1] or groups[0] & groups[2] or groups[1] & groups[2])
    with pytest.raises(splits.ManifestError):
        splits.create_queue(db_session, queue="validation-v1", split="validation", seed="v")
    with pytest.raises(splits.ManifestError):
        splits.create_queue(db_session, queue="x", split="holdout", seed="x")


def test_metrics_values_and_lints():
    assert metrics.token_f1("a b c", "a b c") == 1.0
    assert metrics.token_f1("a b", "c d") == 0.0
    assert metrics.rouge_l("the cat sat", "the cat sat") == 1.0
    assert 0 < metrics.rouge_l("the cat sat down", "the cat stood") < 1
    ctx = {"lead_facts": [{"id": "fact-company_name", "field": "company_name", "value": "Acme", "kind": "structured_field"},
                          {"id": "fact-website", "field": "website", "value": "acme.example", "kind": "structured_field"}],
           "unknowns": [], "seller": {"capabilities": [{"id": "cap-3", "text": "x"}], "approved_claims": []}}
    good = {"subject": "GTMFlow workflow demonstration for Acme",
            "email_body": "Hi there,\n\nGTMFlow is a portfolio demonstration and not a commercial offer.",
            "lead_facts_used": ["fact-company_name"], "capabilities_used": ["cap-3"], "claims_used": [],
            "unknowns_acknowledged": [], "call_note": "", "confidence": "low"}
    assert all(metrics.lints("outreach_email", good, ctx).values())
    bad = dict(good, email_body="Hi, My name is from GTMFlow and we specialize in outreach. Check out our website at acme.example.\\n[Your Name]")
    result = metrics.lints("outreach_email", bad, ctx)
    assert not result["demo_label"] and not result["no_placeholder"] and not result["no_escape_text"]
    assert not result["no_invented_phrasing"] and not result["no_prospect_web_as_own"]
    summary_ctx = {"lead_facts": [], "unknowns": [], "seller": None}
    s = {"company_summary": "x", "evidence": [], "unknowns": [], "hypotheses": ["The lead may be interested in B2B services"],
         "seller_relevance": None, "confidence": "low"}
    assert metrics.lints("company_summary", s, summary_ctx)["no_need_hypothesis"] is False
    agg = metrics.aggregate([{"exact_match": True, "token_f1": 1.0}, {"exact_match": False, "token_f1": 0.5}])
    assert agg == {"n": 2, "metrics_version": metrics.METRICS_VERSION, "exact_match": 0.5, "token_f1": 0.75}


def test_mock_baseline_output_is_scoreable():
    """The free mock baseline runs on any grounded input snapshot."""
    from tests.test_ai_generation import _context
    ctx = _context()
    out = MockAIClient().generate_outreach(ctx)
    result = metrics.score("outreach_email", out, out, ctx)
    assert result["exact_match"] is True and result["validator_pass"] is True


def test_queue_listing_endpoint(client, db_session):
    seed_cohort(db_session)
    splits.freeze_manifest(db_session)
    splits.create_pilot_queue(db_session, max_examples=4)
    splits.create_queue(db_session, queue="test-v1", split="test", seed="t", max_examples=2)
    splits.create_queue(db_session, queue="validation-v1", split="validation", seed="v", max_examples=2)
    db_session.commit()
    response = client.get("/api/annotation/queues")
    assert response.status_code == 200
    assert response.json() == [
        {"queue": "pilot-v1", "split": "train", "candidates": 4, "generated": 0},
        {"queue": "validation-v1", "split": "validation", "candidates": 2, "generated": 0},
        {"queue": "test-v1", "split": "test", "candidates": 2, "generated": 0},
    ]


def test_held_out_queue_builds_human_only_and_evaluates_as_held_out(client, db_session, tmp_path):
    seed_cohort(db_session)
    splits.freeze_manifest(db_session)
    splits.create_pilot_queue(db_session, max_examples=4)
    splits.create_queue(db_session, queue="validation-v1", split="validation", seed="v", max_examples=2)
    db_session.commit()
    save_and_activate(client, DEMO)
    items = client.get("/api/annotation/queues/validation-v1/candidates").json()["items"]
    for c in items:
        d = client.post(f"/api/annotation/candidates/{c['id']}/generate", json={"provider": "mock"}).json()
        _submit(client, c["id"], d, decision="accepted", **ASSESSED)
    out = tmp_path / "val"
    args = argparse.Namespace(queue="validation-v1", ai_export=None, out_dir=str(out), cap=5)
    assert dataset_cli.cmd_build(args, db_session) == 0
    manifest = json.loads((out / "dataset-manifest.json").read_text())
    assert manifest["counts"]["eligible"]["by_split"] == {"validation": 2}
    assert manifest["counts"]["eligible"]["by_review_source"] == {"human": 2}
    result_path = tmp_path / "eval.json"
    assert dataset_cli.cmd_evaluate(argparse.Namespace(dataset=str(out / "eligible.jsonl"), system="mock",
                                                       out=str(result_path)), db_session) == 0
    result = json.loads(result_path.read_text())
    assert result["held_out"] is True and result["label"] == "HELD-OUT evaluation"
    assert result["criteria_id"] == "heldout-criteria-v1" and len(result["criteria_sha256"]) == 64
    assert manifest["allowed_use"].startswith("evaluation only")
    # These predictions came from the mock, not the frozen system under test:
    # a held-out "source" evaluation of them is refused.
    args = argparse.Namespace(dataset=str(out / "eligible.jsonl"), system="source", out=str(tmp_path / "x.json"))
    assert dataset_cli.cmd_evaluate(args, db_session) == 1


def test_frozen_criteria_digest_is_pinned():
    """The held-out criteria were frozen before generation and review; any
    edit must come with a new CRITERIA_ID (and this pin updated knowingly)."""
    from app.evaluation import criteria
    assert criteria.CRITERIA_ID == "heldout-criteria-v1"
    assert criteria.criteria_digest() == FROZEN_CRITERIA_SHA256
