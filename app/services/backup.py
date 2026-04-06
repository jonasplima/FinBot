"""Backup and restore service for user data."""

import base64
import hashlib
import json
import logging
from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.database.models import (
    BackupRestoreAudit,
    Budget,
    BudgetAlert,
    Category,
    Expense,
    Goal,
    GoalUpdate,
    PaymentMethod,
    User,
)
from app.services.operational_status import OperationalStatusService
from app.utils.validators import normalize_phone

logger = logging.getLogger(__name__)
settings = get_settings()
operational_status = OperationalStatusService()

BACKUP_SCHEMA_VERSION = 1
EXPENSE_ALLOWED_KEYS = {
    "description",
    "amount",
    "category",
    "payment_method",
    "type",
    "installment_current",
    "installment_total",
    "is_shared",
    "shared_percentage",
    "original_currency",
    "original_amount",
    "exchange_rate",
    "is_recurring",
    "recurring_day",
    "recurring_active",
    "date",
    "created_at",
}
EXPENSE_ALLOWED_TYPES = {"Negativo", "Positivo"}
BUDGET_ALLOWED_KEYS = {
    "category",
    "monthly_limit",
    "is_active",
    "created_at",
    "updated_at",
    "alerts",
}
BUDGET_ALERT_ALLOWED_KEYS = {"threshold_percent", "month", "year", "sent_at"}
GOAL_ALLOWED_KEYS = {
    "description",
    "target_amount",
    "current_amount",
    "deadline",
    "start_date",
    "is_active",
    "is_achieved",
    "created_at",
    "updated_at",
    "updates",
}
GOAL_UPDATE_ALLOWED_KEYS = {"previous_amount", "new_amount", "update_type", "created_at"}
GOAL_UPDATE_ALLOWED_TYPES = {"automatic", "manual", "deposit"}


