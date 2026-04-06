"""Recurring expenses scheduler service."""

import inspect
import logging
from collections.abc import Awaitable, Callable
from datetime import date, datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from redis.asyncio import Redis
from sqlalchemy import extract, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database.connection import async_session
from app.database.models import Expense, PendingConfirmation
from app.services.evolution import EvolutionService
from app.services.operational_status import OperationalStatusService
from app.utils.validators import normalize_phone

logger = logging.getLogger(__name__)
settings = get_settings()
operational_status = OperationalStatusService()


class SchedulerService:
    """Service for managing scheduled tasks."""

    def __init__(self) -> None:
        self.scheduler: AsyncIOScheduler | None = None
        self.evolution: EvolutionService = EvolutionService()
        self.redis_url: str = settings.redis_url
        self.instance_id: str = settings.effective_instance_id or f"scheduler-{uuid4().hex}"
        self._redis: Redis | None = None

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

        # Add weekly goal motivation job (runs every Sunday at 10:00)
        self.scheduler.add_job(
            self.send_weekly_goal_motivation,
            CronTrigger(
                day_of_week="sun",
                hour=10,
                minute=0,
                timezone=timezone,
            ),
            id="weekly_goal_motivation",
            name="Send weekly goal motivation messages",
            replace_existing=True,
        )

        # Add weekly exchange rates update job (runs every Monday at 6:00)
        self.scheduler.add_job(
            self.update_exchange_rates,
            CronTrigger(
                day_of_week="mon",
                hour=6,
                minute=0,
                timezone=timezone,
            ),
            id="weekly_exchange_rates_update",
            name="Update exchange rates in database",
            replace_existing=True,
        )

        self.scheduler.start()
        logger.info(
            f"Scheduler started. Recurring expenses job scheduled at "
            f"{settings.scheduler_hour:02d}:{settings.scheduler_minute:02d} "
            f"({settings.scheduler_timezone}). "
            f"Weekly goal motivation scheduled for Sundays at 10:00. "
            f"Weekly exchange rates update scheduled for Mondays at 06:00. "
            f"Deployment mode: {settings.normalized_deployment_mode}. "
            f"Scheduler lock TTL: {settings.effective_scheduler_lock_ttl_seconds}s."
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
        await self._run_singleton_job(
            "process_recurring_expenses",
            self._process_recurring_job_impl,
        )

    async def _process_recurring_job_impl(self) -> None:
        """Actual recurring processing logic executed under the scheduler guard."""
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
        expense_list: list[dict[str, int | float | str]] = []
        total = 0.0

        for exp in expenses:
            amount = float(exp.amount)
            total += amount
            expense_list.append(
                {
                    "id": int(exp.id),
                    "description": str(exp.description),
                    "amount": amount,
                    "category": exp.display_category,
                    "payment_method": exp.payment_method.name if exp.payment_method else "Pix",
                    "category_id": int(exp.category_id),
                    "payment_method_id": int(exp.payment_method_id),
                    "custom_category_name": str(exp.custom_category_name)
                    if exp.custom_category_name
                    else "",
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

    def _format_confirmation_message(
        self,
        expenses: list[dict[str, int | float | str]],
        total: float,
    ) -> str:
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

    async def send_weekly_goal_motivation(self) -> None:
        """
        Job function to send weekly motivation messages for active goals.

        This is called by the scheduler every Sunday at 10:00.
        """
        await self._run_singleton_job(
            "weekly_goal_motivation",
            self._send_weekly_goal_motivation_impl,
        )

    async def _send_weekly_goal_motivation_impl(self) -> None:
        """Actual weekly goal motivation logic executed under the scheduler guard."""
        from app.services.ai import AIService
        from app.services.goal import GoalService

        logger.info("Starting weekly goal motivation job...")

        goal_service = GoalService()
        ai_service = AIService()

        try:
            async with async_session() as session:
                # Get all users with active goals
                users_with_goals = await goal_service.get_users_with_active_goals(session)

                if not users_with_goals:
                    logger.info("No users with active goals")
                    return

                sent_count = 0
                for phone in users_with_goals:
                    try:
                        motivations = await goal_service.get_weekly_motivation(session, phone)

                        for motivation in motivations:
                            msg = ai_service.format_goal_motivation(motivation)
                            await self.evolution.send_text(phone, msg)
                            sent_count += 1

                    except Exception as e:
                        logger.error(f"Error sending motivation to {phone}: {e}")
                        continue

                logger.info(
                    f"Sent {sent_count} goal motivation message(s) to "
                    f"{len(users_with_goals)} user(s)"
                )

        except Exception as e:
            logger.error(f"Error in weekly goal motivation job: {e}", exc_info=True)

    async def trigger_goal_motivation_manually(self) -> dict[str, str]:
        """
        Manually trigger the goal motivation job (for testing).

        Returns dict with result information.
        """
        logger.info("Manually triggering weekly goal motivation job...")
        await self.send_weekly_goal_motivation()
        return {"status": "completed", "timestamp": datetime.now().isoformat()}

    async def trigger_recurring_job_manually(self) -> dict[str, str]:
        """
        Manually trigger the recurring job (for testing).

        Returns dict with result information.
        """
        logger.info("Manually triggering recurring expenses job...")
        await self.process_recurring_job()
        return {"status": "completed", "timestamp": datetime.now().isoformat()}

    async def update_exchange_rates(self) -> None:
        """
        Job function to update exchange rates in database.

        This is called by the scheduler every Monday at 06:00.
        """
        await self._run_singleton_job(
            "weekly_exchange_rates_update",
            self._update_exchange_rates_impl,
        )

    async def _update_exchange_rates_impl(self) -> None:
        """Actual exchange-rate refresh logic executed under the scheduler guard."""
        from app.services.currency import CurrencyService

        logger.info("Starting weekly exchange rates update job...")

        try:
            currency_service = CurrencyService()
            updated = await currency_service.update_fallback_rates()

            if updated:
                logger.info("Exchange rates updated successfully")
            else:
                logger.warning("Failed to update exchange rates")

        except Exception as e:
            logger.error(f"Error in exchange rates update job: {e}", exc_info=True)

    async def trigger_exchange_rates_update_manually(self) -> dict[str, str]:
        """
        Manually trigger the exchange rates update job (for testing).

        Returns dict with result information.
        """
        logger.info("Manually triggering exchange rates update job...")
        await self.update_exchange_rates()
        return {"status": "completed", "timestamp": datetime.now().isoformat()}

    async def _run_singleton_job(
        self,
        job_name: str,
        job_coro: Callable[[], Awaitable[None]],
    ) -> None:
        """Run a scheduler job under a distributed lock when needed."""
        lock_token = await self._acquire_job_lock(job_name)
        if lock_token is False:
            return

        try:
            await job_coro()
        finally:
            if isinstance(lock_token, str):
                await self._release_job_lock(job_name, lock_token)

    async def _acquire_job_lock(self, job_name: str) -> str | None | bool:
        """Acquire distributed lock for a job.

        Returns:
            str token when a distributed lock was acquired
            None when local execution is allowed without distributed lock
            False when the job should be skipped
        """
        redis_client = await self._get_redis()
        if redis_client is None:
            if settings.normalized_deployment_mode == "multi_instance":
                logger.error(
                    "Skipping scheduler job '%s' because Redis is unavailable in multi-instance mode",
                    job_name,
                )
                operational_status.record_event(
                    "scheduler",
                    "error",
                    f"Scheduler job '{job_name}' skipped because Redis is unavailable in multi-instance mode.",
                )
                return False

            logger.warning(
                "Running scheduler job '%s' without distributed lock because deployment mode is single_instance",
                job_name,
            )
            operational_status.record_event(
                "scheduler",
                "warning",
                f"Scheduler job '{job_name}' running without distributed lock in single-instance mode.",
            )
            return None

        lock_key = self._build_lock_key(job_name)
        token = f"{self.instance_id}:{uuid4().hex}"
        try:
            acquired = await redis_client.set(
                lock_key,
                token,
                ex=settings.effective_scheduler_lock_ttl_seconds,
                nx=True,
            )
        except Exception as exc:
            if settings.normalized_deployment_mode == "multi_instance":
                logger.error(
                    "Skipping scheduler job '%s' because lock acquisition failed in multi-instance mode: %s",
                    job_name,
                    exc,
                )
                operational_status.record_event(
                    "scheduler",
                    "error",
                    f"Scheduler job '{job_name}' skipped because lock acquisition failed in multi-instance mode.",
                )
                return False

            logger.warning(
                "Running scheduler job '%s' without distributed lock after Redis failure: %s",
                job_name,
                exc,
            )
            operational_status.record_event(
                "scheduler",
                "warning",
                f"Scheduler job '{job_name}' running without distributed lock after Redis failure in single-instance mode.",
            )
            return None

        if not acquired:
            logger.info(
                "Skipping scheduler job '%s' because another instance owns the lock", job_name
            )
            operational_status.record_event(
                "scheduler",
                "info",
                f"Scheduler job '{job_name}' skipped because another instance owns the lock.",
            )
            return False

        logger.info("Acquired scheduler lock for '%s' on instance %s", job_name, self.instance_id)
        return token

    async def _release_job_lock(self, job_name: str, token: str) -> None:
        """Release distributed lock only when owned by this instance."""
        redis_client = await self._get_redis()
        if redis_client is None:
            return

        script = """
        if redis.call('get', KEYS[1]) == ARGV[1] then
            return redis.call('del', KEYS[1])
        end
        return 0
        """
        try:
            release_result = redis_client.eval(script, 1, self._build_lock_key(job_name), token)
            if inspect.isawaitable(release_result):
                await release_result
        except Exception as exc:
            logger.warning("Failed to release scheduler lock for '%s': %s", job_name, exc)

    async def _get_redis(self) -> Redis | None:
        """Lazily initialize Redis client for scheduler locks."""
        if self._redis is not None:
            return self._redis

        try:
            self._redis = Redis.from_url(self.redis_url, decode_responses=True)
            return self._redis
        except Exception as exc:
            logger.warning(f"Could not initialize Redis client for scheduler locks: {exc}")
            return None

    def _build_lock_key(self, job_name: str) -> str:
        """Build Redis lock key for a scheduler job."""
        return f"finbot:scheduler:lock:{job_name}"


# Singleton instance
_scheduler_service: SchedulerService | None = None


def get_scheduler_service() -> SchedulerService:
    """Get or create the scheduler service singleton."""
    global _scheduler_service
    if _scheduler_service is None:
        _scheduler_service = SchedulerService()
    return _scheduler_service
