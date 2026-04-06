"""Tests for SchedulerService."""

from datetime import date, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select

from tests.conftest import Category, Expense, PaymentMethod, PendingConfirmation


class TestSchedulerService:
    """Tests for SchedulerService."""

    @pytest.fixture
    def mock_evolution(self):
        """Mock EvolutionService."""
        with patch("app.services.scheduler.EvolutionService") as MockEvolution:
            mock_instance = MagicMock()
            mock_instance.send_text = AsyncMock(return_value={"status": "ok"})
            MockEvolution.return_value = mock_instance
            yield mock_instance

    @pytest.fixture
    def scheduler_service(self, mock_evolution):
        """Create SchedulerService instance with mocked dependencies."""
        from app.services.scheduler import SchedulerService

        service = SchedulerService()
        service.evolution = mock_evolution
        return service

    @pytest.fixture
    async def recurring_expense_today(self, seeded_session, test_phone):
        """Create a recurring expense for today's day."""
        cat_result = await seeded_session.execute(
            select(Category).where(Category.name == "Assinatura")
        )
        category = cat_result.scalar_one()

        pm_result = await seeded_session.execute(
            select(PaymentMethod).where(PaymentMethod.name == "Cartão de Crédito")
        )
        payment_method = pm_result.scalar_one()

        today = date.today()
        expense = Expense(
            user_phone=test_phone,
            description="Netflix",
            amount=Decimal("55.90"),
            category_id=category.id,
            payment_method_id=payment_method.id,
            type="Negativo",
            is_recurring=True,
            recurring_day=today.day,
            recurring_active=True,
            date=today,
            created_at=datetime.now(),
        )

        seeded_session.add(expense)
        await seeded_session.commit()
        await seeded_session.refresh(expense)

        return expense

    async def test_get_todays_recurring_by_user_finds_expenses(
        self, scheduler_service, seeded_session, recurring_expense_today, test_phone
    ):
        """Test that _get_todays_recurring_by_user finds recurring expenses for today."""
        user_expenses = await scheduler_service._get_todays_recurring_by_user(seeded_session)

        assert test_phone in user_expenses
        assert len(user_expenses[test_phone]) == 1
        assert user_expenses[test_phone][0].description == "Netflix"

    async def test_get_todays_recurring_excludes_already_processed(
        self, scheduler_service, seeded_session, recurring_expense_today, test_phone
    ):
        """Test that already processed expenses are excluded."""
        # Create a non-recurring expense with same description this month
        cat_result = await seeded_session.execute(
            select(Category).where(Category.name == "Assinatura")
        )
        category = cat_result.scalar_one()

        pm_result = await seeded_session.execute(
            select(PaymentMethod).where(PaymentMethod.name == "Cartão de Crédito")
        )
        payment_method = pm_result.scalar_one()

        today = date.today()
        processed_expense = Expense(
            user_phone=test_phone,
            description="Netflix",
            amount=Decimal("55.90"),
            category_id=category.id,
            payment_method_id=payment_method.id,
            type="Negativo",
            is_recurring=False,  # This is the generated expense
            date=today,
            created_at=datetime.now(),
        )

        seeded_session.add(processed_expense)
        await seeded_session.commit()

        user_expenses = await scheduler_service._get_todays_recurring_by_user(seeded_session)

        # Should not include the recurring expense since it was already processed
        assert test_phone not in user_expenses or len(user_expenses.get(test_phone, [])) == 0

    async def test_get_todays_recurring_excludes_different_day(
        self, scheduler_service, seeded_session, test_phone
    ):
        """Test that recurring expenses for different days are excluded."""
        cat_result = await seeded_session.execute(
            select(Category).where(Category.name == "Assinatura")
        )
        category = cat_result.scalar_one()

        pm_result = await seeded_session.execute(
            select(PaymentMethod).where(PaymentMethod.name == "Cartão de Crédito")
        )
        payment_method = pm_result.scalar_one()

        today = date.today()
        different_day = (today.day % 28) + 1  # A different day

        expense = Expense(
            user_phone=test_phone,
            description="Spotify",
            amount=Decimal("21.90"),
            category_id=category.id,
            payment_method_id=payment_method.id,
            type="Negativo",
            is_recurring=True,
            recurring_day=different_day,
            recurring_active=True,
            date=today,
            created_at=datetime.now(),
        )

        seeded_session.add(expense)
        await seeded_session.commit()

        user_expenses = await scheduler_service._get_todays_recurring_by_user(seeded_session)

        assert test_phone not in user_expenses or len(user_expenses.get(test_phone, [])) == 0

    async def test_format_confirmation_message(self, scheduler_service):
        """Test confirmation message formatting."""
        expenses = [
            {"description": "Netflix", "amount": 55.90},
            {"description": "Spotify", "amount": 21.90},
        ]
        total = 77.80

        message = scheduler_service._format_confirmation_message(expenses, total)

        assert "*Despesas recorrentes de hoje:*" in message
        assert "Netflix" in message
        assert "R$ 55.90" in message
        assert "Spotify" in message
        assert "R$ 21.90" in message
        assert "*Total:* R$ 77.80" in message
        assert "Responda *sim* ou *nao*" in message

    async def test_save_recurring_pending(self, scheduler_service, seeded_session, test_phone):
        """Test saving pending confirmation for recurring expenses."""
        expenses = [
            {
                "id": 1,
                "description": "Netflix",
                "amount": 55.90,
                "category": "Assinatura",
                "payment_method": "Cartão de Crédito",
                "category_id": 1,
                "payment_method_id": 1,
            }
        ]
        total = 55.90

        await scheduler_service._save_recurring_pending(seeded_session, test_phone, expenses, total)

        # Check that pending was created
        result = await seeded_session.execute(
            select(PendingConfirmation).where(PendingConfirmation.user_phone == test_phone)
        )
        pending = result.scalar_one_or_none()

        assert pending is not None
        assert pending.data["type"] == "recurring_confirmation"
        assert pending.data["total"] == 55.90
        assert len(pending.data["expenses"]) == 1
        assert pending.expires_at > datetime.now()

    async def test_send_recurring_confirmation(
        self, scheduler_service, seeded_session, recurring_expense_today, test_phone
    ):
        """Test sending confirmation request to user."""
        expenses = [recurring_expense_today]

        await scheduler_service._send_recurring_confirmation(seeded_session, test_phone, expenses)

        # Check that message was sent
        scheduler_service.evolution.send_text.assert_called_once()
        call_args = scheduler_service.evolution.send_text.call_args
        assert call_args[0][0] == test_phone
        assert "Netflix" in call_args[0][1]

        # Check that pending was created
        result = await seeded_session.execute(
            select(PendingConfirmation).where(PendingConfirmation.user_phone == test_phone)
        )
        pending = result.scalar_one_or_none()
        assert pending is not None

    async def test_trigger_recurring_job_manually(
        self, scheduler_service, seeded_session, recurring_expense_today
    ):
        """Test manual trigger of recurring job."""
        with (
            patch.object(scheduler_service, "_get_todays_recurring_by_user") as mock_get,
            patch.object(scheduler_service, "_send_recurring_confirmation") as mock_send,
        ):
            mock_get.return_value = {"5511999999999": [recurring_expense_today]}
            mock_send.return_value = None

            result = await scheduler_service.trigger_recurring_job_manually()

            assert result["status"] == "completed"
            assert "timestamp" in result


