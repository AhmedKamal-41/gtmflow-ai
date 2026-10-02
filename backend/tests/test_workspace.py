"""The rep's lead inbox, the drafting-model status, and the fine-tuned fallback."""
from __future__ import annotations

import httpx
import pytest

from app.ai import client as client_module
from app.ai import status as status_module
from app.ai.lora_client import LocalLoRAClient
from app.ai.mock_client import MockAIClient
from app.api import auth as auth_api
from app.core import config
from tests.conftest import create_test_user, sign_in


@pytest.fixture()
def operator(app_client, db_session_factory):
    create_test_user(db_session_factory, "rep")
    client = app_client()
    sign_in(client, "rep")
    return client


@pytest.fixture()
def configure(monkeypatch):
    def apply(**changes):
        patched = config.Settings(**{**config.settings.__dict__, **changes})
        for module in (client_module, status_module, auth_api):
            monkeypatch.setattr(module, "settings", patched)
        status_module.clear_probe_cache()
        return patched
    yield apply
    status_module.clear_probe_cache()


def _activate_demo_seller(client):
    template = client.get("/api/seller-profile/demonstration-template").json()
    saved = client.post("/api/seller-profile", json={"expected_version": 0, "profile": template}).json()
    sequence = client.get("/api/seller-profile/status").json()["activation_sequence"]
    assert client.post("/api/seller-profile/activate", json={
        "seller_profile_id": saved["id"], "expected_activation_sequence": sequence,
        "confirm_reviewed": True, "acknowledge_demo": True}).status_code == 201


def _by_company(page):
    return {item["company_name"]: item for item in page["items"]}


# ------------------------------------------------------------ inbox

def test_inbox_needs_a_session(anon_client):
    assert anon_client.get("/api/inbox").status_code == 401
    assert anon_client.get("/api/ai/status").status_code == 401


def test_inbox_ranks_hot_first_and_tracks_each_stage(operator):
    assert operator.post("/api/demo/run").status_code == 201
    page = operator.get("/api/inbox?limit=100").json()
    assert page["total"] == 10 and page["counts"]["all"] == 10 and page["counts"]["hot"] == 2
    priorities = [item["priority"] for item in page["items"]]
    assert priorities == sorted(priorities, key=["Hot", "Warm", "Cold"].index)
    leads = _by_company(page)
    assert leads["Cascade Modular Homes"]["stage"] == "sent"  # approved and delivered by the demo
    assert page["counts"]["sent"] == 2 and page["counts"]["needs_draft"] == 8

    _activate_demo_seller(operator)
    warm = [item for item in page["items"] if item["priority"] == "Warm"]
    to_review, to_approve, to_reject = warm[0]["id"], warm[1]["id"], warm[2]["id"]
    drafts = {}
    for lead_id in (to_review, to_approve, to_reject):
        response = operator.post(f"/api/leads/{lead_id}/generate-outreach")
        assert response.status_code == 200, response.text
        drafts[lead_id] = response.json()
    approve = drafts[to_approve]
    assert operator.post(f"/api/leads/{to_approve}/approve-outreach", json={
        "ai_output_id": approve["id"], "content_hash": approve["content_hash"],
        "acknowledged_quality_flags": sorted({f["code"] for f in approve.get("quality_flags") or []})}).status_code == 200
    reject = drafts[to_reject]
    assert operator.post(f"/api/leads/{to_reject}/reject-outreach", json={
        "ai_output_id": reject["id"], "content_hash": reject["content_hash"], "reason": "too generic"}).status_code == 200

    stages = {item["id"]: item for item in operator.get("/api/inbox?limit=100").json()["items"]}
    assert stages[to_review]["stage"] == "to_review" and stages[to_review]["draft_model"] == "mock"
    assert stages[to_approve]["stage"] == "approved"
    assert stages[to_reject]["stage"] == "rejected"


def test_inbox_filters_and_counts(operator):
    operator.post("/api/demo/run")
    csv = b"company_name,industry\nUnscored Example Co,Retail\n"
    assert operator.post("/api/batches/upload", files={"file": ("new.csv", csv, "text/csv")}).status_code == 201
    unscored = operator.get("/api/inbox?stage=needs_score").json()
    assert [item["company_name"] for item in unscored["items"]] == ["Unscored Example Co"]
    assert unscored["items"][0]["priority"] is None and unscored["counts"]["needs_score"] == 1
    hot = operator.get("/api/inbox?priority=Hot").json()
    assert hot["total"] == 2 and all(item["priority"] == "Hot" for item in hot["items"])
    search = operator.get("/api/inbox?q=cascade").json()
    assert [item["company_name"] for item in search["items"]] == ["Cascade Modular Homes"]
    paged = operator.get("/api/inbox?limit=3&offset=3").json()
    assert len(paged["items"]) == 3 and paged["total"] == 11
    assert operator.get("/api/inbox?stage=bogus").status_code == 422


def test_viewers_can_read_the_inbox(app_client, db_session_factory, operator):
    operator.post("/api/demo/run")
    create_test_user(db_session_factory, "vera", role="viewer")
    viewer = app_client()
    sign_in(viewer, "vera")
    assert viewer.get("/api/inbox").json()["total"] == 10


# ------------------------------------------------------------ drafting model

