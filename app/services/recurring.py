"""Recurring expenses service."""

import logging
from datetime import date, timedelta

from sqlalchemy import extract, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Expense
from app.utils.validators import normalize_phone

logger = logging.getLogger(__name__)


class RecurringService:
    """Service for managing recurring expenses."""

    async def process_recurring_expenses(
        self,
        session: AsyncSession,
    ) -> int:
        """
        Process all recurring expenses for today.

        This should be called daily by a scheduler.
        Returns the number of expenses created.
        """
        today = date.today()
        created_count = 0

        # Get all active recurring expenses for today's day
        result = await session.execute(
            select(Expense)
            .where(Expense.is_recurring == True)
            .where(Expense.recurring_active == True)
            .where(Expense.recurring_day == today.day)
        )

        recurring_expenses = result.scalars().all()

        for recurring in recurring_expenses:
            # Check if already created this month
            existing = await session.execute(
                select(Expense)
                .where(Expense.user_phone == recurring.user_phone)
                .where(Expense.description == recurring.description)
                .where(Expense.is_recurring == False)  # Created from recurring
                .where(extract("month", Expense.date) == today.month)
                .where(extract("year", Expense.date) == today.year)
            )

            if existing.scalar_one_or_none():
                logger.debug(f"Recurring expense already processed: {recurring.description}")
                continue

            # Create new expense from recurring
            new_expense = Expense(
                user_phone=recurring.user_phone,
                description=recurring.description,
                amount=recurring.amount,
                category_id=recurring.category_id,
                payment_method_id=recurring.payment_method_id,
                type=recurring.type,
                is_shared=recurring.is_shared,
                shared_percentage=recurring.shared_percentage,
                is_recurring=False,  # This is a generated expense
                date=today,
            )

            session.add(new_expense)
            created_count += 1

            logger.info(
                f"Created expense from recurring: {recurring.description} "
                f"for {recurring.user_phone}"
            )

        await session.commit()
        return created_count

    async def get_upcoming_recurring(
        self,
        session: AsyncSession,
        phone: str,
        days: int = 7,
    ) -> list[dict]:
        """Get recurring expenses coming up in the next N days."""
        normalized_phone = normalize_phone(phone)
        today = date.today()

        upcoming_days = { (today + timedelta(days=i)).day for i in range(days) }

        result = await session.execute(
            select(Expense)
            .where(Expense.user_phone == normalized_phone)
            .where(Expense.is_recurring == True)
            .where(Expense.recurring_active == True)
            .where(Expense.recurring_day.in_(upcoming_days))
            .order_by(Expense.recurring_day)
        )

        expenses = result.scalars().all()

        return [
            {
                "description": exp.description,
                "amount": float(exp.amount),
                "day": exp.recurring_day,
            }
            for exp in expenses
        ]
