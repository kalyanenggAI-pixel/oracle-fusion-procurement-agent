"""PDF extraction tool for supplier quotes."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import fitz
import pdfplumber
from openai import OpenAI

from config import get_settings
from models import SupplierQuote

LOGGER = logging.getLogger(__name__)

PARSING_PROMPT = """You are a procurement data extraction assistant. Extract all line items from this supplier quote.
Return ONLY valid JSON matching this schema:
{
  "supplier_name": "string",
  "quote_date": "YYYY-MM-DD",
  "currency": "ISO 4217 code",
  "lines": [
    {
      "line_number": 1,
      "item_description": "string",
      "quantity": number,
      "unit_price": number,
      "currency": "ISO 4217 code",
      "unit_of_measure": "string",
      "category_hint": "string - infer from item description, e.g. IT Equipment, Office Supplies"
    }
  ]
}
Rules:
- If currency is not found, default to USD
- If UOM is not found, default to "Each"
- line_number starts at 1
- Return only JSON, no markdown, no explanation"""


class PdfExtractionError(RuntimeError):
    """Raised when a supplier quote PDF cannot be parsed reliably."""


def _extract_with_pdfplumber(pdf_path: Path) -> str:
    """Extract table-oriented text from a PDF using pdfplumber."""

    parts: list[str] = []
    with pdfplumber.open(pdf_path) as pdf:
        for page_number, page in enumerate(pdf.pages, start=1):
            tables = page.extract_tables() or []
            for table in tables:
                for row in table:
                    cleaned = [cell.strip() for cell in row if cell]
                    if cleaned:
                        parts.append(" | ".join(cleaned))
            page_text = page.extract_text() or ""
            if page_text.strip():
                parts.append(f"[Page {page_number} Text]\n{page_text.strip()}")
    return "\n".join(parts).strip()


def _extract_with_pymupdf(pdf_path: Path) -> str:
    """Extract plain text from a PDF using PyMuPDF as a fallback."""

    parts: list[str] = []
    with fitz.open(pdf_path) as document:
        for page_number, page in enumerate(document, start=1):
            text = page.get_text("text").strip()
            if text:
                parts.append(f"[Page {page_number} Text]\n{text}")
    return "\n".join(parts).strip()


def _parse_quote_with_openai(extracted_text: str) -> SupplierQuote:
    """Use OpenAI JSON mode to normalize extracted quote content."""

    settings = get_settings()
    client = OpenAI(api_key=settings.openai_api_key)
    response = client.chat.completions.create(
        model=settings.openai_model,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": PARSING_PROMPT},
            {
                "role": "user",
                "content": (
                    "Extract the supplier quote into structured JSON from the text below.\n\n"
                    f"{extracted_text}"
                ),
            },
        ],
    )
    content = response.choices[0].message.content or ""
    try:
        payload = json.loads(content)
        return SupplierQuote.model_validate(payload)
    except (json.JSONDecodeError, ValueError) as exc:
        snippet = extracted_text[:4000]
        raise PdfExtractionError(
            "OpenAI extraction failed. Showing raw extracted text for manual confirmation:\n"
            f"{snippet}"
        ) from exc


def extract_quote_from_pdf(pdf_path: str) -> SupplierQuote:
    """Extract a structured supplier quote from a PDF file."""

    path = Path(pdf_path).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"PDF file not found: {path}")

    LOGGER.info("Extracting supplier quote from PDF: %s", path)
    extracted_text = _extract_with_pdfplumber(path)

    if not extracted_text:
        LOGGER.info("pdfplumber found no text. Falling back to PyMuPDF for %s", path)
        extracted_text = _extract_with_pymupdf(path)

    if not extracted_text:
        raise PdfExtractionError("Could not find structured data in PDF. Try a different PDF.")

    quote = _parse_quote_with_openai(extracted_text)
    LOGGER.info("Extracted %s quote lines from supplier %s", len(quote.lines), quote.supplier_name)
    return quote
