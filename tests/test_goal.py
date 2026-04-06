"""Tests for GoalService."""

from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from tests.conftest import Category, Expense, Goal, GoalUpdate, PaymentMethod


class TestGoalService:
    """Tests for GoalService class."""

    @pytest.fixture
    def goal_service(self):
        """Create GoalService instance."""
        from app.services.goal import GoalService

        return GoalService()

    @pytest.mark.anyio
    async def test_create_goal(self, seeded_session, goal_service, test_phone):
        """Test creating a savings goal."""
        deadline = date.today() + timedelta(days=90)
        result = await goal_service.create_goal(
            seeded_session,
            test_phone,
            "Viagem",
            Decimal("5000.00"),
            deadline,
        )

        assert result["success"] is True
        assert result["goal_id"] is not None
        assert result["description"] == "Viagem"
        assert result["target_amount"] == 5000.00

        # Verify goal was saved
        goal = await seeded_session.execute(select(Goal).where(Goal.user_phone == test_phone))
        goal = goal.scalar_one()
        assert goal.target_amount == Decimal("5000.00")
        assert goal.is_active is True
        assert goal.is_achieved is False

    @pytest.mark.anyio
    async def test_create_goal_duplicate_description(
        self, seeded_session, goal_service, test_phone
    ):
        """Test that duplicate goal descriptions are rejected."""
        deadline = date.today() + timedelta(days=90)

        # Create first goal
        await goal_service.create_goal(
            seeded_session,
            test_phone,
            "Viagem",
            Decimal("5000.00"),
            deadline,
        )

        # Try to create with same description
        result = await goal_service.create_goal(
            seeded_session,
            test_phone,
            "Viagem",
            Decimal("3000.00"),
            deadline,
        )

        assert result["success"] is False
        assert "Ja existe uma meta" in result["error"]

    @pytest.mark.anyio
    async def test_create_goal_past_deadline(self, seeded_session, goal_service, test_phone):
        """Test that past deadlines are rejected."""
        past_deadline = date.today() - timedelta(days=1)

        result = await goal_service.create_goal(
            seeded_session,
            test_phone,
            "Viagem",
            Decimal("5000.00"),
            past_deadline,
        )

        assert result["success"] is False
        assert "data futura" in result["error"]

    @pytest.mark.anyio
    async def test_create_goal_zero_amount(self, seeded_session, goal_service, test_phone):
        """Test that zero target amount is rejected."""
        deadline = date.today() + timedelta(days=90)

        result = await goal_service.create_goal(
            seeded_session,
            test_phone,
            "Viagem",
            Decimal("0.00"),
            deadline,
        )

        assert result["success"] is False
        assert "maior que zero" in result["error"]

    @pytest.mark.anyio
    async def test_list_goals_empty(self, seeded_session, goal_service, test_phone):
        """Test listing goals when none exist."""
        result = await goal_service.list_goals(seeded_session, test_phone)

        assert result["success"] is True
        assert result["goals"] == []
        assert "nao tem metas" in result["message"]

    @pytest.mark.anyio
    async def test_list_goals_with_goals(self, seeded_session, goal_service, test_phone):
        """Test listing goals with existing goals."""
        deadline1 = date.today() + timedelta(days=90)
        deadline2 = date.today() + timedelta(days=180)

        # Create goals
        await goal_service.create_goal(
            seeded_session, test_phone, "Viagem", Decimal("5000.00"), deadline1
        )
        await goal_service.create_goal(
            seeded_session, test_phone, "Carro", Decimal("30000.00"), deadline2
        )

        # List goals
        result = await goal_service.list_goals(seeded_session, test_phone)

        assert result["success"] is True
        assert len(result["goals"]) == 2
        # Should be ordered by deadline
        assert result["goals"][0]["description"] == "Viagem"
        assert result["goals"][1]["description"] == "Carro"

    @pytest.mark.anyio
    async def test_check_goal_progress(self, seeded_session, goal_service, test_phone):
        """Test checking specific goal progress."""
        deadline = date.today() + timedelta(days=90)

        # Create goal
        await goal_service.create_goal(
            seeded_session, test_phone, "Viagem", Decimal("5000.00"), deadline
        )

        # Check progress
        result = await goal_service.check_goal_progress(seeded_session, test_phone, "Viagem")

        assert result["success"] is True
        progress = result["progress"]
        assert progress["description"] == "Viagem"
        assert progress["target_amount"] == 5000.00
        assert progress["percentage"] == 0.0

    @pytest.mark.anyio
    async def test_check_goal_progress_not_found(self, seeded_session, goal_service, test_phone):
        """Test checking progress for non-existent goal."""
        result = await goal_service.check_goal_progress(seeded_session, test_phone, "NonExistent")

        assert result["success"] is False
        assert "nao encontrada" in result["error"]

    @pytest.mark.anyio
    async def test_check_goal_progress_no_goals(self, seeded_session, goal_service, test_phone):
        """Test checking progress when no goals exist."""
        result = await goal_service.check_goal_progress(seeded_session, test_phone)

        assert result["success"] is False
        assert "nao tem metas" in result["error"]

    @pytest.mark.anyio
    async def test_calculate_progress_with_income_and_expenses(
        self, seeded_session, goal_service, test_phone
    ):
        """Test progress calculation based on net savings."""
        deadline = date.today() + timedelta(days=90)

        # Create goal
        create_result = await goal_service.create_goal(
            seeded_session, test_phone, "Viagem", Decimal("1000.00"), deadline
        )

        # Get categories and payment method
        cat_income = await seeded_session.execute(
            select(Category).where(Category.name == "Salario")
        )
        cat_income = cat_income.scalar_one()

        cat_expense = await seeded_session.execute(
            select(Category).where(Category.name == "Alimentação")
        )
        cat_expense = cat_expense.scalar_one()

        pm_result = await seeded_session.execute(
            select(PaymentMethod).where(PaymentMethod.name == "Pix")
        )
        payment_method = pm_result.scalar_one()

        # Add income of 1500
        income = Expense(
            user_phone=test_phone,
            description="Salario",
            amount=Decimal("1500.00"),
            category_id=cat_income.id,
            payment_method_id=payment_method.id,
            type="Positivo",
            date=date.today(),
            created_at=datetime.now(),
        )
        seeded_session.add(income)

        # Add expense of 1000
        expense = Expense(
            user_phone=test_phone,
            description="Compras",
            amount=Decimal("1000.00"),
            category_id=cat_expense.id,
            payment_method_id=payment_method.id,
            type="Negativo",
            date=date.today(),
            created_at=datetime.now(),
        )
        seeded_session.add(expense)
        await seeded_session.commit()

        # Check progress (net savings = 1500 - 1000 = 500, which is 50% of 1000)
        result = await goal_service.check_goal_progress(seeded_session, test_phone, "Viagem")

        assert result["success"] is True
        progress = result["progress"]
        assert progress["current_progress"] == 500.00
        assert progress["percentage"] == 50.0
        assert progress["remaining_amount"] == 500.00

    @pytest.mark.anyio
    async def test_add_to_goal(self, seeded_session, goal_service, test_phone):
        """Test manual deposit to goal."""
        deadline = date.today() + timedelta(days=90)

        # Create goal
        await goal_service.create_goal(
            seeded_session, test_phone, "Viagem", Decimal("1000.00"), deadline
        )

        # Add deposit
        result = await goal_service.add_to_goal(
            seeded_session, test_phone, "Viagem", Decimal("200.00")
        )

        assert result["success"] is True
        progress = result["progress"]
        assert progress["current_progress"] == 200.00
        assert progress["percentage"] == 20.0

        # Verify GoalUpdate was created
        updates = await seeded_session.execute(select(GoalUpdate))
        update = updates.scalar_one()
        assert update.previous_amount == Decimal("0")
        assert update.new_amount == Decimal("200.00")
        assert update.update_type == "deposit"

    @pytest.mark.anyio
    async def test_add_to_goal_not_found(self, seeded_session, goal_service, test_phone):
        """Test adding to non-existent goal."""
        result = await goal_service.add_to_goal(
            seeded_session, test_phone, "NonExistent", Decimal("100.00")
        )

        assert result["success"] is False
        assert "nao encontrada" in result["error"]

    @pytest.mark.anyio
    async def test_add_to_goal_zero_amount(self, seeded_session, goal_service, test_phone):
        """Test that zero deposit is rejected."""
        deadline = date.today() + timedelta(days=90)

        await goal_service.create_goal(
            seeded_session, test_phone, "Viagem", Decimal("1000.00"), deadline
        )

        result = await goal_service.add_to_goal(
            seeded_session, test_phone, "Viagem", Decimal("0.00")
        )

        assert result["success"] is False
        assert "maior que zero" in result["error"]

    @pytest.mark.anyio
    async def test_remove_goal(self, seeded_session, goal_service, test_phone):
        """Test removing/deactivating a goal."""
        deadline = date.today() + timedelta(days=90)

        # Create goal
        await goal_service.create_goal(
            seeded_session, test_phone, "Viagem", Decimal("5000.00"), deadline
        )

        # Remove it
        result = await goal_service.remove_goal(seeded_session, test_phone, "Viagem")

        assert result["success"] is True

        # Verify goal is deactivated
        goal = await seeded_session.execute(select(Goal).where(Goal.user_phone == test_phone))
        goal = goal.scalar_one()
        assert goal.is_active is False

    @pytest.mark.anyio
    async def test_remove_goal_not_found(self, seeded_session, goal_service, test_phone):
        """Test removing non-existent goal."""
        result = await goal_service.remove_goal(seeded_session, test_phone, "NonExistent")

        assert result["success"] is False
        assert "nao encontrada" in result["error"]

    @pytest.mark.anyio
    async def test_goal_achieved_automatically(self, seeded_session, goal_service, test_phone):
        """Test that goal is marked as achieved when 100% reached via deposit."""
        deadline = date.today() + timedelta(days=90)

        # Create goal
        await goal_service.create_goal(
            seeded_session, test_phone, "Viagem", Decimal("1000.00"), deadline
        )

        # Add deposit to reach 100%
        result = await goal_service.add_to_goal(
            seeded_session, test_phone, "Viagem", Decimal("1000.00")
        )

        assert result["success"] is True
        assert result["progress"]["percentage"] >= 100
        assert result["progress"]["is_achieved"] is True

        # Verify goal is marked as achieved in database
        goal = await seeded_session.execute(select(Goal).where(Goal.user_phone == test_phone))
        goal = goal.scalar_one()
        assert goal.is_achieved is True

    @pytest.mark.anyio
    async def test_get_weekly_motivation(self, seeded_session, goal_service, test_phone):
        """Test getting weekly motivation data."""
        deadline1 = date.today() + timedelta(days=90)
        deadline2 = date.today() + timedelta(days=180)

        # Create goals
        await goal_service.create_goal(
            seeded_session, test_phone, "Viagem", Decimal("5000.00"), deadline1
        )
        await goal_service.create_goal(
            seeded_session, test_phone, "Carro", Decimal("30000.00"), deadline2
        )

        # Get motivations
        motivations = await goal_service.get_weekly_motivation(seeded_session, test_phone)

        assert len(motivations) == 2
        assert motivations[0]["description"] == "Viagem"
        assert motivations[1]["description"] == "Carro"

    @pytest.mark.anyio
    async def test_get_users_with_active_goals(self, seeded_session, goal_service, test_phone):
        """Test getting users with active goals."""
        deadline = date.today() + timedelta(days=90)

        # Create goal
        await goal_service.create_goal(
            seeded_session, test_phone, "Viagem", Decimal("5000.00"), deadline
        )

        # Get users
        users = await goal_service.get_users_with_active_goals(seeded_session)

        assert len(users) == 1
        assert test_phone in users

    @pytest.mark.anyio
    async def test_goal_description_case_insensitive(
        self, seeded_session, goal_service, test_phone
    ):
        """Test that goal lookup is case-insensitive."""
        deadline = date.today() + timedelta(days=90)

        # Create goal with lowercase
        await goal_service.create_goal(
            seeded_session, test_phone, "viagem", Decimal("5000.00"), deadline
        )

        # Check with uppercase
        result = await goal_service.check_goal_progress(seeded_session, test_phone, "VIAGEM")

        assert result["success"] is True
        assert result["progress"]["description"] == "viagem"

    @pytest.mark.anyio
    async def test_progress_with_negative_savings(self, seeded_session, goal_service, test_phone):
        """Test that negative savings show as 0% progress."""
        deadline = date.today() + timedelta(days=90)

        # Create goal
        await goal_service.create_goal(
            seeded_session, test_phone, "Viagem", Decimal("1000.00"), deadline
        )

        # Get categories and payment method
        cat_expense = await seeded_session.execute(
            select(Category).where(Category.name == "Alimentação")
        )
        cat_expense = cat_expense.scalar_one()

        pm_result = await seeded_session.execute(
            select(PaymentMethod).where(PaymentMethod.name == "Pix")
        )
        payment_method = pm_result.scalar_one()

        # Add expense only (no income = negative savings)
        expense = Expense(
            user_phone=test_phone,
            description="Compras",
            amount=Decimal("500.00"),
            category_id=cat_expense.id,
            payment_method_id=payment_method.id,
            type="Negativo",
            date=date.today(),
            created_at=datetime.now(),
        )
        seeded_session.add(expense)
        await seeded_session.commit()

        # Check progress
        result = await goal_service.check_goal_progress(seeded_session, test_phone, "Viagem")

        assert result["success"] is True
        progress = result["progress"]
        # Negative savings should show as 0
        assert progress["current_progress"] == 0.0
        assert progress["percentage"] == 0.0