class TestRecurringService:
    """Tests for recurring date calculations."""

    @pytest.fixture
    def recurring_service(self):
        from app.services.recurring import RecurringService

        return RecurringService()

    async def test_get_upcoming_recurring_handles_month_boundary(
        self, recurring_service, seeded_session, test_phone
    ):
        """Test upcoming recurring preview works across month boundaries."""
        cat_result = await seeded_session.execute(
            select(Category).where(Category.name == "Assinatura")
        )
        category = cat_result.scalar_one()

        pm_result = await seeded_session.execute(
            select(PaymentMethod).where(PaymentMethod.name == "Cartão de Crédito")
        )
        payment_method = pm_result.scalar_one()

        seeded_session.add_all(
            [
                Expense(
                    user_phone=test_phone,
                    description="Netflix",
                    amount=Decimal("55.90"),
                    category_id=category.id,
                    payment_method_id=payment_method.id,
                    type="Negativo",
                    is_recurring=True,
                    recurring_day=30,
                    recurring_active=True,
                    date=date(2026, 1, 1),
                    created_at=datetime.now(),
                ),
                Expense(
                    user_phone=test_phone,
                    description="Spotify",
                    amount=Decimal("21.90"),
                    category_id=category.id,
                    payment_method_id=payment_method.id,
                    type="Negativo",
                    is_recurring=True,
                    recurring_day=2,
                    recurring_active=True,
                    date=date(2026, 1, 1),
                    created_at=datetime.now(),
                ),
            ]
        )
        await seeded_session.commit()

        class FakeDate(date):
            @classmethod
            def today(cls):
                return cls(2026, 1, 28)

        with patch("app.services.recurring.date", FakeDate):
            upcoming = await recurring_service.get_upcoming_recurring(
                seeded_session,
                test_phone,
                days=7,
            )

        assert [item["day"] for item in upcoming] == [2, 30]


