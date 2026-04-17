from types import SimpleNamespace

from hermes_cli.status import show_status


def test_show_status_includes_tavily_key(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-1234567890abcdef")

    show_status(SimpleNamespace(all=False, deep=False))

    output = capsys.readouterr().out
    assert "Tavily" in output
    assert "tvly...cdef" in output


def test_show_status_termux_gateway_section_skips_systemctl(monkeypatch, capsys, tmp_path):
    from hermes_cli import status as status_mod
    import hermes_cli.auth as auth_mod
    import hermes_cli.gateway as gateway_mod

    monkeypatch.setenv("TERMUX_VERSION", "0.118.3")
    monkeypatch.setenv("PREFIX", "/data/data/com.termux/files/usr")
    monkeypatch.setattr(status_mod, "get_env_path", lambda: tmp_path / ".env", raising=False)
    monkeypatch.setattr(status_mod, "get_hermes_home", lambda: tmp_path, raising=False)
    monkeypatch.setattr(status_mod, "load_config", lambda: {"model": "gpt-5.4"}, raising=False)
    monkeypatch.setattr(status_mod, "resolve_requested_provider", lambda requested=None: "openai-codex", raising=False)
    monkeypatch.setattr(status_mod, "resolve_provider", lambda requested=None, **kwargs: "openai-codex", raising=False)
    monkeypatch.setattr(status_mod, "provider_label", lambda provider: "OpenAI Codex", raising=False)
    monkeypatch.setattr(auth_mod, "get_nous_auth_status", lambda: {}, raising=False)
    monkeypatch.setattr(auth_mod, "get_codex_auth_status", lambda: {}, raising=False)
    monkeypatch.setattr(gateway_mod, "find_gateway_pids", lambda exclude_pids=None: [], raising=False)

    def _unexpected_systemctl(*args, **kwargs):
        raise AssertionError("systemctl should not be called in the Termux status view")

    monkeypatch.setattr(status_mod.subprocess, "run", _unexpected_systemctl)

    status_mod.show_status(SimpleNamespace(all=False, deep=False))

    output = capsys.readouterr().out
    assert "Manager:      Termux / manual process" in output
    assert "Start with:   hermes gateway" in output
    assert "systemd (user)" not in output


def _setup_linux_gateway_mocks(monkeypatch, tmp_path):
    """Common setup for Linux non-container gateway status tests."""
    from hermes_cli import status as status_mod
    import hermes_cli.auth as auth_mod

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(status_mod, "get_env_path", lambda: tmp_path / ".env", raising=False)
    monkeypatch.setattr(status_mod, "get_hermes_home", lambda: tmp_path, raising=False)
    monkeypatch.setattr(status_mod, "load_config", lambda: {"model": "gpt-5.4"}, raising=False)
    monkeypatch.setattr(status_mod, "resolve_requested_provider", lambda requested=None: "openai", raising=False)
    monkeypatch.setattr(status_mod, "resolve_provider", lambda requested=None, **kwargs: "openai", raising=False)
    monkeypatch.setattr(status_mod, "provider_label", lambda provider: "OpenAI", raising=False)
    monkeypatch.setattr(auth_mod, "get_nous_auth_status", lambda: {}, raising=False)
    monkeypatch.setattr(auth_mod, "get_codex_auth_status", lambda: {}, raising=False)
    monkeypatch.setattr("hermes_cli.status.sys.platform", "linux")
    monkeypatch.setattr("hermes_constants.is_container", lambda: False)


def _mock_run_for_cmds(cmd_responses):
    """Return a mock subprocess.run that responds based on exact command list."""
    import subprocess

    def _run(cmd, **kwargs):
        for key_cmd, stdout, rc in cmd_responses:
            if tuple(cmd) == tuple(key_cmd):
                return subprocess.CompletedProcess(cmd, rc, stdout=stdout, stderr="")
        # Fallback: not active
        return subprocess.CompletedProcess(cmd, 1, stdout="inactive\n", stderr="")

    return _run


def test_gateway_status_user_service_active_shows_user_manager(monkeypatch, capsys, tmp_path):
    """When user-level service is active, Manager should show 'systemd (user)'."""
    _setup_linux_gateway_mocks(monkeypatch, tmp_path)
    from hermes_cli import status as status_mod

    monkeypatch.setattr(
        status_mod.subprocess, "run",
        _mock_run_for_cmds([
            (["systemctl", "--user", "is-active", "hermes-gateway"], "active\n", 0),
        ])
    )

    status_mod.show_status(SimpleNamespace(all=False, deep=False))

    output = capsys.readouterr().out
    assert "running" in output
    assert "Manager:      systemd (user)" in output
    assert "systemd (system)" not in output


def test_gateway_status_fallback_to_system_when_user_inactive(monkeypatch, capsys, tmp_path):
    """When user service is inactive but system service is active, show 'systemd (system)'."""
    _setup_linux_gateway_mocks(monkeypatch, tmp_path)
    from hermes_cli import status as status_mod

    monkeypatch.setattr(
        status_mod.subprocess, "run",
        _mock_run_for_cmds([
            (["systemctl", "--user", "is-active", "hermes-gateway"], "inactive\n", 3),
            (["systemctl", "is-active", "hermes-gateway"], "active\n", 0),
        ])
    )

    status_mod.show_status(SimpleNamespace(all=False, deep=False))

    output = capsys.readouterr().out
    assert "running" in output
    assert "Manager:      systemd (system)" in output
