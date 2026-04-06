"""Expense management service."""

import logging
import unicodedata
from calendar import monthrange
from datetime import date, datetime
from decimal import Decimal
from typing import cast

from sqlalchemy import extract, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database.models import (
    Category,
    Expense,
    ExpenseUpdateAudit,
    Goal,
    GoalTransaction,
    GoalUpdate,
    PaymentMethod,
    User,
)
from app.services.category import CategoryService
from app.services.user import UserService
from app.utils.parser import parse_date
from app.utils.validators import normalize_phone

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

    def __init__(self) -> None:
        self.category_service = CategoryService()
        self.user_service = UserService()

    def _validate_financial_consistency(self, data: dict) -> str | None:
        """Validate financial field combinations before persisting."""
        installments = data.get("installments")
        if installments is not None:
            try:
                installments = int(installments)
            except (TypeError, ValueError):
                return "Numero de parcelas invalido"
            if installments < 2:
                return "Parcelamento deve ter pelo menos 2 parcelas"

        shared_percentage = data.get("shared_percentage")
        if data.get("is_shared") and shared_percentage in (None, ""):
            return "Informe o percentual da sua parte para despesas divididas."
        if shared_percentage is not None:
            try:
                shared_percentage = Decimal(str(shared_percentage))
            except Exception:
                return "Percentual compartilhado invalido"
            if shared_percentage <= 0 or shared_percentage > 100:
                return "Percentual compartilhado deve estar entre 0 e 100"

        original_currency = data.get("original_currency")
        original_amount = data.get("original_amount")
        exchange_rate = data.get("exchange_rate")
        if original_currency and (original_amount in (None, "") or exchange_rate in (None, "")):
            return "Conversao de moeda incompleta"

        recurring_day = data.get("recurring_day")
        if data.get("is_recurring") and recurring_day is not None:
            try:
                recurring_day = int(recurring_day)
            except (TypeError, ValueError):
                return "Dia da recorrencia invalido"
            if recurring_day < 1 or recurring_day > 31:
                return "Dia da recorrencia deve estar entre 1 e 31"

        return None

    def _resolve_expense_date(self, data: dict) -> tuple[date | None, str | None]:
        """Resolve the effective expense date from the payload."""
        raw_expense_date = data.get("expense_date")
        if raw_expense_date in (None, ""):
            return date.today(), None

        if isinstance(raw_expense_date, date) and not isinstance(raw_expense_date, datetime):
            return raw_expense_date, None

        try:
            return date.fromisoformat(str(raw_expense_date)), None
        except ValueError:
            parsed_expense_date = parse_date(str(raw_expense_date))
            if parsed_expense_date is not None:
                return parsed_expense_date, None
            return None, "Data da despesa invalida. Use o formato YYYY-MM-DD."

    def _build_expense_snapshot(self, expense: Expense) -> dict[str, str | float]:
        """Build a normalized snapshot for expense audit records."""
        return {
            "description": str(expense.description),
            "amount": float(expense.amount),
            "category": expense.display_category,
            "payment_method": expense.payment_method.name if expense.payment_method else "",
            "date": expense.date.isoformat(),
            "is_shared": bool(expense.is_shared),
            "shared_percentage": float(expense.shared_percentage)
            if expense.shared_percentage is not None
            else None,
            "goal_id": int(expense.goal_id) if expense.goal_id is not None else None,
        }

    async def create_expense(
        self,
        session: AsyncSession,
        phone: str,
        data: dict,
    ) -> dict:
        """Create a new expense record."""
        try:
            normalized_phone = normalize_phone(phone)
            user = await self.user_service.get_or_create_user(session, normalized_phone)

            # Get category
            category_name = data.get("category", "Outros")
            try:
                (
                    category,
                    custom_category_name,
                ) = await self.category_service.resolve_category_for_user(
                    session, user, category_name
                )
            except ValueError as exc:
                return {"success": False, "error": str(exc)}

            # Get payment method
            payment_name = data.get("payment_method", "Pix")
            payment_method = await self._get_payment_method(session, payment_name)
            if not payment_method:
                return {
                    "success": False,
                    "error": f"Metodo de pagamento '{payment_name}' nao encontrado",
                }

            # Parse amount
            amount = Decimal(str(data.get("amount", 0)))
            if amount <= 0:
                return {"success": False, "error": "Valor deve ser maior que zero"}

            if validation_error := self._validate_financial_consistency(data):
                return {"success": False, "error": validation_error}

            expense_date, expense_date_error = self._resolve_expense_date(data)
            if expense_date_error:
                return {"success": False, "error": expense_date_error}

            # Handle installments
            installments = data.get("installments")
            if installments:
                return await self._create_installment_expenses(
                    session,
                    normalized_phone,
                    data,
                    category,
                    custom_category_name,
                    payment_method,
                    amount,
                    installments,
                    expense_date,
                )

            # Create single expense
            expense = Expense(
                user_phone=normalized_phone,
                description=data.get("description", ""),
                amount=amount,
                custom_category_name=custom_category_name,
                category_id=category.id,
                payment_method_id=payment_method.id,
                goal_id=data.get("goal_id"),
                type=category.type,
                is_shared=data.get("is_shared", False),
                shared_percentage=data.get("shared_percentage"),
                is_recurring=data.get("is_recurring", False),
                recurring_day=data.get("recurring_day"),
                recurring_active=data.get("is_recurring", False),
                date=expense_date or date.today(),
                # Currency conversion fields
                original_currency=data.get("original_currency"),
                original_amount=Decimal(str(data["original_amount"]))
                if data.get("original_amount")
                else None,
                exchange_rate=Decimal(str(data["exchange_rate"]))
                if data.get("exchange_rate")
                else None,
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
        custom_category_name: str | None,
        payment_method: PaymentMethod,
        total_amount: Decimal,
        installments: int,
        base_date: date | None,
    ) -> dict:
        """Create multiple expense records for installments."""
        try:
            base_installment_amount = (total_amount / installments).quantize(Decimal("0.01"))
            allocated_amount = base_installment_amount * installments
            remainder = total_amount - allocated_amount
            today = base_date or date.today()
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

                installment_amount = base_installment_amount
                if i == installments:
                    installment_amount += remainder

                expense = Expense(
                    user_phone=phone,
                    description=data.get("description", ""),
                    amount=installment_amount,
                    custom_category_name=custom_category_name,
                    category_id=category.id,
                    payment_method_id=payment_method.id,
                    goal_id=data.get("goal_id"),
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
                "installment_amount": float(base_installment_amount),
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

    async def find_expenses_for_update(
        self,
        session: AsyncSession,
        phone: str,
        criteria: dict,
        limit: int = 5,
    ) -> list[Expense]:
        """Find candidate expenses that match update criteria."""
        normalized_phone = normalize_phone(phone)
        query = (
            select(Expense)
            .options(
                selectinload(Expense.category),
                selectinload(Expense.payment_method),
            )
            .where(Expense.user_phone == normalized_phone)
        )

        target_description = (criteria.get("target_description") or "").strip()
        if target_description:
            query = query.where(
                func.lower(Expense.description).contains(target_description.lower())
            )

        target_amount = criteria.get("target_amount")
        if target_amount not in (None, ""):
            query = query.where(Expense.amount == Decimal(str(target_amount)))

        target_date_raw = criteria.get("target_date")
        if target_date_raw not in (None, ""):
            if isinstance(target_date_raw, date) and not isinstance(target_date_raw, datetime):
                target_date = target_date_raw
            else:
                target_date = parse_date(str(target_date_raw))
            if target_date is not None:
                query = query.where(Expense.date == target_date)

        result = await session.execute(
            query.order_by(Expense.date.desc(), Expense.created_at.desc())
        )
        return result.scalars().all()[:limit]

    async def get_expense_by_id(
        self,
        session: AsyncSession,
        phone: str,
        expense_id: int,
    ) -> Expense | None:
        """Return a single expense by id scoped to the current user."""
        normalized_phone = normalize_phone(phone)
        result = await session.execute(
            select(Expense)
            .options(selectinload(Expense.category), selectinload(Expense.payment_method))
            .where(Expense.id == expense_id)
            .where(Expense.user_phone == normalized_phone)
        )
        return result.scalar_one_or_none()

    async def update_expense(
        self,
        session: AsyncSession,
        phone: str,
        expense_id: int,
        update_data: dict,
    ) -> dict:
        """Update an existing expense owned by the user."""
        normalized_phone = normalize_phone(phone)
        expense = await self.get_expense_by_id(session, normalized_phone, expense_id)
        if expense is None:
            return {"success": False, "error": "Lancamento nao encontrado."}

        user = await self.user_service.get_or_create_user(session, normalized_phone)
        previous_snapshot = self._build_expense_snapshot(expense)

        new_amount = update_data.get("new_amount")
        if new_amount not in (None, ""):
            amount_decimal = Decimal(str(new_amount))
            if amount_decimal <= 0:
                return {"success": False, "error": "Valor deve ser maior que zero"}
            expense.amount = amount_decimal

        new_original_currency = update_data.get("new_original_currency")
        if new_original_currency is not None:
            normalized_original_currency = str(new_original_currency).strip().upper()
            expense.original_currency = normalized_original_currency or None

        new_original_amount = update_data.get("new_original_amount")
        if new_original_amount not in (None, ""):
            expense.original_amount = Decimal(str(new_original_amount))
        elif new_original_currency is not None and not expense.original_currency:
            expense.original_amount = None

        new_exchange_rate = update_data.get("new_exchange_rate")
        if new_exchange_rate not in (None, ""):
            expense.exchange_rate = Decimal(str(new_exchange_rate))
        elif new_original_currency is not None and not expense.original_currency:
            expense.exchange_rate = None

        new_description = update_data.get("new_description")
        if new_description:
            expense.description = str(new_description).strip()

        new_category = update_data.get("new_category")
        if new_category:
            try:
                (
                    category,
                    custom_category_name,
                ) = await self.category_service.resolve_category_for_user(
                    session, user, str(new_category)
                )
            except ValueError as exc:
                return {"success": False, "error": str(exc)}
            expense.category_id = category.id
            expense.category = category
            expense.custom_category_name = custom_category_name
            expense.type = category.type

        new_payment_method = update_data.get("new_payment_method")
        if new_payment_method:
            payment_method = await self._get_payment_method(session, str(new_payment_method))
            if payment_method is None:
                return {
                    "success": False,
                    "error": f"Metodo de pagamento '{new_payment_method}' nao encontrado",
                }
            expense.payment_method_id = payment_method.id
            expense.payment_method = payment_method

        new_expense_date = update_data.get("new_expense_date")
        if new_expense_date not in (None, ""):
            try:
                parsed_date = date.fromisoformat(str(new_expense_date))
            except ValueError:
                parsed_date = parse_date(str(new_expense_date))
            if parsed_date is None:
                return {"success": False, "error": "Data da despesa invalida."}
            expense.date = parsed_date

        if "new_goal_id" in update_data:
            expense.goal_id = update_data.get("new_goal_id")

        new_is_shared = update_data.get("new_is_shared")
        new_shared_percentage = update_data.get("new_shared_percentage")
        if new_is_shared is not None:
            expense.is_shared = bool(new_is_shared)
            if expense.is_shared:
                if new_shared_percentage in (None, ""):
                    return {
                        "success": False,
                        "error": "Informe o percentual da sua parte para despesas divididas.",
                    }
                try:
                    shared_percentage_decimal = Decimal(str(new_shared_percentage))
                except Exception:
                    return {"success": False, "error": "Percentual compartilhado invalido"}
                if shared_percentage_decimal <= 0 or shared_percentage_decimal > 100:
                    return {
                        "success": False,
                        "error": "Percentual compartilhado deve estar entre 0 e 100",
                    }
                expense.shared_percentage = shared_percentage_decimal
            else:
                expense.shared_percentage = None

        updated_snapshot = self._build_expense_snapshot(expense)
        if updated_snapshot == previous_snapshot:
            return {
                "success": False,
                "error": "Nenhuma alteracao valida foi identificada para esse lancamento.",
            }

        audit = ExpenseUpdateAudit(
            expense_id=expense.id,
            user_phone=normalized_phone,
            previous_snapshot=previous_snapshot,
            updated_snapshot=updated_snapshot,
        )
        session.add(audit)
        await session.commit()
        await session.refresh(expense)

        return {
            "success": True,
            "expense": {
                "id": expense.id,
                "description": expense.description,
                "amount": float(expense.amount),
                "category": expense.display_category,
                "payment_method": expense.payment_method.name if expense.payment_method else "",
                "date": expense.date.strftime("%d/%m/%Y"),
            },
        }

    async def list_expenses(
        self,
        session: AsyncSession,
        phone: str,
        month: int | None = None,
        year: int | None = None,
        limit: int = 100,
    ) -> list[dict[str, str | float | int | None | bool]]:
        """Return recent expenses for the dashboard with category and payment details."""
        normalized_phone = normalize_phone(phone)
        query = (
            select(Expense)
            .options(
                selectinload(Expense.category),
                selectinload(Expense.payment_method),
            )
            .where(Expense.user_phone == normalized_phone)
        )

        if month is not None:
            query = query.where(extract("month", Expense.date) == month)
        if year is not None:
            query = query.where(extract("year", Expense.date) == year)

        result = await session.execute(
            query.order_by(Expense.date.desc(), Expense.created_at.desc()).limit(limit)
        )
        expenses = result.scalars().all()
        return [
            {
                "id": int(expense.id),
                "description": str(expense.description),
                "amount": float(expense.amount),
                "category": expense.display_category,
                "payment_method": expense.payment_method.name if expense.payment_method else "",
                "date": expense.date.isoformat(),
                "date_label": expense.date.strftime("%d/%m/%Y"),
                "type": str(expense.type),
                "installment": expense.installment_display,
                "is_shared": bool(expense.is_shared),
                "shared_percentage": float(expense.shared_percentage)
                if expense.shared_percentage is not None
                else None,
                "goal_id": int(expense.goal_id) if expense.goal_id is not None else None,
                "goal_description": await self._get_goal_description(session, expense.goal_id),
                "funding_goal_description": await self._get_funding_goal_description(
                    session, expense.id
                ),
                "original_currency": str(expense.original_currency)
                if expense.original_currency
                else None,
                "original_amount": float(expense.original_amount)
                if expense.original_amount is not None
                else None,
                "exchange_rate": float(expense.exchange_rate)
                if expense.exchange_rate is not None
                else None,
            }
            for expense in expenses
        ]

    async def _get_goal_description(
        self,
        session: AsyncSession,
        goal_id: int | None,
    ) -> str | None:
        """Resolve a goal description when an expense is linked to a goal."""
        if goal_id is None:
            return None
        result = await session.execute(select(Goal.description).where(Goal.id == goal_id))
        return result.scalar_one_or_none()

    async def _get_funding_goal_description(
        self,
        session: AsyncSession,
        expense_id: int | None,
    ) -> str | None:
        """Resolve the goal description that funded a given expense, when available."""
        if expense_id is None:
            return None
        result = await session.execute(
            select(Goal.description)
            .join(GoalTransaction, GoalTransaction.goal_id == Goal.id)
            .where(GoalTransaction.related_expense_id == expense_id)
            .where(GoalTransaction.transaction_type == "withdrawal")
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def undo_last_expense(
        self,
        session: AsyncSession,
        phone: str,
        time_limit_minutes: int = 5,
    ) -> dict:
        """
        Undo (delete) the last expense created by the user.

        Only allows undoing expenses created within the time limit.

        Args:
            session: Database session
            phone: User phone number
            time_limit_minutes: Maximum age of expense that can be undone (default 5 minutes)

        Returns:
            Dict with success status and expense details or error message
        """
        try:
            normalized_phone = normalize_phone(phone)

            # Find the most recent expense for this user with eager loading
            result = await session.execute(
                select(Expense)
                .options(selectinload(Expense.category))
                .where(Expense.user_phone == normalized_phone)
                .order_by(Expense.created_at.desc())
                .limit(1)
            )

            expense = result.scalar_one_or_none()

            if not expense:
                return {"success": False, "error": "Voce nao tem nenhum gasto registrado."}

            # Check if expense was created within the time limit
            time_diff = datetime.now() - expense.created_at
            if time_diff.total_seconds() > (time_limit_minutes * 60):
                return {
                    "success": False,
                    "error": f"Nao e possivel desfazer. O ultimo gasto foi registrado ha mais de {time_limit_minutes} minutos.",
                }

            # Store expense details for the response message
            expense_details = {
                "description": expense.description,
                "amount": float(expense.amount),
                "category": expense.display_category,
            }

            # Delete the expense
            await session.delete(expense)
            await session.commit()

            logger.info(f"Undid expense: {expense.id} for {normalized_phone}")
            return {"success": True, "expense": expense_details}

        except Exception as e:
            logger.error(f"Error undoing expense: {e}")
            await session.rollback()
            return {"success": False, "error": str(e)}

    async def delete_expense(
        self,
        session: AsyncSession,
        phone: str,
        expense_id: int,
    ) -> dict:
        """Delete a specific expense and revert linked goal withdrawals when necessary."""
        try:
            normalized_phone = normalize_phone(phone)
            result = await session.execute(
                select(Expense)
                .options(selectinload(Expense.category), selectinload(Expense.payment_method))
                .where(Expense.id == expense_id)
                .where(Expense.user_phone == normalized_phone)
            )
            expense = result.scalar_one_or_none()
            if not expense:
                return {"success": False, "error": "Lancamento nao encontrado."}

            withdrawal_result = await session.execute(
                select(GoalTransaction, Goal)
                .join(Goal, Goal.id == GoalTransaction.goal_id)
                .where(GoalTransaction.related_expense_id == expense.id)
                .where(GoalTransaction.transaction_type == "withdrawal")
                .limit(1)
            )
            withdrawal_row = withdrawal_result.first()
            restored_goal_description = None
            if withdrawal_row is not None:
                withdrawal_transaction, goal = withdrawal_row
                previous_amount = goal.current_amount
                goal.current_amount = goal.current_amount + withdrawal_transaction.amount
                goal.updated_at = datetime.now()
                session.add(
                    GoalUpdate(
                        goal_id=goal.id,
                        previous_amount=previous_amount,
                        new_amount=goal.current_amount,
                        update_type="withdrawal_reversal",
                    )
                )
                restored_goal_description = goal.description
                await session.delete(withdrawal_transaction)

            expense_details = {
                "description": expense.description,
                "amount": float(expense.amount),
                "category": expense.display_category,
                "restored_goal": restored_goal_description,
            }

            await session.delete(expense)
            await session.commit()

            logger.info(f"Deleted expense: {expense.id} for {normalized_phone}")
            return {"success": True, "expense": expense_details}

        except Exception as e:
            logger.error(f"Error deleting expense: {e}")
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
        month: int | None = None,
        year: int | None = None,
    ) -> str:
        """Get monthly expense summary."""
        normalized_phone = normalize_phone(phone)
        today = date.today()

        if month is None:
            month = today.month
        if year is None:
            year = today.year

        # Query expenses for the month with eager loading of category
        result = await session.execute(
            select(Expense)
            .options(selectinload(Expense.category))
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
                cat_name = exp.display_category
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
            .options(
                selectinload(Expense.category),
                selectinload(Expense.payment_method),
            )
            .where(Expense.user_phone == normalized_phone)
            .where(extract("month", Expense.date) == month)
            .where(extract("year", Expense.date) == year)
            .order_by(Expense.date)
        )

        expenses = result.scalars().all()

        # Convert to list of dicts for export
        export_data = []
        for exp in expenses:
            export_data.append(
                {
                    "Data": exp.date.strftime("%d/%m/%Y"),
                    "Descricao": exp.description,
                    "Categoria": exp.display_category,
                    "Forma de Pagamento": exp.payment_method.name if exp.payment_method else "",
                    "Tipo": exp.type,
                    "Parcela": exp.installment_display or "",
                    "Valor": float(exp.amount),
                    "Compartilhada": "Sim" if exp.is_shared else "Nao",
                    "Percentual": float(exp.shared_percentage) / 100
                    if exp.shared_percentage
                    else "",
                }
            )

        return export_data

    async def get_expenses_by_category(
        self,
        session: AsyncSession,
        phone: str,
        month: int | None = None,
        year: int | None = None,
    ) -> list[dict]:
        """
        Get expenses grouped by category for chart generation.

        Returns list of dicts with 'category' and 'amount' keys,
        sorted by amount descending.
        """
        normalized_phone = normalize_phone(phone)
        today = date.today()

        if month is None:
            month = today.month
        if year is None:
            year = today.year

        # Query expenses for the month with eager loading of category
        result = await session.execute(
            select(Expense)
            .options(selectinload(Expense.category))
            .where(Expense.user_phone == normalized_phone)
            .where(Expense.type == "Negativo")
            .where(extract("month", Expense.date) == month)
            .where(extract("year", Expense.date) == year)
        )

        expenses = result.scalars().all()

        # Group by category
        by_category: dict[str, Decimal] = {}
        for exp in expenses:
            cat_name = exp.display_category
            by_category[cat_name] = by_category.get(cat_name, Decimal("0")) + exp.amount

        # Convert to list of dicts sorted by amount
        return [
            {"category": cat, "amount": amount}
            for cat, amount in sorted(by_category.items(), key=lambda x: x[1], reverse=True)
        ]

    async def get_top_expenses(
        self,
        session: AsyncSession,
        phone: str,
        month: int | None = None,
        year: int | None = None,
        limit: int = 10,
    ) -> list[dict]:
        """
        Get top expenses for bar chart generation.

        Returns list of dicts with 'description' and 'amount' keys,
        sorted by amount descending, limited to top N.
        """
        normalized_phone = normalize_phone(phone)
        today = date.today()

        if month is None:
            month = today.month
        if year is None:
            year = today.year

        # Query expenses for the month ordered by amount
        result = await session.execute(
            select(Expense)
            .where(Expense.user_phone == normalized_phone)
            .where(Expense.type == "Negativo")
            .where(extract("month", Expense.date) == month)
            .where(extract("year", Expense.date) == year)
            .order_by(Expense.amount.desc())
            .limit(limit)
        )

        expenses = result.scalars().all()

        return [{"description": exp.description, "amount": exp.amount} for exp in expenses]

    async def get_daily_totals(
        self,
        session: AsyncSession,
        phone: str,
        month: int | None = None,
        year: int | None = None,
    ) -> list[dict]:
        """
        Get daily expense totals for line chart generation.

        Returns list of dicts with 'date' (formatted as 'DD/MM') and 'amount' keys,
        sorted by date ascending.
        """
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
            .where(Expense.type == "Negativo")
            .where(extract("month", Expense.date) == month)
            .where(extract("year", Expense.date) == year)
            .order_by(Expense.date)
        )

        expenses = result.scalars().all()

        # Group by date
        by_date: dict[date, Decimal] = {}
        for exp in expenses:
            exp_date = cast(date, exp.date)
            by_date[exp_date] = by_date.get(exp_date, Decimal("0")) + exp.amount

        # Convert to list of dicts with formatted dates
        return [
            {"date": d.strftime("%d/%m"), "amount": amount} for d, amount in sorted(by_date.items())
        ]

    async def get_categories_list(self, session: AsyncSession, phone: str) -> str:
        """Return formatted list of all categories."""
        user = await self._get_user(session, normalize_phone(phone))
        if user is None:
            return "Nao consegui localizar seu perfil para listar categorias."
        return await self.category_service.format_categories_message(session, user)

    async def get_payment_methods_list(self, session: AsyncSession) -> str:
        """Return formatted list of all payment methods."""
        result = await session.execute(select(PaymentMethod).order_by(PaymentMethod.name))
        methods = result.scalars().all()

        msg = "Formas de pagamento disponiveis:\n\n"
        for method in methods:
            msg += f"  • {method.name}\n"

        return msg

    async def _get_category(
        self,
        session: AsyncSession,
        name: str,
    ) -> Category | None:
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
    ) -> PaymentMethod | None:
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

    async def _get_user(
        self,
        session: AsyncSession,
        phone: str,
    ) -> User | None:
        """Get a user profile by normalized phone."""
        result = await session.execute(select(User).where(User.phone == phone))
        return result.scalar_one_or_none()
