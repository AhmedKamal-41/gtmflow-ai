"""Phase 9: failure-inclusive scoring of generated predictions and the blind review protocol."""
from __future__ import annotations

import json

from app.ai.mock_client import MockAIClient
from app.evaluation import criteria, phase9
from tests.test_ai_generation import _context


def _row(example_id: str, task: str) -> dict:
    ctx = _context(task)
    mock = MockAIClient()
    target = mock.generate_company_summary(ctx) if task == "company_summary" else mock.generate_outreach(ctx)
    return {"example_id": example_id, "task": task, "input_snapshot": ctx, "target": target}


def _pred(output) -> dict:
    return {"text": json.dumps(output), "finish": "eos"}


def test_report_view_is_pinned_to_the_frozen_criteria():
    assert phase9.REPORT_VIEW["criteria_id"] == criteria.CRITERIA_ID == "heldout-criteria-v1"
    assert len(phase9.report_view_digest()) == 64
    assert "never" not in phase9.REPORT_VIEW["denominator"] and "failures" in phase9.REPORT_VIEW["denominator"]


def test_a_correct_prediction_scores_like_the_frozen_metrics():
    row = _row("a", "outreach_email")
    s = phase9.score_prediction(row, _pred(row["target"]))
    assert s["generation"] == "ok" and s["parse_ok"] and s["valid_structure"] and s["factual_support"]
    assert s["frozen_metrics"]["exact_match"] is True and s["frozen_metrics"]["validator_pass"] is True
    fenced = {"text": "```json\n" + json.dumps(row["target"]) + "\n```", "finish": "eos"}  # production parser accepts fences
    assert phase9.score_prediction(row, fenced)["valid_structure"]


def test_failed_generations_are_scored_as_failures_not_dropped():
    row = _row("a", "company_summary")
    cases = {
        "not_generated": None,
        "empty": {"text": "   ", "finish": "eos"},
        "truncated": {"text": '{"company_summary": "cut off', "finish": "length"},
        "ok": {"text": "Here is the JSON you asked for.", "finish": "eos"},  # prose, not JSON
    }
    for expected, pred in cases.items():
        s = phase9.score_prediction(row, pred)
        assert s["generation"] == expected
        assert not s["parse_ok"] and not s["valid_structure"] and not s["factual_support"]
        assert not s["missing_info_handling"]
        assert s["frozen_metrics"] == {"validator_pass": False, "exact_match": False, "token_f1": 0.0, "rouge_l": 0.0}
    agg = phase9.aggregate([phase9.score_prediction(row, p) for p in cases.values()]
                           + [phase9.score_prediction(row, _pred(row["target"]))])
    assert agg["n"] == 5 and agg["valid_structure"] == 0.2  # 1 of 5: failures stay in the denominator
    assert agg["generation"] == {"not_generated": 1, "empty": 1, "truncated": 1, "ok": 2}


def test_categories_follow_validator_codes_and_frozen_lints():
    row = _row("a", "outreach_email")
    bad_schema = dict(row["target"])
    bad_schema.pop("subject")
    s = phase9.score_prediction(row, _pred(bad_schema))
    assert s["parse_ok"] and not s["valid_structure"] and "schema_invalid" in s["validator_codes"]
    invented_ref = dict(row["target"], lead_facts_used=["fact-does-not-exist"])
    s = phase9.score_prediction(row, _pred(invented_ref))
    assert s["valid_structure"] and not s["factual_support"] and "unknown_fact_reference" in s["validator_codes"]
    placeholder = dict(row["target"], email_body=row["target"]["email_body"] + "\n[Your Name]")
    s = phase9.score_prediction(row, _pred(placeholder))
    assert s["valid_structure"] and not s["missing_info_handling"]
    summary = _row("b", "company_summary")
    need = dict(summary["target"], hypotheses=["They may be interested in scheduling tools"])
    s = phase9.score_prediction(summary, _pred(need))
    assert s["valid_structure"] and not s["missing_info_handling"] and s["writing_acceptability_automated"] is None


