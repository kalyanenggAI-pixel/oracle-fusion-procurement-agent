"""Pydantic models shared across the Oracle Fusion procurement agent."""

from __future__ import annotations

from pydantic import BaseModel, Field


class QuoteLine(BaseModel):
    """A single supplier quote line extracted from a PDF."""

    line_number: int
    item_description: str
    quantity: float
    unit_price: float
    currency: str
    unit_of_measure: str
    category_hint: str = ""


class SupplierQuote(BaseModel):
    """A supplier quote parsed into structured header and line data."""

    supplier_name: str
    quote_date: str
    currency: str
    lines: list[QuoteLine]


class FusionLine(BaseModel):
    """A quote line resolved to Oracle Fusion identifiers."""

    line_number: int
    item_description: str
    quantity: float
    unit_price: float
    currency: str
    uom_code: str
    category_id: int
    category_name: str
    need_by_date: str


class RequisitionPayload(BaseModel):
    """Payload used to create a purchase requisition in Oracle Fusion."""

    header_description: str
    requester_email: str
    business_unit_name: str
    lines: list[FusionLine]


class RequisitionResult(BaseModel):
    """Normalized result returned after requisition creation."""

    requisition_id: str
    requisition_number: str
    status: str
    total_amount: float
    currency: str
    lines_created: int


class PreviewSummary(BaseModel):
    """A CLI-friendly requisition preview summary."""

    header_description: str
    requester_email: str
    business_unit_name: str
    currency: str
    total_amount: float
    lines: list[FusionLine] = Field(default_factory=list)