class TestSchedulerServiceStartStop:
    """Tests for scheduler start/stop functionality."""

    async def test_start_when_enabled(self):
        """Test that scheduler starts when enabled."""
        from app.services.scheduler import SchedulerService

        mock_settings = MagicMock()
        mock_settings.scheduler_enabled = True
        mock_settings.scheduler_timezone = "America/Sao_Paulo"
        mock_settings.scheduler_hour = 8
        mock_settings.scheduler_minute = 0

        with (
            patch("app.services.scheduler.settings", mock_settings),
            patch("app.services.scheduler.EvolutionService"),
        ):
            service = SchedulerService()
            service.start()

            assert service.scheduler is not None
            assert service.scheduler.running

            service.shutdown()

    async def test_start_when_disabled(self):
        """Test that scheduler doesn't start when disabled."""
        from app.services.scheduler import SchedulerService

        mock_settings = MagicMock()
        mock_settings.scheduler_enabled = False

        with (
            patch("app.services.scheduler.settings", mock_settings),
            patch("app.services.scheduler.EvolutionService"),
        ):
            service = SchedulerService()
            service.start()

            assert service.scheduler is None

    async def test_shutdown_gracefully(self):
        """Test graceful shutdown does not raise exception."""
        from app.services.scheduler import SchedulerService

        mock_settings = MagicMock()
        mock_settings.scheduler_enabled = True
        mock_settings.scheduler_timezone = "America/Sao_Paulo"
        mock_settings.scheduler_hour = 8
        mock_settings.scheduler_minute = 0

        with (
            patch("app.services.scheduler.settings", mock_settings),
            patch("app.services.scheduler.EvolutionService"),
        ):
            service = SchedulerService()
            service.start()

            assert service.scheduler.running

            # Shutdown should not raise any exception
            service.shutdown()


class TestSchedulerDistributedLock:
    """Tests for scheduler distributed lock behavior."""

    async def test_runs_without_redis_in_single_instance_mode(self):
        """Single-instance mode may continue without Redis lock."""
        from app.services.scheduler import SchedulerService

        mock_settings = MagicMock()
        mock_settings.redis_url = "redis://localhost:6379"
        mock_settings.effective_instance_id = "instance-a"
        mock_settings.normalized_deployment_mode = "single_instance"
        mock_settings.effective_scheduler_lock_ttl_seconds = 1800

        with (
            patch("app.services.scheduler.settings", mock_settings),
            patch("app.services.scheduler.EvolutionService"),
        ):
            service = SchedulerService()
            service._get_redis = AsyncMock(return_value=None)
            job = AsyncMock()

            await service._run_singleton_job("test-job", job)

        job.assert_awaited_once()

    async def test_skips_without_redis_in_multi_instance_mode(self):
        """Multi-instance mode must skip jobs when Redis is unavailable."""
        from app.services.scheduler import SchedulerService

        mock_settings = MagicMock()
        mock_settings.redis_url = "redis://localhost:6379"
        mock_settings.effective_instance_id = "instance-a"
        mock_settings.normalized_deployment_mode = "multi_instance"
        mock_settings.effective_scheduler_lock_ttl_seconds = 1800

        with (
            patch("app.services.scheduler.settings", mock_settings),
            patch("app.services.scheduler.EvolutionService"),
        ):
            service = SchedulerService()
            service._get_redis = AsyncMock(return_value=None)
            job = AsyncMock()

            await service._run_singleton_job("test-job", job)

        job.assert_not_awaited()

    async def test_skips_when_lock_is_already_held(self):
        """Job should not run when another instance already owns the lock."""
        from app.services.scheduler import SchedulerService

        class MockRedis:
            async def set(self, *args, **kwargs):
                return False

        mock_settings = MagicMock()
        mock_settings.redis_url = "redis://localhost:6379"
        mock_settings.effective_instance_id = "instance-a"
        mock_settings.normalized_deployment_mode = "multi_instance"
        mock_settings.effective_scheduler_lock_ttl_seconds = 1800

        with (
            patch("app.services.scheduler.settings", mock_settings),
            patch("app.services.scheduler.EvolutionService"),
        ):
            service = SchedulerService()
            service._get_redis = AsyncMock(return_value=MockRedis())
            service._release_job_lock = AsyncMock()
            job = AsyncMock()

            await service._run_singleton_job("test-job", job)

        job.assert_not_awaited()
        service._release_job_lock.assert_not_awaited()

    async def test_runs_and_releases_lock_when_acquired(self):
        """Job should run and release lock when acquisition succeeds."""
        from app.services.scheduler import SchedulerService

        class MockRedis:
            async def set(self, *args, **kwargs):
                return True

        mock_settings = MagicMock()
        mock_settings.redis_url = "redis://localhost:6379"
        mock_settings.effective_instance_id = "instance-a"
        mock_settings.normalized_deployment_mode = "multi_instance"
        mock_settings.effective_scheduler_lock_ttl_seconds = 1800

        with (
            patch("app.services.scheduler.settings", mock_settings),
            patch("app.services.scheduler.EvolutionService"),
        ):
            service = SchedulerService()
            service._get_redis = AsyncMock(return_value=MockRedis())
            service._release_job_lock = AsyncMock()
            job = AsyncMock()

            await service._run_singleton_job("test-job", job)

        job.assert_awaited_once()
        service._release_job_lock.assert_awaited_once()


