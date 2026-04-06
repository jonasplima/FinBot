"""Onboarding state management for the web setup flow."""

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import User, UserOnboardingState


class OnboardingService:
    """Manage onboarding progress for authenticated web users."""

    STEP_ORDER = [
        "welcome",
        "terms",
        "ai_keys",
        "currency_keys",
        "whatsapp_prepare",
        "whatsapp_qrcode",
        "profile",
        "notifications",
        "categories",
        "review",
        "completed",
    ]

    def build_state_payload(
        self, user: User, onboarding_state: UserOnboardingState
    ) -> dict[str, Any]:
        """Serialize onboarding progress and user status for the frontend."""
        return {
            "user": {
                "id": user.id,
                "name": user.name,
                "display_name": user.display_name,
                "email": user.email,
                "phone": user.phone,
                "timezone": user.timezone,
                "accepted_terms": user.accepted_terms,
                "terms_version": user.terms_version,
                "onboarding_completed": user.onboarding_completed,
            },
            "onboarding": {
                "current_step": onboarding_state.current_step,
                "is_completed": onboarding_state.is_completed,
                "completed_at": onboarding_state.completed_at.isoformat()
                if onboarding_state.completed_at
                else None,
                "whatsapp_connected_at": onboarding_state.whatsapp_connected_at.isoformat()
                if onboarding_state.whatsapp_connected_at
                else None,
                "steps": self.STEP_ORDER,
            },
        }

    async def get_or_create_state(
        self,
        session: AsyncSession,
        user: User,
    ) -> UserOnboardingState:
        """Return an onboarding state for the user, creating it when missing."""
        result = await session.execute(
            select(UserOnboardingState).where(UserOnboardingState.user_id == user.id)
        )
        onboarding_state = result.scalar_one_or_none()
        if onboarding_state is not None:
            return onboarding_state

        onboarding_state = UserOnboardingState(user_id=user.id, current_step="terms")
        session.add(onboarding_state)
        await session.commit()
        await session.refresh(onboarding_state)
        return onboarding_state

    async def update_step(
        self,
        session: AsyncSession,
        user: User,
        current_step: str,
    ) -> UserOnboardingState:
        """Update the current onboarding step if it is a supported value."""
        normalized_step = current_step.strip().lower()
        if normalized_step not in self.STEP_ORDER:
            raise ValueError("Etapa de onboarding invalida.")

        onboarding_state = await self.get_or_create_state(session, user)
        onboarding_state.current_step = normalized_step
        onboarding_state.updated_at = datetime.now()
        await session.commit()
        await session.refresh(onboarding_state)
        return onboarding_state

    async def mark_completed(
        self,
        session: AsyncSession,
        user: User,
    ) -> UserOnboardingState:
        """Mark onboarding as completed for the user."""
        onboarding_state = await self.get_or_create_state(session, user)
        onboarding_state.current_step = "completed"
        onboarding_state.is_completed = True
        onboarding_state.completed_at = datetime.now()
        onboarding_state.updated_at = datetime.now()

        user.onboarding_completed = True
        user.updated_at = datetime.now()
        user.last_seen_at = datetime.now()

        await session.commit()
        await session.refresh(onboarding_state)
        await session.refresh(user)
        return onboarding_state

    async def mark_whatsapp_connected(
        self,
        session: AsyncSession,
        user: User,
    ) -> UserOnboardingState:
        """Store the timestamp of WhatsApp connection during onboarding."""
        onboarding_state = await self.get_or_create_state(session, user)
        onboarding_state.whatsapp_connected_at = datetime.now()
        onboarding_state.updated_at = datetime.now()
        await session.commit()
        await session.refresh(onboarding_state)
        return onboarding_state
