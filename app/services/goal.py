"""Savings goal management service."""

import logging
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database.models import Expense, Goal, GoalUpdate
from app.utils.validators import normalize_phone

logger = logging.getLogger(__name__)


class GoalService:
    """Service for managing savings goals."""

    async def create_goal(
        self,
        session: AsyncSession,
        phone: str,
        description: str,
        target_amount: Decimal,
        deadline: date,
    ) -> dict:
        """
        Create a new savings goal.

        Args:
            session: Database session
            phone: User phone number
            description: Goal description
            target_amount: Target amount to save
            deadline: Goal deadline date

        Returns:
            Dict with success status and goal details or error message
        """
        try:
            normalized_phone = normalize_phone(phone)

            # Validate target amount
            if target_amount <= 0:
                return {"success": False, "error": "O valor da meta deve ser maior que zero."}

            # Validate deadline
            today = date.today()
            if deadline <= today:
                return {"success": False, "error": "O prazo da meta deve ser uma data futura."}

            # Check if goal with same description exists
            existing = await self._get_goal_by_description(session, normalized_phone, description)
            if existing:
                return {
                    "success": False,
                    "error": f"Ja existe uma meta com a descricao '{description}'.",
                }

            # Create new goal
            goal = Goal(
                user_phone=normalized_phone,
                description=description,
                target_amount=target_amount,
                current_amount=Decimal("0"),
                deadline=deadline,
                start_date=today,
                is_active=True,
                is_achieved=False,
            )

            session.add(goal)
            await session.commit()

            logger.info(f"Created goal {goal.id} for {normalized_phone}: {description}")
            return {
                "success": True,
                "goal_id": goal.id,
                "description": description,
                "target_amount": float(target_amount),
                "deadline": deadline.strftime("%d/%m/%Y"),
            }

        except Exception as e:
            logger.error(f"Error creating goal: {e}")
            await session.rollback()
            return {"success": False, "error": str(e)}

    async def list_goals(
        self,
        session: AsyncSession,
        phone: str,
        include_achieved: bool = False,
    ) -> dict:
        """
        List all goals for a user with progress.

        Args:
            session: Database session
            phone: User phone number
            include_achieved: Whether to include achieved goals

        Returns:
            Dict with success status and list of goals with progress
        """
        try:
            normalized_phone = normalize_phone(phone)

            # Build query
            query = (
                select(Goal)
                .where(Goal.user_phone == normalized_phone)
                .where(Goal.is_active == True)
            )

            if not include_achieved:
                query = query.where(Goal.is_achieved == False)

            query = query.order_by(Goal.deadline)

            result = await session.execute(query)
            goals = result.scalars().all()

            if not goals:
                return {
                    "success": True,
                    "goals": [],
                    "message": "Voce nao tem metas ativas.",
                }

            # Calculate progress for each goal
            goal_list = []
            for goal in goals:
                progress = await self.calculate_progress(session, normalized_phone, goal)
                goal_list.append(progress)

            return {"success": True, "goals": goal_list}

        except Exception as e:
            logger.error(f"Error listing goals: {e}")
            return {"success": False, "error": str(e)}

    async def check_goal_progress(
        self,
        session: AsyncSession,
        phone: str,
        description: str | None = None,
    ) -> dict:
        """
        Check progress for a specific goal or first active goal.

        Args:
            session: Database session
            phone: User phone number
            description: Optional goal description to check

        Returns:
            Dict with goal progress details
        """
        try:
            normalized_phone = normalize_phone(phone)

            if description:
                goal = await self._get_goal_by_description(session, normalized_phone, description)
                if not goal:
                    return {
                        "success": False,
                        "error": f"Meta '{description}' nao encontrada.",
                    }
            else:
                # Get first active goal
                result = await session.execute(
                    select(Goal)
                    .where(Goal.user_phone == normalized_phone)
                    .where(Goal.is_active == True)
                    .where(Goal.is_achieved == False)
                    .order_by(Goal.deadline)
                    .limit(1)
                )
                goal = result.scalar_one_or_none()

                if not goal:
                    return {
                        "success": False,
                        "error": "Voce nao tem metas ativas.",
                    }

            progress = await self.calculate_progress(session, normalized_phone, goal)
            return {"success": True, "progress": progress}

        except Exception as e:
            logger.error(f"Error checking goal progress: {e}")
            return {"success": False, "error": str(e)}

    async def add_to_goal(
        self,
        session: AsyncSession,
        phone: str,
        description: str,
        amount: Decimal,
    ) -> dict:
        """
        Add a manual deposit to a goal.

        Args:
            session: Database session
            phone: User phone number
            description: Goal description
            amount: Amount to deposit

        Returns:
            Dict with success status and updated progress
        """
        try:
            normalized_phone = normalize_phone(phone)

            # Validate amount
            if amount <= 0:
                return {"success": False, "error": "O valor deve ser maior que zero."}

            # Get goal
            goal = await self._get_goal_by_description(session, normalized_phone, description)
            if not goal:
                return {
                    "success": False,
                    "error": f"Meta '{description}' nao encontrada.",
                }

            # Record update
            previous_amount = goal.current_amount
            goal.current_amount = goal.current_amount + amount
            goal.updated_at = datetime.now()

            # Create update record
            update = GoalUpdate(
                goal_id=goal.id,
                previous_amount=previous_amount,
                new_amount=goal.current_amount,
                update_type="deposit",
            )
            session.add(update)

            await session.commit()

            # Calculate new progress
            progress = await self.calculate_progress(session, normalized_phone, goal)

            # Check if goal is achieved
            if progress["percentage"] >= 100 and not goal.is_achieved:
                goal.is_achieved = True
                await session.commit()

            logger.info(f"Added {amount} to goal {goal.id} for {normalized_phone}")
            return {"success": True, "progress": progress}

        except Exception as e:
            logger.error(f"Error adding to goal: {e}")
            await session.rollback()
            return {"success": False, "error": str(e)}

    async def remove_goal(
        self,
        session: AsyncSession,
        phone: str,
        description: str,
    ) -> dict:
        """
        Remove (deactivate) a goal.

        Args:
            session: Database session
            phone: User phone number
            description: Goal description

        Returns:
            Dict with success status
        """
        try:
            normalized_phone = normalize_phone(phone)

            # Get goal
            goal = await self._get_goal_by_description(session, normalized_phone, description)
            if not goal:
                return {
                    "success": False,
                    "error": f"Meta '{description}' nao encontrada.",
                }

            # Deactivate goal
            goal.is_active = False
            goal.updated_at = datetime.now()
            await session.commit()

            logger.info(f"Deactivated goal {goal.id} for {normalized_phone}")
            return {"success": True, "description": description}

        except Exception as e:
            logger.error(f"Error removing goal: {e}")
            await session.rollback()
            return {"success": False, "error": str(e)}

    async def calculate_progress(
        self,
        session: AsyncSession,
        phone: str,
        goal: Goal,
    ) -> dict:
        """
        Calculate goal progress based on net savings in the period.

        The progress is calculated as:
        - Total income in period (Positivo expenses)
        - Minus total expenses in period (Negativo expenses)
        - Plus manual deposits (current_amount)

        Args:
            session: Database session
            phone: User phone number
            goal: Goal object

        Returns:
            Dict with progress details
        """
        normalized_phone = normalize_phone(phone)
        today = date.today()

        # Get total income in the period
        income_result = await session.execute(
            select(func.coalesce(func.sum(Expense.amount), 0))
            .where(Expense.user_phone == normalized_phone)
            .where(Expense.type == "Positivo")
            .where(Expense.date >= goal.start_date)
            .where(Expense.date <= today)
        )
        total_income = Decimal(str(income_result.scalar() or 0))

        # Get total expenses in the period
        expense_result = await session.execute(
            select(func.coalesce(func.sum(Expense.amount), 0))
            .where(Expense.user_phone == normalized_phone)
            .where(Expense.type == "Negativo")
            .where(Expense.date >= goal.start_date)
            .where(Expense.date <= today)
        )
        total_expenses = Decimal(str(expense_result.scalar() or 0))

        # Net savings + manual deposits
        net_savings = total_income - total_expenses
        total_progress = net_savings + goal.current_amount

        # Calculate percentage (cap at 0 if negative)
        if total_progress < 0:
            total_progress = Decimal("0")

        percentage = (
            (total_progress / goal.target_amount * 100) if goal.target_amount > 0 else Decimal("0")
        )

        # Days calculation
        total_days = (goal.deadline - goal.start_date).days
        elapsed_days = (today - goal.start_date).days
        remaining_days = max(0, (goal.deadline - today).days)

        # Calculate daily rate needed
        remaining_amount = goal.target_amount - total_progress
        if remaining_days > 0 and remaining_amount > 0:
            daily_rate_needed = remaining_amount / remaining_days
        else:
            daily_rate_needed = Decimal("0")

        # Check if on track
        expected_percentage = (elapsed_days / total_days * 100) if total_days > 0 else 0
        is_on_track = float(percentage) >= expected_percentage

        return {
            "goal_id": goal.id,
            "description": goal.description,
            "target_amount": float(goal.target_amount),
            "current_progress": float(total_progress),
            "percentage": float(percentage),
            "remaining_amount": float(max(0, remaining_amount)),
            "remaining_days": remaining_days,
            "daily_rate_needed": float(daily_rate_needed),
            "is_on_track": is_on_track,
            "deadline": goal.deadline.strftime("%d/%m/%Y"),
            "is_achieved": goal.is_achieved or float(percentage) >= 100,
        }

    async def get_weekly_motivation(
        self,
        session: AsyncSession,
        phone: str,
    ) -> list[dict]:
        """
        Get motivation data for active goals (called by scheduler).

        Args:
            session: Database session
            phone: User phone number

        Returns:
            List of dicts with goal progress for motivation messages
        """
        try:
            normalized_phone = normalize_phone(phone)

            result = await session.execute(
                select(Goal)
                .where(Goal.user_phone == normalized_phone)
                .where(Goal.is_active == True)
                .where(Goal.is_achieved == False)
                .order_by(Goal.deadline)
            )
            goals = result.scalars().all()

            motivations = []
            for goal in goals:
                progress = await self.calculate_progress(session, normalized_phone, goal)
                motivations.append(progress)

            return motivations

        except Exception as e:
            logger.error(f"Error getting weekly motivation: {e}")
            return []

    async def get_users_with_active_goals(
        self,
        session: AsyncSession,
    ) -> list[str]:
        """
        Get list of users with active goals (for scheduler).

        Args:
            session: Database session

        Returns:
            List of phone numbers with active goals
        """
        try:
            result = await session.execute(
                select(Goal.user_phone)
                .where(Goal.is_active == True)
                .where(Goal.is_achieved == False)
                .distinct()
            )
            return [row[0] for row in result.fetchall()]

        except Exception as e:
            logger.error(f"Error getting users with active goals: {e}")
            return []

    async def _get_goal_by_description(
        self,
        session: AsyncSession,
        phone: str,
        description: str,
    ) -> Goal | None:
        """Get active goal by description (case-insensitive, accent-insensitive)."""
        import unicodedata

        def remove_accents(text: str) -> str:
            if not text:
                return ""
            normalized = unicodedata.normalize("NFD", text)
            return "".join(c for c in normalized if unicodedata.category(c) != "Mn")

        # First try exact match (case-insensitive)
        result = await session.execute(
            select(Goal)
            .options(selectinload(Goal.updates))
            .where(Goal.user_phone == phone)
            .where(Goal.is_active == True)
            .where(func.lower(Goal.description) == description.lower())
        )
        goal = result.scalar_one_or_none()

        if goal:
            return goal

        # Try matching without accents
        normalized_desc = remove_accents(description.lower())
        result = await session.execute(
            select(Goal)
            .options(selectinload(Goal.updates))
            .where(Goal.user_phone == phone)
            .where(Goal.is_active == True)
        )
        goals = result.scalars().all()

        for g in goals:
            if remove_accents(g.description.lower()) == normalized_desc:
                return g

        return None
