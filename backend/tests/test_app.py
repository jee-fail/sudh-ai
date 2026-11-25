from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from main import Config, create_app


def make_config(
    tmp_path: Path,
    *,
    simulation: bool = True,
    mode: str = "litellm",
    keys: tuple[str, ...] = ("first-key", "second-key"),
    database_name: str = "test.db",
) -> Config:
    return Config(
        mode=mode,
        keys=keys,
        model="test/model",
        database_url=f"sqlite:///{tmp_path / database_name}",
        allowed_origins=("http://localhost:3000",),
        failure_simulation=simulation,
    )


def response(text: str = "hello"):
    return {
        "model": "test/model",
        "choices": [{"message": {"content": text}}],
        "usage": {"prompt_tokens": 12, "completion_tokens": 3},
    }


def test_health_and_offline_demo_mode(tmp_path):
    config = make_config(
        tmp_path,
        mode="mock",
        keys=("demo-one", "demo-two"),
        database_name="demo.db",
    )
    app = create_app(config)
    with TestClient(app) as client:
        health = client.get("/health")
        chat = client.post(
            "/chat",
            json={"session_id": "demo", "message": "Capital of France?"},
        )

    assert health.json() == {
        "status": "ok",
        "keys_configured": 2,
        "cache_enabled": True,
    }
    assert chat.status_code == 200
    assert chat.json()["reply"] == "The capital of France is Paris."
    assert chat.json()["metadata"]["model"] == "test/model"


def test_cache_works_across_sessions_and_keeps_usage(tmp_path):
    calls = []

    async def fake_completion(**kwargs):
        calls.append(kwargs)
        return response("Paris")

    app = create_app(make_config(tmp_path), completion=fake_completion)
    with TestClient(app) as client:
        first = client.post(
            "/chat",
            json={"session_id": "one", "message": "Capital of France?"},
        )
        second = client.post(
            "/chat",
            json={"session_id": "two", "message": "Capital of France?"},
        )

    assert first.status_code == 200
    assert second.status_code == 200
    assert len(calls) == 1
    assert calls[0]["use_cache"] is True
    assert first.json()["metadata"]["resolution_source"] == "LIVE_INFERENCE"
    cached = second.json()["metadata"]
    assert second.json()["reply"] == first.json()["reply"]
    assert cached["resolution_source"] == "LLM_CACHE"
    assert cached["key_id"] is None
    assert cached["fallback_attempts"] == 0
    assert cached["input_tokens"] == 12
    assert cached["output_tokens"] == 3
    assert cached["latency_ms"] < first.json()["metadata"]["latency_ms"]


def test_cache_key_includes_the_complete_message_list(tmp_path):
    calls = []

    async def fake_completion(**kwargs):
        calls.append(kwargs)
        return response(f"reply-{len(calls)}")

    app = create_app(make_config(tmp_path), completion=fake_completion)
    with TestClient(app) as client:
        first = client.post(
            "/chat",
            json={"session_id": "first", "message": "What is one plus one?"},
        )
        different = client.post(
            "/chat",
            json={"session_id": "different", "message": "What is two plus two?"},
        )
        repeated = client.post(
            "/chat",
            json={"session_id": "repeated", "message": "What is one plus one?"},
        )

    assert len(calls) == 2
    assert different.json()["metadata"]["resolution_source"] == "LIVE_INFERENCE"
    assert repeated.json()["metadata"]["resolution_source"] == "LLM_CACHE"
    assert repeated.json()["reply"] == first.json()["reply"]


def test_litellm_cache_hit_has_cache_metadata(tmp_path):
    async def cached_completion(**_):
        result = response("Cached by LiteLLM")
        result["_hidden_params"] = {"cache_hit": True}
        return result

    app = create_app(make_config(tmp_path), completion=cached_completion)
    with TestClient(app) as client:
        result = client.post(
            "/chat",
            json={"session_id": "litellm-cache", "message": "Use the LiteLLM cache"},
        )

    assert result.status_code == 200
    assert result.json()["metadata"]["resolution_source"] == "LLM_CACHE"
    assert result.json()["metadata"]["key_id"] is None
    assert result.json()["metadata"]["fallback_attempts"] == 0


@pytest.mark.parametrize("status", [429, 500, 502, 503])
def test_failover_hook_skips_the_simulated_key(tmp_path, status):
    used_keys = []

    async def fake_completion(**kwargs):
        used_keys.append(kwargs["api_key"])
        return response("A real answer")

    app = create_app(make_config(tmp_path), completion=fake_completion)
    with TestClient(app) as client:
        result = client.post(
            "/chat",
            headers={"X-Simulate-Failure": f"key_1={status}"},
            json={"session_id": f"fallback-{status}", "message": "try fallback"},
        )

    assert result.status_code == 200
    assert used_keys == ["second-key"]
    assert result.json()["reply"] == "A real answer"
    assert result.json()["metadata"]["key_id"] == "key_2"
    assert result.json()["metadata"]["fallback_attempts"] == 1


