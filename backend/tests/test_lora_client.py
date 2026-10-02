"""Phase 10: the qwen3-4b-lora-v1 AIClient against a mocked OpenAI-compatible
inference server (httpx.MockTransport -- no socket is opened). These tests
prove the integration code; they do not prove real model serving."""
from __future__ import annotations

import json

import httpx
import pytest
from sqlalchemy import select

from app.ai import client as client_module
from app.ai import lora_client
from app.ai.client import AIConfigError, OpenAIClient, get_ai_client
from app.ai.lora_client import LocalLoRAClient
from app.ai.mock_client import MockAIClient
from app.ai.prompts import JSON_SYSTEM_MESSAGE, build_outreach_prompt
from app.core import config
from app.models import AIOutput, WorkflowEvent
from app.services import ai_generation
from tests.conftest import SYNTHETIC_SELLER_PROFILE, save_and_activate
from tests.test_grounded_generation import count, event_count, upload

SECRET = "lora-server-secret-token"


class FakeServer:
    """Records requests; answers /models and /chat/completions."""

    def __init__(self, served=("qwen3-4b-lora-v1",), reply=None, finish="stop", status=200):
        self.requests: list[httpx.Request] = []
        self.served, self.reply, self.finish, self.status = served, reply, finish, status

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"object": "list", "data": [
                {"id": m, "object": "model", "created": 0, "owned_by": "vllm"} for m in self.served]})
        if self.status != 200:
            return httpx.Response(self.status, json={"error": {"message": f"internal detail {SECRET}"}})
        body = json.loads(request.content)
        ctx_prompt = body["messages"][1]["content"]
        content = self.reply(ctx_prompt) if callable(self.reply) else self.reply
        return httpx.Response(200, json={
            "id": "cmpl-1", "object": "chat.completion", "created": 0, "model": body["model"],
            "choices": [{"index": 0, "finish_reason": self.finish,
                         "message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": 1800, "completion_tokens": 290, "total_tokens": 2090},
        })


def lora(server: FakeServer, **kw) -> LocalLoRAClient:
    return LocalLoRAClient(base_url="http://localhost:8000/v1", api_key=SECRET,
                           http_client=httpx.Client(transport=httpx.MockTransport(server)), **kw)


@pytest.fixture(autouse=True)
def _fresh_served_cache():
    lora_client._verified.clear()
    yield
    lora_client._verified.clear()


def _settings(monkeypatch, **changes):
    monkeypatch.setattr(client_module, "settings", config.Settings(**{**config.settings.__dict__, **changes}))


# ------------------------------------------------------------ selection

def test_mock_stays_the_default_even_when_the_lora_provider_is_named(monkeypatch):
    _settings(monkeypatch, use_mock_ai=True, ai_provider="qwen3-4b-lora-v1",
              lora_inference_base_url="http://localhost:8000/v1")
    assert isinstance(get_ai_client(), MockAIClient)


def test_real_mode_keeps_openai_as_the_default_provider(monkeypatch):
    _settings(monkeypatch, use_mock_ai=False, ai_provider="openai", openai_api_key="k")
    assert isinstance(get_ai_client(), OpenAIClient)


@pytest.mark.parametrize("url, message", [
    ("", "needs LORA_INFERENCE_BASE_URL"),
    ("ftp://localhost/v1", "http(s) URL"),
    ("http://inference.example.com/v1", "must use https"),
    ("https://user:pw@inference.example.com/v1", "must not carry credentials"),
])
def test_lora_configuration_fails_closed_before_any_network_call(monkeypatch, url, message):
    _settings(monkeypatch, use_mock_ai=False, ai_provider="qwen3-4b-lora-v1", lora_inference_base_url=url)
    with pytest.raises(AIConfigError, match=message.replace("(", r"\(").replace(")", r"\)")):
        get_ai_client()


def test_unknown_provider_fails_closed(monkeypatch):
    _settings(monkeypatch, use_mock_ai=False, ai_provider="gpt-5-turbo-ultra")
    with pytest.raises(AIConfigError, match="Unknown AI_PROVIDER"):
        get_ai_client()


def test_private_http_and_https_urls_are_accepted(monkeypatch):
    for url in ("http://127.0.0.1:8000/v1", "http://10.0.0.5:8000/v1", "http://vllm:8000/v1",
                "https://inference.example.com/v1"):
        _settings(monkeypatch, use_mock_ai=False, ai_provider="qwen3-4b-lora-v1", lora_inference_base_url=url)
        assert isinstance(get_ai_client(), LocalLoRAClient)


# ------------------------------------------------------------ requests and provenance

def _activate_and_lead(client):
    save_and_activate(client, SYNTHETIC_SELLER_PROFILE)
    return upload(client)


def test_generation_sends_the_evaluated_request_and_records_adapter_provenance(client, db_session, monkeypatch):
    lead = _activate_and_lead(client)
    server = FakeServer(reply=lambda _: "")  # replaced below once the context is known
    captured = {}

    def reply(prompt):
        ctx = captured["ctx"]
        assert prompt == build_outreach_prompt(ctx)  # the exact grounded-v2 prompt
        return json.dumps(MockAIClient().generate_outreach(ctx))

    server.reply = reply
    real_build = ai_generation._build_context

    def capture(session, lead_row, task, seller):
        captured["ctx"] = real_build(session, lead_row, task, seller)
        return captured["ctx"]

    monkeypatch.setattr(ai_generation, "_build_context", capture)
    monkeypatch.setattr(ai_generation, "get_ai_client", lambda: lora(server))
    response = client.post(f"/api/leads/{lead['id']}/generate-outreach")
    assert response.status_code == 200, response.text

    chat = [r for r in server.requests if r.url.path.endswith("/chat/completions")]
    assert len(chat) == 1 and len(server.requests) == 2  # one model check, one generation
    body = json.loads(chat[0].content)
    assert body["model"] == "qwen3-4b-lora-v1" and body["temperature"] == 0 and body["max_tokens"] == 1024
    assert body["messages"][0] == {"role": "system", "content": JSON_SYSTEM_MESSAGE}
    assert chat[0].headers["authorization"] == f"Bearer {SECRET}"

    row = db_session.scalar(select(AIOutput))
    assert row.model_used == "qwen3-4b-lora-v1"
    assert row.model_revision == "Qwen/Qwen3-4B-Instruct-2507@cdbee75f17c01a7cc42f958dc650907174af0554"
    assert row.adapter_revision.endswith(lora_client.ADAPTER_SHA256)
    event = db_session.scalar(select(WorkflowEvent).where(WorkflowEvent.event_type == "outreach_generated"))
    assert event.event_data["adapter_revision"] == row.adapter_revision
    assert event.event_data["quality_checks_version"] == "runtime-checks-v1"
    assert event.event_data["usage"]["completion_tokens"] == 290
    assert SECRET not in json.dumps(event.event_data) and SECRET not in response.text


def test_a_server_that_does_not_serve_the_adapter_is_refused_and_nothing_is_saved(client, db_session, monkeypatch):
    lead = _activate_and_lead(client)
    server = FakeServer(served=("Qwen/Qwen3-4B-Instruct-2507",))  # base model only
    monkeypatch.setattr(ai_generation, "get_ai_client", lambda: lora(server))
    response = client.post(f"/api/leads/{lead['id']}/generate-outreach")
    assert response.status_code == 503 and "does not serve 'qwen3-4b-lora-v1'" in response.json()["detail"]
    assert not [r for r in server.requests if r.url.path.endswith("/chat/completions")]
    assert count(db_session, AIOutput) == 0


def test_a_truncated_reply_is_rejected_not_repaired(client, db_session, monkeypatch):
    lead = _activate_and_lead(client)
    server = FakeServer(reply='{"subject": "cut off', finish="length")
    monkeypatch.setattr(ai_generation, "get_ai_client", lambda: lora(server))
    response = client.post(f"/api/leads/{lead['id']}/generate-outreach")
    assert response.status_code == 502 and "truncated_output" in response.json()["detail"]
    assert count(db_session, AIOutput) == 0
    assert event_count(db_session, "ai_generation_rejected") == 1


def test_an_ungrounded_reply_fails_validation_like_any_provider(client, db_session, monkeypatch):
    lead = _activate_and_lead(client)
    bad = MockAIClient().generate_outreach  # valid shape, then an invented fact reference

    def reply(prompt):
        return json.dumps({"subject": "Hi", "email_body": "Hello", "lead_facts_used": ["fact-invented"],
                           "capabilities_used": [], "claims_used": [], "unknowns_acknowledged": [],
                           "call_note": "n", "confidence": "low"})

    monkeypatch.setattr(ai_generation, "get_ai_client", lambda: lora(FakeServer(reply=reply)))
    response = client.post(f"/api/leads/{lead['id']}/generate-outreach")
    assert response.status_code == 502 and "unknown_fact_reference" in response.json()["detail"]
    assert count(db_session, AIOutput) == 0 and bad is not None


def test_provider_errors_are_sanitized(client, db_session, monkeypatch):
    lead = _activate_and_lead(client)
    monkeypatch.setattr(ai_generation, "get_ai_client", lambda: lora(FakeServer(status=500)))
    response = client.post(f"/api/leads/{lead['id']}/generate-outreach")
    assert response.status_code == 502
    assert SECRET not in response.text and "internal detail" not in response.text
    assert count(db_session, AIOutput) == 0


def test_the_model_check_runs_once_per_server(client, monkeypatch):
    lead = _activate_and_lead(client)
    server = FakeServer(reply=lambda p: json.dumps({}))  # invalid, but still a completed call
    monkeypatch.setattr(ai_generation, "get_ai_client", lambda: lora(server))
    client.post(f"/api/leads/{lead['id']}/generate-summary")
    client.post(f"/api/leads/{lead['id']}/generate-summary")
    assert sum(r.url.path.endswith("/models") for r in server.requests) == 1
