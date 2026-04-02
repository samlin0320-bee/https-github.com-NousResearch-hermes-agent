"""ByteRover CLI client — binary resolution and subprocess runner.

Handles finding the ``brv`` binary on PATH or well-known install locations,
and provides ``run_brv()`` for safely executing brv commands as subprocesses.
"""

import json
import logging
import os
import shutil
import subprocess
import threading
import time
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# brv binary resolution (mirrors OpenClaw plugin pattern)
# ---------------------------------------------------------------------------

_brv_path_lock = threading.Lock()
_cached_brv_path: Optional[str] = None


def _resolve_brv_path() -> Optional[str]:
    """Find the brv binary, checking well-known install locations."""
    global _cached_brv_path
    with _brv_path_lock:
        if _cached_brv_path is not None:
            return _cached_brv_path if _cached_brv_path != "" else None

    # Filesystem probes outside lock (potentially slow on NFS/remote mounts)
    found = shutil.which("brv")
    if not found:
        home = Path.home()
        candidates = [
            home / ".brv-cli" / "bin" / "brv",
            Path("/usr/local/bin/brv"),
            Path("/usr/bin/brv"),
            home / ".npm-global" / "bin" / "brv",
        ]
        for candidate in candidates:
            if candidate.exists():
                found = str(candidate)
                break

    # Double-checked locking: another thread may have cached a result
    with _brv_path_lock:
        if _cached_brv_path is not None:
            return _cached_brv_path if _cached_brv_path != "" else None
        _cached_brv_path = found or ""
    return found


def check_requirements() -> bool:
    """Return True if brv CLI is available."""
    return _resolve_brv_path() is not None


def _reset_cache() -> None:
    """Reset the cached brv path (for testing)."""
    global _cached_brv_path
    with _brv_path_lock:
        _cached_brv_path = None


# ---------------------------------------------------------------------------
# Default working directory — centralises the context tree under ~/.hermes/
# ---------------------------------------------------------------------------


def get_hermes_home() -> Path:
    """Return the Hermes home directory (lazy, testable).

    Tests can monkeypatch this single function instead of patching
    module-level variables in three separate submodules.
    """
    return Path(os.getenv("HERMES_HOME", Path.home() / ".hermes"))


def get_brv_default_cwd() -> Path:
    """Return the default brv working directory (~/.hermes/byterover/)."""
    return get_hermes_home() / "byterover"


# ---------------------------------------------------------------------------
# Operation logging — writes to ~/.hermes/byterover/logs/brv.log
# ---------------------------------------------------------------------------

_brv_logger_lock = threading.Lock()
_brv_file_logger: Optional[logging.Logger] = None


def _get_brv_file_logger() -> logging.Logger:
    """Return (or create) a dedicated file logger for brv operations."""
    global _brv_file_logger
    with _brv_logger_lock:
        if _brv_file_logger is not None:
            return _brv_file_logger

        brv_logger = logging.getLogger("byterover.operations")
        brv_logger.setLevel(logging.DEBUG)
        brv_logger.propagate = False  # Don't bubble to root logger

        log_dir = get_brv_default_cwd() / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / "brv.log"

        handler = RotatingFileHandler(
            log_path, maxBytes=2 * 1024 * 1024, backupCount=2,
        )
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S",
        ))
        brv_logger.addHandler(handler)
        _brv_file_logger = brv_logger
    return _brv_file_logger


def _reset_brv_file_logger() -> None:
    """Reset the cached file logger (for testing)."""
    global _brv_file_logger
    with _brv_logger_lock:
        if _brv_file_logger is not None:
            for h in _brv_file_logger.handlers[:]:
                h.close()
                _brv_file_logger.removeHandler(h)
        _brv_file_logger = None


def _log_brv_operation(command: str, result: dict, duration_s: float) -> None:
    """Write a structured log entry for a brv operation."""
    try:
        brv_log = _get_brv_file_logger()
        status = "OK" if result.get("success") else "ERROR"
        detail = result.get("output", "") or result.get("error", "")
        # Truncate long detail to keep log lines reasonable
        if len(detail) > 500:
            detail = detail[:500] + "…"
        brv_log.info(
            "[%s] %s (%.1fs) %s",
            status, command, duration_s, detail,
        )
    except Exception:
        pass  # Logging must never break the caller


# ---------------------------------------------------------------------------
# Subprocess helpers
# ---------------------------------------------------------------------------

BRV_TIMEOUT = 120  # seconds — brv query can be slow on large context trees
BRV_CURATE_TIMEOUT = 300  # curate may involve LLM processing

def _build_env(extra_env: dict = None) -> dict:
    """Build environment with resolved brv bin dir on PATH.

    *extra_env* is merged last so callers can pass secrets (e.g. API keys)
    via environment variables instead of CLI arguments, keeping them out of
    ``/proc/*/cmdline``.
    """
    env = os.environ.copy()
    brv_path = _resolve_brv_path()
    if brv_path:
        brv_bin_dir = str(Path(brv_path).parent)
    else:
        brv_bin_dir = str(Path.home() / ".brv-cli" / "bin")
    env["PATH"] = brv_bin_dir + os.pathsep + env.get("PATH", "")
    if extra_env:
        env.update(extra_env)
    return env


