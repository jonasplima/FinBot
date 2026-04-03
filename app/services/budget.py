"""Budget management service with alerts."""

import logging
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import extract, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database.models import Budget, BudgetAlert, Category, Expense
from app.utils.validators import normalize_phone

logger = logging.getLogger(__name__)

# Alert thresholds (percentage of budget consumed)
ALERT_THRESHOLDS = [50, 80, 100]


class BudgetService:
    """Service for managing budgets and alerts."""

    async def create_budget(
        self,
        session: AsyncSession,
        phone: str,
        category_name: str | None,
        monthly_limit: Decimal,
    ) -> dict:
        """
        Create a new budget for a user.

        Args:
            session: Database session
            phone: User phone number
            category_name: Category name (None for all categories)
            monthly_limit: Monthly spending limit

        Returns:
            Dict with success status and budget details or error message
        """
        try:
            normalized_phone = normalize_phone(phone)

            # Validate limit
            if monthly_limit <= 0:
                return {"success": False, "error": "O limite deve ser maior que zero."}

            # Get category if specified
            category_id = None
            if category_name:
                category = await self._get_category(session, category_name)
                if not category:
                    return {
                        "success": False,
                        "error": f"Categoria '{category_name}' nao encontrada.",
                    }
                if category.type != "Negativo":
                    return {
                        "success": False,
                        "error": "Orcamentos so podem ser definidos para categorias de gastos.",
                    }
                category_id = category.id

            # Check if budget already exists for this user/category
            existing = await self._get_existing_budget(
                session, normalized_phone, category_id
            )
            if existing:
                # Update existing budget
                existing.monthly_limit = monthly_limit
                existing.is_active = True
                existing.updated_at = datetime.now()
                await session.commit()
                logger.info(f"Updated budget {existing.id} for {normalized_phone}")
                return {
                    "success": True,
                    "budget_id": existing.id,
                    "updated": True,
                    "category": category_name,
                    "limit": float(monthly_limit),
                }

            # Create new budget
            budget = Budget(
                user_phone=normalized_phone,
                category_id=category_id,
                monthly_limit=monthly_limit,
                is_active=True,
            )

            session.add(budget)
            await session.commit()

            logger.info(f"Created budget {budget.id} for {normalized_phone}")
            return {
                "success": True,
                "budget_id": budget.id,
                "updated": False,
                "category": category_name,
                "limit": float(monthly_limit),
            }

        except Exception as e:
            logger.error(f"Error creating budget: {e}")
            await session.rollback()
            return {"success": False, "error": str(e)}

    async def remove_budget(
        self,
        session: AsyncSession,
        phone: str,
        category_name: str | None,
    ) -> dict:
        """
        Remove (deactivate) a budget.

        Args:
            session: Database session
            phone: User phone number
            category_name: Category name (None for general budget)

        Returns:
            Dict with success status or error message
        """
        try:
            normalized_phone = normalize_phone(phone)

            # Get category if specified
            category_id = None
            if category_name:
                category = await self._get_category(session, category_name)
                if not category:
                    return {
                        "success": False,
                        "error": f"Categoria '{category_name}' nao encontrada.",
                    }
                category_id = category.id

            # Find existing budget
            budget = await self._get_existing_budget(
                session, normalized_phone, category_id
            )
            if not budget:
                return {
                    "success": False,
                    "error": "Orcamento nao encontrado para esta categoria.",
                }

            # Deactivate budget
            budget.is_active = False
            budget.updated_at = datetime.now()
            await session.commit()

            logger.info(f"Deactivated budget {budget.id} for {normalized_phone}")
            return {"success": True, "category": category_name}

        except Exception as e:
            logger.error(f"Error removing budget: {e}")
            await session.rollback()
            return {"success": False, "error": str(e)}

    async def list_budgets(
        self,
        session: AsyncSession,
        phone: str,
    ) -> dict:
        """
        List all active budgets for a user with current spending.

        Args:
            session: Database session
            phone: User phone number

        Returns:
            Dict with success status and list of budgets with status
        """
        try:
            normalized_phone = normalize_phone(phone)
            today = date.today()
            month = today.month
            year = today.year

            # Get all active budgets with category info
            result = await session.execute(
                select(Budget)
                .options(selectinload(Budget.category))
                .where(Budget.user_phone == normalized_phone)
                .where(Budget.is_active == True)
                .order_by(Budget.category_id)
            )

            budgets = result.scalars().all()

            if not budgets:
                return {"success": True, "budgets": [], "message": "Voce nao tem orcamentos definidos."}

            # Build budget status list
            budget_list = []
            for budget in budgets:
                spent = await self._get_spent_amount(
                    session, normalized_phone, budget.category_id, month, year
                )
                limit = budget.monthly_limit
                percentage = (spent / limit * 100) if limit > 0 else Decimal("0")
                remaining = limit - spent

                budget_list.append({
                    "id": budget.id,
                    "category": budget.category.name if budget.category else "Geral",
                    "limit": float(limit),
                    "spent": float(spent),
                    "remaining": float(remaining),
                    "percentage": float(percentage),
                })

            return {"success": True, "budgets": budget_list}

        except Exception as e:
            logger.error(f"Error listing budgets: {e}")
            return {"success": False, "error": str(e)}

    async def check_budget_status(
        self,
        session: AsyncSession,
        phone: str,
        category_name: str | None = None,
    ) -> dict:
        """
        Check budget status for a specific category or all budgets.

        Args:
            session: Database session
            phone: User phone number
            category_name: Optional category name to check

        Returns:
            Dict with budget status details
        """
        try:
            normalized_phone = normalize_phone(phone)
            today = date.today()
            month = today.month
            year = today.year

            # Get category if specified
            category_id = None
            if category_name:
                category = await self._get_category(session, category_name)
                if not category:
                    return {
                        "success": False,
                        "error": f"Categoria '{category_name}' nao encontrada.",
                    }
                category_id = category.id

            # Get budget
            budget = await self._get_existing_budget(
                session, normalized_phone, category_id
            )

            if not budget:
                return {
                    "success": False,
                    "error": "Orcamento nao definido para esta categoria.",
                }

            spent = await self._get_spent_amount(
                session, normalized_phone, budget.category_id, month, year
            )
            limit = budget.monthly_limit
            percentage = (spent / limit * 100) if limit > 0 else Decimal("0")
            remaining = limit - spent

            return {
                "success": True,
                "category": budget.category.name if budget.category else "Geral",
                "limit": float(limit),
                "spent": float(spent),
                "remaining": float(remaining),
                "percentage": float(percentage),
            }

        except Exception as e:
            logger.error(f"Error checking budget status: {e}")
            return {"success": False, "error": str(e)}

    async def check_and_send_alerts(
        self,
        session: AsyncSession,
        phone: str,
        category_id: int | None,
    ) -> list[dict]:
        """
        Check if any budget alerts should be triggered after an expense.

        This method calculates the current spending percentage and returns
        alerts for any thresholds that have been crossed but not yet notified.

        Args:
            session: Database session
            phone: User phone number
            category_id: Category ID of the expense (to check specific category budget)

        Returns:
            List of alert dicts with threshold, category, spent, limit info
        """
        try:
            normalized_phone = normalize_phone(phone)
            today = date.today()
            month = today.month
            year = today.year

            alerts_to_send = []

            # Get all budgets that might be affected (specific category + general)
            budgets_to_check = []

            # Check specific category budget
            if category_id:
                specific_budget = await self._get_existing_budget(
                    session, normalized_phone, category_id
                )
                if specific_budget:
                    budgets_to_check.append(specific_budget)

            # Check general budget (all categories)
            general_budget = await self._get_existing_budget(
                session, normalized_phone, None
            )
            if general_budget:
                budgets_to_check.append(general_budget)

            # Process each budget
            for budget in budgets_to_check:
                spent = await self._get_spent_amount(
                    session, normalized_phone, budget.category_id, month, year
                )
                limit = budget.monthly_limit
                percentage = float((spent / limit * 100) if limit > 0 else 0)

                # Check each threshold
                for threshold in ALERT_THRESHOLDS:
                    if percentage >= threshold:
                        # Check if alert already sent this month
                        alert_sent = await self._was_alert_sent(
                            session, budget.id, threshold, month, year
                        )

                        if not alert_sent:
                            # Record alert
                            alert = BudgetAlert(
                                budget_id=budget.id,
                                threshold_percent=threshold,
                                month=month,
                                year=year,
                            )
                            session.add(alert)

                            category_name = budget.category.name if budget.category else "Geral"
                            alerts_to_send.append({
                                "threshold": threshold,
                                "category": category_name,
                                "spent": float(spent),
                                "limit": float(limit),
                                "percentage": percentage,
                                "exceeded": percentage >= 100,
                            })

            if alerts_to_send:
                await session.commit()

            return alerts_to_send

        except Exception as e:
            logger.error(f"Error checking budget alerts: {e}")
            await session.rollback()
            return []

    async def _get_existing_budget(
        self,
        session: AsyncSession,
        phone: str,
        category_id: int | None,
    ) -> Budget | None:
        """Get existing active budget for user/category."""
        query = (
            select(Budget)
            .options(selectinload(Budget.category))
            .where(Budget.user_phone == phone)
            .where(Budget.is_active == True)
        )

        if category_id is None:
            query = query.where(Budget.category_id.is_(None))
        else:
            query = query.where(Budget.category_id == category_id)

        result = await session.execute(query)
        return result.scalar_one_or_none()

    async def _get_spent_amount(
        self,
        session: AsyncSession,
        phone: str,
        category_id: int | None,
        month: int,
        year: int,
    ) -> Decimal:
        """Get total spent amount for a category in a month."""
        query = (
            select(func.coalesce(func.sum(Expense.amount), 0))
            .where(Expense.user_phone == phone)
            .where(Expense.type == "Negativo")
            .where(extract("month", Expense.date) == month)
            .where(extract("year", Expense.date) == year)
        )

        if category_id is not None:
            query = query.where(Expense.category_id == category_id)

        result = await session.execute(query)
        total = result.scalar()
        return Decimal(str(total)) if total else Decimal("0")

    async def _was_alert_sent(
        self,
        session: AsyncSession,
        budget_id: int,
        threshold: int,
        month: int,
        year: int,
    ) -> bool:
        """Check if an alert was already sent for this budget/threshold/month."""
        result = await session.execute(
            select(BudgetAlert)
            .where(BudgetAlert.budget_id == budget_id)
            .where(BudgetAlert.threshold_percent == threshold)
            .where(BudgetAlert.month == month)
            .where(BudgetAlert.year == year)
        )
        return result.scalar_one_or_none() is not None

    async def _get_category(
        self,
        session: AsyncSession,
        name: str,
    ) -> Category | None:
        """Get category by name (case-insensitive)."""
        import unicodedata

        def remove_accents(text: str) -> str:
            if not text:
                return ""
            normalized = unicodedata.normalize("NFD", text)
            return "".join(c for c in normalized if unicodedata.category(c) != "Mn")

        # First try exact match (case-insensitive)
        result = await session.execute(
            select(Category).where(func.lower(Category.name) == name.lower())
        )
        category = result.scalar_one_or_none()

        if category:
            return category

        # If not found, try matching without accents
        normalized_name = remove_accents(name.lower())
        result = await session.execute(select(Category))
        categories = result.scalars().all()

        for cat in categories:
            if remove_accents(cat.name.lower()) == normalized_name:
                return cat

        return None