class BackupService:
    """Service for exporting and restoring user backups."""

    _fallback_temp_storage: dict[str, tuple[str, datetime]] = {}

    def __init__(self) -> None:
        self.redis_url = settings.redis_url
        self._redis: Redis | None = None

    def _allow_local_fallback(self) -> bool:
        """Whether local in-memory temporary storage is allowed in this deployment mode."""
        return settings.normalized_deployment_mode == "single_instance"

    async def export_user_backup(
        self,
        session: AsyncSession,
        phone: str,
    ) -> dict:
        """Export a user's data to a JSON backup payload."""
        normalized_phone = normalize_phone(phone)
        user = await self._get_user(session, normalized_phone)

        expenses = await self._get_expenses(session, normalized_phone)
        budgets = await self._get_budgets(session, normalized_phone)
        goals = await self._get_goals(session, normalized_phone)

        payload = {
            "metadata": {
                "schema_version": BACKUP_SCHEMA_VERSION,
                "exported_at": datetime.now().isoformat(),
                "source_phone": normalized_phone,
                "source_backup_owner_id": user.backup_owner_id if user else None,
            },
            "expenses": [self._serialize_expense(expense) for expense in expenses],
            "budgets": [self._serialize_budget(budget) for budget in budgets],
            "goals": [self._serialize_goal(goal) for goal in goals],
        }

        json_bytes = json.dumps(payload, ensure_ascii=True, indent=2).encode("utf-8")
        if len(json_bytes) > settings.effective_max_backup_size_bytes:
            return {
                "success": False,
                "error": (
                    "Seu backup excede o limite seguro de tamanho para exportacao. "
                    "Reduza o volume de dados antes de tentar novamente."
                ),
            }
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
        if len(document_bytes) > settings.effective_max_backup_size_bytes:
            max_mb = settings.effective_max_backup_size_bytes / 1_000_000
            return {
                "success": False,
                "error": (f"O arquivo JSON excede o limite seguro do servidor ({max_mb:.1f} MB)."),
            }

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
        if not isinstance(backup_data, dict):
            return {"success": False, "error": "Backup invalido: estrutura principal ausente."}

        metadata = backup_data.get("metadata")
        if not isinstance(metadata, dict):
            return {"success": False, "error": "Backup sem metadata valida."}

        schema_version = metadata.get("schema_version")
        if schema_version != BACKUP_SCHEMA_VERSION:
            return {
                "success": False,
                "error": f"Versao de backup nao suportada: {schema_version}.",
            }

        source_backup_owner_id = metadata.get("source_backup_owner_id")
        if source_backup_owner_id is not None:
            if not isinstance(source_backup_owner_id, str) or not source_backup_owner_id.strip():
                return {"success": False, "error": "Metadata.source_backup_owner_id invalido."}
            if len(source_backup_owner_id.strip()) > 64:
                return {"success": False, "error": "Metadata.source_backup_owner_id invalido."}

        for key in ("expenses", "budgets", "goals"):
            if key not in backup_data or not isinstance(backup_data[key], list):
                return {"success": False, "error": f"Campo '{key}' ausente ou invalido."}

        allowed_root_keys = {"metadata", "expenses", "budgets", "goals"}
        if unknown := set(backup_data) - allowed_root_keys:
            return {
                "success": False,
                "error": f"Backup contem campos nao suportados: {', '.join(sorted(unknown))}.",
            }

        if len(backup_data["expenses"]) > settings.effective_max_backup_expenses:
            return {
                "success": False,
                "error": "Backup excede o limite de despesas suportadas.",
            }
        if len(backup_data["budgets"]) > settings.effective_max_backup_budgets:
            return {
                "success": False,
                "error": "Backup excede o limite de orcamentos suportados.",
            }
        if len(backup_data["goals"]) > settings.effective_max_backup_goals:
            return {
                "success": False,
                "error": "Backup excede o limite de metas suportadas.",
            }

        try:
            for expense in backup_data["expenses"]:
                self._validate_expense_item(expense)
            for budget in backup_data["budgets"]:
                self._validate_budget_item(budget)
            for goal in backup_data["goals"]:
                self._validate_goal_item(goal)
        except ValueError as exc:
            return {"success": False, "error": str(exc)}

        return {"success": True}

    def summarize_backup(self, backup_data: dict[str, Any]) -> dict:
        """Build a short summary of a backup payload."""
        return {
            "source_phone": backup_data.get("metadata", {}).get("source_phone", "desconhecido"),
            "source_backup_owner_id": backup_data.get("metadata", {}).get("source_backup_owner_id"),
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

    async def store_temporary_backup(self, backup_data: dict[str, Any]) -> dict[str, Any]:
        """Store backup payload temporarily outside the database blob column."""
        validation = self.validate_backup_data(backup_data)
        if not validation["success"]:
            return validation

        serialized = json.dumps(backup_data, ensure_ascii=True, separators=(",", ":"))
        backup_ref = f"finbot:backup:{uuid4().hex}"
        expires_at = datetime.now().timestamp() + settings.effective_backup_temp_ttl_seconds

        redis_client = await self._get_redis()
        if redis_client is not None:
            try:
                await redis_client.setex(
                    backup_ref,
                    settings.effective_backup_temp_ttl_seconds,
                    serialized,
                )
            except Exception as exc:
                if not self._allow_local_fallback():
                    logger.error(
                        "Temporary backup storage unavailable in multi-instance mode: %s",
                        exc,
                    )
                    operational_status.record_event(
                        "backup_temp_storage",
                        "error",
                        "Temporary backup storage unavailable in multi-instance mode.",
                    )
                    return {
                        "success": False,
                        "error": (
                            "O armazenamento temporario do backup esta indisponivel no momento. "
                            "Tente novamente em instantes."
                        ),
                    }
                logger.warning(
                    f"Redis unavailable for temporary backup store, using fallback: {exc}"
                )
                operational_status.record_event(
                    "backup_temp_storage",
                    "warning",
                    "Redis unavailable; using local fallback for temporary backup storage in single-instance mode.",
                )
                self._fallback_temp_storage[backup_ref] = (
                    serialized,
                    datetime.fromtimestamp(expires_at),
                )
        else:
            if not self._allow_local_fallback():
                logger.error("Temporary backup storage unavailable in multi-instance mode")
                operational_status.record_event(
                    "backup_temp_storage",
                    "error",
                    "Temporary backup storage unavailable in multi-instance mode.",
                )
                return {
                    "success": False,
                    "error": (
                        "O armazenamento temporario do backup esta indisponivel no momento. "
                        "Tente novamente em instantes."
                    ),
                }
            self._fallback_temp_storage[backup_ref] = (
                serialized,
                datetime.fromtimestamp(expires_at),
            )
            operational_status.record_event(
                "backup_temp_storage",
                "warning",
                "Redis unavailable; using local fallback for temporary backup storage in single-instance mode.",
            )

        return {
            "success": True,
            "backup_ref": backup_ref,
            "backup_hash": hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
        }

    async def load_temporary_backup(self, backup_ref: str) -> dict[str, Any]:
        """Load a previously stored temporary backup payload."""
        if not backup_ref:
            return {"success": False, "error": "Backup temporario invalido ou ausente."}

        serialized: str | None = None
        redis_client = await self._get_redis()
        if redis_client is not None:
            try:
                serialized = await redis_client.get(backup_ref)
            except Exception as exc:
                if not self._allow_local_fallback():
                    logger.error(
                        "Temporary backup load unavailable in multi-instance mode: %s",
                        exc,
                    )
                    operational_status.record_event(
                        "backup_temp_storage",
                        "error",
                        "Temporary backup load unavailable in multi-instance mode.",
                    )
                    return {
                        "success": False,
                        "error": (
                            "Nao foi possivel acessar o armazenamento temporario do backup agora. "
                            "Tente novamente em instantes."
                        ),
                    }
                logger.warning(
                    f"Redis unavailable for temporary backup load, using fallback: {exc}"
                )
                operational_status.record_event(
                    "backup_temp_storage",
                    "warning",
                    "Redis read failed; using local fallback for temporary backup storage in single-instance mode.",
                )

        if serialized is None:
            if not self._allow_local_fallback() and redis_client is None:
                logger.error("Temporary backup load unavailable in multi-instance mode")
                operational_status.record_event(
                    "backup_temp_storage",
                    "error",
                    "Temporary backup load unavailable in multi-instance mode.",
                )
                return {
                    "success": False,
                    "error": (
                        "Nao foi possivel acessar o armazenamento temporario do backup agora. "
                        "Tente novamente em instantes."
                    ),
                }
            entry = self._fallback_temp_storage.get(backup_ref)
            if entry is None:
                return {"success": False, "error": "O backup expirou ou nao esta mais disponivel."}
            serialized, expires_at = entry
            if expires_at <= datetime.now():
                self._fallback_temp_storage.pop(backup_ref, None)
                return {"success": False, "error": "O backup expirou ou nao esta mais disponivel."}

        try:
            data = json.loads(serialized)
        except json.JSONDecodeError:
            return {"success": False, "error": "Backup temporario corrompido ou invalido."}

        validation = self.validate_backup_data(data)
        if not validation["success"]:
            return {
                "success": False,
                "error": validation.get("error", "Backup temporario invalido."),
            }
        return {"success": True, "backup_data": data}

    async def delete_temporary_backup(self, backup_ref: str) -> None:
        """Delete a temporary backup payload after use or cancellation."""
        if not backup_ref:
            return

        self._fallback_temp_storage.pop(backup_ref, None)
        redis_client = await self._get_redis()
        if redis_client is None:
            return

        try:
            await redis_client.delete(backup_ref)
        except Exception as exc:
            logger.warning(f"Redis unavailable for temporary backup delete: {exc}")

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

    async def record_restore_audit(
        self,
        session: AsyncSession,
        target_phone: str,
        source_phone: str | None,
        status: str,
        *,
        requires_migration_confirmation: bool,
        explicit_migration_confirmation: bool,
        restored_counts: dict[str, int] | None = None,
        error_message: str | None = None,
    ) -> None:
        """Persist an audit entry for a backup restore attempt or completion."""
        audit = BackupRestoreAudit(
            target_phone=normalize_phone(target_phone),
            source_phone=normalize_phone(source_phone) if source_phone else None,
            status=status,
            requires_migration_confirmation=requires_migration_confirmation,
            explicit_migration_confirmation=explicit_migration_confirmation,
            restored_counts=restored_counts,
            error_message=error_message[:500] if error_message else None,
        )
        session.add(audit)
        await session.commit()

    async def _get_expenses(self, session: AsyncSession, phone: str) -> list[Expense]:
        result = await session.execute(
            select(Expense)
            .options(selectinload(Expense.category), selectinload(Expense.payment_method))
            .where(Expense.user_phone == phone)
            .order_by(Expense.date, Expense.id)
        )
        return list(result.scalars().all())

    async def _get_user(self, session: AsyncSession, phone: str) -> User | None:
        """Load the user profile associated with a phone number when available."""
        result = await session.execute(select(User).where(User.phone == phone))
        return result.scalar_one_or_none()

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
                    created_at=self._parse_datetime(update_data.get("created_at"))
                    or datetime.now(),
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

    async def _get_redis(self) -> Redis | None:
        """Lazily initialize Redis client for temporary backup storage."""
        if self._redis is not None:
            return self._redis

        try:
            self._redis = Redis.from_url(self.redis_url, decode_responses=True)
            return self._redis
        except Exception as exc:
            logger.warning(f"Could not initialize Redis client for backups: {exc}")
            return None

    def _validate_expense_item(self, expense: Mapping[str, Any]) -> None:
        self._ensure_mapping(expense, "Despesa")
        self._reject_unknown_fields(expense, EXPENSE_ALLOWED_KEYS, "Despesa")
        self._require_fields(
            expense,
            ("description", "amount", "category", "payment_method", "type", "date"),
            "Despesa",
        )

        if str(expense["type"]) not in EXPENSE_ALLOWED_TYPES:
            raise ValueError("Despesa com tipo invalido no backup.")

        self._ensure_decimal(expense["amount"], "Despesa.amount")
        self._ensure_date(expense["date"], "Despesa.date")
        self._ensure_optional_datetime(expense.get("created_at"), "Despesa.created_at")
        self._ensure_optional_positive_int(
            expense.get("installment_current"),
            "Despesa.installment_current",
        )
        self._ensure_optional_positive_int(
            expense.get("installment_total"), "Despesa.installment_total"
        )
        self._ensure_optional_decimal(expense.get("shared_percentage"), "Despesa.shared_percentage")
        self._ensure_optional_decimal(expense.get("original_amount"), "Despesa.original_amount")
        self._ensure_optional_decimal(expense.get("exchange_rate"), "Despesa.exchange_rate")
        self._ensure_optional_positive_int(
            expense.get("recurring_day"), "Despesa.recurring_day", 31
        )
        self._ensure_optional_bool(expense.get("is_shared"), "Despesa.is_shared")
        self._ensure_optional_bool(expense.get("is_recurring"), "Despesa.is_recurring")
        self._ensure_optional_bool(expense.get("recurring_active"), "Despesa.recurring_active")
        self._validate_expense_combinations(expense)

    def _validate_budget_item(self, budget: Mapping[str, Any]) -> None:
        self._ensure_mapping(budget, "Orcamento")
        self._reject_unknown_fields(budget, BUDGET_ALLOWED_KEYS, "Orcamento")
        self._require_fields(budget, ("monthly_limit",), "Orcamento")
        self._ensure_decimal(budget["monthly_limit"], "Orcamento.monthly_limit")
        self._ensure_optional_bool(budget.get("is_active"), "Orcamento.is_active")
        self._ensure_optional_datetime(budget.get("created_at"), "Orcamento.created_at")
        self._ensure_optional_datetime(budget.get("updated_at"), "Orcamento.updated_at")

        alerts = budget.get("alerts", [])
        if not isinstance(alerts, list):
            raise ValueError("Orcamento.alerts invalido no backup.")
        if len(alerts) > settings.effective_max_backup_budget_alerts:
            raise ValueError("Backup excede o limite de alertas de orcamento suportados.")
        for alert in alerts:
            self._validate_budget_alert_item(alert)

    def _validate_budget_alert_item(self, alert: Mapping[str, Any]) -> None:
        self._ensure_mapping(alert, "Alerta de orcamento")
        self._reject_unknown_fields(alert, BUDGET_ALERT_ALLOWED_KEYS, "Alerta de orcamento")
        self._require_fields(alert, ("threshold_percent", "month", "year"), "Alerta de orcamento")
        threshold = self._ensure_int(alert["threshold_percent"], "Alerta.threshold_percent")
        if threshold not in {50, 80, 100}:
            raise ValueError("Alerta de orcamento com threshold invalido no backup.")
        self._ensure_positive_int(alert["month"], "Alerta.month", 12)
        self._ensure_positive_int(alert["year"], "Alerta.year", 9999)
        self._ensure_optional_datetime(alert.get("sent_at"), "Alerta.sent_at")

    def _validate_goal_item(self, goal: Mapping[str, Any]) -> None:
        self._ensure_mapping(goal, "Meta")
        self._reject_unknown_fields(goal, GOAL_ALLOWED_KEYS, "Meta")
        self._require_fields(
            goal,
            ("description", "target_amount", "current_amount", "deadline", "start_date"),
            "Meta",
        )
        self._ensure_decimal(goal["target_amount"], "Meta.target_amount")
        self._ensure_decimal(goal["current_amount"], "Meta.current_amount")
        self._ensure_date(goal["deadline"], "Meta.deadline")
        self._ensure_date(goal["start_date"], "Meta.start_date")
        self._ensure_optional_bool(goal.get("is_active"), "Meta.is_active")
        self._ensure_optional_bool(goal.get("is_achieved"), "Meta.is_achieved")
        self._ensure_optional_datetime(goal.get("created_at"), "Meta.created_at")
        self._ensure_optional_datetime(goal.get("updated_at"), "Meta.updated_at")

        updates = goal.get("updates", [])
        if not isinstance(updates, list):
            raise ValueError("Meta.updates invalido no backup.")
        if len(updates) > settings.effective_max_backup_goal_updates:
            raise ValueError("Backup excede o limite de atualizacoes de metas suportadas.")
        for update in updates:
            self._validate_goal_update_item(update)

    def _validate_goal_update_item(self, update: Mapping[str, Any]) -> None:
        self._ensure_mapping(update, "Atualizacao de meta")
        self._reject_unknown_fields(update, GOAL_UPDATE_ALLOWED_KEYS, "Atualizacao de meta")
        self._require_fields(
            update, ("previous_amount", "new_amount", "update_type"), "Atualizacao de meta"
        )
        self._ensure_decimal(update["previous_amount"], "GoalUpdate.previous_amount")
        self._ensure_decimal(update["new_amount"], "GoalUpdate.new_amount")
        if str(update["update_type"]) not in GOAL_UPDATE_ALLOWED_TYPES:
            raise ValueError("Atualizacao de meta com tipo invalido no backup.")
        self._ensure_optional_datetime(update.get("created_at"), "GoalUpdate.created_at")

    def _ensure_mapping(self, value: Any, label: str) -> None:
        if not isinstance(value, Mapping):
            raise ValueError(f"{label} invalido no backup.")

    def _reject_unknown_fields(
        self,
        item: Mapping[str, Any],
        allowed_keys: set[str],
        label: str,
    ) -> None:
        unknown = set(item) - allowed_keys
        if unknown:
            raise ValueError(f"{label} contem campos nao suportados: {', '.join(sorted(unknown))}.")

    def _require_fields(
        self,
        item: Mapping[str, Any],
        fields: tuple[str, ...],
        label: str,
    ) -> None:
        for field in fields:
            if item.get(field) in (None, ""):
                raise ValueError(f"{label} com campo obrigatorio ausente: {field}")

    def _ensure_decimal(self, value: Any, label: str) -> Decimal:
        try:
            return Decimal(str(value))
        except Exception as exc:
            raise ValueError(f"{label} invalido no backup.") from exc

    def _ensure_optional_decimal(self, value: Any, label: str) -> Decimal | None:
        if value in (None, ""):
            return None
        return self._ensure_decimal(value, label)

    def _ensure_int(self, value: Any, label: str) -> int:
        try:
            return int(value)
        except Exception as exc:
            raise ValueError(f"{label} invalido no backup.") from exc

    def _ensure_positive_int(self, value: Any, label: str, max_value: int) -> int:
        parsed = self._ensure_int(value, label)
        if parsed < 1 or parsed > max_value:
            raise ValueError(f"{label} fora do limite permitido no backup.")
        return parsed

    def _ensure_optional_positive_int(
        self,
        value: Any,
        label: str,
        max_value: int = 999,
    ) -> int | None:
        if value in (None, ""):
            return None
        return self._ensure_positive_int(value, label, max_value)

    def _ensure_date(self, value: Any, label: str) -> date:
        try:
            return date.fromisoformat(str(value))
        except Exception as exc:
            raise ValueError(f"{label} invalido no backup.") from exc

    def _ensure_optional_datetime(self, value: Any, label: str) -> datetime | None:
        if value in (None, ""):
            return None
        try:
            return datetime.fromisoformat(str(value))
        except Exception as exc:
            raise ValueError(f"{label} invalido no backup.") from exc

    def _ensure_optional_bool(self, value: Any, label: str) -> bool | None:
        if value is None:
            return None
        if not isinstance(value, bool):
            raise ValueError(f"{label} invalido no backup.")
        return value

    def _validate_expense_combinations(self, expense: Mapping[str, Any]) -> None:
        """Validate cross-field consistency for expense backups."""
        installment_current = expense.get("installment_current")
        installment_total = expense.get("installment_total")
        if installment_current in (None, "") and installment_total not in (None, ""):
            raise ValueError("Despesa com parcelamento inconsistente no backup.")
        if installment_current not in (None, "") and installment_total in (None, ""):
            raise ValueError("Despesa com parcelamento inconsistente no backup.")
        if installment_current not in (None, "") and installment_total not in (None, ""):
            current = int(installment_current)
            total = int(installment_total)
            if current > total:
                raise ValueError("Despesa com parcelamento inconsistente no backup.")

        shared_percentage = expense.get("shared_percentage")
        if shared_percentage not in (None, ""):
            shared_value = Decimal(str(shared_percentage))
            if shared_value <= 0 or shared_value > 100:
                raise ValueError("Despesa com percentual compartilhado invalido no backup.")

        original_currency = expense.get("original_currency")
        original_amount = expense.get("original_amount")
        exchange_rate = expense.get("exchange_rate")
        if original_currency and (original_amount in (None, "") or exchange_rate in (None, "")):
            raise ValueError("Despesa com conversao de moeda incompleta no backup.")

        is_recurring = expense.get("is_recurring")
        recurring_day = expense.get("recurring_day")
        if is_recurring and recurring_day in (None, ""):
            raise ValueError("Despesa recorrente sem recurring_day no backup.")