def run_brv(args: List[str], timeout: int = BRV_TIMEOUT, cwd: str = None,
            extra_env: dict = None) -> dict:
    """Run a brv CLI command and return structured result.

    Returns dict with keys: success, output, error.
    When *cwd* is not provided, defaults to ``~/.hermes/byterover/``
    so the context tree is stored centrally.

    *extra_env* is merged into the subprocess environment — use this to
    pass secrets instead of CLI arguments (avoids ``/proc`` exposure).
    """
    brv_path = _resolve_brv_path()
    if not brv_path:
        return {"success": False, "error": "brv CLI not found. Install with: npm install -g byterover-cli"}

    cmd = [brv_path] + args
    effective_cwd = cwd or str(get_brv_default_cwd())
    # Ensure the directory exists
    Path(effective_cwd).mkdir(parents=True, exist_ok=True)

    t0 = time.monotonic()
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=effective_cwd,
            env=_build_env(extra_env),
        )

        stdout = result.stdout.strip()
        stderr = result.stderr.strip()

        if result.returncode == 0:
            out = {"success": True, "output": stdout}
        else:
            error_msg = stderr or stdout or f"brv exited with code {result.returncode}"
            out = {"success": False, "error": error_msg}

        _log_brv_operation(" ".join(args[:2]), out, time.monotonic() - t0)
        return out

    except subprocess.TimeoutExpired:
        out = {"success": False, "error": f"brv command timed out after {timeout}s"}
        _log_brv_operation(" ".join(args[:2]), out, time.monotonic() - t0)
        return out
    except FileNotFoundError:
        _reset_cache()
        return {"success": False, "error": "brv CLI not found. Install with: npm install -g byterover-cli"}
    except Exception as e:
        out = {"success": False, "error": f"Failed to run brv: {str(e)}"}
        _log_brv_operation(" ".join(args[:2]), out, time.monotonic() - t0)
        return out


def run_brv_curate_sync(args: List[str], timeout: int = BRV_CURATE_TIMEOUT,
                          cwd: str = None, extra_env: dict = None) -> dict:
    """Run ``brv curate`` synchronously, parsing streaming JSON events.

    Unlike ``run_brv()``, this adds ``--format json`` and parses the
    line-delimited JSON output to detect actual task success or failure
    (brv curate always exits 0 even when the async LLM task fails).

    Returns dict with keys: success, output, error.
    """
    brv_path = _resolve_brv_path()
    if not brv_path:
        return {"success": False, "error": "brv CLI not found. Install with: npm install -g byterover-cli"}

    # Inject --format json if not already present
    full_args = list(args)
    if "--format" not in full_args:
        full_args.extend(["--format", "json"])

    cmd = [brv_path] + full_args
    effective_cwd = cwd or str(get_brv_default_cwd())
    Path(effective_cwd).mkdir(parents=True, exist_ok=True)

    t0 = time.monotonic()
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=effective_cwd,
            env=_build_env(extra_env),
        )

        stdout = result.stdout.strip()
        stderr = result.stderr.strip()

        if result.returncode != 0:
            error_msg = stderr or stdout or f"brv exited with code {result.returncode}"
            out = {"success": False, "error": error_msg}
            _log_brv_operation("curate", out, time.monotonic() - t0)
            return out

        # Parse line-delimited JSON events to find actual status.
        # brv curate --format json streams events like:
        #   {"data":{"event":"thinking",...},"success":true,...}
        #   {"data":{"event":"completed",...},"success":true,...}
        #   {"data":{"event":"error","message":"..."},"success":false,...}
        last_error = None
        completed = False
        completed_data = None

        for line in stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue

            data = event.get("data", {})
            evt_type = data.get("event", "")

            if evt_type == "error":
                last_error = data.get("message") or event.get("error") or "Unknown curate error"
            elif evt_type == "completed":
                completed = True
                completed_data = data

        if last_error:
            out = {"success": False, "error": last_error}
        elif completed:
            # Build a summary from operations if available
            summary_parts = []
            ops = completed_data.get("operations", []) if completed_data else []
            for op in ops:
                op_type = op.get("type", "unknown")
                op_file = op.get("file", "")
                summary_parts.append(f"{op_type}: {op_file}" if op_file else op_type)
            summary = "; ".join(summary_parts) if summary_parts else "Knowledge curated successfully."
            out = {"success": True, "output": summary}
        else:
            # No error and no completed event — treat as success with raw output
            out = {"success": True, "output": stdout or "Curate task submitted."}

        _log_brv_operation("curate", out, time.monotonic() - t0)
        return out

    except subprocess.TimeoutExpired:
        out = {"success": False, "error": f"brv curate timed out after {timeout}s"}
        _log_brv_operation("curate", out, time.monotonic() - t0)
        return out
    except FileNotFoundError:
        _reset_cache()
        return {"success": False, "error": "brv CLI not found. Install with: npm install -g byterover-cli"}
    except Exception as e:
        out = {"success": False, "error": f"Failed to run brv curate: {str(e)}"}
        _log_brv_operation("curate", out, time.monotonic() - t0)
        return out
