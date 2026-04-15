import httpx

from agent.copilot_acp_client import CopilotACPClient


def test_create_chat_completion_accepts_httpx_timeout_object(monkeypatch):
    client = CopilotACPClient(acp_command="python", acp_args=["-c", "pass"], acp_cwd=".")
    captured = {}

    def fake_run_prompt(prompt_text, *, timeout_seconds):
        captured["prompt_text"] = prompt_text
        captured["timeout_seconds"] = timeout_seconds
        return "OK", ""

    monkeypatch.setattr(client, "_run_prompt", fake_run_prompt)

    response = client._create_chat_completion(
        model="gpt-5.4-high",
        messages=[{"role": "user", "content": "hello"}],
        timeout=httpx.Timeout(connect=30.0, read=120.0, write=1800.0, pool=30.0),
    )

    assert response.choices[0].message.content == "OK"
    assert captured["timeout_seconds"] == 120.0