def test_blind_review_packet_hides_systems_and_counts_unreviewable_as_not_acceptable():
    rows = [_row(f"e{i}", "outreach_email" if i % 2 else "company_summary") for i in range(4)]
    preds = {
        "qwen3-4b-base": {r["example_id"]: _pred(r["target"]) for r in rows[:3]},  # e3 not generated
        "qwen3-4b-lora-v1": {r["example_id"]: _pred(r["target"]) for r in rows},
    }
    packet, sealed = phase9.build_review_packet(rows, preds, seed=7)
    again, _ = phase9.build_review_packet(rows, preds, seed=7)
    assert packet == again and len(packet) == 7
    assert all(set(item) == {"review_id", "task", "input_snapshot", "output"} for item in packet)
    assert "qwen" not in json.dumps(packet)  # no system identity in what the reviewer sees
    assert sealed["not_reviewable"] == [{"system": "qwen3-4b-base", "example_id": "e3", "reason": "not_generated"}]
    decisions = [{"review_id": item["review_id"], "factual_support": "supported", "missing_info_handling": "good",
                  "writing_quality": 4, "acceptable_as_is": True, "issues": []} for item in packet]
    assert phase9.validate_decisions(decisions, {i["review_id"] for i in packet}) == []
    bad = [dict(decisions[0], factual_support="unsupported")] + decisions[1:]
    assert any("requires factual_support" in p for p in phase9.validate_decisions(bad, {i["review_id"] for i in packet}))
    summary = phase9.aggregate_review(decisions, sealed, {"qwen3-4b-base": 4, "qwen3-4b-lora-v1": 4})
    assert summary["qwen3-4b-base"]["acceptable_as_is_rate"] == 0.75  # the missing output counts as not acceptable
    assert summary["qwen3-4b-lora-v1"]["acceptable_as_is_rate"] == 1.0


def test_review_cli_keeps_the_key_sealed_and_unblinds_only_valid_complete_decisions(tmp_path, capsys):
    from app import dataset_cli
    rows = [dict(_row(f"e{i}", "outreach_email" if i % 2 else "company_summary"), split="test", uncertain=False)
            for i in range(4)]
    dataset = tmp_path / "eligible.jsonl"
    dataset.write_text("".join(json.dumps(r) + "\n" for r in rows))
    preds = tmp_path / "preds.jsonl"
    preds.write_text("".join(json.dumps({"system": s, "example_id": r["example_id"], **_pred(r["target"])}) + "\n"
                             for s in ("qwen3-4b-base", "qwen3-4b-lora-v1") for r in rows))
    packet, key, out = tmp_path / "review" / "packet.jsonl", tmp_path / "sealed" / "key.json", tmp_path / "result.json"
    argv = ["review-packet", "--dataset", str(dataset), "--predictions", str(preds), "--seed", "7",
            "--packet-out", str(packet), "--key-out", str(key)]
    assert dataset_cli.main(argv) == 0
    assert "qwen" not in packet.read_text() and "qwen" not in capsys.readouterr().out  # nothing identifying
    assert "factual_support" not in packet.read_text()  # and no scores
    assert dataset_cli.main(argv) == 2  # never overwrites a sealed packet
    items = [json.loads(line) for line in packet.read_text().splitlines()]
    decisions = tmp_path / "decisions.jsonl"
    good = [{"review_id": i["review_id"], "factual_support": "supported", "missing_info_handling": "good",
             "writing_quality": 4, "acceptable_as_is": True, "issues": []} for i in items]
    agg = ["review-aggregate", "--packet", str(packet), "--key", str(key), "--decisions", str(decisions), "--out", str(out)]
    decisions.write_text("".join(json.dumps(d) + "\n" for d in good[:-1]))  # one missing
    assert dataset_cli.main(agg) == 1 and not out.exists()
    decisions.write_text("".join(json.dumps(d) + "\n" for d in good))
    original = packet.read_text()
    tampered = original.replace('"task": "company_summary"', '"task": "outreach_email"', 1)
    assert tampered != original
    packet.write_text(tampered)  # content changed, every review id intact: only the sealed hash can catch it
    assert dataset_cli.main(agg) == 1 and not out.exists()
    packet.write_text(original)
    assert dataset_cli.main(agg) == 0
    result = json.loads(out.read_text())
    assert result["label"].startswith("AI-evaluated") and "not human-verified" in result["label"]
    assert {s: v["acceptable_as_is_rate"] for s, v in result["by_system"].items()} == \
        {"qwen3-4b-base": 1.0, "qwen3-4b-lora-v1": 1.0}
    assert {p["system"] for p in result["per_item"]} == {"qwen3-4b-base", "qwen3-4b-lora-v1"}
    assert set(result["by_system_and_task"]) == {"company_summary", "outreach_email"}
    assert sum(v["qwen3-4b-lora-v1"]["n_examples"] for v in result["by_system_and_task"].values()) == 4
    held_out_scope = tmp_path / "flagged.jsonl"
    held_out_scope.write_text(json.dumps(dict(rows[0], uncertain=True)) + "\n")
    assert dataset_cli.main(["review-packet", "--dataset", str(held_out_scope), "--predictions", str(preds),
                             "--seed", "7", "--packet-out", str(tmp_path / "p2"), "--key-out", str(tmp_path / "k2")]) == 1
