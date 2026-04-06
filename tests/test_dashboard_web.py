from unittest.mock import AsyncMock, patch

from fastapi import Response
from sqlalchemy import delete, select
from starlette.requests import Request

from app.database.connection import async_session, init_db
from app.database.models import Expense, User, UserOnboardingState, UserWebSession
from app.database.seed import seed_all
from app.main import (
    DashboardBaseCurrencyRequest,
    DashboardBudgetDeleteRequest,
    DashboardBudgetRequest,
    DashboardCurrencyConvertRequest,
    DashboardExpenseCreateRequest,
    DashboardExpenseRecognitionRequest,
    DashboardExpenseUpdateRequest,
    DashboardExportRequest,
    DashboardGoalDeleteRequest,
    DashboardGoalRequest,
    RegisterRequest,
    auth_register,
    dashboard_create_budget,
    dashboard_create_expense,
    dashboard_create_goal,
    dashboard_currency_convert,
    dashboard_delete_budget,
    dashboard_delete_goal,
    dashboard_export,
    dashboard_recognize_expense,
    dashboard_state,
    dashboard_update_base_currency,
    dashboard_update_expense,
    web_dashboard_page,
)


class TestDashboardWeb:
    """Tests for the authenticated financial dashboard."""

    async def _reset_real_db(self) -> None:
        await init_db()
        async with async_session() as session:
            await seed_all(session)
            await session.execute(delete(Expense))
            await session.execute(delete(UserWebSession))
            await session.execute(delete(UserOnboardingState))
            await session.execute(delete(User))
            await session.commit()

    @staticmethod
    def _request_with_cookie(path: str, cookie_value: str | None = None) -> Request:
        headers = []
        if cookie_value is not None:
            headers.append((b"cookie", f"finbot_session={cookie_value}".encode("latin-1")))

        async def receive() -> dict:
            return {"type": "http.request", "body": b"", "more_body": False}

        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode("utf-8"),
            "query_string": b"",
            "headers": headers,
            "client": ("testclient", 123),
            "server": ("testserver", 80),
        }
        return Request(scope, receive)

    @staticmethod
    def _extract_cookie(response: Response) -> str:
        return (
            response.headers.get("set-cookie", "")
            .split("finbot_session=", maxsplit=1)[1]
            .split(";", maxsplit=1)[0]
        )

    async def _register_dashboard_request(self, *, email: str, phone: str) -> Request:
        register_response = Response()
        await auth_register(
            RegisterRequest(
                name="Paula",
                email=email,
                password="senha-super-segura",
                phone=phone,
            ),
            register_response,
        )
        async with async_session() as session:
            result = await session.execute(select(User).where(User.phone == phone))
            user = result.scalar_one()
            user.accepted_terms = True
            user.onboarding_completed = True
            await session.commit()
        session_cookie = self._extract_cookie(register_response)
        return self._request_with_cookie("/web/dashboard", session_cookie)

    async def test_web_dashboard_redirects_when_unauthenticated(self):
        """Dashboard route should redirect unauthenticated browsers to login."""
        response = await web_dashboard_page(self._request_with_cookie("/web/dashboard"))

        assert response.status_code == 303
        assert response.headers["location"] == "/web/login"

    async def test_dashboard_state_and_base_currency_update(self):
        """Authenticated users should view dashboard state and change base currency."""
        await self._reset_real_db()
        request = await self._register_dashboard_request(
            email="dashboard@example.com",
            phone="5511910707070",
        )

        payload = await dashboard_state(request)
        assert payload["user"]["base_currency"] == "BRL"
        assert payload["summary"]["expense_count"] == 0
        assert any(item["code"] == "USD" for item in payload["currencies"])

        updated = await dashboard_update_base_currency(
            request,
            DashboardBaseCurrencyRequest(base_currency="USD"),
        )
        assert updated["base_currency"] == "USD"

        refreshed = await dashboard_state(request)
        assert refreshed["user"]["base_currency"] == "USD"

    async def test_dashboard_create_and_update_expense_with_currency_conversion(self):
        """Dashboard expenses should support non-BRL registration and editing."""
        await self._reset_real_db()
        request = await self._register_dashboard_request(
            email="expenses@example.com",
            phone="5511910808080",
        )

        with patch.object(
            __import__("app.main", fromlist=["currency_service"]).currency_service,
            "convert_to_brl",
            new=AsyncMock(
                return_value={
                    "success": True,
                    "converted_amount": 500,
                    "exchange_rate": 5,
                }
            ),
        ):
            created = await dashboard_create_expense(
                request,
                DashboardExpenseCreateRequest(
                    description="Hotel em Buenos Aires",
                    amount=100,
                    category="Viagem",
                    payment_method="Pix",
                    expense_date="2026-04-01",
                    currency="USD",
                ),
            )

        assert created["status"] == "ok"
        expense_id = created["expense_id"]

        payload = await dashboard_state(request, month=4, year=2026)
        assert payload["summary"]["expense_count"] == 1
        assert payload["expenses"][0]["original_currency"] == "USD"

        with patch.object(
            __import__("app.main", fromlist=["currency_service"]).currency_service,
            "convert_to_brl",
            new=AsyncMock(
                return_value={
                    "success": True,
                    "converted_amount": 600,
                    "exchange_rate": 5,
                }
            ),
        ):
            updated = await dashboard_update_expense(
                request,
                expense_id,
                DashboardExpenseUpdateRequest(
                    amount=120,
                    currency="USD",
                    description="Hotel em Buenos Aires - ajuste",
                    category="Viagem",
                    payment_method="Pix",
                    expense_date="2026-04-01",
                ),
            )

        assert updated["status"] == "ok"
        updated_state = await dashboard_state(request, month=4, year=2026)
        assert updated_state["expenses"][0]["description"] == "Hotel em Buenos Aires - ajuste"
        assert updated_state["expenses"][0]["amount"] == 600.0

    async def test_dashboard_budget_goal_export_and_conversion(self):
        """Dashboard should manage budgets, goals, exports and quick conversions."""
        await self._reset_real_db()
        request = await self._register_dashboard_request(
            email="planning@example.com",
            phone="5511910909090",
        )

        await dashboard_create_expense(
            request,
            DashboardExpenseCreateRequest(
                description="Mercado do mês",
                amount=250,
                category="Mercado",
                payment_method="Pix",
                expense_date="2026-04-02",
                currency="BRL",
            ),
        )

        budget = await dashboard_create_budget(
            request,
            DashboardBudgetRequest(category_name="Mercado", monthly_limit=800),
        )
        assert budget["status"] == "ok"

        goal = await dashboard_create_goal(
            request,
            DashboardGoalRequest(
                description="Reserva",
                target_amount=5000,
                deadline="2026-12-31",
            ),
        )
        assert goal["status"] == "ok"

        conversion = await dashboard_currency_convert(
            request,
            DashboardCurrencyConvertRequest(amount=100, from_currency="BRL", to_currency="BRL"),
        )
        assert conversion["converted_amount"] == 100.0
        assert conversion["target_currency"] == "BRL"

        export_payload = await dashboard_export(
            request,
            DashboardExportRequest(format="xlsx", month=4, year=2026),
        )
        assert export_payload["filename"].endswith(".xlsx")
        assert export_payload["mimetype"].startswith("application/")

        deleted_budget = await dashboard_delete_budget(
            request,
            DashboardBudgetDeleteRequest(category_name="Mercado"),
        )
        assert deleted_budget["status"] == "ok"

        deleted_goal = await dashboard_delete_goal(
            request,
            DashboardGoalDeleteRequest(description="Reserva"),
        )
        assert deleted_goal["status"] == "ok"

    async def test_dashboard_can_recognize_expense_from_image(self):
        """Dashboard should recognize receipt data from a pasted/uploaded image payload."""
        await self._reset_real_db()
        request = await self._register_dashboard_request(
            email="ocr@example.com",
            phone="5511911111111",
        )

        with patch.object(
            __import__("app.main", fromlist=["ai_service"]).ai_service,
            "process_image",
            new=AsyncMock(
                return_value={
                    "success": True,
                    "intent": "register_expense",
                    "data": {
                        "description": "Cafeteria Centro",
                        "amount": 32.5,
                        "category": "Alimentação",
                        "payment_method": "Pix",
                        "expense_date": "2026-04-03",
                        "currency": "BRL",
                    },
                }
            ),
        ):
            payload = await dashboard_recognize_expense(
                request,
                DashboardExpenseRecognitionRequest(
                    image_base64="data:image/png;base64,aGVsbG8=",
                    additional_text="considere a data impressa no comprovante",
                ),
            )

        assert payload["status"] == "ok"
        assert payload["recognized"]["description"] == "Cafeteria Centro"
        assert payload["recognized"]["amount"] == 32.5
        assert payload["recognized"]["payment_method"] == "Pix"
