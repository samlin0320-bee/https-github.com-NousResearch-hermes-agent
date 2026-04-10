"""Tests for ``hermes update --check``."""

from types import SimpleNamespace

import hermes_cli.config as hermes_config
import hermes_cli.main as hermes_main


def _setup_repo(monkeypatch, tmp_path):
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(hermes_main, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(hermes_config, "is_managed", lambda: False)


def test_update_check_reports_official_checkout_up_to_date(monkeypatch, tmp_path, capsys):
    _setup_repo(monkeypatch, tmp_path)

    recorded = []

    def fake_run(cmd, **kwargs):
        recorded.append(cmd)
        if cmd == ["git", "remote", "get-url", "origin"]:
            return SimpleNamespace(returncode=0, stdout=f"{hermes_main.OFFICIAL_REPO_URL}\n", stderr="")
        if cmd == ["git", "rev-parse", "--abbrev-ref", "HEAD"]:
            return SimpleNamespace(returncode=0, stdout="main\n", stderr="")
        if cmd == ["git", "fetch", "origin", "--quiet"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if cmd == ["git", "rev-list", "--count", "HEAD..origin/main"]:
            return SimpleNamespace(returncode=0, stdout="0\n", stderr="")
        if cmd == ["git", "rev-list", "--count", "origin/main..HEAD"]:
            return SimpleNamespace(returncode=0, stdout="0\n", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(hermes_main.subprocess, "run", fake_run)

    hermes_main.cmd_update(SimpleNamespace(check=True, gateway=False))

    out = capsys.readouterr().out
    assert "Checking for updates" in out
    assert "Repo: official checkout" in out
    assert "Current branch: main" in out
    assert "✓ Up to date with origin/main" in out
    assert not any("pull" in " ".join(c) for c in recorded)


def test_update_check_uses_upstream_for_forks(monkeypatch, tmp_path, capsys):
    _setup_repo(monkeypatch, tmp_path)

    recorded = []

    def fake_run(cmd, **kwargs):
        recorded.append(cmd)
        if cmd == ["git", "remote", "get-url", "origin"]:
            return SimpleNamespace(returncode=0, stdout="https://github.com/example/hermes-agent.git\n", stderr="")
        if cmd == ["git", "remote", "get-url", "upstream"]:
            return SimpleNamespace(returncode=0, stdout=f"{hermes_main.OFFICIAL_REPO_URL}\n", stderr="")
        if cmd == ["git", "rev-parse", "--abbrev-ref", "HEAD"]:
            return SimpleNamespace(returncode=0, stdout="docs/test\n", stderr="")
        if cmd == ["git", "fetch", "upstream", "--quiet"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if cmd == ["git", "rev-list", "--count", "HEAD..upstream/main"]:
            return SimpleNamespace(returncode=0, stdout="2\n", stderr="")
        if cmd == ["git", "rev-list", "--count", "upstream/main..HEAD"]:
            return SimpleNamespace(returncode=0, stdout="0\n", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(hermes_main.subprocess, "run", fake_run)

    hermes_main.cmd_update(SimpleNamespace(check=True, gateway=False))

    out = capsys.readouterr().out
    assert "Repo: fork" in out
    assert "Comparison target: official upstream/main" in out
    assert "Update available: 2 commit(s) behind upstream/main" in out
    assert "switch this checkout from branch 'docs/test' to main" in out
    assert not any("pull" in " ".join(c) for c in recorded)


def test_update_check_falls_back_to_origin_when_no_upstream(monkeypatch, tmp_path, capsys):
    _setup_repo(monkeypatch, tmp_path)

    def fake_run(cmd, **kwargs):
        if cmd == ["git", "remote", "get-url", "origin"]:
            return SimpleNamespace(returncode=0, stdout="https://github.com/example/hermes-agent.git\n", stderr="")
        if cmd == ["git", "remote", "get-url", "upstream"]:
            return SimpleNamespace(returncode=2, stdout="", stderr="no upstream\n")
        if cmd == ["git", "rev-parse", "--abbrev-ref", "HEAD"]:
            return SimpleNamespace(returncode=0, stdout="main\n", stderr="")
        if cmd == ["git", "fetch", "origin", "--quiet"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if cmd == ["git", "rev-list", "--count", "HEAD..origin/main"]:
            return SimpleNamespace(returncode=0, stdout="0\n", stderr="")
        if cmd == ["git", "rev-list", "--count", "origin/main..HEAD"]:
            return SimpleNamespace(returncode=0, stdout="1\n", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(hermes_main.subprocess, "run", fake_run)

    hermes_main.cmd_update(SimpleNamespace(check=True, gateway=False))

    out = capsys.readouterr().out
    assert "Comparison target: origin/main (fork origin, no upstream remote configured)" in out
    assert "Local checkout is 1 commit(s) ahead of origin/main" in out
