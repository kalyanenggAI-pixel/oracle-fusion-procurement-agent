"""Lookup helpers for Oracle Fusion business units, UOMs, and categories."""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

import requests
from openai import OpenAI

from config import get_settings
from models import FusionLine, QuoteLine
from tools.fusion_auth import AuthenticationError, get_auth_header

LOGGER = logging.getLogger(__name__)
REQUEST_TIMEOUT = 30
UOM_FALLBACK_MAP = {
    "each": "Ea",
    "ea": "Ea",
    "unit": "Ea",
    "box": "BX",
    "boxes": "BX",
    "pack": "PK",
    "packs": "PK",
    "kg": "KG",
    "kilogram": "KG",
    "ltr": "LT",
    "litre": "LT",
    "liter": "LT",
    "mtr": "M",
    "metre": "M",
    "meter": "M",
}
STOP_WORDS = {"and", "the", "for", "with", "from", "a", "an", "of"}


def _request(method: str, endpoint: str, **kwargs: Any) -> dict:
    """Send an authenticated request to Oracle Fusion and return JSON."""

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
        LOGGER.info("DRY_RUN %s %s params=%s", method.upper(), url, kwargs.get("params"))
        return {"items": [], "dry_run": True, "url": url, "params": kwargs.get("params")}

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

    response.raise_for_status()
    return response.json()


def get_business_unit_id(bu_name: str) -> int:
    """Resolve a Business Unit name to its Oracle Fusion identifier."""

    if get_settings().dry_run:
        LOGGER.info("DRY_RUN business unit lookup for %s", bu_name)
        return 0

    payload = _request(
        "GET",
        "businessUnits",
        params={
            "q": f'BusinessUnitName="{bu_name}"',
            "fields": "BusinessUnitId,BusinessUnitName",
        },
    )
    items = payload.get("items", [])
    if not items:
        raise RuntimeError(f"Business Unit not found in Oracle Fusion: {bu_name}")
    business_unit_id = int(items[0]["BusinessUnitId"])
    LOGGER.info("Resolved business unit %s to %s", bu_name, business_unit_id)
    return business_unit_id


def list_business_units(limit: int = 25) -> list[dict[str, Any]]:
    """Return available Business Units from Oracle Fusion."""

    if get_settings().dry_run:
        LOGGER.info("DRY_RUN business unit listing.")
        return [{"BusinessUnitId": 0, "BusinessUnitName": "Dry Run BU"}]

    payload = _request(
        "GET",
        "businessUnits",
        params={
            "limit": limit,
            "fields": "BusinessUnitId,BusinessUnitName",
        },
    )
    return payload.get("items", [])


def get_default_business_unit_name() -> str:
    """Return the configured BU or discover a default BU from Fusion."""

    settings = get_settings()
    if settings.fusion_bu_name:
        return settings.fusion_bu_name

    business_units = list_business_units(limit=5)
    if not business_units:
        raise RuntimeError(
            "Could not discover a Business Unit from Oracle Fusion. Set FUSION_BU_NAME manually."
        )

    chosen_name = business_units[0]["BusinessUnitName"]
    if len(business_units) > 1:
        LOGGER.warning(
            "Multiple Business Units found. Defaulting to the first available BU: %s",
            chosen_name,
        )
    else:
        LOGGER.info("Discovered default Business Unit: %s", chosen_name)
    return chosen_name


