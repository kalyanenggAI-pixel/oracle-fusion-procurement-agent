"""CLI entry point for the Oracle Fusion Procurement Agent.

Example run:
    $ python main.py --pdf quotes/techsupply_quote.pdf

    ═══════════════════════════════════════════════
     Oracle Fusion Procurement Agent (External)
     Powered by OpenAI GPT-4o
    ═══════════════════════════════════════════════
    ✓ Connected to Oracle Fusion: https://demo.fa.us2.oraclecloud.com

    Agent: I'll process the supplier quote PDF now.
    [Extracted 5 line items from TechSupply Solutions quote dated 2026-05-01]

    ┌─────┬─────────────────────────────────┬──────┬───────────┬──────────┐
    │ Line│ Item                            │  Qty │ Unit Price│ UOM      │
    ├─────┼─────────────────────────────────┼──────┼───────────┼──────────┤
    │  1  │ Dell Latitude 5540 Laptop       │    5 │  1,200.00 │ Each     │
    │  2  │ Logitech Wireless Keyboard+Mouse│    2 │    545.00 │ Each     │
    │  3  │ HP LaserJet Printer             │    3 │    450.00 │ Each     │
    │  4  │ A4 Paper (box)                  │   50 │     12.00 │ Box      │
    │  5  │ WD 2TB External HDD             │    1 │    589.00 │ Each     │
    └─────┴─────────────────────────────────┴──────┴───────────┴──────────┘
    Total: USD 20,147.00

    Does this look correct? Should I proceed to map to Oracle Fusion categories?

    You: yes

    Agent: Resolving Oracle Fusion categories and UOM codes...
    [Mapped all 5 lines successfully]
    Ready to create requisition. Reply YES to confirm.

    You: YES

    Agent: ✓ Purchase Requisition created!
      Requisition Number: 280503
      Total Amount: USD 20,147.00
      Lines: 5
      Status: Incomplete (pending approval)
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from agent import FusionAgent
from config import get_settings
from models import FusionLine, PreviewSummary, SupplierQuote
from tools.fusion_auth import test_connection

LOGGER = logging.getLogger(__name__)
console = Console()


def build_parser() -> argparse.ArgumentParser:
    """Create the CLI argument parser."""

    parser = argparse.ArgumentParser(description="Oracle Fusion Procurement Agent")
    parser.add_argument("--pdf", required=True, help="Path to the supplier quote PDF")
    return parser


def print_banner() -> None:
    """Render the startup banner using Rich."""

    banner = Text()
    banner.append(" Oracle Fusion Procurement Agent (External)\n", style="bold cyan")
    banner.append(" Powered by OpenAI GPT-4o", style="bold")
    console.print(
        Panel.fit(
            banner,
            border_style="cyan",
            title="",
            subtitle="",
        )
    )


def render_quote_table(quote: SupplierQuote) -> None:
    """Render extracted supplier quote lines as a Rich table."""

    table = Table(title=f"Extracted Quote Lines - {quote.supplier_name}", show_lines=False)
    table.add_column("Line", justify="right")
    table.add_column("Item")
    table.add_column("Qty", justify="right")
    table.add_column("Unit Price", justify="right")
    table.add_column("UOM")
    table.add_column("Category Hint")

    total_amount = 0.0
    for line in quote.lines:
        total_amount += line.quantity * line.unit_price
        table.add_row(
            str(line.line_number),
            line.item_description,
            f"{line.quantity:,.2f}",
            f"{line.currency} {line.unit_price:,.2f}",
            line.unit_of_measure,
            line.category_hint or "-",
        )

    console.print(table)
    console.print(f"Total: {quote.currency} {total_amount:,.2f}")


def render_resolved_table(payload: dict) -> None:
    """Render resolved Oracle mappings as a Rich table."""

    table = Table(title="Resolved Oracle Fusion Mappings", show_lines=False)
    table.add_column("Line", justify="right")
    table.add_column("Item")
    table.add_column("Oracle Category")
    table.add_column("Oracle UOM")
    table.add_column("Price", justify="right")

    for line_data in payload.get("lines", []):
        line = FusionLine.model_validate(line_data)
        table.add_row(
            str(line.line_number),
            line.item_description,
            line.category_name,
            line.uom_code,
            f"{line.currency} {line.unit_price:,.2f}",
        )

    console.print(table)


def render_preview_table(preview_data: dict) -> None:
    """Render the final requisition preview as a Rich table."""

    preview = PreviewSummary.model_validate(preview_data)
    table = Table(title="Requisition Preview", show_lines=False)
    table.add_column("Line", justify="right")
    table.add_column("Item")
    table.add_column("Qty", justify="right")
    table.add_column("UOM")
    table.add_column("Category")
    table.add_column("Need By")
    table.add_column("Unit Price", justify="right")

    for line in preview.lines:
        table.add_row(
            str(line.line_number),
            line.item_description,
            f"{line.quantity:,.2f}",
            line.uom_code,
            line.category_name,
            line.need_by_date,
            f"{line.currency} {line.unit_price:,.2f}",
        )

    console.print(table)
    console.print(f"Description: {preview.header_description}")
    console.print(f"Requester: {preview.requester_email}")
    console.print(f"Business Unit: {preview.business_unit_name}")
    console.print(f"Total: {preview.currency} {preview.total_amount:,.2f}")


def render_tool_output(agent: FusionAgent, last_seen_event_id: int) -> int:
    """Render any newly produced structured tool output and return the latest event id."""

    if agent.last_tool_event_id == last_seen_event_id:
        return last_seen_event_id

    if agent.last_tool_name == "extract_quote_from_pdf" and agent.last_quote is not None:
        render_quote_table(agent.last_quote)
    elif agent.last_tool_name == "resolve_line_items" and agent.last_resolved_payload is not None:
        render_resolved_table(agent.last_resolved_payload)
    elif agent.last_tool_name == "preview_requisition" and agent.last_preview is not None:
        render_preview_table(agent.last_preview)

    return agent.last_tool_event_id


def main() -> None:
    """Run the interactive Oracle Fusion procurement agent CLI."""

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    args = build_parser().parse_args()
    settings = get_settings()
    pdf_path = Path(args.pdf).expanduser().resolve()
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF file not found: {pdf_path}")

    print_banner()
    test_connection()
    console.print(f"[green]✓ Connected to Oracle Fusion: {settings.fusion_base_url}[/green]")

    agent = FusionAgent()
    last_seen_event_id = 0
    first_message = (
        "Please process this supplier quote PDF and follow the required workflow. "
        f"PDF path: {pdf_path}"
    )

    response = agent.run(first_message)
    last_seen_event_id = render_tool_output(agent, last_seen_event_id)
    console.print(f"\n[bold]Agent:[/bold] {response}")

    try:
        while True:
            user_input = console.input("\n[bold]You:[/bold] ").strip()
            if not user_input:
                continue
            response = agent.run(user_input)
            last_seen_event_id = render_tool_output(agent, last_seen_event_id)
            console.print(f"\n[bold]Agent:[/bold] {response}")
    except KeyboardInterrupt:
        console.print("\nSession ended")


if __name__ == "__main__":
    main()
