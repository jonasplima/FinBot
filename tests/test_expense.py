"""Tests for ExpenseService."""

from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.services.expense import MONTH_NAMES, ExpenseService, remove_accents
from tests.conftest import Expense, ExpenseUpdateAudit


class TestRemoveAccents:
    """Tests for the remove_accents utility function."""

    def test_removes_accents(self):
        assert remove_accents("alimentação") == "alimentacao"
        assert remove_accents("café") == "cafe"
        assert remove_accents("saúde") == "saude"

    def test_handles_empty_string(self):
        assert remove_accents("") == ""

    def test_handles_none(self):
        assert remove_accents(None) == ""

    def test_preserves_unaccented_text(self):
        assert remove_accents("hello world") == "hello world"


class TestExpenseServiceCreateExpense:
    """Tests for ExpenseService.create_expense method."""

    @pytest.fixture
    def service(self):
        return ExpenseService()

    async def test_create_simple_expense(
        self, service, seeded_session, test_phone, sample_expense_data
    ):
        """Test creating a simple expense."""
        result = await service.create_expense(seeded_session, test_phone, sample_expense_data)

        assert result["success"] is True
        assert "expense_id" in result

    async def test_create_expense_invalid_category(self, service, seeded_session, test_phone):
        """Test creating expense with invalid category."""
        data = {
            "description": "Test",
            "amount": 50.00,
            "category": "CategoriaInexistente",
            "payment_method": "Pix",
        }

        result = await service.create_expense(seeded_session, test_phone, data)

        assert result["success"] is False
        assert "nao encontrada" in result["error"]

    async def test_create_expense_invalid_payment_method(self, service, seeded_session, test_phone):
        """Test creating expense with invalid payment method."""
        data = {
            "description": "Test",
            "amount": 50.00,
            "category": "Alimentação",
            "payment_method": "MetodoInexistente",
        }

        result = await service.create_expense(seeded_session, test_phone, data)

        assert result["success"] is False
        assert "nao encontrado" in result["error"]

    async def test_create_expense_zero_amount(self, service, seeded_session, test_phone):
        """Test creating expense with zero amount."""
        data = {
            "description": "Test",
            "amount": 0,
            "category": "Alimentação",
            "payment_method": "Pix",
        }

        result = await service.create_expense(seeded_session, test_phone, data)

        assert result["success"] is False
        assert "maior que zero" in result["error"]

    async def test_create_expense_negative_amount(self, service, seeded_session, test_phone):
        """Test creating expense with negative amount."""
        data = {
            "description": "Test",
            "amount": -50.00,
            "category": "Alimentação",
            "payment_method": "Pix",
        }

        result = await service.create_expense(seeded_session, test_phone, data)

        assert result["success"] is False
        assert "maior que zero" in result["error"]

    async def test_create_installment_expense(
        self, service, seeded_session, test_phone, sample_installment_data
    ):
        """Test creating an installment expense."""
        result = await service.create_expense(seeded_session, test_phone, sample_installment_data)

        assert result["success"] is True
        assert result["installments_created"] == 3
        assert result["installment_amount"] == 100.00

    async def test_create_installment_expense_distributes_rounding_remainder(
        self, service, seeded_session, test_phone, sample_installment_data
    ):
        """Test installment rounding residue is added to the last installment."""
        data = sample_installment_data.copy()
        data["amount"] = 100.00

        result = await service.create_expense(seeded_session, test_phone, data)

        assert result["success"] is True

        query = await seeded_session.execute(
            select(Expense)
            .where(Expense.user_phone == test_phone)
            .order_by(Expense.installment_current)
        )
        expenses = query.scalars().all()

        amounts = [Decimal(str(exp.amount)) for exp in expenses]
        assert amounts == [Decimal("33.33"), Decimal("33.33"), Decimal("33.34")]
        assert sum(amounts) == Decimal("100.00")

    async def test_create_recurring_expense(
        self, service, seeded_session, test_phone, sample_recurring_data
    ):
        """Test creating a recurring expense."""
        result = await service.create_expense(seeded_session, test_phone, sample_recurring_data)

        assert result["success"] is True
        assert "expense_id" in result

    async def test_create_shared_expense(
        self, service, seeded_session, test_phone, sample_shared_data
    ):
        """Test creating a shared expense."""
        result = await service.create_expense(seeded_session, test_phone, sample_shared_data)

        assert result["success"] is True
        assert "expense_id" in result

    async def test_create_expense_uses_explicit_expense_date(
        self, service, seeded_session, test_phone, sample_expense_data
    ):
        """Test explicit retroactive expense date is persisted."""
        data = sample_expense_data.copy()
        data["expense_date"] = "2026-04-01"

        result = await service.create_expense(seeded_session, test_phone, data)

        assert result["success"] is True

        query = await seeded_session.execute(
            select(Expense).where(Expense.id == result["expense_id"])
        )
        expense = query.scalar_one()
        assert expense.date == date(2026, 4, 1)

    async def test_create_expense_rejects_invalid_expense_date(
        self, service, seeded_session, test_phone, sample_expense_data
    ):
        """Test invalid explicit expense date is rejected."""
        data = sample_expense_data.copy()
        data["expense_date"] = "2026-99-99"

        result = await service.create_expense(seeded_session, test_phone, data)

        assert result["success"] is False
        assert "data da despesa invalida" in result["error"].lower()

    async def test_category_matching_case_insensitive(self, service, seeded_session, test_phone):
        """Test that category matching is case insensitive."""
        data = {
            "description": "Test",
            "amount": 50.00,
            "category": "ALIMENTACAO",
            "payment_method": "pix",
        }

        result = await service.create_expense(seeded_session, test_phone, data)

        assert result["success"] is True

    async def test_create_installment_expense_rejects_single_installment(
        self, service, seeded_session, test_phone, sample_installment_data
    ):
        """Test installment flow rejects values lower than 2."""
        data = sample_installment_data.copy()
        data["installments"] = 1

        result = await service.create_expense(seeded_session, test_phone, data)

        assert result["success"] is False
        assert "pelo menos 2 parcelas" in result["error"].lower()

    async def test_create_shared_expense_rejects_invalid_percentage(
        self, service, seeded_session, test_phone, sample_shared_data
    ):
        """Test shared expense rejects invalid percentage values."""
        data = sample_shared_data.copy()
        data["shared_percentage"] = 120

        result = await service.create_expense(seeded_session, test_phone, data)

        assert result["success"] is False
        assert "entre 0 e 100" in result["error"].lower()

    async def test_category_matching_accent_insensitive(self, service, seeded_session, test_phone):
        """Test that category matching ignores accents."""
        data = {
            "description": "Test",
            "amount": 50.00,
            "category": "Alimentação",
            "payment_method": "Pix",
        }

        result = await service.create_expense(seeded_session, test_phone, data)

        assert result["success"] is True


