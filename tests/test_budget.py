"""Tests for BudgetService."""

from datetime import date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select

from tests.conftest import Budget, Category, Expense, PaymentMethod


class TestBudgetService:
    """Tests for BudgetService class."""

    @pytest.fixture
    def budget_service(self):
        """Create BudgetService instance."""
        from app.services.budget import BudgetService

        return BudgetService()

    @pytest.mark.anyio
    async def test_create_budget_for_category(self, seeded_session, budget_service, test_phone):
        """Test creating a budget for a specific category."""
        result = await budget_service.create_budget(
            seeded_session, test_phone, "Alimentação", Decimal("500.00")
        )

        assert result["success"] is True
        assert result["budget_id"] is not None
        assert result["updated"] is False
        assert result["category"] == "Alimentação"
        assert result["limit"] == 500.00

        # Verify budget was saved
        budget = await seeded_session.execute(select(Budget).where(Budget.user_phone == test_phone))
        budget = budget.scalar_one()
        assert budget.monthly_limit == Decimal("500.00")
        assert budget.is_active is True

    @pytest.mark.anyio
    async def test_create_budget_updates_existing(self, seeded_session, budget_service, test_phone):
        """Test that creating a budget for same category updates existing one."""
        # Create initial budget
        await budget_service.create_budget(
            seeded_session, test_phone, "Alimentação", Decimal("500.00")
        )

        # Create again with different limit
        result = await budget_service.create_budget(
            seeded_session, test_phone, "Alimentação", Decimal("800.00")
        )

        assert result["success"] is True
        assert result["updated"] is True
        assert result["limit"] == 800.00

        # Verify only one budget exists
        budgets = await seeded_session.execute(
            select(Budget).where(Budget.user_phone == test_phone)
        )
        budgets = budgets.scalars().all()
        assert len(budgets) == 1
        assert budgets[0].monthly_limit == Decimal("800.00")

    @pytest.mark.anyio
    async def test_create_budget_invalid_category(self, seeded_session, budget_service, test_phone):
        """Test creating a budget with invalid category."""
        result = await budget_service.create_budget(
            seeded_session, test_phone, "InvalidCategory", Decimal("500.00")
        )

        assert result["success"] is False
        assert "nao encontrada" in result["error"]

    @pytest.mark.anyio
    async def test_create_budget_for_income_category_fails(
        self, seeded_session, budget_service, test_phone
    ):
        """Test that creating a budget for income category fails."""
        result = await budget_service.create_budget(
            seeded_session, test_phone, "Salario", Decimal("5000.00")
        )

        assert result["success"] is False
        assert "categorias de gastos" in result["error"]

    @pytest.mark.anyio
    async def test_create_budget_zero_limit_fails(self, seeded_session, budget_service, test_phone):
        """Test that creating a budget with zero limit fails."""
        result = await budget_service.create_budget(
            seeded_session, test_phone, "Alimentação", Decimal("0.00")
        )

        assert result["success"] is False
        assert "maior que zero" in result["error"]

    @pytest.mark.anyio
    async def test_remove_budget(self, seeded_session, budget_service, test_phone):
        """Test removing a budget."""
        # Create budget first
        await budget_service.create_budget(
            seeded_session, test_phone, "Alimentação", Decimal("500.00")
        )

        # Remove it
        result = await budget_service.remove_budget(seeded_session, test_phone, "Alimentação")

        assert result["success"] is True

        # Verify budget is deactivated
        budget = await seeded_session.execute(select(Budget).where(Budget.user_phone == test_phone))
        budget = budget.scalar_one()
        assert budget.is_active is False

    @pytest.mark.anyio
    async def test_remove_nonexistent_budget(self, seeded_session, budget_service, test_phone):
        """Test removing a budget that doesn't exist."""
        result = await budget_service.remove_budget(seeded_session, test_phone, "Alimentação")

        assert result["success"] is False
        assert "nao encontrado" in result["error"]

    @pytest.mark.anyio
    async def test_list_budgets_empty(self, seeded_session, budget_service, test_phone):
        """Test listing budgets when none exist."""
        result = await budget_service.list_budgets(seeded_session, test_phone)

        assert result["success"] is True
        assert result["budgets"] == []

    @pytest.mark.anyio
    async def test_list_budgets_with_spending(self, seeded_session, budget_service, test_phone):
        """Test listing budgets with spending data."""
        # Create budget
        await budget_service.create_budget(
            seeded_session, test_phone, "Alimentação", Decimal("500.00")
        )

        # Get category and payment method for expense
        cat_result = await seeded_session.execute(
            select(Category).where(Category.name == "Alimentação")
        )
        category = cat_result.scalar_one()

        pm_result = await seeded_session.execute(
            select(PaymentMethod).where(PaymentMethod.name == "Pix")
        )
        payment_method = pm_result.scalar_one()

        # Create expense
        expense = Expense(
            user_phone=test_phone,
            description="Almoco",
            amount=Decimal("100.00"),
            category_id=category.id,
            payment_method_id=payment_method.id,
            type="Negativo",
            date=date.today(),
            created_at=datetime.now(),
        )
        seeded_session.add(expense)
        await seeded_session.commit()

        # List budgets
        result = await budget_service.list_budgets(seeded_session, test_phone)

        assert result["success"] is True
        assert len(result["budgets"]) == 1

        budget = result["budgets"][0]
        assert budget["category"] == "Alimentação"
        assert budget["limit"] == 500.00
        assert budget["spent"] == 100.00
        assert budget["remaining"] == 400.00
        assert budget["percentage"] == 20.0

    @pytest.mark.anyio
    async def test_check_budget_status(self, seeded_session, budget_service, test_phone):
        """Test checking budget status for a category."""
        # Create budget
        await budget_service.create_budget(
            seeded_session, test_phone, "Alimentação", Decimal("500.00")
        )

        result = await budget_service.check_budget_status(seeded_session, test_phone, "Alimentação")

        assert result["success"] is True
        assert result["category"] == "Alimentação"
        assert result["limit"] == 500.00
        assert result["spent"] == 0.00
        assert result["remaining"] == 500.00
        assert result["percentage"] == 0.0

    @pytest.mark.anyio
    async def test_check_budget_status_no_budget(self, seeded_session, budget_service, test_phone):
        """Test checking budget status when no budget exists."""
        result = await budget_service.check_budget_status(seeded_session, test_phone, "Alimentação")

        assert result["success"] is False
        assert "nao definido" in result["error"]

    @pytest.mark.anyio
    async def test_check_and_send_alerts_50_percent(
        self, seeded_session, budget_service, test_phone
    ):
        """Test that 50% alert is triggered."""
        # Create budget
        await budget_service.create_budget(
            seeded_session, test_phone, "Alimentação", Decimal("100.00")
        )

        # Get category and payment method
        cat_result = await seeded_session.execute(
            select(Category).where(Category.name == "Alimentação")
        )
        category = cat_result.scalar_one()

        pm_result = await seeded_session.execute(
            select(PaymentMethod).where(PaymentMethod.name == "Pix")
        )
        payment_method = pm_result.scalar_one()

        # Create expense for 50%
        expense = Expense(
            user_phone=test_phone,
            description="Almoco",
            amount=Decimal("50.00"),
            category_id=category.id,
            payment_method_id=payment_method.id,
            type="Negativo",
            date=date.today(),
            created_at=datetime.now(),
        )
        seeded_session.add(expense)
        await seeded_session.commit()

        # Check alerts
        alerts = await budget_service.check_and_send_alerts(seeded_session, test_phone, category.id)

        assert len(alerts) == 1
        assert alerts[0]["threshold"] == 50
        assert alerts[0]["category"] == "Alimentação"
        assert alerts[0]["percentage"] == 50.0

    @pytest.mark.anyio
    async def test_check_and_send_alerts_100_percent(
        self, seeded_session, budget_service, test_phone
    ):
        """Test that 100% alert is triggered."""
        # Create budget
        await budget_service.create_budget(
            seeded_session, test_phone, "Alimentação", Decimal("100.00")
        )

        # Get category and payment method
        cat_result = await seeded_session.execute(
            select(Category).where(Category.name == "Alimentação")
        )
        category = cat_result.scalar_one()

        pm_result = await seeded_session.execute(
            select(PaymentMethod).where(PaymentMethod.name == "Pix")
        )
        payment_method = pm_result.scalar_one()

        # Create expense for 100%
        expense = Expense(
            user_phone=test_phone,
            description="Almoco",
            amount=Decimal("100.00"),
            category_id=category.id,
            payment_method_id=payment_method.id,
            type="Negativo",
            date=date.today(),
            created_at=datetime.now(),
        )
        seeded_session.add(expense)
        await seeded_session.commit()

        # Check alerts
        alerts = await budget_service.check_and_send_alerts(seeded_session, test_phone, category.id)

        # Should trigger 50%, 80%, and 100% alerts
        assert len(alerts) == 3
        thresholds = {a["threshold"] for a in alerts}
        assert thresholds == {50, 80, 100}

    @pytest.mark.anyio
    async def test_alerts_not_sent_twice(self, seeded_session, budget_service, test_phone):
        """Test that alerts are not sent twice for the same threshold."""
        # Create budget
        await budget_service.create_budget(
            seeded_session, test_phone, "Alimentação", Decimal("100.00")
        )

        # Get category and payment method
        cat_result = await seeded_session.execute(
            select(Category).where(Category.name == "Alimentação")
        )
        category = cat_result.scalar_one()

        pm_result = await seeded_session.execute(
            select(PaymentMethod).where(PaymentMethod.name == "Pix")
        )
        payment_method = pm_result.scalar_one()

        # Create expense for 50%
        expense = Expense(
            user_phone=test_phone,
            description="Almoco",
            amount=Decimal("50.00"),
            category_id=category.id,
            payment_method_id=payment_method.id,
            type="Negativo",
            date=date.today(),
            created_at=datetime.now(),
        )
        seeded_session.add(expense)
        await seeded_session.commit()

        # Check alerts first time
        alerts1 = await budget_service.check_and_send_alerts(
            seeded_session, test_phone, category.id
        )
        assert len(alerts1) == 1

        # Check alerts second time
        alerts2 = await budget_service.check_and_send_alerts(
            seeded_session, test_phone, category.id
        )
        # No new alerts should be sent
        assert len(alerts2) == 0


