"""Requisition preview and creation helpers for Oracle Fusion."""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import requests

from config import get_settings
from models import PreviewSummary, RequisitionPayload, RequisitionResult
from tools.fusion_auth import AuthenticationError, get_auth_header
from tools.fusion_lookup import escape_oracle_q_value, get_default_business_unit_name

LOGGER = logging.getLogger(__name__)
REQUEST_TIMEOUT = 30


def _request(method: str, endpoint: str, **kwargs: Any) -> requests.Response | dict[str, Any]:
    """Send an authenticated request to Oracle Fusion or log it in dry-run mode."""

    settings = get_settings()
    url = f"{settings.fusion_api_base}/{endpoint.lstrip('/')}"
    headers = kwargs.pop("headers", {})
    merged_headers = {
        **get_auth_header(),
        "Accept": "application/json",
        "Content-Type": "application/json",
        **headers,
    }

    if settings.dry_run:
        LOGGER.info(
            "DRY_RUN %s %s payload=%s params=%s",
            method.upper(),
            url,
            json.dumps(kwargs.get("json"), indent=2) if kwargs.get("json") is not None else None,
            kwargs.get("params"),
        )
        return {"dry_run": True, "url": url, "method": method.upper(), **kwargs}

    try:
        response = requests.request(
            method=method,
            url=url,
            headers=merged_headers,
            timeout=REQUEST_TIMEOUT,
            **kwargs,
        )
    except requests.Timeout as exc:
        raise RuntimeError("Could not reach Oracle Fusion. Check FUSION_BASE_URL.") from exc
    except requests.RequestException as exc:
        raise RuntimeError(f"Oracle Fusion request failed: {exc}") from exc

    if response.status_code == 401:
        raise AuthenticationError(
            "Oracle Fusion authentication failed. Check FUSION_USERNAME and FUSION_PASSWORD."
        )

    return response


def get_requester_person_id(email: str) -> int:
    """Resolve a requester's Fusion PersonId from their work email."""

    response = _request(
        "GET",
        "workers",
        params={
            "q": f'WorkEmail="{escape_oracle_q_value(email)}"',
            "fields": "PersonId,DisplayName",
        },
    )

    if isinstance(response, dict):
        LOGGER.info("DRY_RUN requester lookup for %s", email)
        return 0

    response.raise_for_status()
    payload = response.json()
    items = payload.get("items", [])
    if not items:
        raise RuntimeError(f"Requester not found in Oracle Fusion for email: {email}")
    person_id = int(items[0]["PersonId"])
    LOGGER.info("Resolved requester email %s to PersonId %s", email, person_id)
    return person_id


def discover_requester_email() -> str:
    """Return the configured requester email or derive it from the connected Fusion user."""

    settings = get_settings()
    if settings.fusion_requester_email:
        return settings.fusion_requester_email

    if settings.dry_run:
        derived = settings.fusion_username if "@" in settings.fusion_username else "dryrun@example.com"
        LOGGER.info("DRY_RUN requester email discovery -> %s", derived)
        return derived

    candidates: list[str] = []
    if "@" in settings.fusion_username:
        candidates.append(settings.fusion_username)

    for candidate in candidates:
        response = _request(
            "GET",
            "workers",
            params={
                "q": f'WorkEmail="{escape_oracle_q_value(candidate)}"',
                "fields": "PersonId,DisplayName,WorkEmail",
            },
        )
        assert isinstance(response, requests.Response)
        response.raise_for_status()
        items = response.json().get("items", [])
        if items:
            LOGGER.info("Discovered requester email from connected user context: %s", candidate)
            return candidate

    raise RuntimeError(
        "Could not discover requester email from the connected Fusion user. "
        "Set FUSION_REQUESTER_EMAIL manually."
    )


def format_preview(payload: dict) -> dict:
    """Return a normalized requisition preview for CLI display."""

    requisition_payload = RequisitionPayload.model_validate(payload)
    currency = (
        requisition_payload.lines[0].currency
        if requisition_payload.lines
        else get_settings().fusion_currency
    )
    total_amount = sum(line.quantity * line.unit_price for line in requisition_payload.lines)
    preview = PreviewSummary(
        header_description=requisition_payload.header_description,
        requester_email=requisition_payload.requester_email or "Needs configuration before live creation",
        business_unit_name=requisition_payload.business_unit_name or "Needs configuration before live creation",
        currency=currency,
        total_amount=total_amount,
        lines=requisition_payload.lines,
    )
    return preview.model_dump()


