"""Backup and restore service for user data."""

import base64
import json
import logging
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database.models import (
    Budget,
    BudgetAlert,
    Category,
    Expense,
    Goal,
    GoalUpdate,
    PaymentMethod,
)
from app.utils.validators import normalize_phone

logger = logging.getLogger(__name__)

BACKUP_SCHEMA_VERSION = 1


class BackupService:
    """Service for exporting and restoring user backups."""

    async def export_user_backup(
        self,
        session: AsyncSession,
        phone: str,
    ) -> dict:
        """Export a user's data to a JSON backup payload."""
        normalized_phone = normalize_phone(phone)

        expenses = await self._get_expenses(session, normalized_phone)
        budgets = await self._get_budgets(session, normalized_phone)
        goals = await self._get_goals(session, normalized_phone)

        payload = {
            "metadata": {
                "schema_version": BACKUP_SCHEMA_VERSION,
                "exported_at": datetime.now().isoformat(),
                "source_phone": normalized_phone,
            },
            "expenses": [self._serialize_expense(expense) for expense in expenses],
            "budgets": [self._serialize_budget(budget) for budget in budgets],
            "goals": [self._serialize_goal(goal) for goal in goals],
        }

        json_bytes = json.dumps(payload, ensure_ascii=True, indent=2).encode("utf-8")
        file_base64 = base64.b64encode(json_bytes).decode("utf-8")
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        return {
            "success": True,
            "backup_data": payload,
            "file_base64": file_base64,
            "filename": f"finbot_backup_{timestamp}.json",
            "mimetype": "application/json",
        }

    def parse_backup_document(self, document_bytes: bytes) -> dict:
        """Parse a JSON backup document sent by the user."""
        try:
            text = document_bytes.decode("utf-8-sig")
        except UnicodeDecodeError:
            return {"success": False, "error": "O arquivo nao esta em UTF-8 valido."}

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return {"success": False, "error": "O arquivo nao contem um JSON valido."}

        validation = self.validate_backup_data(data)
        if not validation["success"]:
            return validation

        return {"success": True, "backup_data": data}

    def validate_backup_data(self, backup_data: dict[str, Any]) -> dict:
        """Validate backup structure before restore."""
        metadata = backup_data.get("metadata")
        if not isinstance(metadata, dict):
            return {"success": False, "error": "Backup sem metadata valida."}

        schema_version = metadata.get("schema_version")
        if schema_version != BACKUP_SCHEMA_VERSION:
            return {
                "success": False,
                "error": f"Versao de backup nao suportada: {schema_version}.",
            }

        for key in ("expenses", "budgets", "goals"):
            if key not in backup_data or not isinstance(backup_data[key], list):
                return {"success": False, "error": f"Campo '{key}' ausente ou invalido."}

        return {"success": True}

    def summarize_backup(self, backup_data: dict[str, Any]) -> dict:
        """Build a short summary of a backup payload."""
        return {
            "source_phone": backup_data.get("metadata", {}).get("source_phone", "desconhecido"),
            "expenses": len(backup_data.get("expenses", [])),
            "budgets": len(backup_data.get("budgets", [])),
            "goals": len(backup_data.get("goals", [])),
            "goal_updates": sum(
                len(goal.get("updates", [])) for goal in backup_data.get("goals", [])
            ),
            "budget_alerts": sum(
                len(budget.get("alerts", [])) for budget in backup_data.get("budgets", [])
            ),
        }

    async def restore_user_backup(
        self,
        session: AsyncSession,
        target_phone: str,
        backup_data: dict[str, Any],
    ) -> dict:
        """Restore backup data for a target phone using append semantics."""
        validation = self.validate_backup_data(backup_data)
        if not validation["success"]:
            return validation

        normalized_phone = normalize_phone(target_phone)

        try:
            restored_counts = {
                "expenses": 0,
                "budgets": 0,
                "budget_alerts": 0,
                "goals": 0,
                "goal_updates": 0,
            }

            for expense_data in backup_data["expenses"]:
                restored = await self._restore_expense(session, normalized_phone, expense_data)
                if restored:
                    restored_counts["expenses"] += 1

            for budget_data in backup_data["budgets"]:
                restored_budget, alerts_count = await self._restore_budget(
                    session, normalized_phone, budget_data
                )
                if restored_budget:
                    restored_counts["budgets"] += 1
                restored_counts["budget_alerts"] += alerts_count

            for goal_data in backup_data["goals"]:
                restored_goal, updates_count = await self._restore_goal(
                    session, normalized_phone, goal_data
                )
                if restored_goal:
                    restored_counts["goals"] += 1
                restored_counts["goal_updates"] += updates_count

            await session.commit()
            return {"success": True, "restored": restored_counts}

        except Exception as e:
            logger.error(f"Error restoring backup: {e}")
            await session.rollback()
            return {"success": False, "error": str(e)}

    async def _get_expenses(self, session: AsyncSession, phone: str) -> list[Expense]:
        result = await session.execute(
            select(Expense)
            .options(selectinload(Expense.category), selectinload(Expense.payment_method))
            .where(Expense.user_phone == phone)
            .order_by(Expense.date, Expense.id)
        )
        return list(result.scalars().all())

    async def _get_budgets(self, session: AsyncSession, phone: str) -> list[Budget]:
        result = await session.execute(
            select(Budget)
            .options(selectinload(Budget.category), selectinload(Budget.alerts))
            .where(Budget.user_phone == phone)
            .order_by(Budget.id)
        )
        return list(result.scalars().all())

    async def _get_goals(self, session: AsyncSession, phone: str) -> list[Goal]:
        result = await session.execute(
            select(Goal)
            .options(selectinload(Goal.updates))
            .where(Goal.user_phone == phone)
            .order_by(Goal.id)
        )
        return list(result.scalars().all())

    def _serialize_expense(self, expense: Expense) -> dict[str, Any]:
        return {
            "description": expense.description,
            "amount": self._decimal_to_float(expense.amount),
            "category": expense.category.name if expense.category else None,
            "payment_method": expense.payment_method.name if expense.payment_method else None,
            "type": expense.type,
            "installment_current": expense.installment_current,
            "installment_total": expense.installment_total,
            "is_shared": expense.is_shared,
            "shared_percentage": self._decimal_to_float(expense.shared_percentage),
            "original_currency": expense.original_currency,
            "original_amount": self._decimal_to_float(expense.original_amount),
            "exchange_rate": self._decimal_to_float(expense.exchange_rate),
            "is_recurring": expense.is_recurring,
            "recurring_day": expense.recurring_day,
            "recurring_active": expense.recurring_active,
            "date": expense.date.isoformat(),
            "created_at": expense.created_at.isoformat() if expense.created_at else None,
        }

    def _serialize_budget(self, budget: Budget) -> dict[str, Any]:
        return {
            "category": budget.category.name if budget.category else None,
            "monthly_limit": self._decimal_to_float(budget.monthly_limit),
            "is_active": budget.is_active,
            "created_at": budget.created_at.isoformat() if budget.created_at else None,
            "updated_at": budget.updated_at.isoformat() if budget.updated_at else None,
            "alerts": [
                {
                    "threshold_percent": alert.threshold_percent,
                    "month": alert.month,
                    "year": alert.year,
                    "sent_at": alert.sent_at.isoformat() if alert.sent_at else None,
                }
                for alert in budget.alerts
            ],
        }

    def _serialize_goal(self, goal: Goal) -> dict[str, Any]:
        return {
            "description": goal.description,
            "target_amount": self._decimal_to_float(goal.target_amount),
            "current_amount": self._decimal_to_float(goal.current_amount),
            "deadline": goal.deadline.isoformat(),
            "start_date": goal.start_date.isoformat(),
            "is_active": goal.is_active,
            "is_achieved": goal.is_achieved,
            "created_at": goal.created_at.isoformat() if goal.created_at else None,
            "updated_at": goal.updated_at.isoformat() if goal.updated_at else None,
            "updates": [
                {
                    "previous_amount": self._decimal_to_float(update.previous_amount),
                    "new_amount": self._decimal_to_float(update.new_amount),
                    "update_type": update.update_type,
                    "created_at": update.created_at.isoformat() if update.created_at else None,
                }
                for update in goal.updates
            ],
        }

    async def _restore_expense(
        self,
        session: AsyncSession,
        phone: str,
        data: dict[str, Any],
    ) -> bool:
        required_fields = ("description", "amount", "category", "payment_method", "type", "date")
        for field in required_fields:
            if data.get(field) in (None, ""):
                raise ValueError(f"Despesa com campo obrigatorio ausente: {field}")

        category = await self._get_category(session, str(data["category"]))
        if not category:
            raise ValueError(f"Categoria inexistente no backup: {data['category']}")

        payment_method = await self._get_payment_method(session, str(data["payment_method"]))
        if not payment_method:
            raise ValueError(f"Metodo de pagamento inexistente no backup: {data['payment_method']}")

        expense_date = date.fromisoformat(str(data["date"]))
        amount = Decimal(str(data["amount"]))

        existing = await session.execute(
            select(Expense.id)
            .where(Expense.user_phone == phone)
            .where(Expense.description == str(data["description"]))
            .where(Expense.amount == amount)
            .where(Expense.date == expense_date)
            .where(Expense.category_id == category.id)
            .where(Expense.payment_method_id == payment_method.id)
        )
        if existing.scalar_one_or_none():
            return False

        expense = Expense(
            user_phone=phone,
            description=str(data["description"]),
            amount=amount,
            category_id=category.id,
            payment_method_id=payment_method.id,
            type=str(data["type"]),
            installment_current=data.get("installment_current"),
            installment_total=data.get("installment_total"),
            is_shared=bool(data.get("is_shared", False)),
            shared_percentage=self._to_decimal(data.get("shared_percentage")),
            original_currency=data.get("original_currency"),
            original_amount=self._to_decimal(data.get("original_amount")),
            exchange_rate=self._to_decimal(data.get("exchange_rate")),
            is_recurring=bool(data.get("is_recurring", False)),
            recurring_day=data.get("recurring_day"),
            recurring_active=data.get("recurring_active"),
            date=expense_date,
            created_at=self._parse_datetime(data.get("created_at")) or datetime.now(),
        )
        session.add(expense)
        await session.flush()
        return True

    async def _restore_budget(
        self,
        session: AsyncSession,
        phone: str,
        data: dict[str, Any],
    ) -> tuple[bool, int]:
        category_name = data.get("category")
        category_id = None
        if category_name is not None:
            category = await self._get_category(session, str(category_name))
            if not category:
                raise ValueError(f"Categoria inexistente no backup: {category_name}")
            category_id = category.id

        monthly_limit = Decimal(str(data["monthly_limit"]))
        result = await session.execute(
            select(Budget)
            .where(Budget.user_phone == phone)
            .where(Budget.category_id == category_id)
            .where(Budget.monthly_limit == monthly_limit)
        )
        budget = result.scalar_one_or_none()
        created = False
        if not budget:
            budget = Budget(
                user_phone=phone,
                category_id=category_id,
                monthly_limit=monthly_limit,
                is_active=bool(data.get("is_active", True)),
                created_at=self._parse_datetime(data.get("created_at")) or datetime.now(),
                updated_at=self._parse_datetime(data.get("updated_at")),
            )
            session.add(budget)
            await session.flush()
            created = True

        alerts_created = 0
        existing_alerts = await session.execute(
            select(BudgetAlert).where(BudgetAlert.budget_id == budget.id)
        )
        existing_alert_keys = {
            (alert.threshold_percent, alert.month, alert.year)
            for alert in existing_alerts.scalars().all()
        }
        for alert_data in data.get("alerts", []):
            alert_key = (
                alert_data["threshold_percent"],
                alert_data["month"],
                alert_data["year"],
            )
            if alert_key in existing_alert_keys:
                continue
            session.add(
                BudgetAlert(
                    budget_id=budget.id,
                    threshold_percent=alert_data["threshold_percent"],
                    month=alert_data["month"],
                    year=alert_data["year"],
                    sent_at=self._parse_datetime(alert_data.get("sent_at")) or datetime.now(),
                )
            )
            alerts_created += 1
            existing_alert_keys.add(alert_key)

        return created, alerts_created

    async def _restore_goal(
        self,
        session: AsyncSession,
        phone: str,
        data: dict[str, Any],
    ) -> tuple[bool, int]:
        description = str(data["description"])
        target_amount = Decimal(str(data["target_amount"]))

        result = await session.execute(
            select(Goal)
            .where(Goal.user_phone == phone)
            .where(Goal.description == description)
            .where(Goal.target_amount == target_amount)
        )
        goal = result.scalar_one_or_none()
        created = False
        if not goal:
            goal = Goal(
                user_phone=phone,
                description=description,
                target_amount=target_amount,
                current_amount=self._to_decimal(data.get("current_amount")) or Decimal("0"),
                deadline=date.fromisoformat(str(data["deadline"])),
                start_date=date.fromisoformat(str(data["start_date"])),
                is_active=bool(data.get("is_active", True)),
                is_achieved=bool(data.get("is_achieved", False)),
                created_at=self._parse_datetime(data.get("created_at")) or datetime.now(),
                updated_at=self._parse_datetime(data.get("updated_at")),
            )
            session.add(goal)
            await session.flush()
            created = True

        updates_created = 0
        existing_goal_updates = await session.execute(
            select(GoalUpdate).where(GoalUpdate.goal_id == goal.id)
        )
        existing_updates = {
            (
                self._decimal_to_float(update.previous_amount),
                self._decimal_to_float(update.new_amount),
                update.update_type,
                update.created_at.isoformat() if update.created_at else None,
            )
            for update in existing_goal_updates.scalars().all()
        }
        for update_data in data.get("updates", []):
            update_key = (
                float(update_data["previous_amount"]),
                float(update_data["new_amount"]),
                str(update_data["update_type"]),
                update_data.get("created_at"),
            )
            if update_key in existing_updates:
                continue
            session.add(
                GoalUpdate(
                    goal_id=goal.id,
                    previous_amount=Decimal(str(update_data["previous_amount"])),
                    new_amount=Decimal(str(update_data["new_amount"])),
                    update_type=str(update_data["update_type"]),
                    created_at=self._parse_datetime(update_data.get("created_at")) or datetime.now(),
                )
            )
            updates_created += 1
            existing_updates.add(update_key)

        return created, updates_created

    async def _get_category(self, session: AsyncSession, name: str) -> Category | None:
        result = await session.execute(select(Category).where(Category.name == name))
        return result.scalar_one_or_none()

    async def _get_payment_method(self, session: AsyncSession, name: str) -> PaymentMethod | None:
        result = await session.execute(select(PaymentMethod).where(PaymentMethod.name == name))
        return result.scalar_one_or_none()

    def _decimal_to_float(self, value: Decimal | None) -> float | None:
        if value is None:
            return None
        return float(value)

    def _to_decimal(self, value: Any) -> Decimal | None:
        if value in (None, ""):
            return None
        return Decimal(str(value))

    def _parse_datetime(self, value: Any) -> datetime | None:
        if not value:
            return None
        return datetime.fromisoformat(str(value))
