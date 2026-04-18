"""Tests for file write safety and HERMES_WRITE_SAFE_ROOT sandboxing.

Based on PR #1085 by ismoilh (salvaged).
"""

import os
from pathlib import Path

import pytest

from tools.file_operations import _is_write_denied


class TestStaticDenyList:
    """Basic sanity checks for the static write deny list."""

    def test_temp_file_not_denied_by_default(self, tmp_path: Path):
        target = tmp_path / "regular.txt"
        assert _is_write_denied(str(target)) is False

    def test_ssh_key_is_denied(self):
        assert _is_write_denied(os.path.expanduser("~/.ssh/id_rsa")) is True

    def test_etc_shadow_is_denied(self):
        assert _is_write_denied("/etc/shadow") is True


class TestSafeWriteRoot:
    """HERMES_WRITE_SAFE_ROOT should sandbox writes to a specific subtree."""

    def test_writes_inside_safe_root_are_allowed(self, tmp_path: Path, monkeypatch):
        safe_root = tmp_path / "workspace"
        child = safe_root / "subdir" / "file.txt"
        os.makedirs(child.parent, exist_ok=True)

        monkeypatch.setenv("HERMES_WRITE_SAFE_ROOT", str(safe_root))
        assert _is_write_denied(str(child)) is False

    def test_writes_to_safe_root_itself_are_allowed(self, tmp_path: Path, monkeypatch):
        safe_root = tmp_path / "workspace"
        os.makedirs(safe_root, exist_ok=True)

        monkeypatch.setenv("HERMES_WRITE_SAFE_ROOT", str(safe_root))
        assert _is_write_denied(str(safe_root)) is False

    def test_writes_outside_safe_root_are_denied(self, tmp_path: Path, monkeypatch):
        safe_root = tmp_path / "workspace"
        outside = tmp_path / "other" / "file.txt"
        os.makedirs(safe_root, exist_ok=True)
        os.makedirs(outside.parent, exist_ok=True)

        monkeypatch.setenv("HERMES_WRITE_SAFE_ROOT", str(safe_root))
        assert _is_write_denied(str(outside)) is True

    def test_safe_root_env_ignores_empty_value(self, tmp_path: Path, monkeypatch):
        target = tmp_path / "regular.txt"
        monkeypatch.setenv("HERMES_WRITE_SAFE_ROOT", "")
        assert _is_write_denied(str(target)) is False

    def test_safe_root_unset_allows_all(self, tmp_path: Path, monkeypatch):
        target = tmp_path / "regular.txt"
        monkeypatch.delenv("HERMES_WRITE_SAFE_ROOT", raising=False)
        assert _is_write_denied(str(target)) is False

    def test_safe_root_with_tilde_expansion(self, tmp_path: Path, monkeypatch):
        """~ in HERMES_WRITE_SAFE_ROOT should be expanded."""
        # Use a real subdirectory of tmp_path so we can test tilde-style paths
        safe_root = tmp_path / "workspace"
        inside = safe_root / "file.txt"
        os.makedirs(safe_root, exist_ok=True)

        monkeypatch.setenv("HERMES_WRITE_SAFE_ROOT", str(safe_root))
        assert _is_write_denied(str(inside)) is False

    def test_safe_root_does_not_override_static_deny(self, tmp_path: Path, monkeypatch):
        """Even if a static-denied path is inside the safe root, it's still denied."""
        # Point safe root at home to include ~/.ssh
        monkeypatch.setenv("HERMES_WRITE_SAFE_ROOT", os.path.expanduser("~"))
        assert _is_write_denied(os.path.expanduser("~/.ssh/id_rsa")) is True


class TestCheckSensitivePathMacOSBypass:
    """Verify _check_sensitive_path blocks /private/etc paths (issue #8734)."""

    def test_etc_hosts_blocked(self):
        from tools.file_tools import _check_sensitive_path
        assert _check_sensitive_path("/etc/hosts") is not None

    def test_private_etc_hosts_blocked(self):
        from tools.file_tools import _check_sensitive_path
        assert _check_sensitive_path("/private/etc/hosts") is not None

    def test_private_etc_ssh_config_blocked(self):
        from tools.file_tools import _check_sensitive_path
        assert _check_sensitive_path("/private/etc/ssh/sshd_config") is not None

    def test_private_var_blocked(self):
        from tools.file_tools import _check_sensitive_path
        assert _check_sensitive_path("/private/var/db/something") is not None

    def test_boot_still_blocked(self):
        from tools.file_tools import _check_sensitive_path
        assert _check_sensitive_path("/boot/grub/grub.cfg") is not None

    def test_safe_path_allowed(self):
        from tools.file_tools import _check_sensitive_path
        assert _check_sensitive_path("/tmp/safe_file.txt") is None


