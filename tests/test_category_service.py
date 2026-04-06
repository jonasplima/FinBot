"""Tests for user category customization."""

from app.services.category import CategoryService


class TestCategoryService:
    """Tests for custom and user-scoped category behavior."""

    async def test_create_custom_category_and_resolve_for_user(
        self,
        seeded_session,
        accepted_user_in_db,
    ):
        """Custom categories should resolve to a base category with a preserved custom label."""
        service = CategoryService()

        created = await service.create_custom_category(
            seeded_session,
            accepted_user_in_db,
            name="Pets",
            category_type="Negativo",
        )

        assert created.name == "Pets"
        assert created.base_category_id is not None

        base_category, custom_name = await service.resolve_category_for_user(
            seeded_session,
            accepted_user_in_db,
            "Pets",
        )

        assert base_category.name == "Outros"
        assert custom_name == "Pets"

    async def test_hide_system_category_for_user(
        self,
        seeded_session,
        accepted_user_in_db,
    ):
        """System categories can be hidden per user without global deletion."""
        service = CategoryService()

        await service.set_system_category_visibility(
            seeded_session,
            accepted_user_in_db,
            category_name="Lazer",
            is_active=False,
        )

        payload = await service.list_available_categories(seeded_session, accepted_user_in_db)

        assert any(item["name"] == "Lazer" for item in payload["inactive"])
        assert not any(item["name"] == "Lazer" for item in payload["active"])

    async def test_format_categories_message_includes_custom_entries(
        self,
        seeded_session,
        accepted_user_in_db,
    ):
        """The user-facing category list should reflect active custom categories."""
        service = CategoryService()

        await service.create_custom_category(
            seeded_session,
            accepted_user_in_db,
            name="Freelance",
            category_type="Positivo",
        )

        message = await service.format_categories_message(seeded_session, accepted_user_in_db)

        assert "Freelance" in message
        assert "Categorias disponiveis para voce" in message
