"""Export service for generating XLSX files."""

import base64
import io
import logging
from datetime import date

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.expense import MONTH_NAMES, ExpenseService

logger = logging.getLogger(__name__)


class ExportService:
    """Service for exporting expenses to XLSX."""

    def __init__(self):
        self.expense_service = ExpenseService()

    async def export_month(
        self,
        session: AsyncSession,
        phone: str,
        month: int | None = None,
        year: int | None = None,
    ) -> dict:
        """
        Export expenses for a month to XLSX.

        Returns:
            dict with success, file_base64, filename, month_name
        """
        today = date.today()

        if month is None:
            month = today.month
        if year is None:
            year = today.year

        month_name = MONTH_NAMES.get(month, str(month))

        # Get expenses
        expenses = await self.expense_service.get_expenses_for_export(session, phone, month, year)

        if not expenses:
            return {
                "success": False,
                "message": f"Voce nao tem gastos em {month_name} de {year}.",
            }

        # Create workbook
        wb = Workbook()
        ws = wb.active
        ws.title = f"{month_name} {year}"

        # Define headers
        headers = [
            "Data",
            "Descricao",
            "Categoria",
            "Forma de Pagamento",
            "Tipo",
            "Parcela",
            "Valor",
            "Compartilhada",
            "Percentual",
        ]

        # Style definitions
        header_font = Font(bold=True, color="FFFFFF")
        header_fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
        header_alignment = Alignment(horizontal="center", vertical="center")
        thin_border = Border(
            left=Side(style="thin"),
            right=Side(style="thin"),
            top=Side(style="thin"),
            bottom=Side(style="thin"),
        )

        # Write headers
        for col, header in enumerate(headers, 1):
            cell = ws.cell(row=1, column=col, value=header)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = header_alignment
            cell.border = thin_border

        # Write data
        for row_idx, expense in enumerate(expenses, 2):
            for col_idx, header in enumerate(headers, 1):
                value = expense.get(header, "")
                cell = ws.cell(row=row_idx, column=col_idx, value=value)
                cell.border = thin_border

                # Format numbers
                if header == "Valor":
                    cell.number_format = "R$ #,##0.00"
                elif header == "Percentual" and value:
                    cell.number_format = "0.00%"

        # Auto-adjust column widths
        for col in range(1, len(headers) + 1):
            max_length = 0
            column_letter = get_column_letter(col)

            for row in range(1, len(expenses) + 2):
                cell = ws.cell(row=row, column=col)
                if cell.value:
                    max_length = max(max_length, len(str(cell.value)))

            adjusted_width = min(max_length + 2, 50)
            ws.column_dimensions[column_letter].width = adjusted_width

        # Add summary row
        summary_row = len(expenses) + 3
        ws.cell(row=summary_row, column=1, value="TOTAL").font = Font(bold=True)

        # Calculate totals
        total_negativo = sum(e["Valor"] for e in expenses if e.get("Tipo") == "Negativo")
        total_positivo = sum(e["Valor"] for e in expenses if e.get("Tipo") == "Positivo")

        ws.cell(row=summary_row, column=6, value="Gastos:").font = Font(bold=True)
        ws.cell(row=summary_row, column=7, value=total_negativo).number_format = "R$ #,##0.00"

        ws.cell(row=summary_row + 1, column=6, value="Entradas:").font = Font(bold=True)
        ws.cell(row=summary_row + 1, column=7, value=total_positivo).number_format = "R$ #,##0.00"

        ws.cell(row=summary_row + 2, column=6, value="Saldo:").font = Font(bold=True)
        saldo_cell = ws.cell(row=summary_row + 2, column=7, value=total_positivo - total_negativo)
        saldo_cell.number_format = "R$ #,##0.00"
        saldo_cell.font = Font(bold=True)

        # Save to bytes
        output = io.BytesIO()
        wb.save(output)
        output.seek(0)

        # Encode to base64
        file_base64 = base64.b64encode(output.getvalue()).decode("utf-8")

        filename = f"gastos_{month_name.lower()}_{year}.xlsx"

        return {
            "success": True,
            "file_base64": file_base64,
            "filename": filename,
            "month_name": f"{month_name} de {year}",
        }