def resolve_uom_details(uom_text: str) -> dict[str, str]:
    """Resolve a raw UOM label to an Oracle Fusion UOM code with derivation metadata."""

    normalized = (uom_text or "").strip()
    if normalized:
        payload = _request(
            "GET",
            "unitOfMeasures",
            params={
                "q": f'UnitOfMeasureName="{normalized}"',
                "fields": "UomCode,UnitOfMeasureName",
            },
        )
        items = payload.get("items", [])
        if items:
            uom_code = items[0]["UomCode"]
            LOGGER.info("Resolved UOM %s to %s via Fusion lookup", normalized, uom_code)
            return {
                "uom_code": uom_code,
                "derivation_source": "oracle_uom_lookup",
                "derivation_notes": f'Oracle UOM lookup matched source UOM "{normalized}".',
            }

    fallback = UOM_FALLBACK_MAP.get(normalized.lower())
    if fallback:
        LOGGER.warning("Using fallback UOM mapping for %s -> %s", normalized, fallback)
        return {
            "uom_code": fallback,
            "derivation_source": "fallback_uom_map",
            "derivation_notes": (
                f'Oracle UOM was not matched directly. Used fallback map from "{normalized}" to "{fallback}".'
            ),
        }

    LOGGER.warning('UOM "%s" was not found. Defaulting to "Ea".', normalized or "<empty>")
    return {
        "uom_code": "Ea",
        "derivation_source": "default_uom",
        "derivation_notes": 'Oracle UOM could not be derived. Defaulted to "Ea".',
    }


def resolve_uom(uom_text: str) -> str:
    """Resolve a raw UOM label to an Oracle Fusion UOM code."""

    return resolve_uom_details(uom_text)["uom_code"]


def _search_category(keyword: str) -> tuple[int, str] | None:
    """Search Fusion purchasing categories using a keyword."""

    cleaned = keyword.strip()
    if not cleaned:
        return None

    payload = _request(
        "GET",
        "purchasingCategories",
        params={
            "q": f'CategoryName like "%{cleaned}%"',
            "fields": "CategoryId,CategoryName,SegmentCode",
        },
    )
    items = payload.get("items", [])
    if not items:
        return None
    return int(items[0]["CategoryId"]), items[0]["CategoryName"]


def _meaningful_words(item_description: str) -> list[str]:
    """Return the first meaningful words from an item description."""

    words = [
        word.strip(",.()[]{}").lower()
        for word in item_description.split()
        if word.strip(",.()[]{}")
    ]
    filtered = [word for word in words if word not in STOP_WORDS and len(word) > 1]
    return filtered[:2]


def _suggest_category_keyword(item_description: str) -> str | None:
    """Ask OpenAI for a short purchasing category keyword."""

    settings = get_settings()
    client = OpenAI(api_key=settings.openai_api_key)
    response = client.chat.completions.create(
        model=settings.openai_model,
        messages=[
            {
                "role": "user",
                "content": (
                    f"What Oracle Purchasing category best fits: "
                    f"'{item_description}'? Reply with one short keyword only."
                ),
            }
        ],
    )
    raw = (response.choices[0].message.content or "").strip()
    suggestion = raw.splitlines()[0] if raw else ""
    LOGGER.info("OpenAI suggested category keyword %s for %s", suggestion, item_description)
    return suggestion or None


def _get_first_available_category() -> tuple[int, str]:
    """Return the first available purchasing category as a safe default."""

    if get_settings().dry_run:
        LOGGER.info("DRY_RUN category fallback lookup.")
        return 0, "Dry Run Category"

    payload = _request(
        "GET",
        "purchasingCategories",
        params={"limit": 1, "fields": "CategoryId,CategoryName"},
    )
    items = payload.get("items", [])
    if not items:
        raise RuntimeError("Could not find any Oracle purchasing categories to use as default.")
    return int(items[0]["CategoryId"]), items[0]["CategoryName"]


