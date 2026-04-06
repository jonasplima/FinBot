"""Tests for BackupService."""

import base64
import json
from datetime import date, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.services.backup import BACKUP_SCHEMA_VERSION, BackupService
from app.services.backup import settings as backup_settings
from tests.conftest import Budget, BudgetAlert, Category, Expense, Goal, GoalUpdate, PaymentMethod


class TestBackupService:
    """Tests for backup export and restore."""

    @pytest.fixture
    def service(self):
        return BackupService()

    async def test_export_user_backup_with_data(self, service, seeded_session, test_phone):
        """Test exporting backup with expenses, budgets, and goals."""
        payment_method = await seeded_session.execute(
            select(PaymentMethod).where(PaymentMethod.name == "Pix")
        )
        pix = payment_method.scalar_one()

        category = await seeded_session.execute(
            select(Category).where(Category.name == "Alimentação")
        )
        alimentacao = category.scalar_one()

        expense = Expense(
            user_phone=test_phone,
            description="Almoco",
            amount=Decimal("25.50"),
            category_id=alimentacao.id,
            payment_method_id=pix.id,
            type="Negativo",
            date=date(2026, 4, 1),
            created_at=datetime(2026, 4, 1, 12, 0, 0),
        )
        budget = Budget(
            user_phone=test_phone,
            category_id=alimentacao.id,
            monthly_limit=Decimal("500.00"),
            is_active=True,
        )
        budget.alerts.append(BudgetAlert(threshold_percent=50, month=4, year=2026))
        goal = Goal(
            user_phone=test_phone,
            description="Viagem",
            target_amount=Decimal("1000.00"),
            current_amount=Decimal("200.00"),
            deadline=date(2026, 12, 31),
            start_date=date(2026, 1, 1),
            is_active=True,
            is_achieved=False,
        )
        goal.updates.append(
            GoalUpdate(
                previous_amount=Decimal("0"),
                new_amount=Decimal("200.00"),
                update_type="deposit",
            )
        )

        seeded_session.add_all([expense, budget, goal])
        await seeded_session.commit()

        result = await service.export_user_backup(seeded_session, test_phone)

        assert result["success"] is True
        assert result["filename"].endswith(".json")
        payload = result["backup_data"]
        assert payload["metadata"]["schema_version"] == BACKUP_SCHEMA_VERSION
        assert len(payload["expenses"]) == 1
        assert len(payload["budgets"]) == 1
        assert len(payload["goals"]) == 1

        decoded = json.loads(base64.b64decode(result["file_base64"]).decode("utf-8"))
        assert decoded["expenses"][0]["description"] == "Almoco"

    def test_parse_backup_document_invalid_json(self, service):
        """Test parsing invalid backup JSON."""
        result = service.parse_backup_document(b"{invalid")

        assert result["success"] is False
        assert "json valido" in result["error"].lower()

    def test_parse_backup_document_rejects_large_payload(self, service):
        """Test parsing rejects oversized backup payloads before JSON decode."""
        with patch.object(backup_settings, "max_backup_size_bytes", 1024):
            result = service.parse_backup_document(b"x" * 1025)

        assert result["success"] is False
        assert "excede o limite" in result["error"].lower()

    def test_validate_backup_document_rejects_unknown_fields(self, service):
        """Test validation rejects unexpected root-level fields."""
        result = service.validate_backup_data(
            {
                "metadata": {
                    "schema_version": BACKUP_SCHEMA_VERSION,
                    "exported_at": "2026-04-05T10:00:00",
                    "source_phone": "5511888888888",
                },
                "expenses": [],
                "budgets": [],
                "goals": [],
                "evil": True,
            }
        )

        assert result["success"] is False
        assert "nao suportados" in result["error"].lower()

    def test_validate_backup_document_rejects_invalid_goal_update_type(self, service):
        """Test validation rejects invalid goal update enums."""
        result = service.validate_backup_data(
            {
                "metadata": {
                    "schema_version": BACKUP_SCHEMA_VERSION,
                    "exported_at": "2026-04-05T10:00:00",
                    "source_phone": "5511888888888",
                },
                "expenses": [],
                "budgets": [],
                "goals": [
                    {
                        "description": "Reserva",
                        "target_amount": 1000.0,
                        "current_amount": 100.0,
                        "deadline": "2026-12-31",
                        "start_date": "2026-01-01",
                        "is_active": True,
                        "is_achieved": False,
                        "created_at": "2026-01-01T10:00:00",
                        "updated_at": None,
                        "updates": [
                            {
                                "previous_amount": 0.0,
                                "new_amount": 100.0,
                                "update_type": "hack",
                                "created_at": "2026-02-01T10:00:00",
                            }
                        ],
                    }
                ],
            }
        )

        assert result["success"] is False
        assert "tipo invalido" in result["error"].lower()

    def test_validate_backup_document_rejects_inconsistent_installments(self, service):
        """Test validation rejects inconsistent installment fields."""
        result = service.validate_backup_data(
            {
                "metadata": {
                    "schema_version": BACKUP_SCHEMA_VERSION,
                    "exported_at": "2026-04-05T10:00:00",
                    "source_phone": "5511888888888",
                },
                "expenses": [
                    {
                        "description": "Notebook",
                        "amount": 1000.0,
                        "category": "Mercado",
                        "payment_method": "Pix",
                        "type": "Negativo",
                        "installment_current": 4,
                        "installment_total": 3,
                        "is_shared": False,
                        "shared_percentage": None,
                        "original_currency": None,
                        "original_amount": None,
                        "exchange_rate": None,
                        "is_recurring": False,
                        "recurring_day": None,
                        "recurring_active": None,
                        "date": "2026-04-02",
                        "created_at": "2026-04-02T09:00:00",
                    }
                ],
                "budgets": [],
                "goals": [],
            }
        )

        assert result["success"] is False
        assert "parcelamento inconsistente" in result["error"].lower()

    def test_validate_backup_document_rejects_invalid_shared_percentage(self, service):
        """Test validation rejects invalid shared percentages."""
        result = service.validate_backup_data(
            {
                "metadata": {
                    "schema_version": BACKUP_SCHEMA_VERSION,
                    "exported_at": "2026-04-05T10:00:00",
                    "source_phone": "5511888888888",
                },
                "expenses": [
                    {
                        "description": "Mercado",
                        "amount": 100.0,
                        "category": "Mercado",
                        "payment_method": "Pix",
                        "type": "Negativo",
                        "installment_current": None,
                        "installment_total": None,
                        "is_shared": True,
                        "shared_percentage": 150,
                        "original_currency": None,
                        "original_amount": None,
                        "exchange_rate": None,
                        "is_recurring": False,
                        "recurring_day": None,
                        "recurring_active": None,
                        "date": "2026-04-02",
                        "created_at": "2026-04-02T09:00:00",
                    }
                ],
                "budgets": [],
                "goals": [],
            }
        )

        assert result["success"] is False
        assert "percentual compartilhado invalido" in result["error"].lower()

    def test_validate_backup_document_rejects_incomplete_currency_conversion(self, service):
        """Test validation rejects incomplete currency conversion payloads."""
        result = service.validate_backup_data(
            {
                "metadata": {
                    "schema_version": BACKUP_SCHEMA_VERSION,
                    "exported_at": "2026-04-05T10:00:00",
                    "source_phone": "5511888888888",
                },
                "expenses": [
                    {
                        "description": "Uber",
                        "amount": 50.0,
                        "category": "Transporte",
                        "payment_method": "Pix",
                        "type": "Negativo",
                        "installment_current": None,
                        "installment_total": None,
                        "is_shared": False,
                        "shared_percentage": None,
                        "original_currency": "USD",
                        "original_amount": None,
                        "exchange_rate": 5.0,
                        "is_recurring": False,
                        "recurring_day": None,
                        "recurring_active": None,
                        "date": "2026-04-02",
                        "created_at": "2026-04-02T09:00:00",
                    }
                ],
                "budgets": [],
                "goals": [],
            }
        )

        assert result["success"] is False
        assert "conversao de moeda incompleta" in result["error"].lower()

    async def test_temporary_backup_storage_roundtrip(self, service):
        """Test temporary backup storage roundtrip outside pending confirmations."""
        backup_data = {
            "metadata": {
                "schema_version": BACKUP_SCHEMA_VERSION,
                "exported_at": "2026-04-05T10:00:00",
                "source_phone": "5511888888888",
            },
            "expenses": [],
            "budgets": [],
            "goals": [],
        }

        stored = await service.store_temporary_backup(backup_data)

        assert stored["success"] is True
        loaded = await service.load_temporary_backup(stored["backup_ref"])
        assert loaded["success"] is True
        assert loaded["backup_data"] == backup_data

        await service.delete_temporary_backup(stored["backup_ref"])
        missing = await service.load_temporary_backup(stored["backup_ref"])
        assert missing["success"] is False
        assert "expirou" in missing["error"].lower() or "disponivel" in missing["error"].lower()

    async def test_temporary_backup_storage_fails_without_redis_in_multi_instance_mode(
        self, service
    ):
        """Test temporary backup storage fails closed in multi-instance mode without Redis."""
        backup_data = {
            "metadata": {
                "schema_version": BACKUP_SCHEMA_VERSION,
                "exported_at": "2026-04-05T10:00:00",
                "source_phone": "5511888888888",
            },
            "expenses": [],
            "budgets": [],
            "goals": [],
        }

        with patch("app.services.backup.settings.deployment_mode", "multi_instance"):
            service._get_redis = AsyncMock(return_value=None)
            stored = await service.store_temporary_backup(backup_data)

        assert stored["success"] is False
        assert "indisponivel" in stored["error"].lower()

    async def test_restore_user_backup_success(self, service, seeded_session, test_phone):
        """Test restoring backup data successfully."""
        backup_data = {
            "metadata": {
                "schema_version": BACKUP_SCHEMA_VERSION,
                "exported_at": "2026-04-05T10:00:00",
                "source_phone": "5511888888888",
            },
            "expenses": [
                {
                    "description": "Mercado",
                    "amount": 89.90,
                    "category": "Mercado",
                    "payment_method": "Pix",
                    "type": "Negativo",
                    "installment_current": None,
                    "installment_total": None,
                    "is_shared": False,
                    "shared_percentage": None,
                    "original_currency": None,
                    "original_amount": None,
                    "exchange_rate": None,
                    "is_recurring": False,
                    "recurring_day": None,
                    "recurring_active": None,
                    "date": "2026-04-02",
                    "created_at": "2026-04-02T09:00:00",
                }
            ],
            "budgets": [
                {
                    "category": "Mercado",
                    "monthly_limit": 600.0,
                    "is_active": True,
                    "created_at": "2026-04-01T10:00:00",
                    "updated_at": None,
                    "alerts": [
                        {
                            "threshold_percent": 50,
                            "month": 4,
                            "year": 2026,
                            "sent_at": "2026-04-15T10:00:00",
                        }
                    ],
                }
            ],
            "goals": [
                {
                    "description": "Reserva",
                    "target_amount": 2000.0,
                    "current_amount": 300.0,
                    "deadline": "2026-12-31",
                    "start_date": "2026-01-01",
                    "is_active": True,
                    "is_achieved": False,
                    "created_at": "2026-01-01T10:00:00",
                    "updated_at": None,
                    "updates": [
                        {
                            "previous_amount": 0.0,
                            "new_amount": 300.0,
                            "update_type": "deposit",
                            "created_at": "2026-02-01T10:00:00",
                        }
                    ],
                }
            ],
        }

        result = await service.restore_user_backup(seeded_session, test_phone, backup_data)

        assert result["success"] is True
        assert result["restored"]["expenses"] == 1
        assert result["restored"]["budgets"] == 1
        assert result["restored"]["budget_alerts"] == 1
        assert result["restored"]["goals"] == 1
        assert result["restored"]["goal_updates"] == 1

    async def test_restore_user_backup_rolls_back_on_invalid_reference(
        self, service, seeded_session, test_phone
    ):
        """Test restore rollback when category reference is invalid."""
        backup_data = {
            "metadata": {
                "schema_version": BACKUP_SCHEMA_VERSION,
                "exported_at": "2026-04-05T10:00:00",
                "source_phone": "5511888888888",
            },
            "expenses": [],
            "budgets": [],
            "goals": [
                {
                    "description": "Meta invalida",
                    "target_amount": 500.0,
                    "current_amount": 0.0,
                    "deadline": "2026-12-31",
                    "start_date": "2026-01-01",
                    "is_active": True,
                    "is_achieved": False,
                    "created_at": "2026-01-01T10:00:00",
                    "updated_at": None,
                    "updates": [],
                }
            ],
        }
        backup_data["expenses"].append(
            {
                "description": "Despesa invalida",
                "amount": 30.0,
                "category": "Categoria Fantasma",
                "payment_method": "Pix",
                "type": "Negativo",
                "installment_current": None,
                "installment_total": None,
                "is_shared": False,
                "shared_percentage": None,
                "original_currency": None,
                "original_amount": None,
                "exchange_rate": None,
                "is_recurring": False,
                "recurring_day": None,
                "recurring_active": None,
                "date": "2026-04-02",
                "created_at": "2026-04-02T09:00:00",
            }
        )

        result = await service.restore_user_backup(seeded_session, test_phone, backup_data)

        assert result["success"] is False

        expenses = await seeded_session.execute(
            select(Expense).where(Expense.user_phone == test_phone)
        )
        goals = await seeded_session.execute(select(Goal).where(Goal.user_phone == test_phone))
        assert expenses.scalars().all() == []
        assert goals.scalars().all() == []
