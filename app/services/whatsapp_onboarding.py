"""User-scoped WhatsApp onboarding over Evolution sessions."""

from __future__ import annotations

from datetime import datetime
from secrets import token_hex

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database.models import User, UserWhatsAppSession
from app.services.evolution import EvolutionService
from app.services.onboarding import OnboardingService


class WhatsAppOnboardingService:
    """Manage WhatsApp onboarding sessions for authenticated web users."""

    def __init__(self) -> None:
        self.settings = get_settings()
        self.onboarding_service = OnboardingService()

    async def get_or_create_session(
        self,
        session: AsyncSession,
        user: User,
    ) -> UserWhatsAppSession:
        """Return the persisted WhatsApp session metadata for a user."""
        result = await session.execute(
            select(UserWhatsAppSession).where(UserWhatsAppSession.user_id == user.id)
        )
        whatsapp_session = result.scalar_one_or_none()
        if whatsapp_session is not None:
            return whatsapp_session

        session_key = self._build_session_key(user.id)
        whatsapp_session = UserWhatsAppSession(
            user_id=user.id,
            evolution_instance=self._build_instance_name(session_key),
            session_key=session_key,
            connection_status="pending",
            created_at=datetime.now(),
        )
        session.add(whatsapp_session)
        await session.commit()
        await session.refresh(whatsapp_session)
        return whatsapp_session

    async def prepare_session(
        self,
        session: AsyncSession,
        user: User,
    ) -> dict:
        """Ensure the user has a dedicated WhatsApp session and advance onboarding."""
        whatsapp_session = await self.get_or_create_session(session, user)
        onboarding_state = await self.onboarding_service.update_step(
            session,
            user,
            "whatsapp_prepare",
        )
        return self.serialize_session(whatsapp_session, onboarding_state.current_step)

    async def get_status(
        self,
        session: AsyncSession,
        user: User,
    ) -> dict:
        """Fetch and persist the latest connection state for a user's session."""
        whatsapp_session = await self.get_or_create_session(session, user)
        evolution = EvolutionService()

        try:
            state_payload = await evolution.get_connection_state(
                whatsapp_session.evolution_instance
            )
            state = (
                state_payload.get("instance", {}).get("state")
                or state_payload.get("state")
                or "unknown"
            )
            await self._sync_connection_state(session, user, whatsapp_session, state)
        except Exception:
            # Keep the last persisted state and let the UI handle degraded operation.
            state = whatsapp_session.connection_status or "pending"

        return self.serialize_session(whatsapp_session, current_step=None)

    async def generate_qrcode(
        self,
        session: AsyncSession,
        user: User,
    ) -> dict:
        """Generate or refresh the WhatsApp QR code for a user's session."""
        whatsapp_session = await self.get_or_create_session(session, user)
        evolution = EvolutionService()
        qrcode_payload = await evolution.get_qrcode(whatsapp_session.evolution_instance)

        status = qrcode_payload.get("status", "pending")
        whatsapp_session.connection_status = self._normalize_connection_status(status)
        whatsapp_session.last_qrcode_at = datetime.now()
        whatsapp_session.updated_at = datetime.now()

        if whatsapp_session.connection_status == "connected":
            whatsapp_session.connected_at = whatsapp_session.connected_at or datetime.now()
            await self.onboarding_service.mark_whatsapp_connected(session, user)
        else:
            await self.onboarding_service.update_step(session, user, "whatsapp_qrcode")

        await session.commit()
        await session.refresh(whatsapp_session)

        payload = self.serialize_session(whatsapp_session, current_step="whatsapp_qrcode")
        payload["qrcode"] = qrcode_payload.get("qrcode")
        payload["pairingCode"] = qrcode_payload.get("pairingCode")
        payload["message"] = qrcode_payload.get("message")
        return payload

    async def refresh_status(
        self,
        session: AsyncSession,
        user: User,
    ) -> dict:
        """Refresh connection status and mark onboarding progress when connected."""
        whatsapp_session = await self.get_or_create_session(session, user)
        evolution = EvolutionService()

        try:
            state_payload = await evolution.get_connection_state(
                whatsapp_session.evolution_instance
            )
            state = (
                state_payload.get("instance", {}).get("state")
                or state_payload.get("state")
                or "unknown"
            )
        except Exception:
            state = whatsapp_session.connection_status or "pending"

        await self._sync_connection_state(session, user, whatsapp_session, state)
        return self.serialize_session(whatsapp_session, current_step=None)

    async def _sync_connection_state(
        self,
        session: AsyncSession,
        user: User,
        whatsapp_session: UserWhatsAppSession,
        raw_state: str,
    ) -> None:
        """Persist a normalized connection state for the user session."""
        whatsapp_session.connection_status = self._normalize_connection_status(raw_state)
        whatsapp_session.updated_at = datetime.now()

        if whatsapp_session.connection_status == "connected":
            whatsapp_session.connected_at = whatsapp_session.connected_at or datetime.now()
            await self.onboarding_service.mark_whatsapp_connected(session, user)

        await session.commit()
        await session.refresh(whatsapp_session)

    def serialize_session(
        self,
        whatsapp_session: UserWhatsAppSession,
        current_step: str | None,
    ) -> dict:
        """Build a safe API payload for the frontend."""
        return {
            "session": {
                "session_key": whatsapp_session.session_key,
                "evolution_instance": whatsapp_session.evolution_instance,
                "connection_status": whatsapp_session.connection_status,
                "connected_at": whatsapp_session.connected_at.isoformat()
                if whatsapp_session.connected_at
                else None,
                "last_qrcode_at": whatsapp_session.last_qrcode_at.isoformat()
                if whatsapp_session.last_qrcode_at
                else None,
            },
            "onboarding_step": current_step,
        }

    def _build_session_key(self, user_id: int) -> str:
        """Build a compact unique key for the user's WhatsApp session."""
        return f"user-{user_id}-{token_hex(4)}"

    def _build_instance_name(self, session_key: str) -> str:
        """Derive an Evolution instance name from the configured prefix."""
        prefix = self.settings.evolution_instance.strip() or "finbot"
        return f"{prefix}-{session_key}"[:120]

    def _normalize_connection_status(self, state: str) -> str:
        """Normalize raw Evolution connection states to UI-friendly values."""
        normalized = state.strip().lower()
        if normalized in {"open", "connected"}:
            return "connected"
        if normalized in {"connecting", "close", "pending", "waiting_qrcode", "waiting_pairing"}:
            return "pending"
        if normalized in {"unknown", ""}:
            return "pending"
        return normalized