class TestExpenseServiceUndoLastExpense:
    """Tests for ExpenseService.undo_last_expense method."""

    @pytest.fixture
    def service(self):
        return ExpenseService()

    async def test_undo_last_expense_success(
        self, service, seeded_session, test_phone, expense_in_db
    ):
        """Test successfully undoing the last expense."""
        result = await service.undo_last_expense(seeded_session, test_phone)

        assert result["success"] is True
        assert "expense" in result
        assert result["expense"]["description"] == "Teste expense"
        assert result["expense"]["amount"] == 50.00

    async def test_undo_no_expenses(self, service, seeded_session, test_phone):
        """Test undoing when there are no expenses."""
        result = await service.undo_last_expense(seeded_session, test_phone)

        assert result["success"] is False
        assert "nenhum gasto" in result["error"].lower()

    async def test_undo_expired_expense(self, service, seeded_session, test_phone, expense_in_db):
        """Test undoing an expense that's too old."""
        # Update expense created_at to be older than the time limit
        expense_in_db.created_at = datetime.now() - timedelta(minutes=10)
        await seeded_session.commit()

        result = await service.undo_last_expense(seeded_session, test_phone, time_limit_minutes=5)

        assert result["success"] is False
        assert "mais de 5 minutos" in result["error"]


class TestExpenseServiceUpdateExpense:
    """Tests for updating previously registered expenses."""

    @pytest.fixture
    def service(self):
        return ExpenseService()

    async def test_find_expenses_for_update_by_description(
        self, service, seeded_session, test_phone, expense_in_db
    ):
        """Test finding an expense candidate using its description."""
        matches = await service.find_expenses_for_update(
            seeded_session,
            test_phone,
            {"target_description": "teste"},
        )

        assert len(matches) == 1
        assert matches[0].id == expense_in_db.id

    async def test_update_expense_amount_and_category(
        self, service, seeded_session, test_phone, expense_in_db
    ):
        """Test updating an existing expense fields."""
        result = await service.update_expense(
            seeded_session,
            test_phone,
            expense_in_db.id,
            {
                "new_amount": 75.0,
                "new_category": "Lazer",
            },
        )

        assert result["success"] is True
        assert result["expense"]["amount"] == 75.0
        assert result["expense"]["category"] == "Lazer"

        audit_result = await seeded_session.execute(select(ExpenseUpdateAudit))
        audit = audit_result.scalar_one()
        assert audit.expense_id == expense_in_db.id
        assert audit.previous_snapshot["amount"] == 50.0
        assert audit.updated_snapshot["amount"] == 75.0
        assert audit.updated_snapshot["category"] == "Lazer"

    async def test_update_expense_not_found(self, service, seeded_session, test_phone):
        """Test update fails safely when expense does not exist."""
        result = await service.update_expense(
            seeded_session,
            test_phone,
            999999,
            {"new_amount": 75.0},
        )

        assert result["success"] is False
        assert "nao encontrado" in result["error"].lower()

    async def test_update_expense_rejects_noop_change(
        self, service, seeded_session, test_phone, expense_in_db
    ):
        """Test update fails when no effective change is made."""
        result = await service.update_expense(
            seeded_session,
            test_phone,
            expense_in_db.id,
            {"new_description": "Teste expense"},
        )

        assert result["success"] is False
        assert "nenhuma alteracao valida" in result["error"].lower()


