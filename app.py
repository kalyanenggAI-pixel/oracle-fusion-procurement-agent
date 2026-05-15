"""FastAPI web UI for the Oracle Fusion Procurement Agent."""

from __future__ import annotations

import logging
import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import fitz
from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from config import get_settings
from models import RequisitionPayload, RequisitionResult
from tools import create_requisition, extract_quote_from_pdf, format_preview, resolve_all_lines
from tools.fusion_requisition import discover_requester_email
from tools.fusion_lookup import get_default_business_unit_name

LOGGER = logging.getLogger(__name__)
ROOT_DIR = Path(__file__).resolve().parent
STATIC_DIR = ROOT_DIR / "static"
TEMPLATES_DIR = ROOT_DIR / "templates"
UPLOADS_DIR = ROOT_DIR / "uploads"
SAMPLE_PDF = ROOT_DIR / "quotes" / "sample_supplier_quote.pdf"
RENDERED_PDF_DIR = ROOT_DIR / "uploads" / "rendered_pages"

UPLOADS_DIR.mkdir(exist_ok=True)
RENDERED_PDF_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class WorkflowState:
    """In-memory state for a single browser session."""

    session_id: str
    pdf_path: str | None = None
    pdf_name: str | None = None
    quote: dict[str, Any] | None = None
    resolved_payload: dict[str, Any] | None = None
    preview: dict[str, Any] | None = None
    requisition_result: dict[str, Any] | None = None
    events: list[str] = field(default_factory=list)

    def add_event(self, message: str) -> None:
        """Append a human-readable workflow event."""

        self.events.append(message)

    def to_dict(self) -> dict[str, Any]:
        """Serialize the state for the frontend."""

        return {
            "session_id": self.session_id,
            "pdf_name": self.pdf_name,
            "pdf_selected": bool(self.pdf_path),
            "pdf_url": f"/api/pdf/current?session_id={self.session_id}" if self.pdf_path else None,
            "pdf_pages": get_pdf_page_urls(self.session_id, self.pdf_path) if self.pdf_path else [],
            "quote": self.quote,
            "resolved_payload": self.resolved_payload,
            "preview": self.preview,
            "requisition_result": self.requisition_result,
            "events": self.events,
            "dry_run": get_settings().dry_run,
        }


app = FastAPI(title="Oracle Fusion Procurement Agent UI")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
SESSION_STORE: dict[str, WorkflowState] = {}


def get_or_create_session(session_id: str | None) -> WorkflowState:
    """Return an existing session or create a new one."""

    resolved_session_id = session_id or str(uuid.uuid4())
    state = SESSION_STORE.get(resolved_session_id)
    if state is None:
        state = WorkflowState(session_id=resolved_session_id)
        SESSION_STORE[resolved_session_id] = state
        LOGGER.info("Created new UI session: %s", resolved_session_id)
    return state


def require_state(session_id: str) -> WorkflowState:
    """Return a workflow state or raise if the session is unknown."""

    state = SESSION_STORE.get(session_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Session not found. Refresh the page.")
    return state


def render_pdf_pages(session_id: str, pdf_path: str) -> list[Path]:
    """Render a PDF into page images for stable in-app viewing."""

    render_dir = RENDERED_PDF_DIR / session_id
    render_dir.mkdir(parents=True, exist_ok=True)
    existing = sorted(render_dir.glob("page-*.png"))
    if existing:
        return existing

    pdf_file = Path(pdf_path)
    with fitz.open(pdf_file) as document:
        for page_index, page in enumerate(document, start=1):
            pixmap = page.get_pixmap(matrix=fitz.Matrix(1.7, 1.7), alpha=False)
            output = render_dir / f"page-{page_index}.png"
            pixmap.save(output)
    return sorted(render_dir.glob("page-*.png"))


def get_pdf_page_urls(session_id: str, pdf_path: str | None) -> list[str]:
    """Return image URLs for rendered PDF pages."""

    if not pdf_path:
        return []
    page_paths = render_pdf_pages(session_id, pdf_path)
    return [f"/api/pdf/page/{page.name}?session_id={session_id}" for page in page_paths]


@app.get("/", response_class=HTMLResponse)
def home(request: Request) -> HTMLResponse:
    """Render the browser-based workflow UI."""

    state = get_or_create_session(request.query_params.get("session_id"))
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "session_id": state.session_id,
            "dry_run": get_settings().dry_run,
        },
    )


@app.get("/api/state")
def get_state(session_id: str = Query(...)) -> JSONResponse:
    """Return the current workflow state for a browser session."""

    state = require_state(session_id)
    return JSONResponse(state.to_dict())


@app.post("/api/use-sample")
def use_sample_pdf(session_id: str = Query(...)) -> JSONResponse:
    """Select the bundled sample PDF for the current session."""

    state = require_state(session_id)
    state.pdf_path = str(SAMPLE_PDF)
    state.pdf_name = SAMPLE_PDF.name
    state.quote = None
    state.resolved_payload = None
    state.preview = None
    state.requisition_result = None
    state.add_event(f"Selected sample PDF: {SAMPLE_PDF.name}")
    return JSONResponse(state.to_dict())