class TestRecurringConfirmationHandler:
    """Tests for recurring confirmation handling in webhook."""

    @pytest.fixture
    def mock_evolution(self):
        """Mock EvolutionService."""
        mock = MagicMock()
        mock.send_text = AsyncMock(return_value={"status": "ok"})
        mock.extract_message_data = MagicMock()
        return mock

    @pytest.fixture
    def mock_ai(self):
        """Mock AIService."""
        mock = MagicMock()
        mock.evaluate_confirmation_response = AsyncMock(
            return_value={"action": "confirm", "adjustments": {}, "confidence": 1.0}
        )
        mock.format_budget_alert = MagicMock(return_value="Alert message")
        return mock

    @pytest.fixture
    async def pending_recurring(self, seeded_session, test_phone):
        """Create a pending recurring confirmation."""
        pending = PendingConfirmation(
            user_phone=test_phone,
            data={
                "type": "recurring_confirmation",
                "expenses": [
                    {
                        "id": 1,
                        "description": "Netflix",
                        "amount": 55.90,
                        "category": "Assinatura",
                        "payment_method": "Cartão de Crédito",
                        "category_id": 5,  # Assinatura
                        "payment_method_id": 2,  # Cartão de Crédito
                    }
                ],
                "total": 55.90,
            },
            expires_at=datetime.now() + timedelta(hours=4),
            created_at=datetime.now(),
        )

        seeded_session.add(pending)
        await seeded_session.commit()
        await seeded_session.refresh(pending)

        return pending

    async def test_confirm_recurring_creates_expenses(
        self,
        seeded_session,
        test_phone,
        pending_recurring,
        mock_evolution,
        mock_ai,
        accepted_user_in_db,
    ):
        """Test that confirming recurring creates expenses."""
        from app.handlers.webhook import WebhookHandler

        handler = WebhookHandler()
        handler.evolution = mock_evolution
        handler.ai = mock_ai

        await handler._handle_recurring_confirmation(
            seeded_session,
            test_phone,
            "sim",
            pending_recurring.data,
            accepted_user_in_db,
        )

        # Check that expense was created
        result = await seeded_session.execute(
            select(Expense).where(
                Expense.user_phone == test_phone,
                Expense.description == "Netflix",
                Expense.is_recurring == False,
            )
        )
        expense = result.scalar_one_or_none()

        assert expense is not None
        assert float(expense.amount) == 55.90

        # Check confirmation message was sent
        mock_evolution.send_text.assert_called()
        call_args = mock_evolution.send_text.call_args
        assert "Lancadas" in call_args[0][1]

    async def test_deny_recurring_ignores_expenses(
        self,
        seeded_session,
        test_phone,
        pending_recurring,
        mock_evolution,
        mock_ai,
        accepted_user_in_db,
    ):
        """Test that denying recurring ignores expenses."""
        from app.handlers.webhook import WebhookHandler

        handler = WebhookHandler()
        handler.evolution = mock_evolution
        handler.ai = mock_ai

        await handler._handle_recurring_confirmation(
            seeded_session,
            test_phone,
            "nao",
            pending_recurring.data,
            accepted_user_in_db,
        )

        # Check that no expense was created
        result = await seeded_session.execute(
            select(Expense).where(
                Expense.user_phone == test_phone,
                Expense.description == "Netflix",
                Expense.is_recurring == False,
            )
        )
        expense = result.scalar_one_or_none()

        assert expense is None

        # Check ignore message was sent
        mock_evolution.send_text.assert_called()
        call_args = mock_evolution.send_text.call_args
        assert "ignoradas" in call_args[0][1]
