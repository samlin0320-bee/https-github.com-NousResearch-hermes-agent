# ABOUTME: Steel (steel.dev) cloud browser provider for headless browser sessions.
# ABOUTME: Implements CloudBrowserProvider ABC using Steel's REST API over CDP.
"""Steel cloud browser provider."""

import logging
import os
import uuid
from typing import Dict

import requests

from tools.browser_providers.base import CloudBrowserProvider

logger = logging.getLogger(__name__)

_DEFAULT_BASE_URL = "https://api.steel.dev"


class SteelProvider(CloudBrowserProvider):
    """Steel (https://steel.dev) cloud browser backend.

    Provides headless browser sessions with optional proxy rotation,
    CAPTCHA solving, and configurable timeouts.  Activated when
    ``STEEL_API_KEY`` is set and ``config["browser"]["cloud_provider"]``
    is ``"steel"``.

    Environment Variables:
    - STEEL_API_KEY: API key (required)
    - STEEL_BASE_URL: Override base URL for self-hosted instances
    - STEEL_USE_PROXY: Enable residential proxy (default: disabled)
    - STEEL_SOLVE_CAPTCHA: Enable CAPTCHA solving (default: disabled)
    - STEEL_SESSION_TIMEOUT: Session timeout in milliseconds
    """

    def provider_name(self) -> str:
        return "Steel"

    def is_configured(self) -> bool:
        return bool(os.environ.get("STEEL_API_KEY"))

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _base_url(self) -> str:
        return os.environ.get("STEEL_BASE_URL", _DEFAULT_BASE_URL).rstrip("/")

    def _headers(self) -> Dict[str, str]:
        api_key = os.environ.get("STEEL_API_KEY")
        if not api_key:
            raise ValueError(
                "STEEL_API_KEY environment variable is required. "
                "Get your key at https://steel.dev"
            )
        return {
            "Content-Type": "application/json",
            "Steel-Api-Key": api_key,
        }

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    def create_session(self, task_id: str) -> Dict[str, object]:
        body: Dict[str, object] = {}

        if os.environ.get("STEEL_USE_PROXY", "").lower() in ("1", "true", "yes"):
            body["useProxy"] = True
        if os.environ.get("STEEL_SOLVE_CAPTCHA", "").lower() in ("1", "true", "yes"):
            body["solveCaptcha"] = True

        timeout_ms = os.environ.get("STEEL_SESSION_TIMEOUT")
        if timeout_ms:
            try:
                body["sessionTimeout"] = int(timeout_ms)
            except ValueError:
                logger.warning("Invalid STEEL_SESSION_TIMEOUT value: %s", timeout_ms)

        response = requests.post(
            f"{self._base_url()}/v1/sessions",
            headers=self._headers(),
            json=body,
            timeout=30,
        )

        if not response.ok:
            raise RuntimeError(
                f"Failed to create Steel session: "
                f"{response.status_code} {response.text}"
            )

        session_data = response.json()
        session_id = session_data["id"]
        api_key = os.environ["STEEL_API_KEY"]
        cdp_url = f"wss://connect.steel.dev?apiKey={api_key}&sessionId={session_id}"
        session_name = f"hermes_{task_id}_{uuid.uuid4().hex[:8]}"

        features: Dict[str, object] = {"steel": True}
        if body.get("useProxy"):
            features["proxy"] = True
        if body.get("solveCaptcha"):
            features["captcha_solving"] = True

        session_viewer_url = session_data.get("sessionViewerUrl", "")
        if session_viewer_url:
            logger.info(
                "Steel session %s — live viewer: %s", session_name, session_viewer_url
            )
        else:
            logger.info("Created Steel session %s", session_name)

        result: Dict[str, object] = {
            "session_name": session_name,
            "bb_session_id": session_id,
            "cdp_url": cdp_url,
            "features": features,
        }
        if session_viewer_url:
            result["session_viewer_url"] = session_viewer_url
        return result

    def close_session(self, session_id: str) -> bool:
        try:
            response = requests.post(
                f"{self._base_url()}/v1/sessions/{session_id}/release",
                headers=self._headers(),
                timeout=10,
            )
            if response.status_code in (200, 201, 204):
                logger.debug("Successfully closed Steel session %s", session_id)
                return True
            elif response.status_code == 404:
                logger.debug("Steel session %s already gone", session_id)
                return True
            else:
                logger.warning(
                    "Failed to close Steel session %s: HTTP %s - %s",
                    session_id,
                    response.status_code,
                    response.text[:200],
                )
                return False
        except Exception as e:
            logger.error("Exception closing Steel session %s: %s", session_id, e)
            return False

    def emergency_cleanup(self, session_id: str) -> None:
        api_key = os.environ.get("STEEL_API_KEY")
        if not api_key:
            logger.warning(
                "Cannot emergency-cleanup Steel session %s — missing credentials",
                session_id,
            )
            return
        base = os.environ.get("STEEL_BASE_URL", _DEFAULT_BASE_URL).rstrip("/")
        try:
            requests.post(
                f"{base}/v1/sessions/{session_id}/release",
                headers={
                    "Content-Type": "application/json",
                    "Steel-Api-Key": api_key,
                },
                timeout=5,
            )
        except Exception as e:
            logger.debug(
                "Emergency cleanup failed for Steel session %s: %s", session_id, e
            )