@app.post("/api/upload")
async def upload_pdf(
    session_id: str = Query(...),
    file: UploadFile = File(...),
) -> JSONResponse:
    """Upload a PDF for the current browser session."""

    state = require_state(session_id)
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Please upload a PDF file.")

    session_dir = UPLOADS_DIR / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    destination = session_dir / Path(file.filename).name
    with destination.open("wb") as handle:
        shutil.copyfileobj(file.file, handle)

    state.pdf_path = str(destination)
    state.pdf_name = destination.name
    state.quote = None
    state.resolved_payload = None
    state.preview = None
    state.requisition_result = None
    state.add_event(f"Uploaded PDF: {destination.name}")
    LOGGER.info("Session %s uploaded %s", session_id, destination)
    return JSONResponse(state.to_dict())


@app.get("/api/pdf/current")
def current_pdf(session_id: str = Query(...)) -> FileResponse:
    """Stream the currently selected PDF for embedding in the UI."""

    state = require_state(session_id)
    if not state.pdf_path:
        raise HTTPException(status_code=404, detail="No PDF selected yet.")
    return FileResponse(state.pdf_path, media_type="application/pdf", filename=state.pdf_name)


@app.get("/api/pdf/page/{page_name}")
def current_pdf_page(page_name: str, session_id: str = Query(...)) -> FileResponse:
    """Serve a rendered PDF page image for the current session."""

    require_state(session_id)
    page_path = RENDERED_PDF_DIR / session_id / page_name
    if not page_path.exists():
        raise HTTPException(status_code=404, detail="Rendered PDF page not found.")
    return FileResponse(page_path, media_type="image/png", filename=page_name)


@app.post("/api/extract")
def extract_pdf(session_id: str = Query(...)) -> JSONResponse:
    """Extract structured quote lines from the selected PDF."""

    state = require_state(session_id)
    if not state.pdf_path:
        raise HTTPException(status_code=400, detail="Select or upload a PDF first.")

    quote = extract_quote_from_pdf(state.pdf_path)
    state.quote = quote.model_dump()
    state.resolved_payload = None
    state.preview = None
    state.requisition_result = None
    state.add_event(
        f"Extracted {len(quote.lines)} line items from {quote.supplier_name} dated {quote.quote_date}."
    )
    return JSONResponse(state.to_dict())


@app.post("/api/prepare")
def prepare_requisition(session_id: str = Query(...)) -> JSONResponse:
    """Run the agent flow through extraction, mapping, and preview in one action."""

    state = require_state(session_id)
    if not state.pdf_path:
        raise HTTPException(status_code=400, detail="Select or upload a PDF first.")

    quote = extract_quote_from_pdf(state.pdf_path)
    state.quote = quote.model_dump()
    state.add_event(
        f"Extracted {len(quote.lines)} line items from {quote.supplier_name} dated {quote.quote_date}."
    )

    resolved_payload = resolve_all_lines(state.quote.get("lines", []))
    preview = format_preview(resolved_payload)
    state.resolved_payload = resolved_payload
    state.preview = preview
    state.requisition_result = None
    state.add_event("Resolved Oracle Fusion categories and UOM codes.")
    state.add_event("Prepared requisition preview. Ready for final creation confirmation.")
    return JSONResponse(state.to_dict())


@app.post("/api/resolve")
def resolve_quote_lines(session_id: str = Query(...)) -> JSONResponse:
    """Resolve UOMs and categories for the extracted quote lines."""

    state = require_state(session_id)
    if not state.quote:
        raise HTTPException(status_code=400, detail="Extract the PDF before resolving line items.")

    lines = state.quote.get("lines", [])
    resolved_payload = resolve_all_lines(lines)
    preview = format_preview(resolved_payload)
    state.resolved_payload = resolved_payload
    state.preview = preview
    state.requisition_result = None
    state.add_event("Resolved Oracle Fusion categories and UOM codes.")
    return JSONResponse(state.to_dict())


@app.post("/api/create")
def create_from_preview(session_id: str = Query(...)) -> JSONResponse:
    """Create the requisition in Oracle Fusion or dry-run mode."""

    state = require_state(session_id)
    if not state.resolved_payload:
        raise HTTPException(status_code=400, detail="Resolve the line items before creation.")

    live_payload = dict(state.resolved_payload)
    if not live_payload.get("requester_email"):
        live_payload["requester_email"] = discover_requester_email()
    if not live_payload.get("business_unit_name"):
        live_payload["business_unit_name"] = get_default_business_unit_name()

    payload = RequisitionPayload.model_validate(live_payload)
    result = create_requisition(payload)
    state.requisition_result = RequisitionResult.model_validate(result).model_dump()
    state.add_event(
        f"Created requisition {state.requisition_result['requisition_number']} "
        f"with status {state.requisition_result['status']}."
    )
    return JSONResponse(state.to_dict())


@app.post("/api/reset")
def reset_session(session_id: str = Query(...)) -> JSONResponse:
    """Reset the workflow for the current session while keeping the session id."""

    state = require_state(session_id)
    state.pdf_path = None
    state.pdf_name = None
    state.quote = None
    state.resolved_payload = None
    state.preview = None
    state.requisition_result = None
    state.events = ["Reset workflow state."]
    return JSONResponse(state.to_dict())


@app.exception_handler(Exception)
async def handle_unexpected_error(_: Request, exc: Exception) -> JSONResponse:
    """Return a clean JSON error for unexpected backend failures."""

    LOGGER.exception("Unhandled UI error: %s", exc)
    return JSONResponse(status_code=500, content={"detail": str(exc)})


if __name__ == "__main__":
    import uvicorn

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=True)
