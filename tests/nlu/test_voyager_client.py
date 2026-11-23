import pytest

from clickless.nlu import voyager_client


class FakeResponse:
    def __init__(self, payload=None):
        self.payload = (
            {"choices": [{"message": {"content": "{}"}}]} if payload is None else payload
        )

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


def test_local_endpoint_allows_empty_api_key(monkeypatch):
    captured = {}

    def fake_post(url, headers, **kwargs):
        captured.update(url=url, headers=headers)
        return FakeResponse()

    monkeypatch.setenv("LOCAL_BASE", "http://localhost:11434/v1")
    monkeypatch.delenv("LOCAL_KEY", raising=False)
    monkeypatch.setattr(voyager_client.requests, "post", fake_post)

    voyager_client.chat([], "qwen3:8b", base_url_env="LOCAL_BASE", api_key_env="LOCAL_KEY")

    assert "Authorization" not in captured["headers"]
    assert captured["url"] == "http://localhost:11434/v1/chat/completions"


def test_remote_endpoint_requires_api_key_before_request(monkeypatch):
    monkeypatch.setenv("REMOTE_BASE", "https://models.example.test/v1")
    monkeypatch.delenv("REMOTE_KEY", raising=False)
    monkeypatch.setattr(
        voyager_client.requests,
        "post",
        lambda *args, **kwargs: pytest.fail("request should not be sent without a remote key"),
    )

    with pytest.raises(RuntimeError, match="REMOTE_KEY"):
        voyager_client.chat([], "model", base_url_env="REMOTE_BASE", api_key_env="REMOTE_KEY")


def test_remote_endpoint_sends_configured_api_key(monkeypatch):
    captured = {}

    def fake_post(url, headers, **kwargs):
        captured.update(url=url, headers=headers)
        return FakeResponse()

    monkeypatch.setenv("REMOTE_BASE", "https://models.example.test/v1")
    monkeypatch.setenv("REMOTE_KEY", "test-secret")
    monkeypatch.setattr(voyager_client.requests, "post", fake_post)

    voyager_client.chat([], "model", base_url_env="REMOTE_BASE", api_key_env="REMOTE_KEY")

    assert captured["headers"]["Authorization"] == "Bearer test-secret"


def _payload(monkeypatch, **extra):
    captured = {}

    def fake_post(url, headers, json, **kwargs):
        captured.update(json)
        return FakeResponse()

    monkeypatch.setenv("LOCAL_BASE", "http://127.0.0.1:11434/v1")
    monkeypatch.setattr(voyager_client.requests, "post", fake_post)
    voyager_client.chat([], "qwen3:8b", base_url_env="LOCAL_BASE", api_key_env="LOCAL_KEY", **extra)
    return captured


def test_think_false_also_sends_reasoning_effort_none(monkeypatch):
    """Ollama's /v1 endpoint ignores `think`; reasoning_effort "none" is what disables reasoning (#114)."""
    payload = _payload(monkeypatch, think=False)
    assert payload["think"] is False and payload["reasoning_effort"] == "none"


def test_think_true_omits_reasoning_effort(monkeypatch):
    payload = _payload(monkeypatch, think=True)
    assert payload["think"] is True and "reasoning_effort" not in payload


def test_no_think_setting_omits_reasoning_effort(monkeypatch):
    assert "reasoning_effort" not in _payload(monkeypatch)


def test_explicit_reasoning_effort_is_not_overridden(monkeypatch):
    assert _payload(monkeypatch, think=False, reasoning_effort="low")["reasoning_effort"] == "low"


def test_missing_base_url_fails_before_request(monkeypatch):
    monkeypatch.delenv("MISSING_BASE", raising=False)

    with pytest.raises(RuntimeError, match="MISSING_BASE"):
        voyager_client.chat([], "model", base_url_env="MISSING_BASE")


@pytest.mark.parametrize("payload", [{}, {"choices": []}, {"choices": [{}]}, {"choices": [{"message": {}}]}])
def test_malformed_completion_shape_is_reported_as_endpoint_failure(monkeypatch, payload):
    monkeypatch.setenv("LOCAL_BASE", "http://localhost:11434/v1")
    monkeypatch.setattr(voyager_client.requests, "post", lambda *args, **kwargs: FakeResponse(payload))

    with pytest.raises(RuntimeError, match="invalid chat completion shape"):
        voyager_client.chat([], "qwen3:8b", base_url_env="LOCAL_BASE")
