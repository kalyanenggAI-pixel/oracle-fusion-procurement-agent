"""OpenAI function-calling orchestrator for Oracle Fusion procurement tasks."""

from __future__ import annotations

import json
import logging
from typing import Any

from openai import OpenAI

from config import get_settings
from models import RequisitionPayload, SupplierQuote
from tools import create_requisition, extract_quote_from_pdf, format_preview, resolve_all_lines

LOGGER = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are an Oracle Fusion Procurement AI Agent. Your job is to help users convert supplier quote PDFs into Oracle Fusion Purchase Requisitions.

Your workflow is ALWAYS this sequence - never skip or reorder steps:
1. Call extract_quote_from_pdf to parse the uploaded PDF
2. Present the extracted lines to the user in a clear table: Line | Item | Qty | Price | UOM | Category hint
3. Ask the user: "Does this look correct? Should I proceed to map to Oracle Fusion categories?"
4. Only after user confirms: call resolve_line_items to get Oracle category IDs and UOM codes
5. Show the resolved mapping: Item | Oracle Category | Oracle UOM | Price
6. Call preview_requisition to show the full requisition before creation
7. Ask: "Ready to create this requisition in Oracle Fusion? Reply YES to confirm."
8. Only after explicit YES: call create_requisition_in_fusion
9. Report the created Requisition Number and summary

Rules:
- NEVER call create_requisition_in_fusion without explicit user confirmation
- If any lookup fails, explain the issue and ask the user to provide the correct category or UOM manually
- If the PDF extraction looks wrong, ask the user to confirm or correct line items before proceeding
- Be concise but clear. Use tables in your responses for line items.
- If Oracle Fusion returns an error, show the exact error message and suggest a fix."""

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "extract_quote_from_pdf",
            "description": "Extract line items from a supplier quote PDF file",
            "parameters": {
                "type": "object",
                "properties": {
                    "pdf_path": {
                        "type": "string",
                        "description": "Absolute path to the PDF file",
                    }
                },
                "required": ["pdf_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "resolve_line_items",
            "description": "Resolve Oracle Fusion category IDs and UOM codes for all extracted quote lines",
            "parameters": {
                "type": "object",
                "properties": {
                    "lines": {
                        "type": "array",
                        "description": "List of quote lines from extract_quote_from_pdf",
                        "items": {"type": "object"},
                    }
                },
                "required": ["lines"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "preview_requisition",
            "description": "Show the user a preview of the requisition that will be created, before actually creating it",
            "parameters": {
                "type": "object",
                "properties": {
                    "payload": {
                        "type": "object",
                        "description": "The full requisition payload",
                    }
                },
                "required": ["payload"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_requisition_in_fusion",
            "description": "Create the purchase requisition in Oracle Fusion. Only call this after the user has confirmed the preview.",
            "parameters": {
                "type": "object",
                "properties": {
                    "payload": {
                        "type": "object",
                        "description": "The confirmed requisition payload",
                    }
                },
                "required": ["payload"],
            },
        },
    },
]


class FusionAgent:
    """Stateful OpenAI chat agent for supplier quote to requisition workflows."""

    def __init__(self) -> None:
        """Initialize the agent client, message state, and safety flags."""

        settings = get_settings()
        self.client = OpenAI(api_key=settings.openai_api_key)
        self.messages: list[dict[str, Any]] = []
        self.confirmed = False
        self.last_tool_name: str | None = None
        self.last_tool_result: dict[str, Any] | None = None
        self.last_tool_event_id = 0
        self.last_quote: SupplierQuote | None = None
        self.last_resolved_payload: dict[str, Any] | None = None
        self.last_preview: dict[str, Any] | None = None

    def run(self, user_message: str) -> str:
        """Process a user message and return the assistant's next response."""

        self._update_confirmation_state(user_message)
        self.messages.append({"role": "user", "content": user_message})

        while True:
            response = self.client.chat.completions.create(
                model=get_settings().openai_model,
                messages=[{"role": "system", "content": SYSTEM_PROMPT}] + self.messages,
                tools=TOOLS,
                tool_choice="auto",
            )

            message = response.choices[0].message

            if message.tool_calls:
                self.messages.append(message.model_dump(exclude_none=True))
                for tool_call in message.tool_calls:
                    result = self._safe_dispatch_tool(tool_call)
                    self.messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": json.dumps(result),
                        }
                    )
                continue

            content = message.content or ""
            self.messages.append({"role": "assistant", "content": content})
            return content

    def _update_confirmation_state(self, user_message: str) -> None:
        """Set the final confirmation gate only when the user explicitly confirms creation."""

        normalized = user_message.strip().upper()
        if normalized != "YES":
            return

        for message in reversed(self.messages):
            if message.get("role") == "assistant":
                content = (message.get("content") or "").upper()
                if "READY TO CREATE THIS REQUISITION IN ORACLE FUSION" in content:
                    self.confirmed = True
                break

    def _safe_dispatch_tool(self, tool_call: Any) -> dict[str, Any]:
        """Dispatch a tool call and convert exceptions into tool-safe error payloads."""

        try:
            return self._dispatch_tool(tool_call)
        except Exception as exc:
            LOGGER.exception("Tool %s failed: %s", tool_call.function.name, exc)
            return {"error": str(exc)}

    def _dispatch_tool(self, tool_call: Any) -> dict[str, Any]:
        """Dispatch a single tool call from the OpenAI model."""

        name = tool_call.function.name
        args = json.loads(tool_call.function.arguments)

        if name == "create_requisition_in_fusion" and not self.confirmed:
            return {"error": "User has not confirmed. Ask for explicit YES confirmation first."}

        if name == "extract_quote_from_pdf":
            result = extract_quote_from_pdf(args["pdf_path"]).model_dump()
            self.last_quote = SupplierQuote.model_validate(result)
        elif name == "resolve_line_items":
            lines = args.get("lines")
            if not lines:
                if self.last_quote is None:
                    return {
                        "error": (
                            "No extracted quote lines are available yet. "
                            "Call extract_quote_from_pdf before resolve_line_items."
                        )
                    }
                LOGGER.warning(
                    "resolve_line_items called without lines; using cached lines from the last extracted quote."
                )
                lines = [line.model_dump() for line in self.last_quote.lines]
            result = resolve_all_lines(lines)
            self.last_resolved_payload = result
        elif name == "preview_requisition":
            result = format_preview(args["payload"])
            self.last_preview = result
        elif name == "create_requisition_in_fusion":
            result = create_requisition(RequisitionPayload(**args["payload"])).model_dump()
        else:
            return {"error": f"Unknown tool: {name}"}

        self.last_tool_name = name
        self.last_tool_result = result
        self.last_tool_event_id += 1
        return result