class TestGeminiServiceGoalFormatting:
    """Tests for GeminiService goal motivation formatting."""

    @pytest.fixture
    def gemini_service(self):
        """Create GeminiService instance."""
        from app.services.gemini import GeminiService

        return GeminiService()

    def test_format_goal_motivation_achieved(self, gemini_service):
        """Test formatting achieved goal message."""
        progress = {
            "goal_id": 1,
            "description": "Viagem",
            "target_amount": 5000.00,
            "current_progress": 5500.00,
            "percentage": 110.0,
            "remaining_amount": 0,
            "remaining_days": 30,
            "daily_rate_needed": 0,
            "is_on_track": True,
            "deadline": "31/12/2026",
            "is_achieved": True,
        }

        msg = gemini_service.format_goal_motivation(progress)

        assert "Parabens" in msg
        assert "atingiu" in msg
        assert "Viagem" in msg

    def test_format_goal_motivation_75_percent(self, gemini_service):
        """Test formatting 75%+ progress message."""
        progress = {
            "goal_id": 1,
            "description": "Viagem",
            "target_amount": 1000.00,
            "current_progress": 800.00,
            "percentage": 80.0,
            "remaining_amount": 200.00,
            "remaining_days": 30,
            "daily_rate_needed": 6.67,
            "is_on_track": True,
            "deadline": "31/12/2026",
            "is_achieved": False,
        }

        msg = gemini_service.format_goal_motivation(progress)

        assert "Quase la" in msg
        assert "80%" in msg

    def test_format_goal_motivation_50_percent(self, gemini_service):
        """Test formatting 50%+ progress message."""
        progress = {
            "goal_id": 1,
            "description": "Viagem",
            "target_amount": 1000.00,
            "current_progress": 500.00,
            "percentage": 50.0,
            "remaining_amount": 500.00,
            "remaining_days": 30,
            "daily_rate_needed": 16.67,
            "is_on_track": True,
            "deadline": "31/12/2026",
            "is_achieved": False,
        }

        msg = gemini_service.format_goal_motivation(progress)

        assert "Metade do caminho" in msg
        assert "50%" in msg

    def test_format_goal_motivation_low_progress(self, gemini_service):
        """Test formatting low progress message."""
        progress = {
            "goal_id": 1,
            "description": "Viagem",
            "target_amount": 1000.00,
            "current_progress": 100.00,
            "percentage": 10.0,
            "remaining_amount": 900.00,
            "remaining_days": 30,
            "daily_rate_needed": 30.0,
            "is_on_track": False,
            "deadline": "31/12/2026",
            "is_achieved": False,
        }

        msg = gemini_service.format_goal_motivation(progress)

        assert "10%" in msg
        assert "30.00" in msg  # daily rate