def _extract_error_message(payload: dict) -> str:
    """Extract Oracle Fusion error details from a REST error response."""

    details = payload.get("o:errorDetails") or []
    messages = [detail.get("detail", "").strip() for detail in details if detail.get("detail")]
    if messages:
        return " | ".join(messages)
    return payload.get("title") or payload.get("detail") or "Unknown Oracle Fusion error."


def _extract_response_error(response: requests.Response) -> str:
    """Extract the most useful error message from a Fusion or gateway response."""

    try:
        payload = response.json()
    except ValueError:
        text = (response.text or "").strip()
        if text:
            compact = " ".join(text.split())
            return compact[:500]
        return f"HTTP {response.status_code} with a non-JSON response body."
    return _extract_error_message(payload)


def create_requisition(payload: RequisitionPayload) -> RequisitionResult:
    """Create a purchase requisition in Oracle Fusion from a validated payload."""

    settings = get_settings()
    requester_email = payload.requester_email or discover_requester_email()
    business_unit_name = payload.business_unit_name or get_default_business_unit_name()
    prepared_by_id = get_requester_person_id(requester_email)
    request_body = {
        "RequisitioningBUName": business_unit_name,
        "Description": payload.header_description,
        "PreparedById": prepared_by_id,
        "RequisitionLines": [
            {
                "LineNumber": line.line_number,
                "ItemDescription": line.item_description,
                "Quantity": line.quantity,
                "UOMCode": line.uom_code,
                "UnitPrice": line.unit_price,
                "CurrencyCode": line.currency,
                "CategoryId": line.category_id,
                "NeedByDate": line.need_by_date,
                "RequestedDeliveryDate": line.need_by_date,
            }
            for line in payload.lines
        ],
    }

    if settings.dry_run:
        _request("POST", "purchaseRequisitions", json=request_body)
        total_amount = sum(line.quantity * line.unit_price for line in payload.lines)
        return RequisitionResult(
            requisition_id="DRY_RUN",
            requisition_number="DRY_RUN",
            status="Dry Run",
            total_amount=total_amount,
            currency=payload.lines[0].currency if payload.lines else settings.fusion_currency,
            lines_created=len(payload.lines),
        )

    last_response: requests.Response | None = None
    for attempt in range(2):
        response = _request("POST", "purchaseRequisitions", json=request_body)
        assert isinstance(response, requests.Response)
        last_response = response

        if response.status_code in {500, 503} and attempt == 0:
            LOGGER.warning(
                "Fusion returned %s when creating requisition. Retrying once in 5 seconds.",
                response.status_code,
            )
            time.sleep(5)
            continue

        if response.status_code in {400, 422}:
            raise RuntimeError(_extract_response_error(response))

        if response.status_code != 201:
            if response.status_code in {301, 302, 303, 307, 308, 403}:
                raise RuntimeError(
                    "Oracle Fusion did not allow requisition creation. "
                    f"HTTP {response.status_code}: {_extract_response_error(response)}"
                )
            response.raise_for_status()

        response_json = response.json()
        total_amount = sum(line.quantity * line.unit_price for line in payload.lines)
        requisition_id = str(
            response_json.get("RequisitionHeaderId")
            or response_json.get("RequisitionId")
            or response_json.get("ReqHeaderId")
            or ""
        )
        requisition_number = str(
            response_json.get("RequisitionNumber")
            or response_json.get("ReqNumber")
            or ""
        )
        status = str(response_json.get("Status") or response_json.get("DocumentStatus") or "Created")
        LOGGER.info("Created Oracle Fusion requisition %s", requisition_number or requisition_id)
        return RequisitionResult(
            requisition_id=requisition_id,
            requisition_number=requisition_number,
            status=status,
            total_amount=total_amount,
            currency=payload.lines[0].currency if payload.lines else settings.fusion_currency,
            lines_created=len(payload.lines),
        )

    assert last_response is not None
    last_response.raise_for_status()
    raise RuntimeError("Unexpected Oracle Fusion requisition creation failure.")
