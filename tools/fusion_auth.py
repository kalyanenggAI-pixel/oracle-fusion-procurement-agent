"""Authentication helpers for Oracle Fusion REST APIs."""

from __future__ import annotations

import base64
import logging
from functools import lru_cache

import requests

from config import get_settings

LOGGER = logging.getLogger(__name__)
REQUEST_TIMEOUT = 30


class AuthenticationError(RuntimeError):
    """Raised when Oracle Fusion authentication fails."""


@lru_cache(maxsize=1)
def get_auth_header() -> dict[str, str]:
    """Return a cached HTTP Basic Auth header for Oracle Fusion."""

    settings = get_settings()
    raw_credentials = f"{settings.fusion_username}:{settings.fusion_password}"
    encoded = base64.b64encode(raw_credentials.encode("utf-8")).decode("utf-8")
    LOGGER.info("Prepared cached Oracle Fusion authentication header.")
    return {"Authorization": f"Basic {encoded}"}


def test_connection() -> dict:
    """Verify that Oracle Fusion credentials and base URL are valid."""

    settings = get_settings()
    endpoint = f"{settings.fusion_api_base}/businessUnits"
    params = {"limit": 1}
    headers = {
        **get_auth_header(),
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    if settings.dry_run:
        LOGGER.info("DRY_RUN enabled. Skipping live Fusion connection test: %s", endpoint)
        return {"status": "dry_run", "url": endpoint, "params": params}

    try:
        response = requests.get(
            endpoint,
            headers=headers,
            params=params,
            timeout=REQUEST_TIMEOUT,
        )
    except requests.Timeout as exc:
        raise RuntimeError("Could not reach Oracle Fusion. Check FUSION_BASE_URL.") from exc
    except requests.RequestException as exc:
        raise RuntimeError(f"Oracle Fusion connection test failed: {exc}") from exc

    if response.status_code == 401:
        raise AuthenticationError(
            "Oracle Fusion authentication failed. Check FUSION_USERNAME and FUSION_PASSWORD."
        )

    response.raise_for_status()
    LOGGER.info("Oracle Fusion connection test succeeded.")
    return response.json()