class FakeModelList:
    def __init__(self, ids, status=200):
        self.status_code, self._ids = status, ids

    def json(self):
        return {"data": [{"id": model_id} for model_id in self._ids]}


def _lora(configure, **extra):
    return configure(use_mock_ai=False, ai_provider="qwen3-4b-lora-v1",
                     lora_inference_base_url="http://127.0.0.1:8001/v1", **extra)


def test_status_reports_the_mock_by_default(operator, configure):
    configure(use_mock_ai=True)
    status = operator.get("/api/ai/status").json()
    assert status["mode"] == "mock" and status["fine_tuned_selected"] is False
    assert "fine-tuned" in status["detail"]


def test_fine_tuned_model_is_used_when_its_server_answers(operator, configure, monkeypatch):
    _lora(configure, lora_fallback_to_mock=True)
    calls = []
    monkeypatch.setattr(httpx, "get", lambda url, **kw: calls.append(url) or FakeModelList(["qwen3-4b-lora-v1"]))
    status = operator.get("/api/ai/status").json()
    assert status["mode"] == "fine_tuned" and status["fine_tuned_connected"] is True
    assert status["label"].startswith("GTMFlow fine-tuned model")
    assert isinstance(client_module.get_ai_client(), LocalLoRAClient)
    assert calls == ["http://127.0.0.1:8001/v1/models"]  # cached: one probe for both


def test_offline_fine_tuned_model_falls_back_to_the_mock_when_enabled(operator, configure, monkeypatch):
    _lora(configure, lora_fallback_to_mock=True)

    def refuse(url, **kwargs):
        raise httpx.ConnectError("refused")
    monkeypatch.setattr(httpx, "get", refuse)
    status = operator.get("/api/ai/status").json()
    assert status["mode"] == "mock" and status["fallback_enabled"] and not status["fine_tuned_connected"]
    assert "offline" in status["detail"]
    assert isinstance(client_module.get_ai_client(), MockAIClient)


def test_without_fallback_an_offline_model_still_fails_closed(operator, configure, monkeypatch):
    _lora(configure, lora_fallback_to_mock=False)
    monkeypatch.setattr(httpx, "get", lambda url, **kw: FakeModelList([], status=503))
    assert operator.get("/api/ai/status").json()["mode"] == "unavailable"
    assert isinstance(client_module.get_ai_client(), LocalLoRAClient)  # the request itself will fail closed


def test_a_server_serving_a_different_model_is_not_connected(configure, monkeypatch):
    _lora(configure, lora_fallback_to_mock=True)
    monkeypatch.setattr(httpx, "get", lambda url, **kw: FakeModelList(["some-other-model"]))
    assert status_module.fine_tuned_reachable() is False


def test_probe_sends_the_key_only_as_a_bearer_header(configure, monkeypatch):
    _lora(configure, lora_fallback_to_mock=True, lora_inference_api_key="probe-secret")
    seen = {}
    monkeypatch.setattr(httpx, "get", lambda url, **kw: seen.update(url=url, **kw) or FakeModelList(["qwen3-4b-lora-v1"]))
    assert status_module.fine_tuned_reachable()
    assert "probe-secret" not in seen["url"] and seen["headers"] == {"Authorization": "Bearer probe-secret"}
    assert "probe-secret" not in str(status_module.ai_status())


@pytest.mark.parametrize("changes, safe", [
    ({"use_mock_ai": True, "slack_webhook_url": ""}, True),
    ({"use_mock_ai": False, "ai_provider": "qwen3-4b-lora-v1", "slack_webhook_url": ""}, True),
    ({"use_mock_ai": False, "ai_provider": "openai", "slack_webhook_url": ""}, False),
    ({"use_mock_ai": True, "slack_webhook_url": "https://hooks.example.invalid/x"}, False),
])
def test_guest_safe_excludes_paid_ai_and_real_slack(changes, safe):
    assert config.Settings(**{**config.settings.__dict__, **changes}).guest_safe is safe


def test_a_changed_input_makes_the_draft_outdated_not_ready(operator):
    """Re-scoring changes a draft's inputs: the list must stop offering
    'ready to send' exactly when review and delivery would refuse it."""
    operator.post("/api/demo/run")
    _activate_demo_seller(operator)
    lead_id = next(i["id"] for i in operator.get("/api/inbox?limit=100").json()["items"] if i["priority"] == "Warm")
    draft = operator.post(f"/api/leads/{lead_id}/generate-outreach").json()
    operator.post(f"/api/leads/{lead_id}/approve-outreach", json={
        "ai_output_id": draft["id"], "content_hash": draft["content_hash"],
        "acknowledged_quality_flags": sorted({f["code"] for f in draft.get("quality_flags") or []})})
    assert _stage(operator, lead_id) == "approved"
    assert operator.post(f"/api/leads/{lead_id}/fit-score").status_code in (200, 201)
    assert operator.get(f"/api/leads/{lead_id}/review-state").json()["approval_applicable"] is False
    assert _stage(operator, lead_id) == "outdated"
    counts = operator.get("/api/inbox").json()["counts"]
    assert counts["approved"] == 0 and counts["outdated"] == 1


def _stage(client, lead_id):
    return next(i["stage"] for i in client.get("/api/inbox?limit=100").json()["items"] if i["id"] == lead_id)
