"""Recurring expenses scheduler service."""

import logging
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import extract, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database.connection import async_session
from app.database.models import Expense, PendingConfirmation
from app.services.evolution import EvolutionService
from app.utils.validators import normalize_phone

logger = logging.getLogger(__name__)
settings = get_settings()


class SchedulerService:
    """Service for managing scheduled tasks."""

    def __init__(self):
        self.scheduler: AsyncIOScheduler | None = None
        self.evolution = EvolutionService()

    def start(self) -> None:
        """Start the scheduler with configured jobs."""
        if not settings.scheduler_enabled:
            logger.info("Scheduler is disabled via configuration")
            return

        timezone = ZoneInfo(settings.scheduler_timezone)

        self.scheduler = AsyncIOScheduler(timezone=timezone)

        # Add recurring expenses job
        self.scheduler.add_job(
            self.process_recurring_job,
            CronTrigger(
                hour=settings.scheduler_hour,
                minute=settings.scheduler_minute,
                timezone=timezone,
            ),
            id="process_recurring_expenses",
            name="Process daily recurring expenses",
            replace_existing=True,
        )

        self.scheduler.start()
        logger.info(
            f"Scheduler started. Recurring expenses job scheduled at "
            f"{settings.scheduler_hour:02d}:{settings.scheduler_minute:02d} "
            f"({settings.scheduler_timezone})"
        )

    def shutdown(self) -> None:
        """Shutdown the scheduler gracefully."""
        if self.scheduler and self.scheduler.running:
            self.scheduler.shutdown(wait=False)
            logger.info("Scheduler shut down")

    async def process_recurring_job(self) -> None:
        """
        Job function to check recurring expenses and send confirmation requests.

        This is called by the scheduler at the configured time daily.
        """
        logger.info("Starting recurring expenses job...")

        try:
            async with async_session() as session:
                # Get all pending recurring expenses for today grouped by user
                user_expenses = await self._get_todays_recurring_by_user(session)

                if not user_expenses:
                    logger.info("No recurring expenses to process today")
                    return

                # Send confirmation request to each user
                for phone, expenses in user_expenses.items():
                    await self._send_recurring_confirmation(session, phone, expenses)

                logger.info(f"Sent recurring confirmations to {len(user_expenses)} user(s)")

        except Exception as e:
            logger.error(f"Error in recurring expenses job: {e}", exc_info=True)

    async def _get_todays_recurring_by_user(
        self,
        session: AsyncSession,
    ) -> dict[str, list[Expense]]:
        """
        Get all recurring expenses due today, grouped by user phone.

        Returns dict mapping phone -> list of recurring expenses.
        """
        today = date.today()

        # Get all active recurring expenses for today's day
        result = await session.execute(
            select(Expense)
            .where(Expense.is_recurring == True)
            .where(Expense.recurring_active == True)
            .where(Expense.recurring_day == today.day)
        )

        recurring_expenses = result.scalars().all()

        # Group by user and filter out already processed this month
        user_expenses: dict[str, list[Expense]] = {}

        for recurring in recurring_expenses:
            # Check if already created this month
            existing = await session.execute(
                select(Expense)
                .where(Expense.user_phone == recurring.user_phone)
                .where(Expense.description == recurring.description)
                .where(Expense.is_recurring == False)
                .where(extract("month", Expense.date) == today.month)
                .where(extract("year", Expense.date) == today.year)
            )

            if existing.scalar_one_or_none():
                logger.debug(
                    f"Recurring expense already processed this month: {recurring.description}"
                )
                continue

            # Add to user's list
            phone = str(recurring.user_phone)
            if phone not in user_expenses:
                user_expenses[phone] = []
            user_expenses[phone].append(recurring)

        return user_expenses

    async def _send_recurring_confirmation(
        self,
        session: AsyncSession,
        phone: str,
        expenses: list[Expense],
    ) -> None:
        """Send confirmation request to user for recurring expenses."""
        normalized_phone = normalize_phone(phone)

        # Build expense list for message
        expense_list = []
        total = 0.0

        for exp in expenses:
            amount = float(exp.amount)
            total += amount
            expense_list.append(
                {
                    "id": exp.id,
                    "description": exp.description,
                    "amount": amount,
                    "category": exp.category.name if exp.category else "Outros",
                    "payment_method": exp.payment_method.name if exp.payment_method else "Pix",
                    "category_id": exp.category_id,
                    "payment_method_id": exp.payment_method_id,
                }
            )

        # Format message
        message = self._format_confirmation_message(expense_list, total)

        # Save pending confirmation
        await self._save_recurring_pending(session, normalized_phone, expense_list, total)

        # Send WhatsApp message
        try:
            await self.evolution.send_text(phone, message)
            logger.info(f"Sent recurring confirmation to {phone} for {len(expenses)} expense(s)")
        except Exception as e:
            logger.error(f"Failed to send recurring confirmation to {phone}: {e}")

    def _format_confirmation_message(self, expenses: list[dict], total: float) -> str:
        """Format the confirmation message for WhatsApp."""
        lines = ["*Despesas recorrentes de hoje:*\n"]

        for exp in expenses:
            lines.append(f"  - {exp['description']}: R$ {exp['amount']:.2f}")

        lines.append(f"\n*Total:* R$ {total:.2f}")
        lines.append("\nJa pagou? Responda *sim* ou *nao*")

        return "\n".join(lines)

    async def _save_recurring_pending(
        self,
        session: AsyncSession,
        phone: str,
        expenses: list[dict],
        total: float,
    ) -> None:
        """Save pending confirmation for recurring expenses."""
        from sqlalchemy import delete

        # Delete any existing pending for this user
        await session.execute(
            delete(PendingConfirmation).where(PendingConfirmation.user_phone == phone)
        )

        # Create new pending confirmation
        pending = PendingConfirmation(
            user_phone=phone,
            data={
                "type": "recurring_confirmation",
                "expenses": expenses,
                "total": total,
            },
            expires_at=datetime.now() + timedelta(hours=4),
        )
        session.add(pending)
        await session.commit()

    async def trigger_recurring_job_manually(self) -> dict:
        """
        Manually trigger the recurring job (for testing).

        Returns dict with result information.
        """
        logger.info("Manually triggering recurring expenses job...")
        await self.process_recurring_job()
        return {"status": "completed", "timestamp": datetime.now().isoformat()}


# Singleton instance
_scheduler_service: SchedulerService | None = None


def get_scheduler_service() -> SchedulerService:
    """Get or create the scheduler service singleton."""
    global _scheduler_service
    if _scheduler_service is None:
        _scheduler_service = SchedulerService()
    return _scheduler_service
