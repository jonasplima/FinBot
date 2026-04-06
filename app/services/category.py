"""Per-user category customization service."""

from __future__ import annotations

import unicodedata
from collections import defaultdict
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Category, User, UserCategory

SYSTEM_CATEGORY_TYPES = {"Negativo", "Positivo"}
DEFAULT_NEGATIVE_FALLBACK = "Outros"
DEFAULT_POSITIVE_FALLBACK = "Outros (entrada)"


def _normalize_name(value: str) -> str:
    """Normalize category names for case/accent insensitive comparisons."""
    normalized = unicodedata.normalize("NFD", value.strip().lower())
    return "".join(char for char in normalized if unicodedata.category(char) != "Mn")


class CategoryService:
    """Manage category visibility and custom categories for a user."""

    async def list_available_categories(
        self,
        session: AsyncSession,
        user: User,
        *,
        include_inactive: bool = True,
    ) -> dict[str, list[dict[str, Any]]]:
        """Return active and inactive categories for the user."""
        categories = await self._get_system_categories(session)
        user_rows = await self._get_user_category_rows(session, user.id)

        custom_rows = [row for row in user_rows if not row.is_system_default]
        visibility_rows = {
            row.base_category_id: row
            for row in user_rows
            if row.is_system_default and row.base_category_id
        }
        custom_base_ids = {
            row.base_category_id for row in custom_rows if row.base_category_id is not None
        }

        payload: dict[str, list[dict[str, Any]]] = {
            "active": [],
            "inactive": [],
            "custom": [],
        }

        for category in categories:
            if category.id in custom_base_ids:
                continue

            override = visibility_rows.get(category.id)
            is_active = True if override is None else bool(override.is_active)
            entry = {
                "name": str(category.name),
                "type": str(category.type),
                "is_active": is_active,
                "is_custom": False,
                "base_category_id": int(category.id),
            }
            if is_active:
                payload["active"].append(entry)
            elif include_inactive:
                payload["inactive"].append(entry)

        for row in sorted(custom_rows, key=lambda item: (str(item.type), str(item.name))):
            entry = {
                "id": int(row.id),
                "name": str(row.name),
                "type": str(row.type),
                "is_active": bool(row.is_active),
                "is_custom": True,
                "base_category_id": int(row.base_category_id) if row.base_category_id else None,
            }
            if row.is_active:
                payload["active"].append(entry)
                payload["custom"].append(entry)
            elif include_inactive:
                payload["inactive"].append(entry)

        payload["active"].sort(key=lambda item: (item["type"], item["name"]))
        payload["inactive"].sort(key=lambda item: (item["type"], item["name"]))
        payload["custom"].sort(key=lambda item: (item["type"], item["name"]))
        return payload

    async def get_active_category_names(
        self,
        session: AsyncSession,
        user: User,
    ) -> dict[str, list[str]]:
        """Return active category names grouped by type for prompt construction."""
        payload = await self.list_available_categories(session, user, include_inactive=False)
        grouped: dict[str, list[str]] = defaultdict(list)
        for item in payload["active"]:
            grouped[str(item["type"])].append(str(item["name"]))
        return {
            "Negativo": sorted(grouped.get("Negativo", [])),
            "Positivo": sorted(grouped.get("Positivo", [])),
        }

    async def create_custom_category(
        self,
        session: AsyncSession,
        user: User,
        *,
        name: str,
        category_type: str,
    ) -> UserCategory:
        """Create a custom category for the user."""
        normalized_name = name.strip()
        normalized_type = category_type.strip()
        if not normalized_name:
            raise ValueError("Nome da categoria e obrigatorio.")
        if normalized_type not in SYSTEM_CATEGORY_TYPES:
            raise ValueError("Tipo de categoria invalido.")

        existing = await self.find_user_category_by_name(session, user, normalized_name)
        system_match = await self._get_system_category_by_name(session, normalized_name)
        if existing is not None or system_match is not None:
            raise ValueError("Voce ja possui uma categoria com esse nome.")

        fallback_category = await self._get_fallback_base_category(session, normalized_type)
        custom_category = UserCategory(
            user_id=user.id,
            name=normalized_name,
            type=normalized_type,
            is_active=True,
            is_system_default=False,
            base_category_id=fallback_category.id,
            created_at=datetime.now(),
        )
        session.add(custom_category)
        await session.commit()
        await session.refresh(custom_category)
        return custom_category

    async def set_system_category_visibility(
        self,
        session: AsyncSession,
        user: User,
        *,
        category_name: str,
        is_active: bool,
    ) -> UserCategory:
        """Hide or reactivate a system category for this user only."""
        category = await self._get_system_category_by_name(session, category_name)
        if category is None:
            raise ValueError("Categoria padrao nao encontrada.")

        result = await session.execute(
            select(UserCategory)
            .where(UserCategory.user_id == user.id)
            .where(UserCategory.is_system_default == True)
            .where(UserCategory.base_category_id == category.id)
        )
        row = result.scalar_one_or_none()

        if row is None:
            row = UserCategory(
                user_id=user.id,
                name=category.name,
                type=category.type,
                is_active=is_active,
                is_system_default=True,
                base_category_id=category.id,
                created_at=datetime.now(),
            )
            session.add(row)
        else:
            row.is_active = is_active
            row.updated_at = datetime.now()

        await session.commit()
        await session.refresh(row)
        return row

    async def resolve_category_for_user(
        self,
        session: AsyncSession,
        user: User,
        category_name: str,
    ) -> tuple[Category, str | None]:
        """Resolve a category name to a persisted base category and optional custom label."""
        custom_row = await self.find_user_category_by_name(session, user, category_name)
        if custom_row is not None:
            if not custom_row.is_active:
                raise ValueError(f"Categoria '{category_name}' esta desativada para voce.")
            if custom_row.base_category_id is None:
                raise ValueError("Categoria personalizada sem categoria base configurada.")
            base_category = await session.get(Category, custom_row.base_category_id)
            if base_category is None:
                raise ValueError("Categoria base da categoria personalizada nao encontrada.")
            return base_category, custom_row.name

        category = await self._get_system_category_by_name(session, category_name)
        if category is None:
            raise ValueError(f"Categoria '{category_name}' nao encontrada")

        result = await session.execute(
            select(UserCategory)
            .where(UserCategory.user_id == user.id)
            .where(UserCategory.is_system_default == True)
            .where(UserCategory.base_category_id == category.id)
        )
        visibility = result.scalar_one_or_none()
        if visibility is not None and not visibility.is_active:
            raise ValueError(f"Categoria '{category_name}' esta desativada para voce.")

        return category, None

    async def find_user_category_by_name(
        self,
        session: AsyncSession,
        user: User,
        category_name: str,
    ) -> UserCategory | None:
        """Find a user category by name ignoring case and accents."""
        target = _normalize_name(category_name)
        result = await session.execute(select(UserCategory).where(UserCategory.user_id == user.id))
        rows = result.scalars().all()
        for row in rows:
            if _normalize_name(str(row.name)) == target:
                return row
        return None

    async def format_categories_message(
        self,
        session: AsyncSession,
        user: User,
    ) -> str:
        """Return a WhatsApp-friendly category list for the user."""
        payload = await self.list_available_categories(session, user)
        grouped_active: dict[str, list[str]] = defaultdict(list)
        grouped_inactive: dict[str, list[str]] = defaultdict(list)

        for item in payload["active"]:
            grouped_active[str(item["type"])].append(str(item["name"]))
        for item in payload["inactive"]:
            grouped_inactive[str(item["type"])].append(str(item["name"]))

        lines = ["Categorias disponiveis para voce:\n"]
        lines.append("GASTOS:")
        for name in grouped_active.get("Negativo", []):
            lines.append(f"  • {name}")
        lines.append("\nENTRADAS:")
        for name in grouped_active.get("Positivo", []):
            lines.append(f"  • {name}")

        if payload["inactive"]:
            lines.append("\nOCULTAS PARA VOCE:")
            for type_name, names in grouped_inactive.items():
                label = "Gastos" if type_name == "Negativo" else "Entradas"
                lines.append(f"{label}: {', '.join(names)}")

        return "\n".join(lines)

    async def _get_system_categories(self, session: AsyncSession) -> list[Category]:
        """Return system categories, excluding custom categories linked to users."""
        result = await session.execute(select(Category).order_by(Category.type, Category.name))
        return list(result.scalars().all())

    async def _get_user_category_rows(
        self,
        session: AsyncSession,
        user_id: int,
    ) -> list[UserCategory]:
        """Return all category customization rows for a user."""
        result = await session.execute(select(UserCategory).where(UserCategory.user_id == user_id))
        return list(result.scalars().all())

    async def _get_system_category_by_name(
        self,
        session: AsyncSession,
        category_name: str,
    ) -> Category | None:
        """Find a base system category by name ignoring case and accents."""
        target = _normalize_name(category_name)
        categories = await self._get_system_categories(session)
        for category in categories:
            if _normalize_name(str(category.name)) == target:
                return category
        return None

    async def _get_fallback_base_category(
        self,
        session: AsyncSession,
        category_type: str,
    ) -> Category:
        """Return the fallback system category used for custom expense persistence."""
        fallback_name = (
            DEFAULT_NEGATIVE_FALLBACK if category_type == "Negativo" else DEFAULT_POSITIVE_FALLBACK
        )
        category = await self._get_system_category_by_name(session, fallback_name)
        if category is not None:
            return category

        result = await session.execute(select(Category).where(Category.type == category_type))
        category = result.scalars().first()
        if category is None:
            raise ValueError("Categoria base fallback nao encontrada.")
        return category