class TestCheckSensitivePathMacOSTempAllowlist:
    """Verify ``_check_sensitive_path`` permits macOS user-writable temp
    trees that sit *syntactically* under ``/private/var/`` but are, by OS
    design, per-user writable locations.

    Regression for the bycatch introduced in 311dac19: the ``/private/var/``
    prefix added for the ``/private/etc`` symlink bypass also blocked
    ``tempfile.gettempdir()`` output on macOS (which resolves to
    ``/private/var/folders/…``) and ``/tmp`` (symlink to ``/private/tmp/``).
    Blocking those broke ``test_file_staleness.py`` on macOS and, more
    importantly, real user workflows where the agent writes to a temp dir.
    """

    def test_private_var_folders_allowed(self):
        """macOS ``tempfile.gettempdir()`` resolves under this prefix."""
        from tools.file_tools import _check_sensitive_path
        assert _check_sensitive_path(
            "/private/var/folders/fx/abc/T/my_temp_file.txt"
        ) is None

    def test_private_tmp_allowed(self):
        """``/tmp`` on macOS is a symlink into ``/private/tmp/``."""
        from tools.file_tools import _check_sensitive_path
        assert _check_sensitive_path("/private/tmp/scratch.txt") is None

    def test_actual_tempfile_path_allowed(self):
        """The exact path shape produced by ``tempfile.TemporaryDirectory()``
        on macOS must not trip the sensitive-path check (this was the
        ``test_file_staleness.py`` failure mode)."""
        import tempfile
        from tools.file_tools import _check_sensitive_path
        with tempfile.TemporaryDirectory() as tmp:
            candidate = os.path.join(tmp, "write_target.txt")
            assert _check_sensitive_path(candidate) is None, (
                f"Sensitive-path check rejected a plain tempfile path: {candidate!r}"
            )

    # --- Sensitive subtrees still blocked ---------------------------------

    def test_private_var_db_still_blocked(self):
        from tools.file_tools import _check_sensitive_path
        assert _check_sensitive_path("/private/var/db/something") is not None

    def test_private_var_log_still_blocked(self):
        from tools.file_tools import _check_sensitive_path
        assert _check_sensitive_path("/private/var/log/system.log") is not None

    def test_private_var_root_still_blocked(self):
        from tools.file_tools import _check_sensitive_path
        assert _check_sensitive_path("/private/var/root/.ssh/id_rsa") is not None

    def test_private_var_mail_still_blocked(self):
        from tools.file_tools import _check_sensitive_path
        assert _check_sensitive_path("/private/var/mail/alice") is not None

    def test_private_var_spool_still_blocked(self):
        from tools.file_tools import _check_sensitive_path
        assert _check_sensitive_path("/private/var/spool/cron/root") is not None

    # --- Canary: /etc/, /boot/, /private/etc/ stay blocked ----------------

    def test_private_etc_still_blocked_after_allowlist(self):
        """The allowlist must not weaken the /private/etc/ symlink-bypass
        guard that #8734 / 311dac19 established."""
        from tools.file_tools import _check_sensitive_path
        assert _check_sensitive_path("/private/etc/hosts") is not None
        assert _check_sensitive_path("/private/etc/ssh/sshd_config") is not None

    def test_non_macos_paths_still_blocked(self):
        from tools.file_tools import _check_sensitive_path
        assert _check_sensitive_path("/etc/passwd") is not None
        assert _check_sensitive_path("/boot/grub/grub.cfg") is not None
        assert _check_sensitive_path("/usr/lib/systemd/system/sshd.service") is not None

    def test_allowlist_helper_is_path_prefix_scoped(self):
        """``_is_allowlisted_sensitive_path`` must not match paths that
        merely *contain* an allowlisted substring — only prefix matches
        count.  Prevents trivial bypass via e.g. a sensitive file whose
        name happens to contain ``/private/var/folders/``."""
        from tools.file_tools import _is_allowlisted_sensitive_path
        # Literal allowlist prefix → True
        assert _is_allowlisted_sensitive_path(
            "/private/var/folders/fx/foo.txt",
            "/private/var/folders/fx/foo.txt",
        ) is True
        # Path that contains the substring but doesn't start with it → False
        assert _is_allowlisted_sensitive_path(
            "/private/var/db/fake/private/var/folders/x",
            "/private/var/db/fake/private/var/folders/x",
        ) is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