def test_all_keys_failed_returns_503_without_creating_a_session(tmp_path):
    async def fake_completion(**_):
        return response("This should not be called")

    app = create_app(make_config(tmp_path), completion=fake_completion)
    with TestClient(app) as client:
        failed = client.post(
            "/chat",
            headers={"X-Simulate-Failure": "*=429"},
            json={"session_id": "empty", "message": "no providers"},
        )
        history = client.get("/sessions/empty/messages")

    assert failed.status_code == 503
    assert failed.json() == {"detail": "all providers unavailable"}
    assert history.status_code == 404
    assert history.json() == {"detail": "session not found"}


def test_failed_request_does_not_change_existing_history(tmp_path):
    async def fake_completion(**_):
        return response("Saved answer")

    app = create_app(make_config(tmp_path), completion=fake_completion)
    with TestClient(app) as client:
        first = client.post(
            "/chat",
            json={"session_id": "stable", "message": "saved question"},
        )
        failed = client.post(
            "/chat",
            headers={"X-Simulate-Failure": "*=429"},
            json={"session_id": "stable", "message": "unsaved question"},
        )
        history = client.get("/sessions/stable/messages")

    assert first.status_code == 200
    assert failed.status_code == 503
    assert [message["content"] for message in history.json()["messages"]] == [
        "saved question",
        "Saved answer",
    ]


def test_history_contains_prior_turns_and_assistant_metadata(tmp_path):
    seen_messages = []

    async def fake_completion(**kwargs):
        seen_messages.append(kwargs["messages"])
        return response("Reply")

    app = create_app(make_config(tmp_path), completion=fake_completion)
    with TestClient(app) as client:
        client.post(
            "/chat",
            json={"session_id": "multi", "message": "first question"},
        )
        client.post(
            "/chat",
            json={"session_id": "multi", "message": "second question"},
        )
        history = client.get("/sessions/multi/messages")

    assert seen_messages[1][-2:] == [
        {"role": "assistant", "content": "Reply"},
        {"role": "user", "content": "second question"},
    ]
    messages = history.json()["messages"]
    assert [item["role"] for item in messages] == [
        "user",
        "assistant",
        "user",
        "assistant",
    ]
    assert messages[1]["metadata"]["input_tokens"] == 12
    assert "system" not in [item["role"] for item in messages]


def test_system_prompt_is_used_and_not_exposed_in_history(tmp_path):
    seen_messages = []

    async def fake_completion(**kwargs):
        seen_messages.append(kwargs["messages"])
        return response("Reply")

    app = create_app(make_config(tmp_path), completion=fake_completion)
    with TestClient(app) as client:
        client.post(
            "/chat",
            json={
                "session_id": "prompted",
                "message": "first turn",
                "system_prompt": "Answer with one sentence.",
            },
        )
        client.post(
            "/chat",
            json={"session_id": "prompted", "message": "second turn"},
        )
        history = client.get("/sessions/prompted/messages")

    assert seen_messages[0][0] == {
        "role": "system",
        "content": "Answer with one sentence.",
    }
    assert seen_messages[1][0] == seen_messages[0][0]
    assert all(message["role"] != "system" for message in history.json()["messages"])


def test_history_survives_app_restart(tmp_path):
    config = make_config(tmp_path, database_name="persistent.db")

    async def fake_completion(**_):
        return response("Persistent answer")

    first_app = create_app(config, completion=fake_completion)
    with TestClient(first_app) as client:
        posted = client.post(
            "/chat",
            json={"session_id": "persistent", "message": "remember this"},
        )
    assert posted.status_code == 200

    second_app = create_app(config, completion=fake_completion)
    with TestClient(second_app) as client:
        history = client.get("/sessions/persistent/messages")

    assert history.status_code == 200
    assert len(history.json()["messages"]) == 2


def test_simulation_header_is_ignored_when_disabled(tmp_path):
    used_keys = []

    async def fake_completion(**kwargs):
        used_keys.append(kwargs["api_key"])
        return response("Normal answer")

    app = create_app(
        make_config(tmp_path, simulation=False),
        completion=fake_completion,
    )
    with TestClient(app) as client:
        result = client.post(
            "/chat",
            headers={"X-Simulate-Failure": "key_1=429"},
            json={"session_id": "simulation-off", "message": "normal request"},
        )

    assert result.status_code == 200
    assert used_keys == ["first-key"]
    assert result.json()["metadata"]["key_id"] == "key_1"
    assert result.json()["metadata"]["fallback_attempts"] == 0


@pytest.mark.parametrize(
    "payload",
    [
        {"session_id": "s1", "message": "   "},
        {"session_id": " ", "message": "hello"},
        {"session_id": "s1", "message": "x" * 4001},
        {"message": "missing session"},
        {"session_id": "missing-message"},
        {"session_id": "s" * 129, "message": "too long session"},
    ],
)
def test_invalid_messages_return_422(tmp_path, payload):
    app = create_app(make_config(tmp_path), completion=lambda **_: response())
    with TestClient(app) as client:
        result = client.post("/chat", json=payload)

    assert result.status_code == 422
    assert "detail" in result.json()


def test_unknown_session_is_404_and_docs_routes_are_disabled(tmp_path):
    app = create_app(make_config(tmp_path), completion=lambda **_: response())
    with TestClient(app) as client:
        missing = client.get("/sessions/unknown/messages")
        docs = client.get("/docs")
        schema = client.get("/openapi.json")

    assert missing.status_code == 404
    assert missing.json() == {"detail": "session not found"}
    assert docs.status_code == 404
    assert schema.status_code == 404
