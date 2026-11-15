import pytest

from clickless.nlu import voyager_client


class FakeResponse:
    def raise_for_status(self):
        return None

    def json(self):
        return {"choices": [{"message": {"content": "{}"}}]}


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


def test_missing_base_url_fails_before_request(monkeypatch):
    monkeypatch.delenv("MISSING_BASE", raising=False)

    with pytest.raises(RuntimeError, match="MISSING_BASE"):
        voyager_client.chat([], "model", base_url_env="MISSING_BASE")
