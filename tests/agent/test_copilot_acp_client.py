import httpx

from agent.copilot_acp_client import CopilotACPClient, _DEFAULT_TIMEOUT_SECONDS


def test_create_chat_completion_accepts_httpx_timeout_object(monkeypatch):
    client = CopilotACPClient(acp_command="copilot", acp_args=["--acp", "--stdio"])
    captured: dict[str, float] = {}

    def fake_run_prompt(prompt_text: str, *, timeout_seconds: float):
        captured["timeout_seconds"] = timeout_seconds
        return "delegate smoke ok", ""

    monkeypatch.setattr(client, "_run_prompt", fake_run_prompt)

    result = client.chat.completions.create(
        model="google/gemma-4-26B-A4B-it",
        messages=[{"role": "user", "content": "hello"}],
        timeout=httpx.Timeout(timeout=123.0, connect=10.0),
    )

    assert result.choices[0].message.content == "delegate smoke ok"
    assert captured["timeout_seconds"] == 123.0


def test_create_chat_completion_defaults_timeout_when_missing(monkeypatch):
    client = CopilotACPClient(acp_command="copilot", acp_args=["--acp", "--stdio"])
    captured: dict[str, float] = {}

    def fake_run_prompt(prompt_text: str, *, timeout_seconds: float):
        captured["timeout_seconds"] = timeout_seconds
        return "delegate smoke ok", ""

    monkeypatch.setattr(client, "_run_prompt", fake_run_prompt)

    client.chat.completions.create(
        model="google/gemma-4-26B-A4B-it",
        messages=[{"role": "user", "content": "hello"}],
    )

    assert captured["timeout_seconds"] == _DEFAULT_TIMEOUT_SECONDS


def test_aiagent_accepts_non_streaming_copilot_acp_chat_completion(monkeypatch):
    from run_agent import AIAgent

    monkeypatch.setattr(
        CopilotACPClient,
        "_run_prompt",
        lambda self, prompt_text, *, timeout_seconds: ("hello from fake acp", ""),
    )

    agent = AIAgent(
        base_url="acp://copilot",
        api_key="dummy",
        provider="copilot-acp",
        api_mode="chat_completions",
        acp_command="hermes",
        acp_args=["--profile", "coder", "acp"],
        model="google/gemma-4-26B-A4B-it",
        quiet_mode=True,
        enabled_toolsets=["delegation"],
        skip_memory=True,
        skip_context_files=True,
    )

    result = agent.run_conversation(user_message="say hello")

    assert result.get("failed", False) is False
    assert result["final_response"] == "hello from fake acp"