class TestExpenseServiceMonthlySummary:
    """Tests for ExpenseService.get_monthly_summary method."""

    @pytest.fixture
    def service(self):
        return ExpenseService()

    async def test_get_monthly_summary_no_expenses(self, service, seeded_session, test_phone):
        """Test getting summary when there are no expenses."""
        result = await service.get_monthly_summary(seeded_session, test_phone)

        assert "nao tem gastos" in result.lower()

    async def test_get_monthly_summary_with_expenses(
        self, service, seeded_session, test_phone, expense_in_db
    ):
        """Test getting summary with expenses."""
        result = await service.get_monthly_summary(seeded_session, test_phone)

        today = date.today()
        assert MONTH_NAMES[today.month] in result
        assert "Gastos:" in result

    async def test_get_monthly_summary_specific_month(self, service, seeded_session, test_phone):
        """Test getting summary for a specific month."""
        result = await service.get_monthly_summary(seeded_session, test_phone, month=1, year=2024)

        assert "Janeiro" in result
        assert "2024" in result


class TestExpenseServiceListRecurring:
    """Tests for ExpenseService.list_recurring method."""

    @pytest.fixture
    def service(self):
        return ExpenseService()

    async def test_list_recurring_no_expenses(self, service, seeded_session, test_phone):
        """Test listing recurring when there are none."""
        result = await service.list_recurring(seeded_session, test_phone)

        assert "nao tem despesas recorrentes" in result.lower()

    async def test_list_recurring_with_expenses(
        self, service, seeded_session, test_phone, sample_recurring_data
    ):
        """Test listing recurring expenses."""
        # Create a recurring expense first
        await service.create_expense(seeded_session, test_phone, sample_recurring_data)

        result = await service.list_recurring(seeded_session, test_phone)

        assert "recorrentes" in result.lower()
        assert "Netflix" in result


class TestExpenseServiceCancelRecurring:
    """Tests for ExpenseService.cancel_recurring method."""

    @pytest.fixture
    def service(self):
        return ExpenseService()

    async def test_cancel_recurring_success(
        self, service, seeded_session, test_phone, sample_recurring_data
    ):
        """Test successfully cancelling a recurring expense."""
        # Create recurring first
        await service.create_expense(seeded_session, test_phone, sample_recurring_data)

        result = await service.cancel_recurring(seeded_session, test_phone, "Netflix")

        assert result["success"] is True

    async def test_cancel_recurring_not_found(self, service, seeded_session, test_phone):
        """Test cancelling a recurring expense that doesn't exist."""
        result = await service.cancel_recurring(seeded_session, test_phone, "Inexistente")

        assert result["success"] is False
        assert "nao encontrada" in result["error"].lower()


class TestExpenseServiceGetExpensesForExport:
    """Tests for ExpenseService.get_expenses_for_export method."""

    @pytest.fixture
    def service(self):
        return ExpenseService()

    async def test_get_expenses_for_export_empty(self, service, seeded_session, test_phone):
        """Test getting expenses for export when there are none."""
        today = date.today()
        result = await service.get_expenses_for_export(
            seeded_session, test_phone, today.month, today.year
        )

        assert result == []

    async def test_get_expenses_for_export_with_data(
        self, service, seeded_session, test_phone, expense_in_db
    ):
        """Test getting expenses for export with data."""
        today = date.today()
        result = await service.get_expenses_for_export(
            seeded_session, test_phone, today.month, today.year
        )

        assert len(result) == 1
        assert result[0]["Descricao"] == "Teste expense"
        assert result[0]["Valor"] == 50.00
        assert "Data" in result[0]
        assert "Categoria" in result[0]
        assert "Forma de Pagamento" in result[0]