class TestGeminiServiceBudgetFormatting:
    """Tests for GeminiService budget alert formatting."""

    @pytest.fixture
    def gemini_service(self):
        """Create GeminiService instance."""
        from app.services.gemini import GeminiService

        return GeminiService()

    def test_format_budget_alert_50_percent(self, gemini_service):
        """Test formatting 50% budget alert."""
        alert = {
            "threshold": 50,
            "category": "Alimentação",
            "spent": 250.00,
            "limit": 500.00,
            "percentage": 50.0,
            "exceeded": False,
        }

        msg = gemini_service.format_budget_alert(alert)

        assert "Aviso de orcamento" in msg
        assert "50%" in msg
        assert "Alimentação" in msg
        assert "500.00" in msg
        assert "250.00" in msg

    def test_format_budget_alert_80_percent(self, gemini_service):
        """Test formatting 80% budget alert."""
        alert = {
            "threshold": 80,
            "category": "Alimentação",
            "spent": 400.00,
            "limit": 500.00,
            "percentage": 80.0,
            "exceeded": False,
        }

        msg = gemini_service.format_budget_alert(alert)

        assert "Cuidado" in msg
        assert "80%" in msg
        assert "Alimentação" in msg

    def test_format_budget_alert_exceeded(self, gemini_service):
        """Test formatting exceeded budget alert."""
        alert = {
            "threshold": 100,
            "category": "Alimentação",
            "spent": 550.00,
            "limit": 500.00,
            "percentage": 110.0,
            "exceeded": True,
        }

        msg = gemini_service.format_budget_alert(alert)

        assert "Limite atingido" in msg
        assert "excedeu" in msg
        assert "50.00" in msg  # The excess amount
