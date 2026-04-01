"""Expense management service."""

import logging
import unicodedata
from datetime import date, datetime
from decimal import Decimal
from typing import Optional
from calendar import monthrange

from sqlalchemy import select, update, func, and_, extract
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Expense, Category, PaymentMethod
from app.utils.validators import normalize_phone
from app.utils.parser import extract_month_year

logger = logging.getLogger(__name__)


def remove_accents(text: str) -> str:
    """Remove accents from text for comparison."""
    if not text:
        return ""
    # Normalize to NFD form (decomposed), then filter out combining characters
    normalized = unicodedata.normalize("NFD", text)
    return "".join(c for c in normalized if unicodedata.category(c) != "Mn")

# Month names in Portuguese
MONTH_NAMES = {
    1: "Janeiro",
    2: "Fevereiro",
    3: "Marco",
    4: "Abril",
    5: "Maio",
    6: "Junho",
    7: "Julho",
    8: "Agosto",
    9: "Setembro",
    10: "Outubro",
    11: "Novembro",
    12: "Dezembro",
}


class ExpenseService:
    """Service for managing expenses."""

    async def create_expense(
        self,
        session: AsyncSession,
        phone: str,
        data: dict,
    ) -> dict:
        """Create a new expense record."""
        try:
            normalized_phone = normalize_phone(phone)

            # Get category
            category_name = data.get("category", "Outros")
            category = await self._get_category(session, category_name)
            if not category:
                return {"success": False, "error": f"Categoria '{category_name}' nao encontrada"}

            # Get payment method
            payment_name = data.get("payment_method", "Pix")
            payment_method = await self._get_payment_method(session, payment_name)
            if not payment_method:
                return {"success": False, "error": f"Metodo de pagamento '{payment_name}' nao encontrado"}

            # Parse amount
            amount = Decimal(str(data.get("amount", 0)))
            if amount <= 0:
                return {"success": False, "error": "Valor deve ser maior que zero"}

            # Handle installments
            installments = data.get("installments")
            if installments:
                return await self._create_installment_expenses(
                    session,
                    normalized_phone,
                    data,
                    category,
                    payment_method,
                    amount,
                    installments,
                )

            # Create single expense
            expense = Expense(
                user_phone=normalized_phone,
                description=data.get("description", ""),
                amount=amount,
                category_id=category.id,
                payment_method_id=payment_method.id,
                type=category.type,
                is_shared=data.get("is_shared", False),
                shared_percentage=data.get("shared_percentage"),
                is_recurring=data.get("is_recurring", False),
                recurring_day=data.get("recurring_day"),
                recurring_active=data.get("is_recurring", False),
                date=date.today(),
            )

            session.add(expense)
            await session.commit()

            logger.info(f"Created expense: {expense.id} for {normalized_phone}")
            return {"success": True, "expense_id": expense.id}

        except Exception as e:
            logger.error(f"Error creating expense: {e}")
            await session.rollback()
            return {"success": False, "error": str(e)}

    async def _create_installment_expenses(
        self,
        session: AsyncSession,
        phone: str,
        data: dict,
        category: Category,
        payment_method: PaymentMethod,
        total_amount: Decimal,
        installments: int,
    ) -> dict:
        """Create multiple expense records for installments."""
        try:
            installment_amount = (total_amount / installments).quantize(Decimal("0.01"))
            today = date.today()
            expenses_created = []

            for i in range(1, installments + 1):
                # Calculate date for each installment
                if i == 1:
                    expense_date = today
                else:
                    # Add months
                    month = today.month + (i - 1)
                    year = today.year + (month - 1) // 12
                    month = ((month - 1) % 12) + 1
                    # Handle day overflow
                    day = min(today.day, monthrange(year, month)[1])
                    expense_date = date(year, month, day)

                expense = Expense(
                    user_phone=phone,
                    description=data.get("description", ""),
                    amount=installment_amount,
                    category_id=category.id,
                    payment_method_id=payment_method.id,
                    type=category.type,
                    installment_current=i,
                    installment_total=installments,
                    is_shared=data.get("is_shared", False),
                    shared_percentage=data.get("shared_percentage"),
                    is_recurring=False,
                    date=expense_date,
                )

                session.add(expense)
                expenses_created.append(expense)

            await session.commit()

            logger.info(f"Created {installments} installment expenses for {phone}")
            return {
                "success": True,
                "installments_created": installments,
                "installment_amount": float(installment_amount),
            }

        except Exception as e:
            logger.error(f"Error creating installments: {e}")
            await session.rollback()
            return {"success": False, "error": str(e)}

    async def cancel_recurring(
        self,
        session: AsyncSession,
        phone: str,
        description: str,
    ) -> dict:
        """Cancel a recurring expense."""
        try:
            normalized_phone = normalize_phone(phone)
            description_lower = description.lower().strip()

            # Find recurring expense by description
            result = await session.execute(
                select(Expense)
                .where(Expense.user_phone == normalized_phone)
                .where(Expense.is_recurring == True)
                .where(Expense.recurring_active == True)
                .where(func.lower(Expense.description).contains(description_lower))
            )

            expense = result.scalar_one_or_none()

            if not expense:
                return {"success": False, "error": "Despesa recorrente nao encontrada"}

            # Deactivate recurring
            expense.recurring_active = False
            await session.commit()

            logger.info(f"Cancelled recurring expense: {expense.id}")
            return {"success": True, "expense_id": expense.id}

        except Exception as e:
            logger.error(f"Error cancelling recurring: {e}")
            await session.rollback()
            return {"success": False, "error": str(e)}

    async def list_recurring(
        self,
        session: AsyncSession,
        phone: str,
    ) -> str:
        """List all active recurring expenses."""
        normalized_phone = normalize_phone(phone)

        result = await session.execute(
            select(Expense)
            .where(Expense.user_phone == normalized_phone)
            .where(Expense.is_recurring == True)
            .where(Expense.recurring_active == True)
            .order_by(Expense.recurring_day)
        )

        expenses = result.scalars().all()

        if not expenses:
            return "Voce nao tem despesas recorrentes ativas."

        msg = "Suas despesas recorrentes:\n\n"
        for exp in expenses:
            msg += f"- {exp.description}: R$ {exp.amount:.2f} (dia {exp.recurring_day})\n"

        return msg

    async def get_monthly_summary(
        self,
        session: AsyncSession,
        phone: str,
        month: Optional[int] = None,
        year: Optional[int] = None,
    ) -> str:
        """Get monthly expense summary."""
        normalized_phone = normalize_phone(phone)
        today = date.today()

        if month is None:
            month = today.month
        if year is None:
            year = today.year

        # Query expenses for the month
        result = await session.execute(
            select(Expense)
            .where(Expense.user_phone == normalized_phone)
            .where(extract("month", Expense.date) == month)
            .where(extract("year", Expense.date) == year)
        )

        expenses = result.scalars().all()

        if not expenses:
            return f"Voce nao tem gastos registrados em {MONTH_NAMES[month]} de {year}."

        # Calculate totals
        total_negativo = Decimal("0")
        total_positivo = Decimal("0")
        by_category = {}

        for exp in expenses:
            if exp.type == "Negativo":
                total_negativo += exp.amount
            else:
                total_positivo += exp.amount

            # Group by category (for expenses only)
            if exp.type == "Negativo":
                cat_name = exp.category.name if exp.category else "Outros"
                by_category[cat_name] = by_category.get(cat_name, Decimal("0")) + exp.amount

        # Build summary message
        msg = f"Resumo de {MONTH_NAMES[month]} de {year}:\n\n"

        if total_positivo > 0:
            msg += f"Entradas: R$ {total_positivo:.2f}\n"

        msg += f"Gastos: R$ {total_negativo:.2f}\n"
        msg += f"Saldo: R$ {(total_positivo - total_negativo):.2f}\n"

        if by_category:
            msg += "\nPor categoria:\n"
            # Sort by amount
            sorted_cats = sorted(by_category.items(), key=lambda x: x[1], reverse=True)
            for cat, amount in sorted_cats[:5]:  # Top 5
                msg += f"- {cat}: R$ {amount:.2f}\n"

        return msg

    async def get_expenses_for_export(
        self,
        session: AsyncSession,
        phone: str,
        month: int,
        year: int,
    ) -> list[dict]:
        """Get expenses for export."""
        normalized_phone = normalize_phone(phone)

        result = await session.execute(
            select(Expense)
            .where(Expense.user_phone == normalized_phone)
            .where(extract("month", Expense.date) == month)
            .where(extract("year", Expense.date) == year)
            .order_by(Expense.date)
        )

        expenses = result.scalars().all()

        # Convert to list of dicts for export
        export_data = []
        for exp in expenses:
            # Load relationships
            await session.refresh(exp, ["category", "payment_method"])

            export_data.append({
                "Data": exp.date.strftime("%d/%m/%Y"),
                "Descricao": exp.description,
                "Categoria": exp.category.name if exp.category else "",
                "Forma de Pagamento": exp.payment_method.name if exp.payment_method else "",
                "Tipo": exp.type,
                "Parcela": exp.installment_display or "",
                "Valor": float(exp.amount),
                "Compartilhada": "Sim" if exp.is_shared else "Nao",
                "Percentual": float(exp.shared_percentage) if exp.shared_percentage else "",
            })

        return export_data

    async def _get_category(
        self,
        session: AsyncSession,
        name: str,
    ) -> Optional[Category]:
        """Get category by name (case-insensitive, accent-insensitive)."""
        # First try exact match (case-insensitive)
        result = await session.execute(
            select(Category).where(func.lower(Category.name) == name.lower())
        )
        category = result.scalar_one_or_none()

        if category:
            return category

        # If not found, try matching without accents
        normalized_name = remove_accents(name.lower())
        result = await session.execute(select(Category))
        categories = result.scalars().all()

        for cat in categories:
            if remove_accents(cat.name.lower()) == normalized_name:
                return cat

        return None

    async def _get_payment_method(
        self,
        session: AsyncSession,
        name: str,
    ) -> Optional[PaymentMethod]:
        """Get payment method by name (case-insensitive, accent-insensitive)."""
        # First try exact match (case-insensitive)
        result = await session.execute(
            select(PaymentMethod).where(func.lower(PaymentMethod.name) == name.lower())
        )
        method = result.scalar_one_or_none()

        if method:
            return method

        # If not found, try matching without accents
        normalized_name = remove_accents(name.lower())
        result = await session.execute(select(PaymentMethod))
        methods = result.scalars().all()

        for method in methods:
            if remove_accents(method.name.lower()) == normalized_name:
                return method

        return None