def resolve_category_details(item_description: str, category_hint: str) -> dict[str, Any]:
    """Resolve an item description to an Oracle Fusion category with derivation metadata."""

    if get_settings().dry_run:
        fallback_name = category_hint.strip() or " ".join(_meaningful_words(item_description)) or "General"
        LOGGER.info("DRY_RUN category resolution for %s -> %s", item_description, fallback_name)
        return {
            "category_id": 0,
            "category_name": fallback_name.title(),
            "derivation_source": "dry_run_hint",
            "derivation_notes": "Dry-run category derived from extracted hint/description heuristics.",
        }

    search_terms: list[str] = []
    if category_hint.strip():
        search_terms.append(category_hint.strip())
    meaningful = _meaningful_words(item_description)
    if meaningful:
        search_terms.append(" ".join(meaningful))

    for term in search_terms:
        match = _search_category(term)
        if match:
            LOGGER.info("Resolved category for %s via keyword %s", item_description, term)
            return {
                "category_id": match[0],
                "category_name": match[1],
                "derivation_source": "oracle_category_lookup",
                "derivation_notes": f'Oracle category matched using keyword "{term}".',
            }

    try:
        suggested = _suggest_category_keyword(item_description)
    except Exception as exc:
        LOGGER.warning("OpenAI category suggestion failed for %s: %s", item_description, exc)
        suggested = None

    if suggested:
        match = _search_category(suggested)
        if match:
            LOGGER.info("Resolved category for %s via OpenAI keyword %s", item_description, suggested)
            return {
                "category_id": match[0],
                "category_name": match[1],
                "derivation_source": "oracle_category_lookup_with_ai_hint",
                "derivation_notes": (
                    f'Oracle category matched after AI suggested the keyword "{suggested}".'
                ),
            }

    fallback = _get_first_available_category()
    LOGGER.warning("Falling back to first available category for %s -> %s", item_description, fallback[1])
    return {
        "category_id": fallback[0],
        "category_name": fallback[1],
        "derivation_source": "oracle_category_default",
        "derivation_notes": "No strong Oracle category match was found. Used the first available category.",
    }


def resolve_category(item_description: str, category_hint: str) -> tuple[int, str]:
    """Resolve an item description to an Oracle Fusion purchasing category."""

    details = resolve_category_details(item_description, category_hint)
    return int(details["category_id"]), str(details["category_name"])


def resolve_all_lines(lines: list[dict]) -> dict[str, Any]:
    """Resolve all quote lines to Oracle Fusion categories and UOMs."""

    from tools.fusion_requisition import discover_requester_email

    settings = get_settings()
    resolved_lines: list[FusionLine] = []
    need_by_date = (date.today() + timedelta(days=7)).isoformat()
    business_unit_name = get_default_business_unit_name()
    requester_email = discover_requester_email()

    for line_dict in lines:
        quote_line = QuoteLine.model_validate(line_dict)
        category_details = resolve_category_details(
            quote_line.item_description,
            quote_line.category_hint,
        )
        uom_details = resolve_uom_details(quote_line.unit_of_measure)
        category_id = int(category_details["category_id"])
        category_name = str(category_details["category_name"])
        uom_code = str(uom_details["uom_code"])
        derivation_source = (
            "oracle_derived"
            if category_details["derivation_source"].startswith("oracle")
            or uom_details["derivation_source"].startswith("oracle")
            else "fallback_derived"
        )
        derivation_notes = " ".join(
            note
            for note in [
                category_details.get("derivation_notes", ""),
                uom_details.get("derivation_notes", ""),
                "Quantity and price are currently preserved from the quote unless Oracle-specific item conversions are added.",
            ]
            if note
        )
        resolved_lines.append(
            FusionLine(
                line_number=quote_line.line_number,
                item_description=quote_line.item_description,
                quantity=quote_line.quantity,
                unit_price=quote_line.unit_price,
                currency=quote_line.currency or settings.fusion_currency,
                uom_code=uom_code,
                category_id=category_id,
                category_name=category_name,
                need_by_date=need_by_date,
                source_quantity=quote_line.quantity,
                source_unit_price=quote_line.unit_price,
                source_currency=quote_line.currency or settings.fusion_currency,
                source_unit_of_measure=quote_line.unit_of_measure,
                normalized_quantity=quote_line.quantity,
                normalized_unit_price=quote_line.unit_price,
                derivation_source=derivation_source,
                derivation_notes=derivation_notes,
            )
        )

    total_amount = sum(line.quantity * line.unit_price for line in resolved_lines)
    return {
        "header_description": "AI Agent - Supplier Quote Requisition",
        "requester_email": requester_email,
        "business_unit_name": business_unit_name,
        "currency": resolved_lines[0].currency if resolved_lines else settings.fusion_currency,
        "total_amount": total_amount,
        "derivation_policy": (
            "Source values come from the supplier quote. Oracle-ready values such as category, UOM code, "
            "and any future quantity/UOM conversions are derived from the connected Oracle environment."
        ),
        "lines": [line.model_dump() for line in resolved_lines],
    }
