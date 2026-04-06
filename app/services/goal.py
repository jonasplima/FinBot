"""Savings goal management service."""

import logging
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database.models import Expense, Goal, GoalTransaction, GoalUpdate
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

    async def list_goal_transactions(
        self,
        session: AsyncSession,
        phone: str,
        limit: int = 30,
    ) -> list[dict[str, str | float | int | None]]:
        """List recent goal contribution and withdrawal movements for the dashboard."""
        normalized_phone = normalize_phone(phone)
        result = await session.execute(
            select(GoalTransaction)
            .options(selectinload(GoalTransaction.goal))
            .where(GoalTransaction.user_phone == normalized_phone)
            .order_by(GoalTransaction.transaction_date.desc(), GoalTransaction.created_at.desc())
            .limit(limit)
        )
        transactions = result.scalars().all()
        return [
            {
                "id": int(item.id),
                "goal_id": int(item.goal_id),
                "goal_description": item.goal.description if item.goal else "",
                "transaction_type": str(item.transaction_type),
                "amount": float(item.amount),
                "description": str(item.description or ""),
                "related_expense_id": int(item.related_expense_id)
                if item.related_expense_id is not None
                else None,
                "transaction_date": item.transaction_date.isoformat(),
                "transaction_date_label": item.transaction_date.strftime("%d/%m/%Y"),
            }
            for item in transactions
        ]

    async def get_available_goal_balance(
        self,
        session: AsyncSession,
        phone: str,
        goal_id: int,
    ) -> Decimal | None:
        """Return the currently available balance for a goal, including legacy contributions."""
        normalized_phone = normalize_phone(phone)
        goal = await self.get_goal_by_id(session, normalized_phone, goal_id)
        if goal is None:
            return None
        await self._migrate_legacy_goal_balance(session, normalized_phone, goal)
        return goal.current_amount

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
        Add a contribution to a goal.

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

            await self._record_goal_transaction(
                session,
                goal=goal,
                user_phone=normalized_phone,
                transaction_type="contribution",
                amount=amount,
                description="Aporte registrado pelo fluxo de metas",
            )

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

    async def contribute_to_goal(
        self,
        session: AsyncSession,
        phone: str,
        goal_id: int,
        amount: Decimal,
        *,
        description: str | None = None,
        transaction_date: date | None = None,
    ) -> dict:
        """Add a dedicated contribution movement to a goal."""
        normalized_phone = normalize_phone(phone)
        if amount <= 0:
            return {"success": False, "error": "O valor do aporte deve ser maior que zero."}

        goal = await self.get_goal_by_id(session, normalized_phone, goal_id)
        if goal is None:
            return {"success": False, "error": "Meta selecionada nao encontrada."}
        await self._migrate_legacy_goal_balance(session, normalized_phone, goal)

        try:
            await self._record_goal_transaction(
                session,
                goal=goal,
                user_phone=normalized_phone,
                transaction_type="contribution",
                amount=amount,
                description=description or f"Aporte para {goal.description}",
                transaction_date=transaction_date,
            )
            progress = await self.calculate_progress(session, normalized_phone, goal)
            if progress["percentage"] >= 100 and not goal.is_achieved:
                goal.is_achieved = True
                await session.commit()
            return {"success": True, "progress": progress}
        except Exception as exc:
            logger.error(f"Error contributing to goal: {exc}")
            await session.rollback()
            return {"success": False, "error": str(exc)}

    async def withdraw_from_goal(
        self,
        session: AsyncSession,
        phone: str,
        goal_id: int,
        amount: Decimal,
        *,
        description: str | None = None,
        related_expense_id: int | None = None,
        transaction_date: date | None = None,
    ) -> dict:
        """Withdraw funds from a goal balance, optionally linking the withdrawal to an expense."""
        normalized_phone = normalize_phone(phone)
        if amount <= 0:
            return {"success": False, "error": "O valor do resgate deve ser maior que zero."}

        goal = await self.get_goal_by_id(session, normalized_phone, goal_id)
        if goal is None:
            return {"success": False, "error": "Meta selecionada nao encontrada."}
        await self._migrate_legacy_goal_balance(session, normalized_phone, goal)
        if goal.current_amount < amount:
            return {
                "success": False,
                "error": "A meta nao possui saldo suficiente para esse resgate.",
            }

        try:
            await self._record_goal_transaction(
                session,
                goal=goal,
                user_phone=normalized_phone,
                transaction_type="withdrawal",
                amount=amount,
                description=description or f"Resgate da meta {goal.description}",
                related_expense_id=related_expense_id,
                transaction_date=transaction_date,
            )
            progress = await self.calculate_progress(session, normalized_phone, goal)
            if progress["percentage"] < 100 and goal.is_achieved:
                goal.is_achieved = False
                await session.commit()
            return {"success": True, "progress": progress}
        except Exception as exc:
            logger.error(f"Error withdrawing from goal: {exc}")
            await session.rollback()
            return {"success": False, "error": str(exc)}

    async def get_goal_by_id(
        self,
        session: AsyncSession,
        phone: str,
        goal_id: int,
    ) -> Goal | None:
        """Return an active goal by id scoped to the current user."""
        normalized_phone = normalize_phone(phone)
        result = await session.execute(
            select(Goal)
            .options(selectinload(Goal.updates))
            .where(Goal.id == goal_id)
            .where(Goal.user_phone == normalized_phone)
            .where(Goal.is_active == True)
        )
        return result.scalar_one_or_none()

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
        Calculate goal progress based on amounts explicitly allocated to the goal.

        Args:
            session: Database session
            phone: User phone number
            goal: Goal object

        Returns:
            Dict with progress details
        """
        normalized_phone = normalize_phone(phone)
        today = date.today()
        transaction_count_result = await session.execute(
            select(func.count(GoalTransaction.id)).where(GoalTransaction.goal_id == goal.id)
        )
        has_dedicated_transactions = int(transaction_count_result.scalar() or 0) > 0

        if has_dedicated_transactions:
            legacy_goal_contributions = Decimal("0")
        else:
            # Legacy linked expenses from the old "Metas" category flow still count toward the goal
            # until the goal receives its first dedicated transaction.
            legacy_contribution_result = await session.execute(
                select(func.coalesce(func.sum(Expense.amount), 0))
                .where(Expense.user_phone == normalized_phone)
                .where(Expense.goal_id == goal.id)
                .where(Expense.date >= goal.start_date)
                .where(Expense.date <= today)
            )
            legacy_goal_contributions = Decimal(str(legacy_contribution_result.scalar() or 0))

        total_progress = goal.current_amount + legacy_goal_contributions

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
            "goal_contributions": float(total_progress),
            "legacy_goal_contributions": float(legacy_goal_contributions),
            "percentage": float(percentage),
            "remaining_amount": float(max(0, remaining_amount)),
            "remaining_days": remaining_days,
            "daily_rate_needed": float(daily_rate_needed),
            "is_on_track": is_on_track,
            "deadline": goal.deadline.strftime("%d/%m/%Y"),
            "is_achieved": goal.is_achieved or float(percentage) >= 100,
        }

    async def _migrate_legacy_goal_balance(
        self,
        session: AsyncSession,
        phone: str,
        goal: Goal,
    ) -> None:
        """Promote legacy goal-linked expenses into the cached balance when the new flow is first used."""
        normalized_phone = normalize_phone(phone)
        transaction_count_result = await session.execute(
            select(func.count(GoalTransaction.id)).where(GoalTransaction.goal_id == goal.id)
        )
        transaction_count = int(transaction_count_result.scalar() or 0)
        if transaction_count > 0:
            return

        legacy_contribution_result = await session.execute(
            select(func.coalesce(func.sum(Expense.amount), 0))
            .where(Expense.user_phone == normalized_phone)
            .where(Expense.goal_id == goal.id)
            .where(Expense.date >= goal.start_date)
            .where(Expense.date <= date.today())
        )
        legacy_goal_contributions = Decimal(str(legacy_contribution_result.scalar() or 0))
        if legacy_goal_contributions <= 0:
            return

        goal.current_amount = goal.current_amount + legacy_goal_contributions
        goal.updated_at = datetime.now()
        session.add(
            GoalUpdate(
                goal_id=goal.id,
                previous_amount=Decimal("0"),
                new_amount=goal.current_amount,
                update_type="legacy_migration",
            )
        )
        await session.commit()

    async def _record_goal_transaction(
        self,
        session: AsyncSession,
        *,
        goal: Goal,
        user_phone: str,
        transaction_type: str,
        amount: Decimal,
        description: str | None = None,
        related_expense_id: int | None = None,
        transaction_date: date | None = None,
    ) -> None:
        """Persist a goal transaction and keep the cached current balance updated."""
        previous_amount = goal.current_amount
        if transaction_type == "contribution":
            new_amount = previous_amount + amount
            update_type = "contribution"
        elif transaction_type == "withdrawal":
            new_amount = previous_amount - amount
            update_type = "withdrawal"
        else:
            raise ValueError("Tipo de transacao de meta invalido.")

        goal.current_amount = new_amount
        goal.updated_at = datetime.now()

        transaction = GoalTransaction(
            goal_id=goal.id,
            user_phone=user_phone,
            transaction_type=transaction_type,
            amount=amount,
            description=description,
            related_expense_id=related_expense_id,
            transaction_date=transaction_date or date.today(),
        )
        update = GoalUpdate(
            goal_id=goal.id,
            previous_amount=previous_amount,
            new_amount=new_amount,
            update_type=update_type,
        )
        session.add(transaction)
        session.add(update)
        await session.commit()

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
