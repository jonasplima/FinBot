"""FinBot - WhatsApp Financial Assistant."""

import base64
import json
import logging
import secrets
from contextlib import asynccontextmanager
from datetime import date
from decimal import Decimal
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel
from redis.asyncio import Redis
from sqlalchemy import select, text

from app.config import get_settings
from app.database.connection import async_session, init_db
from app.database.models import PaymentMethod
from app.database.seed import seed_all
from app.services.admin_rate_limit import AdminRateLimitService
from app.services.ai import AIService
from app.services.auth import AuthService
from app.services.backup import BackupService
from app.services.budget import BudgetService
from app.services.category import CategoryService
from app.services.chart import ChartService
from app.services.credentials import CredentialService
from app.services.currency import SUPPORTED_CURRENCIES, CurrencyService
from app.services.expense import ExpenseService
from app.services.export import ExportService
from app.services.goal import GoalService
from app.services.onboarding import OnboardingService
from app.services.operational_status import OperationalStatusService
from app.services.rate_limit import RateLimitService
from app.services.user import UserService
from app.services.webhook_idempotency import WebhookIdempotencyService
from app.services.whatsapp_onboarding import WhatsAppOnboardingService

# Configure logging
logging.basicConfig(
    level=getattr(logging, get_settings().log_level.upper(), logging.INFO),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

settings = get_settings()
MESSAGE_EVENTS = {"messages.upsert", "messages_upssert", "message"}
operational_status = OperationalStatusService()
auth_service = AuthService()
onboarding_service = OnboardingService()
user_service = UserService()
whatsapp_onboarding_service = WhatsAppOnboardingService()
category_service = CategoryService()
backup_service = BackupService()
rate_limit_service = RateLimitService()
credential_service = CredentialService()
expense_service = ExpenseService()
budget_service = BudgetService()
goal_service = GoalService()
export_service = ExportService()
currency_service = CurrencyService()
chart_service = ChartService()
ai_service = AIService()


class RegisterRequest(BaseModel):
    """Payload for registering web access."""

    name: str
    email: str
    password: str
    phone: str


class LoginRequest(BaseModel):
    """Payload for authenticating web access."""

    email: str
    password: str


class OnboardingStepRequest(BaseModel):
    """Payload to update the current onboarding step."""

    current_step: str


class OnboardingProfileRequest(BaseModel):
    """Payload to update basic profile information during onboarding."""

    name: str | None = None
    display_name: str | None = None
    timezone: str


class SettingsProfileRequest(BaseModel):
    """Payload to update profile information after onboarding."""

    name: str
    display_name: str | None = None
    timezone: str
    email: str | None = None
    base_currency: str = "BRL"


class SettingsNotificationsRequest(BaseModel):
    """Payload to update user notification preferences."""

    budget_alerts: bool = True
    recurring_reminders: bool = True
    goal_updates: bool = True


class SettingsLimitsRequest(BaseModel):
    """Payload to update daily user limits."""

    limits_enabled: bool = True
    daily_text_limit: int
    daily_media_limit: int
    daily_ai_limit: int


class SettingsAuthorizedPhoneRequest(BaseModel):
    """Payload to add or remove an authorized WhatsApp number."""

    phone: str


class SettingsBackupImportRequest(BaseModel):
    """Payload to preview a backup restore from the web settings page."""

    backup_json: str


class SettingsBackupApplyRequest(BaseModel):
    """Payload to apply a previously previewed backup restore."""

    backup_ref: str
    explicit_migration_confirmation: bool = False


class OnboardingCategoryCreateRequest(BaseModel):
    """Payload to create a custom user category."""

    name: str
    type: str


class OnboardingCredentialRequest(BaseModel):
    """Payload to store or update a user-scoped provider credential."""

    provider: str
    api_key: str


class OnboardingCategoryVisibilityRequest(BaseModel):
    """Payload to toggle a system category for the current user."""

    category_name: str
    is_active: bool


class DashboardBaseCurrencyRequest(BaseModel):
    """Payload to update the user's preferred base currency."""

    base_currency: str


class DashboardExpenseCreateRequest(BaseModel):
    """Payload to create a new expense or income from the web dashboard."""

    description: str
    amount: float
    category: str
    payment_method: str
    expense_date: str | None = None
    currency: str = "BRL"
    is_shared: bool = False
    shared_percentage: float | None = None
    goal_id: int | None = None


class DashboardExpenseUpdateRequest(BaseModel):
    """Payload to update an existing expense from the web dashboard."""

    description: str | None = None
    amount: float | None = None
    category: str | None = None
    payment_method: str | None = None
    expense_date: str | None = None
    currency: str | None = None
    is_shared: bool | None = None
    shared_percentage: float | None = None
    goal_id: int | None = None


class DashboardExpenseRecognitionRequest(BaseModel):
    """Payload to recognize expense data from an uploaded or pasted image."""

    image_base64: str
    additional_text: str | None = None


class DashboardBudgetRequest(BaseModel):
    """Payload to create or update a category budget."""

    category_name: str | None = None
    monthly_limit: float


class DashboardBudgetDeleteRequest(BaseModel):
    """Payload to remove a budget."""

    category_name: str | None = None


class DashboardGoalRequest(BaseModel):
    """Payload to create a new savings goal."""

    description: str
    target_amount: float
    deadline: str


class DashboardGoalContributionRequest(BaseModel):
    """Payload to add money to a goal from the dashboard."""

    goal_id: int
    amount: float
    description: str | None = None
    transaction_date: str | None = None


class DashboardGoalWithdrawalRequest(BaseModel):
    """Payload to use money from a goal and register the destination expense."""

    goal_id: int
    amount: float
    category: str
    payment_method: str
    expense_date: str | None = None
    description: str | None = None


class DashboardGoalDeleteRequest(BaseModel):
    """Payload to remove an active goal."""

    description: str


class DashboardExportRequest(BaseModel):
    """Payload to export the selected month in XLSX or PDF."""

    format: str
    month: int | None = None
    year: int | None = None


class DashboardCurrencyConvertRequest(BaseModel):
    """Payload to convert values in the web dashboard."""

    amount: float
    from_currency: str
    to_currency: str = "BRL"


def _bearer_matches(secret_value: str, authorization: str | None) -> bool:
    """Validate a bearer token using constant-time comparison."""
    if not secret_value or not authorization:
        return False

    expected = f"Bearer {secret_value}"
    return secrets.compare_digest(authorization, expected)


def _is_valid_webhook_authorization(authorization: str | None) -> bool:
    """Validate webhook Authorization header."""
    return _bearer_matches(settings.webhook_secret, authorization)


def _is_valid_admin_authorization(authorization: str | None) -> bool:
    """Validate admin Authorization header."""
    return _bearer_matches(settings.admin_secret, authorization)


def _is_message_event(event: str) -> bool:
    """Check whether the webhook event carries a user message."""
    normalized = event.lower()
    return normalized in MESSAGE_EVENTS or normalized == "messages_upsert"


def _extract_webhook_message_id(body: dict) -> str:
    """Extract message ID from webhook payload when available."""
    return str(body.get("data", {}).get("key", {}).get("id", "")).strip()


async def _enforce_admin_rate_limit(request: Request) -> None:
    """Apply rate limiting to administrative endpoints."""
    client_host = request.client.host if request.client else "unknown"
    path = request.url.path if request.url else "unknown"
    identifier = f"{client_host}:{path}"
    service = AdminRateLimitService()

    try:
        result = await service.check_request(identifier)
    except RuntimeError:
        operational_status.record_event(
            "admin_rate_limit",
            "error",
            "Administrative protection unavailable because shared storage is down.",
        )
        raise HTTPException(
            status_code=503,
            detail="A protecao administrativa esta temporariamente indisponivel.",
        )

    if not result["allowed"]:
        logger.warning("Admin rate limit exceeded for %s", identifier)
        operational_status.record_event(
            "admin_rate_limit",
            "warning",
            f"Administrative rate limit exceeded for {identifier}.",
        )
        raise HTTPException(
            status_code=429,
            detail="Muitas tentativas no endpoint administrativo. Tente novamente em instantes.",
            headers={"Retry-After": str(result["retry_after"])},
        )


async def _build_health_payload(
    include_dependencies: bool = True,
) -> tuple[dict[str, Any], int]:
    """Build liveness/readiness payload with dependency details when requested."""
    payload: dict[str, Any] = {
        "status": "healthy",
        "app": "FinBot",
        "version": "1.0.0",
        "deployment_mode": settings.normalized_deployment_mode,
    }
    if not include_dependencies:
        payload["recent_events"] = operational_status.get_recent_events()
        return payload, 200

    checks: dict[str, str] = {}
    status_code = 200

    try:
        async with async_session() as session:
            await session.execute(text("SELECT 1"))
        checks["database"] = "healthy"
    except Exception as exc:
        logger.error(f"Readiness check failed for database: {exc}")
        checks["database"] = "unhealthy"
        status_code = 503

    try:
        redis_client = Redis.from_url(settings.redis_url, decode_responses=True)
        await redis_client.ping()
        await redis_client.aclose()
        checks["redis"] = "healthy"
    except Exception as exc:
        logger.error(f"Readiness check failed for redis: {exc}")
        checks["redis"] = "unhealthy"
        status_code = 503

    try:
        from app.services.evolution import EvolutionService

        evolution = EvolutionService()
        await evolution.get_connection_state()
        checks["evolution"] = "healthy"
    except Exception as exc:
        logger.error(f"Readiness check failed for evolution: {exc}")
        checks["evolution"] = "unhealthy"
        status_code = 503

    payload["checks"] = checks
    payload["status"] = "healthy" if status_code == 200 else "degraded"
    payload["recent_events"] = operational_status.get_recent_events()
    return payload, status_code


def _get_session_cookie_token(request: Request) -> str | None:
    """Read the authenticated browser session token from cookies."""
    cookie_value = request.cookies.get(auth_service.build_session_cookie_settings()["key"], "")
    normalized = cookie_value.strip()
    return normalized or None


async def _get_current_web_user(request: Request) -> Any:
    """Resolve the current authenticated web user from the session cookie."""
    session_token = _get_session_cookie_token(request)
    if not session_token:
        raise HTTPException(status_code=401, detail="Sessao web nao autenticada.")

    async with async_session() as session:
        user = await auth_service.get_user_by_session_token(session, session_token)
    if user is None:
        raise HTTPException(status_code=401, detail="Sessao web invalida ou expirada.")
    return user


async def _get_current_web_user_in_session(session: Any, request: Request) -> Any:
    """Resolve the current authenticated web user using an existing DB session."""
    session_token = _get_session_cookie_token(request)
    if not session_token:
        raise HTTPException(status_code=401, detail="Sessao web nao autenticada.")

    user = await auth_service.get_user_by_session_token(session, session_token)
    if user is None:
        raise HTTPException(status_code=401, detail="Sessao web invalida ou expirada.")
    return user


async def _get_onboarding_user_and_state(session: Any, request: Request) -> tuple[Any, Any]:
    """Resolve the authenticated onboarding user and its onboarding state."""
    user = await _get_current_web_user_in_session(session, request)
    state = await onboarding_service.get_or_create_state(session, user)
    return user, state


async def _build_onboarding_payload(session: Any, user: Any, state: Any) -> dict[str, Any]:
    """Build the onboarding payload enriched with a review summary."""
    payload = onboarding_service.build_state_payload(user, state)
    credential_summary = await credential_service.list_user_credentials(session, user)
    category_summary = await category_service.list_available_categories(session, user)

    configured_providers = [
        str(config["label"]) for config in credential_summary.values() if bool(config["configured"])
    ]
    custom_categories = sorted(
        [str(item["name"]) for item in category_summary.get("custom", [])],
    )
    hidden_categories = sorted(
        [
            str(item["name"])
            for item in category_summary.get("inactive", [])
            if not item["is_custom"]
        ],
    )

    payload["review"] = {
        "configured_providers": configured_providers,
        "custom_categories": custom_categories,
        "hidden_categories": hidden_categories,
    }
    return payload


def _build_settings_payload(
    user: Any,
    usage_summary: dict[str, Any],
    *,
    authorized_phones: list[dict[str, str | bool]] | None = None,
) -> dict[str, Any]:
    """Build the web settings payload returned to the authenticated user."""
    preferences = dict(user.notification_preferences or {})
    return {
        "user": {
            "id": user.id,
            "phone": user.phone,
            "name": user.name,
            "display_name": user.display_name,
            "email": user.email,
            "timezone": user.timezone,
            "base_currency": getattr(user, "base_currency", "BRL"),
            "accepted_terms": user.accepted_terms,
            "accepted_terms_at": user.accepted_terms_at.isoformat()
            if user.accepted_terms_at
            else None,
            "terms_version": user.terms_version,
            "onboarding_completed": user.onboarding_completed,
        },
        "notifications": {
            "whatsapp": preferences.get("whatsapp", True),
            "budget_alerts": preferences.get("budget_alerts", True),
            "recurring_reminders": preferences.get("recurring_reminders", True),
            "goal_updates": preferences.get("goal_updates", True),
        },
        "limits": {
            "limits_enabled": user.limits_enabled,
            "daily_text_limit": user.daily_text_limit,
            "daily_media_limit": user.daily_media_limit,
            "daily_ai_limit": user.daily_ai_limit,
            "usage": usage_summary,
        },
        "backup": {
            "backup_owner_id": user.backup_owner_id,
        },
        "authorized_phones": authorized_phones or [{"phone": user.phone, "is_primary": True}],
        "currencies": _supported_currency_options(),
    }


def _supported_currency_options() -> list[dict[str, str]]:
    """Return the list of currencies available in the dashboard UI."""
    options = [{"code": "BRL", "name": "Real Brasileiro"}]
    for code, metadata in sorted(SUPPORTED_CURRENCIES.items()):
        options.append({"code": code, "name": str(metadata["name"])})
    return options


def _normalize_currency_code(currency_code: str | None, fallback: str = "BRL") -> str:
    """Normalize and validate a currency code coming from the browser."""
    normalized_code = (currency_code or fallback).strip().upper()
    if normalized_code == "BRL":
        return "BRL"
    if normalized_code not in SUPPORTED_CURRENCIES:
        raise HTTPException(status_code=400, detail="Moeda nao suportada.")
    return normalized_code


def _decode_base64_media_payload(data_url_or_base64: str) -> bytes:
    """Decode a base64 payload sent by the browser, supporting data URLs."""
    normalized = (data_url_or_base64 or "").strip()
    if not normalized:
        raise HTTPException(status_code=400, detail="Imagem nao enviada.")
    if "," in normalized and normalized.startswith("data:"):
        normalized = normalized.split(",", maxsplit=1)[1]
    try:
        return base64.b64decode(normalized, validate=True)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Imagem invalida ou corrompida.") from exc


def _resolve_dashboard_period(
    month: int | None,
    year: int | None,
) -> tuple[int, int]:
    """Resolve the requested dashboard month/year, defaulting to today."""
    today = date.today()
    resolved_month = month or today.month
    resolved_year = year or today.year
    if resolved_month < 1 or resolved_month > 12:
        raise HTTPException(status_code=400, detail="Mes invalido.")
    if resolved_year < 2000 or resolved_year > 2100:
        raise HTTPException(status_code=400, detail="Ano invalido.")
    return resolved_month, resolved_year


def _png_data_uri(image_bytes: bytes) -> str:
    """Encode chart bytes as a PNG data URI for the dashboard."""
    return f"data:image/png;base64,{base64.b64encode(image_bytes).decode('ascii')}"


async def _list_payment_methods(session: Any) -> list[str]:
    """Return all payment method names for the dashboard forms."""
    result = await session.execute(select(PaymentMethod).order_by(PaymentMethod.name))
    return [str(item.name) for item in result.scalars().all()]


async def _build_dashboard_payload(
    session: Any,
    user: Any,
    *,
    month: int | None = None,
    year: int | None = None,
) -> dict[str, Any]:
    """Build the authenticated dashboard state payload."""
    resolved_month, resolved_year = _resolve_dashboard_period(month, year)
    expenses = await expense_service.list_expenses(
        session,
        user.phone,
        month=resolved_month,
        year=resolved_year,
    )
    budgets_result = await budget_service.list_budgets(session, user.phone)
    goals_result = await goal_service.list_goals(session, user.phone)
    goal_transactions = await goal_service.list_goal_transactions(session, user.phone)
    category_payload = await category_service.list_available_categories(
        session,
        user,
        include_inactive=True,
    )
    payment_methods = await _list_payment_methods(session)
    category_chart = _png_data_uri(
        chart_service.generate_pie_chart(
            await expense_service.get_expenses_by_category(
                session,
                user.phone,
                month=resolved_month,
                year=resolved_year,
            ),
            title="Gastos por categoria",
        )
    )
    top_expense_chart = _png_data_uri(
        chart_service.generate_bar_chart(
            await expense_service.get_top_expenses(
                session,
                user.phone,
                month=resolved_month,
                year=resolved_year,
            ),
            title="Maiores gastos do período",
        )
    )
    daily_chart = _png_data_uri(
        chart_service.generate_line_chart(
            await expense_service.get_daily_totals(
                session,
                user.phone,
                month=resolved_month,
                year=resolved_year,
            ),
            title="Evolução diária dos gastos",
        )
    )

    total_income = sum(
        float(item.get("amount") or 0) for item in expenses if str(item.get("type")) == "Positivo"
    )
    total_expenses = sum(
        float(item.get("amount") or 0) for item in expenses if str(item.get("type")) == "Negativo"
    )

    return {
        "period": {"month": resolved_month, "year": resolved_year},
        "user": {
            "phone": user.phone,
            "name": user.name,
            "display_name": user.display_name,
            "timezone": user.timezone,
            "base_currency": getattr(user, "base_currency", "BRL"),
        },
        "summary": {
            "income": round(total_income, 2),
            "expenses": round(total_expenses, 2),
            "balance": round(total_income - total_expenses, 2),
            "expense_count": len(expenses),
        },
        "expenses": expenses,
        "budgets": budgets_result.get("budgets", []),
        "goals": goals_result.get("goals", []),
        "goal_transactions": goal_transactions,
        "categories": category_payload,
        "payment_methods": payment_methods,
        "charts": {
            "categories": category_chart,
            "top_expenses": top_expense_chart,
            "daily": daily_chart,
        },
        "currencies": _supported_currency_options(),
    }


def _set_web_session_cookie(response: Response, session_token: str) -> None:
    """Attach the FinBot web session cookie to a response."""
    cookie_settings = auth_service.build_session_cookie_settings()
    response.set_cookie(
        cookie_settings["key"],
        session_token,
        httponly=cookie_settings["httponly"],
        samesite=cookie_settings["samesite"],
        secure=cookie_settings["secure"],
        max_age=cookie_settings["max_age"],
        path=cookie_settings["path"],
    )


def _clear_web_session_cookie(response: Response) -> None:
    """Clear the FinBot web session cookie."""
    cookie_settings = auth_service.build_session_cookie_settings()
    response.delete_cookie(
        cookie_settings["key"],
        path=cookie_settings["path"],
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    # Startup
    logger.info("Starting FinBot...")

    # Initialize database
    logger.info("Initializing database...")
    await init_db()

    # Seed data
    async with async_session() as session:
        await seed_all(session)

    # Initialize Evolution API instance
    from app.services.evolution import EvolutionService

    evolution = EvolutionService()
    try:
        await evolution.setup_instance()
        logger.info("Evolution API instance ready")
    except Exception as e:
        logger.warning(f"Could not setup Evolution instance: {e}")
        operational_status.record_event(
            "startup",
            "warning",
            "Evolution API setup failed during startup; application is running in degraded mode.",
        )

    # Start scheduler for recurring expenses
    from app.services.scheduler import get_scheduler_service

    scheduler = get_scheduler_service()
    scheduler.start()

    logger.info("FinBot started successfully!")

    yield

    # Shutdown
    logger.info("Shutting down FinBot...")
    scheduler.shutdown()


# Create FastAPI app
app = FastAPI(
    title="FinBot",
    description="WhatsApp Financial Assistant",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health_check():
    """Readiness endpoint with dependency checks."""
    payload, status_code = await _build_health_payload(include_dependencies=True)
    return JSONResponse(status_code=status_code, content=payload)


@app.get("/health/live")
async def health_live():
    """Liveness endpoint for process supervision."""
    payload, status_code = await _build_health_payload(include_dependencies=False)
    return JSONResponse(status_code=status_code, content=payload)


@app.get("/health/ready")
async def health_ready():
    """Explicit readiness endpoint with dependency checks."""
    payload, status_code = await _build_health_payload(include_dependencies=True)
    return JSONResponse(status_code=status_code, content=payload)


@app.post("/auth/register")
async def auth_register(payload: RegisterRequest, response: Response):
    """Register web access for a user and open a browser session."""
    async with async_session() as session:
        try:
            user, session_token = await auth_service.register_user(
                session,
                name=payload.name,
                email=payload.email,
                password=payload.password,
                phone=payload.phone,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    _set_web_session_cookie(response, session_token)
    return {
        "status": "ok",
        "user": auth_service.serialize_user(user),
    }


@app.post("/auth/login")
async def auth_login(payload: LoginRequest, response: Response):
    """Authenticate a web user and open a browser session."""
    async with async_session() as session:
        try:
            user, session_token = await auth_service.login_user(
                session,
                email=payload.email,
                password=payload.password,
            )
        except ValueError:
            raise HTTPException(status_code=401, detail="Email ou senha invalidos.")

    _set_web_session_cookie(response, session_token)
    return {
        "status": "ok",
        "user": auth_service.serialize_user(user),
    }


@app.post("/auth/logout")
async def auth_logout(request: Request, response: Response):
    """Revoke the current browser session."""
    session_token = _get_session_cookie_token(request)
    if session_token:
        async with async_session() as session:
            await auth_service.logout_session(session, session_token)
    _clear_web_session_cookie(response)
    return {"status": "ok"}


@app.get("/auth/me")
async def auth_me(request: Request):
    """Return the current authenticated web user."""
    user = await _get_current_web_user(request)
    return {
        "status": "ok",
        "user": auth_service.serialize_user(user),
    }


@app.get("/web/login", response_class=HTMLResponse)
async def web_login_page():
    """Render the initial web access page for login or registration."""
    return HTMLResponse(
        content="""
        <!DOCTYPE html>
        <html lang="pt-BR">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>FinBot • Acesso Web</title>
            <link rel="preconnect" href="https://fonts.googleapis.com">
            <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
            <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600;800&display=swap" rel="stylesheet">
            <style>
                :root {
                    --bg: #07111f;
                    --panel: rgba(12, 24, 42, 0.85);
                    --panel-border: rgba(255, 255, 255, 0.08);
                    --primary: #14b8a6;
                    --primary-strong: #0f766e;
                    --text: #f8fafc;
                    --muted: #94a3b8;
                    --danger: #fb7185;
                }
                * { box-sizing: border-box; }
                body {
                    margin: 0;
                    min-height: 100vh;
                    font-family: 'Outfit', sans-serif;
                    background:
                        radial-gradient(circle at top left, rgba(20, 184, 166, 0.18), transparent 35%),
                        radial-gradient(circle at bottom right, rgba(59, 130, 246, 0.16), transparent 30%),
                        linear-gradient(180deg, #020617 0%, #07111f 100%);
                    color: var(--text);
                    display: grid;
                    place-items: center;
                    padding: 24px;
                }
                .shell {
                    width: min(1080px, 100%);
                    display: grid;
                    grid-template-columns: 1.1fr 0.9fr;
                    gap: 24px;
                }
                .hero, .panel {
                    background: var(--panel);
                    border: 1px solid var(--panel-border);
                    border-radius: 28px;
                    backdrop-filter: blur(16px);
                    padding: 32px;
                    box-shadow: 0 24px 70px rgba(0, 0, 0, 0.35);
                }
                .hero h1 {
                    font-size: clamp(2rem, 3vw, 3.2rem);
                    line-height: 1;
                    margin: 0 0 16px 0;
                    letter-spacing: -0.04em;
                }
                .hero p {
                    color: var(--muted);
                    font-size: 1.02rem;
                    line-height: 1.7;
                }
                .feature-list {
                    display: grid;
                    gap: 12px;
                    margin-top: 24px;
                }
                .feature {
                    display: flex;
                    gap: 12px;
                    align-items: flex-start;
                    padding: 14px 16px;
                    background: rgba(255, 255, 255, 0.03);
                    border: 1px solid rgba(255, 255, 255, 0.05);
                    border-radius: 16px;
                }
                .feature strong { display: block; margin-bottom: 4px; }
                .feature span { color: var(--muted); font-size: 0.95rem; }
                .tabs {
                    display: grid;
                    grid-template-columns: 1fr 1fr;
                    gap: 8px;
                    margin-bottom: 20px;
                    background: rgba(255, 255, 255, 0.03);
                    border-radius: 16px;
                    padding: 6px;
                }
                .tab {
                    border: 0;
                    border-radius: 12px;
                    background: transparent;
                    color: var(--muted);
                    font: inherit;
                    font-weight: 600;
                    padding: 12px 14px;
                    cursor: pointer;
                }
                .tab.active {
                    background: rgba(20, 184, 166, 0.16);
                    color: var(--text);
                }
                form { display: grid; gap: 12px; }
                .hidden { display: none; }
                label {
                    font-size: 0.92rem;
                    color: var(--muted);
                    display: grid;
                    gap: 8px;
                }
                input {
                    width: 100%;
                    border: 1px solid rgba(255, 255, 255, 0.08);
                    background: rgba(255, 255, 255, 0.04);
                    color: var(--text);
                    border-radius: 14px;
                    padding: 14px 16px;
                    font: inherit;
                }
                button[type="submit"] {
                    margin-top: 10px;
                    border: 0;
                    border-radius: 16px;
                    padding: 14px 16px;
                    font: inherit;
                    font-weight: 700;
                    color: white;
                    background: linear-gradient(135deg, var(--primary), var(--primary-strong));
                    cursor: pointer;
                }
                .hint { color: var(--muted); font-size: 0.9rem; line-height: 1.6; }
                .status { min-height: 24px; font-size: 0.92rem; margin-top: 8px; }
                .status.error { color: var(--danger); }
                .status.ok { color: #34d399; }
                @media (max-width: 920px) {
                    .shell { grid-template-columns: 1fr; }
                }
            </style>
        </head>
        <body>
            <div class="shell">
                <section class="hero">
                    <p style="letter-spacing: 0.12em; text-transform: uppercase; font-size: 0.8rem;">FinBot Web</p>
                    <h1>Configure sua conta e conecte seu WhatsApp no navegador.</h1>
                    <p>
                        Esta interface prepara seu acesso ao painel futuro, protege suas credenciais e guia o onboarding
                        sem exigir ferramentas externas para QR Code, headers ou chamadas manuais.
                    </p>
                    <div class="feature-list">
                        <div class="feature">
                            <div>01</div>
                            <div><strong>Acesso protegido</strong><span>Crie sua conta web com email e senha.</span></div>
                        </div>
                        <div class="feature">
                            <div>02</div>
                            <div><strong>Credenciais próprias</strong><span>Use suas chaves de IA e câmbio com fallback da instância.</span></div>
                        </div>
                        <div class="feature">
                            <div>03</div>
                            <div><strong>Onboarding assistido</strong><span>Conecte o WhatsApp e acompanhe o progresso em uma jornada única.</span></div>
                        </div>
                    </div>
                </section>

                <section class="panel">
                    <div class="tabs">
                        <button class="tab active" id="tab-register" type="button">Criar acesso</button>
                        <button class="tab" id="tab-login" type="button">Entrar</button>
                    </div>

                    <form id="register-form">
                        <label>Nome
                            <input name="name" placeholder="Como devemos te chamar?" required>
                        </label>
                        <label>Email
                            <input name="email" type="email" placeholder="voce@exemplo.com" required>
                        </label>
                        <label>Senha
                            <input name="password" type="password" placeholder="Minimo de 8 caracteres" required>
                        </label>
                        <label>Telefone WhatsApp
                            <input name="phone" placeholder="5511999999999" required>
                        </label>
                        <button type="submit">Criar acesso e continuar</button>
                        <div class="hint">Seu acesso web sera usado tambem no painel futuro de finanças.</div>
                    </form>

                    <form id="login-form" class="hidden">
                        <label>Email
                            <input name="email" type="email" placeholder="voce@exemplo.com" required>
                        </label>
                        <label>Senha
                            <input name="password" type="password" placeholder="Sua senha" required>
                        </label>
                        <button type="submit">Entrar</button>
                        <div class="hint">Ao entrar, voce continua do ponto em que parou no onboarding.</div>
                    </form>

                    <div class="status" id="status"></div>
                </section>
            </div>

            <script>
                const registerTab = document.getElementById('tab-register');
                const loginTab = document.getElementById('tab-login');
                const registerForm = document.getElementById('register-form');
                const loginForm = document.getElementById('login-form');
                const statusEl = document.getElementById('status');

                function setMode(mode) {
                    const isRegister = mode === 'register';
                    registerTab.classList.toggle('active', isRegister);
                    loginTab.classList.toggle('active', !isRegister);
                    registerForm.classList.toggle('hidden', !isRegister);
                    loginForm.classList.toggle('hidden', isRegister);
                    statusEl.textContent = '';
                    statusEl.className = 'status';
                }

                registerTab.addEventListener('click', () => setMode('register'));
                loginTab.addEventListener('click', () => setMode('login'));

                async function submitJson(url, payload) {
                    const response = await fetch(url, {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify(payload),
                        credentials: 'same-origin',
                    });
                    const rawText = await response.text();
                    let data = null;
                    if (rawText) {
                        try {
                            data = JSON.parse(rawText);
                        } catch {
                            data = null;
                        }
                    }
                    if (!response.ok) {
                        const fallbackMessage = rawText && !data
                            ? `Erro interno ao processar a solicitação (${response.status}).`
                            : `Erro HTTP ${response.status}.`;
                        throw new Error(data?.detail || data?.message || fallbackMessage);
                    }
                    return data ?? {};
                }

                registerForm.addEventListener('submit', async (event) => {
                    event.preventDefault();
                    const formData = new FormData(registerForm);
                    try {
                        statusEl.textContent = 'Criando seu acesso...';
                        statusEl.className = 'status';
                        await submitJson('/auth/register', Object.fromEntries(formData.entries()));
                        window.location.href = '/web/onboarding';
                    } catch (error) {
                        statusEl.textContent = error.message;
                        statusEl.className = 'status error';
                    }
                });

                loginForm.addEventListener('submit', async (event) => {
                    event.preventDefault();
                    const formData = new FormData(loginForm);
                    try {
                        statusEl.textContent = 'Validando acesso...';
                        statusEl.className = 'status';
                        await submitJson('/auth/login', Object.fromEntries(formData.entries()));
                        window.location.href = '/web/onboarding';
                    } catch (error) {
                        statusEl.textContent = error.message;
                        statusEl.className = 'status error';
                    }
                });
            </script>
        </body>
        </html>
        """
    )


@app.get("/web/onboarding", response_class=HTMLResponse)
async def web_onboarding_page(request: Request):
    """Render the protected onboarding shell for authenticated users."""
    try:
        user = await _get_current_web_user(request)
    except HTTPException:
        return RedirectResponse(url="/web/login", status_code=303)
    if getattr(user, "onboarding_completed", False):
        return RedirectResponse(url="/web/dashboard", status_code=303)

    return HTMLResponse(
        content="""
        <!DOCTYPE html>
        <html lang="pt-BR">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>FinBot • Onboarding</title>
            <link rel="preconnect" href="https://fonts.googleapis.com">
            <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
            <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600;800&display=swap" rel="stylesheet">
            <style>
                :root {
                    --line: #dce3ef;
                    --text: #0f172a;
                    --muted: #64748b;
                    --accent: #0f766e;
                    --accent-soft: #ccfbf1;
                    --danger-soft: #ffe4e6;
                    --warning-soft: #fff7ed;
                }
                * { box-sizing: border-box; }
                body {
                    margin: 0;
                    font-family: 'Outfit', sans-serif;
                    background:
                        radial-gradient(circle at top left, rgba(20, 184, 166, 0.08), transparent 20%),
                        linear-gradient(180deg, #f8fafc 0%, #eef4fb 100%);
                    color: var(--text);
                }
                .layout {
                    display: grid;
                    grid-template-columns: 320px 1fr;
                    min-height: 100vh;
                }
                aside {
                    padding: 32px 24px;
                    border-right: 1px solid var(--line);
                    background: rgba(255, 255, 255, 0.72);
                    backdrop-filter: blur(16px);
                }
                main {
                    padding: 32px;
                    display: grid;
                    gap: 20px;
                }
                .brand h1 { margin: 0 0 8px 0; font-size: 1.9rem; }
                .brand p { color: var(--muted); line-height: 1.6; }
                .step-list { display: grid; gap: 10px; margin-top: 28px; }
                .step {
                    border: 1px solid var(--line);
                    border-radius: 16px;
                    background: white;
                    padding: 14px 16px;
                }
                .step.active {
                    border-color: var(--accent);
                    background: linear-gradient(180deg, #ffffff 0%, var(--accent-soft) 100%);
                }
                .step.done {
                    border-color: #99f6e4;
                    background: #f0fdfa;
                }
                .step small { color: var(--muted); display: block; margin-top: 4px; }
                .card {
                    background: #ffffff;
                    border: 1px solid var(--line);
                    border-radius: 24px;
                    padding: 28px;
                    box-shadow: 0 18px 60px rgba(15, 23, 42, 0.06);
                }
                .card h2 { margin-top: 0; font-size: 1.7rem; }
                .card p { color: var(--muted); line-height: 1.7; }
                .grid {
                    display: grid;
                    grid-template-columns: repeat(2, minmax(0, 1fr));
                    gap: 16px;
                }
                .stat {
                    border: 1px solid var(--line);
                    border-radius: 18px;
                    padding: 18px;
                    background: #fbfdff;
                }
                .stat strong { display: block; font-size: 1.15rem; margin-bottom: 6px; }
                .wizard-step { display: none; }
                .wizard-step.active { display: grid; gap: 16px; }
                .controls, .step-actions { display: flex; flex-wrap: wrap; gap: 12px; }
                button {
                    border: 0;
                    border-radius: 14px;
                    padding: 12px 16px;
                    font: inherit;
                    font-weight: 700;
                    cursor: pointer;
                }
                .primary { background: var(--accent); color: white; }
                .ghost { background: #e2e8f0; color: #0f172a; }
                form { display: grid; gap: 12px; }
                label { display: grid; gap: 8px; color: var(--muted); font-size: 0.92rem; }
                input, select {
                    border: 1px solid var(--line);
                    border-radius: 14px;
                    padding: 14px 16px;
                    font: inherit;
                    background: white;
                }
                .status {
                    min-height: 24px;
                    color: var(--muted);
                    font-size: 0.92rem;
                }
                .status.error { color: #be123c; }
                .notice {
                    padding: 16px;
                    border-radius: 16px;
                    background: var(--danger-soft);
                    color: #9f1239;
                    border: 1px solid #fecdd3;
                }
                .warning {
                    padding: 16px;
                    border-radius: 16px;
                    background: var(--warning-soft);
                    color: #9a3412;
                    border: 1px solid #fed7aa;
                }
                .pill-list {
                    display: flex;
                    flex-wrap: wrap;
                    gap: 10px;
                }
                .category-list {
                    display: grid;
                    gap: 10px;
                    padding-left: 18px;
                    margin: 0;
                }
                .category-item {
                    display: flex;
                    flex-wrap: wrap;
                    justify-content: space-between;
                    gap: 12px;
                    align-items: center;
                }
                .category-meta {
                    display: grid;
                    gap: 4px;
                }
                .category-meta strong {
                    font-size: 0.96rem;
                }
                .category-meta small {
                    color: var(--muted);
                }
                .pill {
                    border: 1px solid var(--line);
                    border-radius: 999px;
                    padding: 8px 12px;
                    background: #fff;
                    font-size: 0.92rem;
                }
                .pill.inactive {
                    background: #e2e8f0;
                    color: var(--muted);
                }
                .qr-shell { display: grid; gap: 16px; }
                .qr-frame {
                    border: 1px dashed var(--line);
                    border-radius: 20px;
                    padding: 20px;
                    min-height: 280px;
                    display: grid;
                    place-items: center;
                    background: #fbfdff;
                }
                .qr-frame img {
                    max-width: min(100%, 320px);
                    border-radius: 18px;
                    background: white;
                    padding: 12px;
                    border: 1px solid var(--line);
                }
                .mono {
                    font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
                    font-size: 0.9rem;
                }
                a.inline-link {
                    color: var(--accent);
                    text-decoration: none;
                    font-weight: 600;
                    word-break: break-all;
                }
                a.inline-link:hover {
                    text-decoration: underline;
                }
                @media (max-width: 960px) {
                    .layout { grid-template-columns: 1fr; }
                    aside { border-right: 0; border-bottom: 1px solid var(--line); }
                    .grid { grid-template-columns: 1fr; }
                }
            </style>
        </head>
        <body>
            <div class="layout">
                <aside>
                    <div class="brand">
                        <h1>FinBot Setup</h1>
                        <p>Um fluxo simples, uma etapa por vez, para você sair do cadastro web até o WhatsApp conectado sem confusão.</p>
                    </div>
                    <div class="step-list" id="step-list"></div>
                </aside>
                <main>
                    <section class="card">
                        <h2 id="welcome-title">Carregando seu onboarding...</h2>
                        <p id="welcome-copy">Estamos preparando a sua etapa atual.</p>
                        <div class="grid" id="stats"></div>
                    </section>

                    <section class="card">
                        <div id="step-terms" class="wizard-step">
                            <h2>1. Leia e aceite os termos</h2>
                            <p>Antes de qualquer conexão com o WhatsApp, precisamos do seu aceite explícito.</p>
                            <div class="notice">
                                Esta instância é self-hosted. O operador da infraestrutura pode ter acesso técnico ou operacional aos dados trafegados pela stack, dependendo da configuração e do nível de acesso ao ambiente.
                            </div>
                            <div class="warning">
                                Só depois do aceite a preparação do WhatsApp será liberada.
                            </div>
                            <div class="step-actions">
                                <button class="primary" id="accept-terms" type="button">Aceitar termos e continuar</button>
                                <button class="ghost" id="reject-terms" type="button">Recusar termos</button>
                                <button class="ghost" id="logout" type="button">Sair</button>
                            </div>
                            <div class="status" id="terms-status"></div>
                        </div>

                        <div id="step-api_keys" class="wizard-step">
                            <h2>2. Configure suas chaves de API</h2>
                            <p>Você pode usar suas próprias credenciais. Se não configurar uma chave, o sistema pode usar o fallback da instância quando existir.</p>
                            <div class="grid">
                                <div class="stat"><strong>Google Gemini</strong><span>Consiga sua chave em <a class="inline-link mono" href="https://aistudio.google.com/apikey" target="_blank" rel="noopener noreferrer">https://aistudio.google.com/apikey</a></span></div>
                                <div class="stat"><strong>Groq</strong><span>Consiga sua chave em <a class="inline-link mono" href="https://console.groq.com/keys" target="_blank" rel="noopener noreferrer">https://console.groq.com/keys</a></span></div>
                                <div class="stat"><strong>Wise</strong><span>Token opcional em <a class="inline-link mono" href="https://wise.com/your-account/integrations-and-tools/api-tokens" target="_blank" rel="noopener noreferrer">https://wise.com/your-account/integrations-and-tools/api-tokens</a></span></div>
                                <div class="stat"><strong>ExchangeRate API</strong><span>Token opcional em <a class="inline-link mono" href="https://www.exchangerate-api.com/" target="_blank" rel="noopener noreferrer">https://www.exchangerate-api.com/</a></span></div>
                            </div>
                            <form id="credential-form">
                                <label>Provedor
                                    <select name="provider" required>
                                        <option value="gemini">Google Gemini</option>
                                        <option value="groq">Groq</option>
                                        <option value="wise">Wise</option>
                                        <option value="exchange_rate">ExchangeRate API</option>
                                    </select>
                                </label>
                                <label>API key
                                    <input name="api_key" type="password" placeholder="Cole a chave do provedor selecionado" required>
                                </label>
                                <button class="primary" type="submit">Salvar chave</button>
                            </form>
                            <div class="grid">
                                <div class="stat">
                                    <strong>Configuradas</strong>
                                    <div id="credential-summary" class="category-list"></div>
                                </div>
                                <div class="stat">
                                    <strong>Fallback da instância</strong>
                                    <div id="credential-fallback" class="category-list"></div>
                                </div>
                            </div>
                            <div class="step-actions">
                                <button class="ghost" id="back-to-terms-from-keys" type="button">Voltar</button>
                                <button class="primary" id="continue-after-keys" type="button">Continuar</button>
                            </div>
                            <div class="status" id="credential-status"></div>
                        </div>

                        <div id="step-whatsapp_prepare" class="wizard-step">
                            <h2>3. Conecte seu WhatsApp</h2>
                            <p>Agora vamos preparar a sua sessão e gerar o QR Code em uma única ação.</p>
                            <div class="qr-shell">
                                <div class="controls">
                                    <button class="primary" id="start-whatsapp" type="button">Preparar sessão e gerar QR Code</button>
                                    <button class="ghost" id="refresh-whatsapp" type="button">Atualizar status</button>
                                </div>
                                <div class="grid">
                                    <div class="stat"><strong id="whatsapp-status">pendente</strong><span>Status da conexão</span></div>
                                    <div class="stat"><strong id="whatsapp-instance" class="mono">-</strong><span>Instância Evolution</span></div>
                                </div>
                                <div class="qr-frame" id="qr-frame">
                                    <span style="color: var(--muted); text-align: center;">Sua sessão ainda não gerou um QR Code.</span>
                                </div>
                                <div class="step-actions">
                                    <button class="ghost" id="back-to-keys" type="button">Voltar</button>
                                    <button class="primary" id="continue-after-whatsapp" type="button" disabled>Continuar</button>
                                </div>
                                <div class="status" id="whatsapp-status-message"></div>
                            </div>
                        </div>

                        <div id="step-profile" class="wizard-step">
                            <h2>4. Ajuste seu perfil</h2>
                            <p>Seu nome já foi informado no cadastro web. Aqui você define apenas nome de exibição e timezone.</p>
                            <form id="profile-form">
                                <label>Nome de exibição
                                    <input name="display_name" placeholder="Como prefere aparecer no FinBot">
                                </label>
                                <label>Timezone
                                    <input name="timezone" value="America/Sao_Paulo" required>
                                </label>
                                <div class="step-actions">
                                    <button class="ghost" id="back-to-whatsapp" type="button">Voltar</button>
                                    <button class="primary" type="submit">Salvar e continuar</button>
                                </div>
                            </form>
                            <div class="status" id="profile-status"></div>
                        </div>

                        <div id="step-categories" class="wizard-step">
                            <h2>5. Personalize suas categorias</h2>
                            <p>Essa etapa é opcional. Você pode criar categorias próprias agora ou pular e fazer isso depois.</p>
                            <form id="category-form">
                                <label>Nome da categoria
                                    <input name="name" placeholder="Ex.: Pets, Freelance, Academia" required>
                                </label>
                                <label>Tipo
                                    <select name="type" required>
                                        <option value="Negativo">Despesa</option>
                                        <option value="Positivo">Entrada</option>
                                    </select>
                                </label>
                                <button class="primary" type="submit">Adicionar categoria</button>
                            </form>
                            <div class="grid">
                                <div class="stat">
                                    <strong>Ativas</strong>
                                    <ul class="category-list" id="categories-active"></ul>
                                </div>
                                <div class="stat">
                                    <strong>Ocultas</strong>
                                    <ul class="category-list" id="categories-inactive"></ul>
                                </div>
                            </div>
                            <div class="step-actions">
                                <button class="ghost" id="back-to-profile" type="button">Voltar</button>
                                <button class="primary" id="go-to-review" type="button">Continuar</button>
                                <button class="ghost" id="skip-categories" type="button">Pular por agora</button>
                            </div>
                            <div class="status" id="category-status"></div>
                        </div>

                        <div id="step-review" class="wizard-step">
                            <h2>6. Revise e conclua</h2>
                            <p>Você já passou pelo essencial. Agora é só revisar rapidamente e encerrar o onboarding.</p>
                            <div class="grid">
                                <div class="stat"><strong id="review-terms">-</strong><span>Termos</span></div>
                                <div class="stat"><strong id="review-whatsapp">-</strong><span>WhatsApp</span></div>
                                <div class="stat"><strong id="review-display-name">-</strong><span>Nome de exibição</span></div>
                                <div class="stat"><strong id="review-timezone">-</strong><span>Timezone</span></div>
                                <div class="stat"><strong id="review-providers">-</strong><span>Chaves de API</span></div>
                                <div class="stat"><strong id="review-custom-categories">-</strong><span>Categorias criadas</span></div>
                                <div class="stat"><strong id="review-hidden-categories">-</strong><span>Categorias ocultadas</span></div>
                            </div>
                            <div class="step-actions">
                                <button class="ghost" id="back-to-categories" type="button">Voltar</button>
                                <button class="primary" id="complete-onboarding" type="button">Concluir onboarding</button>
                            </div>
                            <div class="status" id="flow-status"></div>
                        </div>

                        <div id="step-completed" class="wizard-step">
                            <h2>Onboarding concluído</h2>
                            <p>Seu acesso web já está pronto. Daqui em diante, o fluxo ideal é continuar no painel de configurações.</p>
                            <div class="step-actions">
                                <button class="primary" id="open-settings" type="button">Ir para configurações</button>
                                <button class="ghost" id="completed-logout" type="button">Sair</button>
                            </div>
                        </div>
                    </section>
                </main>
            </div>

            <script>
                const state = { steps: [], currentStep: 'terms', user: null, whatsapp: null, credentials: null };
                const stepLabels = {
                    terms: 'Termos',
                    api_keys: 'Chaves de API',
                    whatsapp_prepare: 'Conectar WhatsApp',
                    whatsapp_qrcode: 'Conectar WhatsApp',
                    profile: 'Perfil',
                    categories: 'Categorias',
                    review: 'Revisão',
                    completed: 'Concluído'
                };

                async function fetchJson(url, options = {}) {
                    const response = await fetch(url, { credentials: 'same-origin', ...options });
                    const rawText = await response.text();
                    let data = null;
                    if (rawText) {
                        try {
                            data = JSON.parse(rawText);
                        } catch {
                            data = null;
                        }
                    }
                    if (!response.ok) {
                        const fallbackMessage = rawText && !data
                            ? `Erro interno ao processar a solicitação (${response.status}).`
                            : `Erro HTTP ${response.status}.`;
                        throw new Error(data?.detail || data?.message || fallbackMessage);
                    }
                    return data ?? {};
                }

                function uiStep(step) {
                    return step === 'whatsapp_qrcode' ? 'whatsapp_prepare' : step;
                }

                function renderSteps() {
                    const container = document.getElementById('step-list');
                    container.innerHTML = '';
                    const visibleSteps = ['terms', 'api_keys', 'whatsapp_prepare', 'profile', 'categories', 'review', 'completed'];
                    const current = uiStep(state.currentStep);
                    const currentIndex = visibleSteps.indexOf(current);
                    for (const step of visibleSteps) {
                        const item = document.createElement('div');
                        const stepIndex = visibleSteps.indexOf(step);
                        const done = current === 'completed' || currentIndex > stepIndex;
                        item.className = 'step' + (step === current ? ' active' : '') + (done ? ' done' : '');
                        item.innerHTML = `<strong>${stepLabels[step]}</strong><small>${step === current ? 'Etapa atual' : (done ? 'Concluída' : 'Próxima etapa')}</small>`;
                        container.appendChild(item);
                    }
                }

                function showStep() {
                    const current = uiStep(state.currentStep);
                    document.querySelectorAll('.wizard-step').forEach((element) => {
                        element.classList.toggle('active', element.id === `step-${current}`);
                    });
                }

                function renderStats(payload) {
                    state.user = payload.user;
                    state.steps = payload.onboarding.steps;
                    state.currentStep = payload.onboarding.current_step;
                    document.getElementById('welcome-title').textContent = `Bem-vindo, ${payload.user.name || payload.user.email || 'usuario'}!`;
                    document.getElementById('welcome-copy').textContent =
                        `Etapa atual: ${stepLabels[payload.onboarding.current_step] || payload.onboarding.current_step}. Vamos seguir uma etapa por vez.`;
                    document.getElementById('stats').innerHTML = `
                        <div class="stat"><strong>${payload.user.accepted_terms ? 'Aceitos' : 'Pendentes'}</strong><span>Termos de uso</span></div>
                        <div class="stat"><strong>${payload.user.onboarding_completed ? 'Concluído' : 'Em andamento'}</strong><span>Status do onboarding</span></div>
                        <div class="stat"><strong>${payload.user.phone}</strong><span>Telefone vinculado</span></div>
                        <div class="stat"><strong>${payload.user.timezone || 'America/Sao_Paulo'}</strong><span>Timezone atual</span></div>
                    `;
                    document.querySelector('#profile-form [name="display_name"]').value = payload.user.display_name || '';
                    document.querySelector('#profile-form [name="timezone"]').value = payload.user.timezone || 'America/Sao_Paulo';
                    document.getElementById('review-terms').textContent = payload.user.accepted_terms ? 'Aceitos' : 'Pendentes';
                    document.getElementById('review-whatsapp').textContent =
                        payload.onboarding.whatsapp_connected_at ? 'Conectado' : 'Pendente';
                    document.getElementById('review-display-name').textContent =
                        payload.user.display_name || payload.user.name || '-';
                    document.getElementById('review-timezone').textContent = payload.user.timezone || '-';
                    document.getElementById('review-providers').textContent =
                        payload.review.configured_providers.length
                            ? payload.review.configured_providers.join(', ')
                            : 'Nenhuma';
                    document.getElementById('review-custom-categories').textContent =
                        payload.review.custom_categories.length
                            ? payload.review.custom_categories.join(', ')
                            : 'Nenhuma';
                    document.getElementById('review-hidden-categories').textContent =
                        payload.review.hidden_categories.length
                            ? payload.review.hidden_categories.join(', ')
                            : 'Nenhuma';
                    renderSteps();
                    showStep();
                }

                function renderWhatsApp(payload) {
                    state.whatsapp = payload.session;
                    document.getElementById('whatsapp-status').textContent = payload.session.connection_status || 'pendente';
                    document.getElementById('whatsapp-instance').textContent = payload.session.evolution_instance || '-';
                    const qrFrame = document.getElementById('qr-frame');
                    if (payload.qrcode) {
                        qrFrame.innerHTML = `<img alt="QR Code do WhatsApp" src="${payload.qrcode}">`;
                    } else if (payload.pairingCode) {
                        qrFrame.innerHTML = `<div><strong>Pairing Code</strong><div class="mono" style="margin-top: 12px;">${payload.pairingCode}</div></div>`;
                    } else if (payload.session.connection_status === 'connected') {
                        qrFrame.innerHTML = '<strong>WhatsApp conectado com sucesso.</strong>';
                    } else {
                        qrFrame.innerHTML = '<span style="color: var(--muted); text-align: center;">Sua sessão ainda não gerou um QR Code.</span>';
                    }
                    document.getElementById('continue-after-whatsapp').disabled = payload.session.connection_status !== 'connected';
                    document.getElementById('whatsapp-status-message').textContent = payload.message || '';
                    document.getElementById('whatsapp-status-message').className = 'status';
                }

                function renderCredentials(payload) {
                    state.credentials = payload;
                    const summary = document.getElementById('credential-summary');
                    const fallback = document.getElementById('credential-fallback');
                    summary.innerHTML = '';
                    fallback.innerHTML = '';

                    for (const [provider, config] of Object.entries(payload.credentials || {})) {
                        const configuredItem = document.createElement('div');
                        configuredItem.className = 'category-item';
                        configuredItem.innerHTML =
                            `<div class="category-meta"><strong>${config.label}</strong><small><a class="inline-link" href="${config.help_url}" target="_blank" rel="noopener noreferrer">${config.help_url}</a></small></div>` +
                            `<span>${config.configured ? `Configurada (${config.last4 || '****'})` : 'Não configurada'}</span>`;
                        summary.appendChild(configuredItem);

                        const fallbackItem = document.createElement('div');
                        fallbackItem.className = 'category-item';
                        fallbackItem.innerHTML =
                            `<div class="category-meta"><strong>${config.label}</strong><small>${config.optional ? 'Opcional' : 'Recomendado'}</small></div>` +
                            `<span>${payload.instance_fallback[provider] ? 'Fallback disponível na instância' : 'Sem fallback global'}</span>`;
                        fallback.appendChild(fallbackItem);
                    }
                }

                function renderCategories(payload) {
                    const active = document.getElementById('categories-active');
                    const inactive = document.getElementById('categories-inactive');
                    active.innerHTML = '';
                    inactive.innerHTML = '';
                    for (const item of payload.active || []) {
                        const row = document.createElement('li');
                        row.className = 'category-item';
                        const meta = document.createElement('div');
                        meta.className = 'category-meta';
                        meta.innerHTML = `<strong>${item.name}</strong><small>${item.is_custom ? 'Categoria personalizada' : `Categoria padrão • ${item.type}`}</small>`;
                        row.appendChild(meta);

                        if (item.is_custom) {
                            const badge = document.createElement('span');
                            badge.className = 'pill';
                            badge.textContent = 'Ativa';
                            row.appendChild(badge);
                        } else {
                            const button = document.createElement('button');
                            button.type = 'button';
                            button.className = 'pill';
                            button.textContent = 'Ocultar';
                            button.addEventListener('click', () => toggleCategory(item.name, false));
                            row.appendChild(button);
                        }
                        active.appendChild(row);
                    }
                    for (const item of payload.inactive || []) {
                        const row = document.createElement('li');
                        row.className = 'category-item';
                        const meta = document.createElement('div');
                        meta.className = 'category-meta';
                        meta.innerHTML = `<strong>${item.name}</strong><small>${item.is_custom ? 'Categoria personalizada oculta' : `Categoria padrão • ${item.type}`}</small>`;
                        row.appendChild(meta);

                        if (item.is_custom) {
                            const badge = document.createElement('span');
                            badge.className = 'pill inactive';
                            badge.textContent = 'Oculta';
                            row.appendChild(badge);
                        } else {
                            const button = document.createElement('button');
                            button.type = 'button';
                            button.className = 'pill inactive';
                            button.textContent = 'Reativar';
                            button.addEventListener('click', () => toggleCategory(item.name, true));
                            row.appendChild(button);
                        }
                        inactive.appendChild(row);
                    }
                }

                async function loadState() {
                    const payload = await fetchJson('/onboarding/state');
                    renderStats(payload);
                }

                async function loadWhatsAppStatus() {
                    try {
                        const payload = await fetchJson('/onboarding/whatsapp/status');
                        renderWhatsApp(payload);
                    } catch (_error) {
                        // Terms may not be accepted yet.
                    }
                }

                async function loadCredentials() {
                    try {
                        const payload = await fetchJson('/onboarding/credentials');
                        renderCredentials(payload);
                    } catch (_error) {
                        // Terms may not be accepted yet.
                    }
                }

                async function loadCategories() {
                    const payload = await fetchJson('/onboarding/categories');
                    renderCategories(payload);
                }

                async function updateStep(nextStep) {
                    const payload = await fetchJson('/onboarding/step', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ current_step: nextStep })
                    });
                    renderStats(payload);
                }

                async function toggleCategory(categoryName, isActive) {
                    try {
                        await fetchJson('/onboarding/categories/visibility', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ category_name: categoryName, is_active: isActive })
                        });
                        await loadCategories();
                        document.getElementById('category-status').textContent = 'Categorias atualizadas.';
                        document.getElementById('category-status').className = 'status';
                    } catch (error) {
                        document.getElementById('category-status').textContent = error.message;
                        document.getElementById('category-status').className = 'status error';
                    }
                }

                document.getElementById('accept-terms').addEventListener('click', async () => {
                    try {
                        const payload = await fetchJson('/onboarding/terms/accept', { method: 'POST' });
                        renderStats(payload);
                        await loadCredentials();
                        document.getElementById('terms-status').textContent = 'Termos aceitos. Agora configure suas chaves.';
                        document.getElementById('terms-status').className = 'status';
                    } catch (error) {
                        document.getElementById('terms-status').textContent = error.message;
                        document.getElementById('terms-status').className = 'status error';
                    }
                });

                document.getElementById('reject-terms').addEventListener('click', async () => {
                    try {
                        const payload = await fetchJson('/onboarding/terms/reject', { method: 'POST' });
                        renderStats(payload);
                        document.getElementById('terms-status').textContent = 'Termos recusados.';
                        document.getElementById('terms-status').className = 'status';
                    } catch (error) {
                        document.getElementById('terms-status').textContent = error.message;
                        document.getElementById('terms-status').className = 'status error';
                    }
                });

                document.getElementById('logout').addEventListener('click', async () => {
                    await fetchJson('/auth/logout', { method: 'POST' });
                    window.location.href = '/web/login';
                });

                document.getElementById('credential-form').addEventListener('submit', async (event) => {
                    event.preventDefault();
                    const form = event.currentTarget;
                    const payload = Object.fromEntries(new FormData(form).entries());
                    try {
                        await fetchJson('/onboarding/credentials', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify(payload)
                        });
                        form.reset();
                        await loadCredentials();
                        document.getElementById('credential-status').textContent = 'Chave salva com sucesso.';
                        document.getElementById('credential-status').className = 'status';
                    } catch (error) {
                        document.getElementById('credential-status').textContent = error.message;
                        document.getElementById('credential-status').className = 'status error';
                    }
                });

                document.getElementById('start-whatsapp').addEventListener('click', async () => {
                    try {
                        await fetchJson('/onboarding/whatsapp/prepare', { method: 'POST' });
                        const payload = await fetchJson('/onboarding/whatsapp/qrcode', { method: 'POST' });
                        renderWhatsApp(payload);
                        await loadState();
                        document.getElementById('whatsapp-status-message').textContent = 'Sessão preparada e QR Code gerado. Faça a leitura no WhatsApp.';
                    } catch (error) {
                        document.getElementById('whatsapp-status-message').textContent = error.message;
                        document.getElementById('whatsapp-status-message').className = 'status error';
                    }
                });

                document.getElementById('refresh-whatsapp').addEventListener('click', async () => {
                    try {
                        const payload = await fetchJson('/onboarding/whatsapp/refresh', { method: 'POST' });
                        renderWhatsApp(payload);
                        await loadState();
                    } catch (error) {
                        document.getElementById('whatsapp-status-message').textContent = error.message;
                        document.getElementById('whatsapp-status-message').className = 'status error';
                    }
                });

                document.getElementById('profile-form').addEventListener('submit', async (event) => {
                    event.preventDefault();
                    const payload = Object.fromEntries(new FormData(event.currentTarget).entries());
                    try {
                        const response = await fetchJson('/onboarding/profile', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify(payload)
                        });
                        renderStats(response);
                        document.getElementById('profile-status').textContent = 'Perfil salvo. Agora você pode revisar categorias.';
                        document.getElementById('profile-status').className = 'status';
                    } catch (error) {
                        document.getElementById('profile-status').textContent = error.message;
                        document.getElementById('profile-status').className = 'status error';
                    }
                });

                document.getElementById('category-form').addEventListener('submit', async (event) => {
                    event.preventDefault();
                    const form = event.currentTarget;
                    const payload = Object.fromEntries(new FormData(form).entries());
                    try {
                        await fetchJson('/onboarding/categories', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify(payload)
                        });
                        form.reset();
                        await loadCategories();
                        document.getElementById('category-status').textContent = 'Categoria adicionada com sucesso.';
                        document.getElementById('category-status').className = 'status';
                    } catch (error) {
                        document.getElementById('category-status').textContent = error.message;
                        document.getElementById('category-status').className = 'status error';
                    }
                });

                document.getElementById('back-to-terms-from-keys').addEventListener('click', async () => {
                    await updateStep('terms');
                });

                document.getElementById('continue-after-keys').addEventListener('click', async () => {
                    await updateStep('whatsapp_prepare');
                });

                document.getElementById('back-to-keys').addEventListener('click', async () => {
                    await updateStep('api_keys');
                });

                document.getElementById('continue-after-whatsapp').addEventListener('click', async () => {
                    await updateStep('profile');
                });

                document.getElementById('back-to-whatsapp').addEventListener('click', async (event) => {
                    event.preventDefault();
                    await updateStep('whatsapp_prepare');
                });

                document.getElementById('back-to-profile').addEventListener('click', async () => {
                    await updateStep('profile');
                });

                document.getElementById('go-to-review').addEventListener('click', async () => {
                    await updateStep('review');
                });

                document.getElementById('skip-categories').addEventListener('click', async () => {
                    await updateStep('review');
                });

                document.getElementById('back-to-categories').addEventListener('click', async () => {
                    await updateStep('categories');
                });

                document.getElementById('complete-onboarding').addEventListener('click', async () => {
                    try {
                        const payload = await fetchJson('/onboarding/complete', { method: 'POST' });
                        renderStats(payload);
                        document.getElementById('flow-status').textContent = 'Onboarding concluído. Redirecionando para configurações...';
                        document.getElementById('flow-status').className = 'status';
                        setTimeout(() => {
                            window.location.href = '/web/dashboard';
                        }, 900);
                    } catch (error) {
                        document.getElementById('flow-status').textContent = error.message;
                        document.getElementById('flow-status').className = 'status error';
                    }
                });

                document.getElementById('open-settings').addEventListener('click', () => {
                    window.location.href = '/web/dashboard';
                });

                document.getElementById('completed-logout').addEventListener('click', async () => {
                    await fetchJson('/auth/logout', { method: 'POST' });
                    window.location.href = '/web/login';
                });

                loadState().catch((error) => {
                    document.getElementById('terms-status').textContent = error.message;
                    document.getElementById('terms-status').className = 'status error';
                });
                loadCredentials();
                loadWhatsAppStatus();
                loadCategories().catch(() => {
                    // Keep the onboarding functional even if category loading degrades.
                });
            </script>
        </body>
        </html>
        """
    )


@app.get("/web/settings", response_class=HTMLResponse)
async def web_settings_page(request: Request):
    """Render the authenticated post-onboarding settings panel."""
    try:
        await _get_current_web_user(request)
    except HTTPException:
        return RedirectResponse(url="/web/login", status_code=303)

    return HTMLResponse(
        content="""
        <!DOCTYPE html>
        <html lang="pt-BR">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>FinBot • Configurações</title>
            <link rel="preconnect" href="https://fonts.googleapis.com">
            <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
            <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600;800&display=swap" rel="stylesheet">
            <style>
                :root {
                    --bg: #f3f7fb;
                    --card: #ffffff;
                    --line: #d7e1ec;
                    --text: #0f172a;
                    --muted: #64748b;
                    --accent: #0f766e;
                    --accent-soft: #ccfbf1;
                    --warn-soft: #fff7ed;
                    --danger-soft: #ffe4e6;
                }
                * { box-sizing: border-box; }
                body {
                    margin: 0;
                    font-family: 'Outfit', sans-serif;
                    color: var(--text);
                    background:
                        radial-gradient(circle at top right, rgba(15, 118, 110, 0.09), transparent 20%),
                        linear-gradient(180deg, #f8fafc 0%, #eef3f8 100%);
                }
                .shell {
                    max-width: 1180px;
                    margin: 0 auto;
                    padding: 32px 20px 56px;
                    display: grid;
                    gap: 20px;
                }
                .hero, .card {
                    background: rgba(255, 255, 255, 0.88);
                    backdrop-filter: blur(18px);
                    border: 1px solid var(--line);
                    border-radius: 24px;
                    box-shadow: 0 18px 50px rgba(15, 23, 42, 0.06);
                }
                .hero {
                    padding: 28px;
                    display: grid;
                    gap: 14px;
                }
                .hero h1, .card h2 { margin: 0; }
                .hero p, .card p { color: var(--muted); line-height: 1.7; }
                .actions, .toggle-row {
                    display: flex;
                    flex-wrap: wrap;
                    gap: 12px;
                    align-items: center;
                }
                .grid {
                    display: grid;
                    grid-template-columns: repeat(2, minmax(0, 1fr));
                    gap: 18px;
                }
                .card {
                    padding: 24px;
                    display: grid;
                    gap: 16px;
                }
                .compact-form {
                    gap: 8px;
                }
                .compact-form label {
                    display: flex;
                    align-items: center;
                    justify-content: space-between;
                    gap: 12px;
                    padding: 10px 12px;
                    border: 1px solid var(--line);
                    border-radius: 14px;
                    background: #fbfdff;
                }
                form {
                    display: grid;
                    gap: 12px;
                }
                label {
                    display: grid;
                    gap: 8px;
                    color: var(--muted);
                    font-size: 0.92rem;
                }
                input, textarea {
                    width: 100%;
                    border: 1px solid var(--line);
                    border-radius: 14px;
                    padding: 14px 16px;
                    font: inherit;
                    background: white;
                }
                textarea {
                    min-height: 200px;
                    resize: vertical;
                }
                button {
                    border: 0;
                    border-radius: 14px;
                    padding: 12px 16px;
                    font: inherit;
                    font-weight: 700;
                    cursor: pointer;
                }
                .primary { background: var(--accent); color: white; }
                .ghost { background: #e2e8f0; color: var(--text); }
                .danger { background: #e11d48; color: white; }
                .stats {
                    display: grid;
                    grid-template-columns: repeat(4, minmax(0, 1fr));
                    gap: 12px;
                }
                .stat {
                    border: 1px solid var(--line);
                    border-radius: 18px;
                    padding: 16px;
                    background: #fbfdff;
                }
                .stat strong {
                    display: block;
                    font-size: 1.15rem;
                    margin-bottom: 6px;
                }
                .status {
                    min-height: 24px;
                    color: var(--muted);
                    font-size: 0.92rem;
                }
                .status.error { color: #be123c; }
                .notice, .warning {
                    border-radius: 18px;
                    padding: 16px;
                }
                .plain-list {
                    list-style: none;
                    margin: 0;
                    padding: 0;
                    display: grid;
                    gap: 10px;
                }
                .plain-list li {
                    display: flex;
                    align-items: center;
                    justify-content: space-between;
                    gap: 12px;
                    padding: 12px 14px;
                    border: 1px solid var(--line);
                    border-radius: 14px;
                    background: #fbfdff;
                }
                .notice {
                    background: var(--accent-soft);
                    color: #115e59;
                    border: 1px solid #99f6e4;
                }
                .warning {
                    background: var(--warn-soft);
                    color: #9a3412;
                    border: 1px solid #fed7aa;
                }
                .mono {
                    font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
                    font-size: 0.9rem;
                }
                @media (max-width: 920px) {
                    .grid, .stats { grid-template-columns: 1fr; }
                }
            </style>
        </head>
        <body>
            <div class="shell">
                <section class="hero">
                    <div class="actions" style="justify-content: space-between;">
                        <div>
                            <p style="margin: 0; letter-spacing: 0.12em; text-transform: uppercase; font-size: 0.78rem;">FinBot Settings</p>
                            <h1>Configure sua conta depois do onboarding.</h1>
                        </div>
                        <div class="actions">
                            <button class="ghost" id="open-dashboard" type="button">Painel</button>
                            <button class="ghost" id="logout" type="button">Sair</button>
                        </div>
                    </div>
                    <p>
                        Este painel concentra ajustes do dia a dia: perfil, preferências, limites, exportação e importação
                        de backup com o mesmo cuidado de segurança usado no fluxo do WhatsApp.
                    </p>
                    <div class="stats" id="settings-stats"></div>
                </section>

                <div class="grid">
                    <section class="card">
                        <h2>Termos aceitos</h2>
                        <p>Os termos continuam acessíveis aqui para consulta sempre que precisar.</p>
                        <div class="notice" id="terms-summary"></div>
                        <div class="warning">
                            <strong>Resumo operacional</strong><br>
                            Esta instância é self-hosted. Dependendo da configuração e do nível de acesso ao ambiente,
                            o operador da infraestrutura pode ter acesso técnico ou operacional aos dados trafegados pela stack.
                        </div>
                    </section>

                    <section class="card">
                        <h2>Perfil</h2>
                        <p>Atualize os dados básicos usados no painel web, a moeda base e o suporte futuro da conta.</p>
                        <form id="profile-form">
                            <label>Nome
                                <input name="name" required>
                            </label>
                            <label>Nome de exibição
                                <input name="display_name">
                            </label>
                            <label>Email
                                <input name="email" type="email" placeholder="voce@exemplo.com">
                            </label>
                            <label>Timezone
                                <input name="timezone" required>
                            </label>
                            <label>Moeda base
                                <select name="base_currency" required></select>
                            </label>
                            <button class="primary" type="submit">Salvar perfil</button>
                        </form>
                        <div class="status" id="profile-status"></div>
                    </section>

                    <section class="card">
                        <h2>Números autorizados</h2>
                        <p>Gerencie quais números de WhatsApp podem usar esta conta. O número principal fica sempre autorizado.</p>
                        <form id="authorized-phones-form">
                            <label>Novo número autorizado
                                <input name="phone" placeholder="5511999999999" required>
                            </label>
                            <button class="primary" type="submit">Adicionar número</button>
                        </form>
                        <ul class="plain-list" id="authorized-phones-list"></ul>
                        <div class="status" id="authorized-phones-status"></div>
                    </section>

                    <section class="card">
                        <h2>Notificações</h2>
                        <p>Ative só o que realmente quer receber.</p>
                        <form id="notifications-form" class="compact-form">
                            <label class="toggle-row"><input name="budget_alerts" type="checkbox"> Alertas de orçamento</label>
                            <label class="toggle-row"><input name="recurring_reminders" type="checkbox"> Lembretes de recorrência</label>
                            <label class="toggle-row"><input name="goal_updates" type="checkbox"> Mensagens sobre metas</label>
                            <button class="primary" type="submit">Salvar notificações</button>
                        </form>
                        <div class="status" id="notifications-status"></div>
                    </section>

                    <section class="card">
                        <h2>Limites diários</h2>
                        <p>Esses limites podem ser ajustados a qualquer momento. Se ficarem desativados, o FinBot deixa de bloquear por cota.</p>
                        <form id="limits-form">
                            <label class="toggle-row"><input name="limits_enabled" type="checkbox"> Limites habilitados</label>
                            <label>Mensagens de texto por dia
                                <input name="daily_text_limit" type="number" min="0" required>
                            </label>
                            <label>Mídias e documentos por dia
                                <input name="daily_media_limit" type="number" min="0" required>
                            </label>
                            <label>Chamadas de IA por dia
                                <input name="daily_ai_limit" type="number" min="0" required>
                            </label>
                            <button class="primary" type="submit">Salvar limites</button>
                        </form>
                        <div class="status" id="limits-status"></div>
                    </section>

                    <section class="card">
                        <h2>Backup</h2>
                        <p>Exporte seus dados atuais ou importe um backup JSON. Para migração entre números, o sistema exige confirmação explícita.</p>
                        <div class="notice">
                            Identidade estável do backup:
                            <span class="mono" id="backup-owner-id">-</span>
                        </div>
                        <div class="actions">
                            <button class="primary" id="export-backup" type="button">Exportar backup</button>
                        </div>
                        <form id="backup-preview-form">
                            <label>Arquivo de backup JSON
                                <input name="backup_file" type="file" accept=".json,application/json" required>
                            </label>
                            <button class="ghost" type="submit">Analisar backup</button>
                        </form>
                        <div id="backup-preview" class="warning" style="display: none;"></div>
                        <div class="actions">
                            <label class="toggle-row" id="migration-confirmation" style="display: none;">
                                <input id="migration-checkbox" type="checkbox">
                                Confirmo que desejo migrar um histórico de outro número para esta conta.
                            </label>
                            <button class="danger" id="apply-backup" type="button" disabled>Aplicar backup</button>
                        </div>
                        <div class="status" id="backup-status"></div>
                    </section>
                </div>
            </div>

            <script>
                const state = { backupRef: null, requiresMigration: false };

                async function fetchJson(url, options = {}) {
                    const response = await fetch(url, { credentials: 'same-origin', ...options });
                    const rawText = await response.text();
                    let data = null;
                    if (rawText) {
                        try {
                            data = JSON.parse(rawText);
                        } catch {
                            data = null;
                        }
                    }
                    if (!response.ok) {
                        const fallbackMessage = rawText && !data
                            ? `Erro interno ao processar a solicitação (${response.status}).`
                            : `Erro HTTP ${response.status}.`;
                        throw new Error(data?.detail || data?.message || fallbackMessage);
                    }
                    return data ?? {};
                }

                function formToObject(form) {
                    const formData = new FormData(form);
                    const data = Object.fromEntries(formData.entries());
                    for (const input of form.querySelectorAll('input[type="checkbox"]')) {
                        data[input.name || input.id] = input.checked;
                    }
                    return data;
                }

                function renderState(payload) {
                    document.querySelector('#profile-form [name="name"]').value = payload.user.name || '';
                    document.querySelector('#profile-form [name="display_name"]').value = payload.user.display_name || '';
                    document.querySelector('#profile-form [name="email"]').value = payload.user.email || '';
                    document.querySelector('#profile-form [name="timezone"]').value = payload.user.timezone || 'America/Sao_Paulo';
                    document.querySelector('#profile-form [name="base_currency"]').innerHTML = (
                        payload.currencies || []
                    ).map((item) => {
                        const selected = item.code === (payload.user.base_currency || 'BRL') ? 'selected' : '';
                        return `<option value="${item.code}" ${selected}>${item.name}</option>`;
                    }).join('');
                    document.getElementById('terms-summary').innerHTML =
                        `Versão: <strong>${payload.user.terms_version || '-'}</strong><br>` +
                        `Status: <strong>${payload.user.accepted_terms ? 'Aceitos' : 'Pendentes'}</strong><br>` +
                        `Aceitos em: <strong>${payload.user.accepted_terms_at || '-'}</strong>`;

                    document.querySelector('#notifications-form [name="budget_alerts"]').checked = !!payload.notifications.budget_alerts;
                    document.querySelector('#notifications-form [name="recurring_reminders"]').checked = !!payload.notifications.recurring_reminders;
                    document.querySelector('#notifications-form [name="goal_updates"]').checked = !!payload.notifications.goal_updates;

                    document.querySelector('#limits-form [name="limits_enabled"]').checked = !!payload.limits.limits_enabled;
                    document.querySelector('#limits-form [name="daily_text_limit"]').value = payload.limits.daily_text_limit;
                    document.querySelector('#limits-form [name="daily_media_limit"]').value = payload.limits.daily_media_limit;
                    document.querySelector('#limits-form [name="daily_ai_limit"]').value = payload.limits.daily_ai_limit;

                    document.getElementById('backup-owner-id').textContent = payload.backup.backup_owner_id || '-';
                    document.getElementById('authorized-phones-list').innerHTML = (
                        payload.authorized_phones || []
                    ).map((item) => `
                        <li>
                            <span>
                                <strong>${item.phone}</strong><br>
                                <small>${item.is_primary ? 'Numero principal da conta' : 'Numero adicional autorizado'}</small>
                            </span>
                            ${item.is_primary ? '<span class="mono">fixo</span>' : `<button class="ghost" type="button" data-remove-phone="${item.phone}">Remover</button>`}
                        </li>
                    `).join('');

                    document.getElementById('settings-stats').innerHTML = `
                        <div class="stat"><strong>${payload.user.phone}</strong><span>Telefone vinculado</span></div>
                        <div class="stat"><strong>${payload.limits.usage.daily_text_limit.used}/${payload.limits.daily_text_limit}</strong><span>Uso de texto hoje</span></div>
                        <div class="stat"><strong>${payload.limits.usage.daily_media_limit.used}/${payload.limits.daily_media_limit}</strong><span>Uso de mídia hoje</span></div>
                        <div class="stat"><strong>${payload.limits.usage.daily_ai_limit.used}/${payload.limits.daily_ai_limit}</strong><span>Uso de IA hoje</span></div>
                    `;
                }

                function resetBackupPreview() {
                    state.backupRef = null;
                    state.requiresMigration = false;
                    document.getElementById('backup-preview').style.display = 'none';
                    document.getElementById('backup-preview').textContent = '';
                    document.getElementById('migration-confirmation').style.display = 'none';
                    document.getElementById('migration-checkbox').checked = false;
                    document.getElementById('apply-backup').disabled = true;
                }

                function renderBackupPreview(payload) {
                    state.backupRef = payload.backup_ref;
                    state.requiresMigration = payload.requires_migration_confirmation;
                    const preview = document.getElementById('backup-preview');
                    preview.style.display = 'block';
                    preview.innerHTML = `
                        <strong>Backup analisado</strong><br>
                        Origem: ${payload.summary.source_phone || 'desconhecido'}<br>
                        Despesas: ${payload.summary.expenses}<br>
                        Orçamentos: ${payload.summary.budgets}<br>
                        Alertas: ${payload.summary.budget_alerts}<br>
                        Metas: ${payload.summary.goals}<br>
                        Atualizações de metas: ${payload.summary.goal_updates}
                    `;
                    document.getElementById('migration-confirmation').style.display =
                        payload.requires_migration_confirmation ? 'flex' : 'none';
                    document.getElementById('apply-backup').disabled = false;
                }

                async function loadState() {
                    const payload = await fetchJson('/settings/state');
                    renderState(payload);
                }

                document.getElementById('profile-form').addEventListener('submit', async (event) => {
                    event.preventDefault();
                    try {
                        const payload = await fetchJson('/settings/profile', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify(formToObject(event.currentTarget)),
                        });
                        renderState(payload);
                        document.getElementById('profile-status').textContent = 'Perfil atualizado com sucesso.';
                        document.getElementById('profile-status').className = 'status';
                    } catch (error) {
                        document.getElementById('profile-status').textContent = error.message;
                        document.getElementById('profile-status').className = 'status error';
                    }
                });

                document.getElementById('notifications-form').addEventListener('submit', async (event) => {
                    event.preventDefault();
                    try {
                        const payload = await fetchJson('/settings/notifications', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify(formToObject(event.currentTarget)),
                        });
                        renderState(payload);
                        document.getElementById('notifications-status').textContent = 'Preferências atualizadas.';
                        document.getElementById('notifications-status').className = 'status';
                    } catch (error) {
                        document.getElementById('notifications-status').textContent = error.message;
                        document.getElementById('notifications-status').className = 'status error';
                    }
                });

                document.getElementById('limits-form').addEventListener('submit', async (event) => {
                    event.preventDefault();
                    const payload = formToObject(event.currentTarget);
                    payload.daily_text_limit = Number(payload.daily_text_limit);
                    payload.daily_media_limit = Number(payload.daily_media_limit);
                    payload.daily_ai_limit = Number(payload.daily_ai_limit);
                    try {
                        const response = await fetchJson('/settings/limits', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify(payload),
                        });
                        renderState(response);
                        document.getElementById('limits-status').textContent = 'Limites atualizados.';
                        document.getElementById('limits-status').className = 'status';
                    } catch (error) {
                        document.getElementById('limits-status').textContent = error.message;
                        document.getElementById('limits-status').className = 'status error';
                    }
                });

                document.getElementById('authorized-phones-form').addEventListener('submit', async (event) => {
                    event.preventDefault();
                    const form = event.currentTarget;
                    try {
                        const payload = await fetchJson('/settings/authorized-phones', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ phone: form.querySelector('input[name="phone"]').value }),
                        });
                        renderState(payload);
                        form.reset();
                        document.getElementById('authorized-phones-status').textContent = 'Numero autorizado com sucesso.';
                        document.getElementById('authorized-phones-status').className = 'status';
                    } catch (error) {
                        document.getElementById('authorized-phones-status').textContent = error.message;
                        document.getElementById('authorized-phones-status').className = 'status error';
                    }
                });

                document.getElementById('authorized-phones-list').addEventListener('click', async (event) => {
                    const target = event.target;
                    const phone = target.dataset.removePhone;
                    if (!phone) {
                        return;
                    }
                    try {
                        const payload = await fetchJson('/settings/authorized-phones', {
                            method: 'DELETE',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ phone }),
                        });
                        renderState(payload);
                        document.getElementById('authorized-phones-status').textContent = 'Numero removido com sucesso.';
                        document.getElementById('authorized-phones-status').className = 'status';
                    } catch (error) {
                        document.getElementById('authorized-phones-status').textContent = error.message;
                        document.getElementById('authorized-phones-status').className = 'status error';
                    }
                });

                document.getElementById('export-backup').addEventListener('click', async () => {
                    try {
                        const payload = await fetchJson('/settings/backup/export', { method: 'POST' });
                        const blob = new Blob([payload.backup_json], { type: 'application/json' });
                        const url = URL.createObjectURL(blob);
                        const anchor = document.createElement('a');
                        anchor.href = url;
                        anchor.download = payload.filename;
                        anchor.click();
                        URL.revokeObjectURL(url);
                        document.getElementById('backup-status').textContent = 'Backup exportado com sucesso.';
                        document.getElementById('backup-status').className = 'status';
                    } catch (error) {
                        document.getElementById('backup-status').textContent = error.message;
                        document.getElementById('backup-status').className = 'status error';
                    }
                });

                document.getElementById('backup-preview-form').addEventListener('submit', async (event) => {
                    event.preventDefault();
                    resetBackupPreview();
                    try {
                        const form = event.currentTarget;
                        const fileInput = form.querySelector('input[name="backup_file"]');
                        const selectedFile = fileInput?.files?.[0];
                        if (!selectedFile) {
                            throw new Error('Selecione um arquivo JSON de backup.');
                        }
                        const backupJson = await selectedFile.text();
                        const payload = await fetchJson('/settings/backup/import/preview', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ backup_json: backupJson }),
                        });
                        renderBackupPreview(payload);
                        document.getElementById('backup-status').textContent = 'Backup analisado. Revise antes de aplicar.';
                        document.getElementById('backup-status').className = 'status';
                    } catch (error) {
                        document.getElementById('backup-status').textContent = error.message;
                        document.getElementById('backup-status').className = 'status error';
                    }
                });

                document.getElementById('apply-backup').addEventListener('click', async () => {
                    if (!state.backupRef) {
                        return;
                    }
                    try {
                        const payload = await fetchJson('/settings/backup/import/apply', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({
                                backup_ref: state.backupRef,
                                explicit_migration_confirmation: document.getElementById('migration-checkbox').checked,
                            }),
                        });
                        resetBackupPreview();
                        document.querySelector('#backup-preview-form input[name="backup_file"]').value = '';
                        document.getElementById('backup-status').textContent =
                            `Backup restaurado: ${payload.restored.expenses} despesas, ${payload.restored.budgets} orçamentos e ${payload.restored.goals} metas.`;
                        document.getElementById('backup-status').className = 'status';
                        await loadState();
                    } catch (error) {
                        document.getElementById('backup-status').textContent = error.message;
                        document.getElementById('backup-status').className = 'status error';
                    }
                });

                document.getElementById('migration-checkbox').addEventListener('change', (event) => {
                    if (state.requiresMigration) {
                        document.getElementById('apply-backup').disabled = !event.currentTarget.checked;
                    }
                });

                document.getElementById('logout').addEventListener('click', async () => {
                    await fetchJson('/auth/logout', { method: 'POST' });
                    window.location.href = '/web/login';
                });

                document.getElementById('open-dashboard').addEventListener('click', () => {
                    window.location.href = '/web/dashboard';
                });

                loadState().catch((error) => {
                    document.getElementById('profile-status').textContent = error.message;
                    document.getElementById('profile-status').className = 'status error';
                });
            </script>
        </body>
        </html>
        """
    )


@app.get("/onboarding/state")
async def onboarding_state(request: Request):
    """Return onboarding state for the authenticated web user."""
    await _get_current_web_user(request)
    async with async_session() as session:
        refreshed_user = await auth_service.get_user_by_session_token(
            session, _get_session_cookie_token(request) or ""
        )
        if refreshed_user is None:
            raise HTTPException(status_code=401, detail="Sessao web invalida ou expirada.")
        state = await onboarding_service.get_or_create_state(session, refreshed_user)
        return await _build_onboarding_payload(session, refreshed_user, state)


@app.post("/onboarding/step")
async def onboarding_step(request: Request, payload: OnboardingStepRequest):
    """Persist the current onboarding step for the authenticated user."""
    await _get_current_web_user(request)
    async with async_session() as session:
        refreshed_user = await auth_service.get_user_by_session_token(
            session, _get_session_cookie_token(request) or ""
        )
        if refreshed_user is None:
            raise HTTPException(status_code=401, detail="Sessao web invalida ou expirada.")
        try:
            state = await onboarding_service.update_step(
                session, refreshed_user, payload.current_step
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return await _build_onboarding_payload(session, refreshed_user, state)


@app.post("/onboarding/terms/accept")
async def onboarding_accept_terms(request: Request):
    """Accept the current terms version for the authenticated web user."""
    await _get_current_web_user(request)
    async with async_session() as session:
        refreshed_user = await auth_service.get_user_by_session_token(
            session, _get_session_cookie_token(request) or ""
        )
        if refreshed_user is None:
            raise HTTPException(status_code=401, detail="Sessao web invalida ou expirada.")
        updated_user = await user_service.accept_terms(session, refreshed_user)
        state = await onboarding_service.update_step(session, updated_user, "api_keys")
        return await _build_onboarding_payload(session, updated_user, state)


@app.post("/onboarding/terms/reject")
async def onboarding_reject_terms(request: Request):
    """Reject the current terms version for the authenticated web user."""
    await _get_current_web_user(request)
    async with async_session() as session:
        refreshed_user = await auth_service.get_user_by_session_token(
            session, _get_session_cookie_token(request) or ""
        )
        if refreshed_user is None:
            raise HTTPException(status_code=401, detail="Sessao web invalida ou expirada.")
        updated_user = await user_service.reject_terms(session, refreshed_user)
        state = await onboarding_service.update_step(session, updated_user, "terms")
        return await _build_onboarding_payload(session, updated_user, state)


@app.post("/onboarding/profile")
async def onboarding_profile(request: Request, payload: OnboardingProfileRequest):
    """Update the authenticated user's profile during onboarding."""
    await _get_current_web_user(request)
    async with async_session() as session:
        refreshed_user = await auth_service.get_user_by_session_token(
            session, _get_session_cookie_token(request) or ""
        )
        if refreshed_user is None:
            raise HTTPException(status_code=401, detail="Sessao web invalida ou expirada.")
        try:
            updated_user = await user_service.update_web_profile(
                session,
                refreshed_user,
                name=payload.name,
                display_name=payload.display_name,
                timezone=payload.timezone,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        state = await onboarding_service.update_step(session, updated_user, "categories")
        return await _build_onboarding_payload(session, updated_user, state)


@app.get("/onboarding/credentials")
async def onboarding_credentials(request: Request):
    """Return user-scoped provider credential summaries for onboarding."""
    await _get_current_web_user(request)
    async with async_session() as session:
        refreshed_user, _state = await _get_onboarding_user_and_state(session, request)
        if not refreshed_user.accepted_terms:
            raise HTTPException(
                status_code=400, detail="Aceite os termos antes de configurar as chaves."
            )
        return {
            "status": "ok",
            "credentials": await credential_service.list_user_credentials(session, refreshed_user),
            "instance_fallback": {
                "gemini": bool(settings.gemini_api_key),
                "groq": bool(settings.groq_api_key),
                "wise": bool(settings.wise_api_key),
                "exchange_rate": bool(settings.exchange_rate_api_key),
            },
        }


@app.post("/onboarding/credentials")
async def onboarding_credentials_upsert(
    request: Request,
    payload: OnboardingCredentialRequest,
):
    """Store or update a user-scoped provider credential during onboarding."""
    await _get_current_web_user(request)
    async with async_session() as session:
        refreshed_user, _state = await _get_onboarding_user_and_state(session, request)
        if not refreshed_user.accepted_terms:
            raise HTTPException(
                status_code=400, detail="Aceite os termos antes de configurar as chaves."
            )
        try:
            credential = await credential_service.upsert_user_credential(
                session,
                refreshed_user,
                provider=payload.provider,
                api_key=payload.api_key,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        await onboarding_service.update_step(session, refreshed_user, "api_keys")
        return {
            "status": "ok",
            "credential": {
                "provider": credential.provider,
                "last4": credential.api_key_last4,
                "validated_at": credential.validated_at.isoformat()
                if credential.validated_at
                else None,
            },
        }


@app.get("/onboarding/categories")
async def onboarding_categories(request: Request):
    """Return category customization data for the authenticated user."""
    await _get_current_web_user(request)
    async with async_session() as session:
        refreshed_user = await auth_service.get_user_by_session_token(
            session, _get_session_cookie_token(request) or ""
        )
        if refreshed_user is None:
            raise HTTPException(status_code=401, detail="Sessao web invalida ou expirada.")
        return await category_service.list_available_categories(session, refreshed_user)


@app.post("/onboarding/categories")
async def onboarding_create_category(request: Request, payload: OnboardingCategoryCreateRequest):
    """Create a custom category for the authenticated user."""
    await _get_current_web_user(request)
    async with async_session() as session:
        refreshed_user = await auth_service.get_user_by_session_token(
            session, _get_session_cookie_token(request) or ""
        )
        if refreshed_user is None:
            raise HTTPException(status_code=401, detail="Sessao web invalida ou expirada.")
        try:
            category = await category_service.create_custom_category(
                session,
                refreshed_user,
                name=payload.name,
                category_type=payload.type,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {
            "status": "ok",
            "category": {
                "id": category.id,
                "name": category.name,
                "type": category.type,
                "is_active": category.is_active,
                "is_custom": True,
            },
        }


@app.post("/onboarding/categories/visibility")
async def onboarding_category_visibility(
    request: Request,
    payload: OnboardingCategoryVisibilityRequest,
):
    """Hide or reactivate a default category for the authenticated user."""
    await _get_current_web_user(request)
    async with async_session() as session:
        refreshed_user = await auth_service.get_user_by_session_token(
            session, _get_session_cookie_token(request) or ""
        )
        if refreshed_user is None:
            raise HTTPException(status_code=401, detail="Sessao web invalida ou expirada.")
        try:
            category = await category_service.set_system_category_visibility(
                session,
                refreshed_user,
                category_name=payload.category_name,
                is_active=payload.is_active,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {
            "status": "ok",
            "category": {
                "name": category.name,
                "type": category.type,
                "is_active": category.is_active,
                "is_custom": False,
            },
        }


@app.post("/onboarding/complete")
async def onboarding_complete(request: Request):
    """Mark onboarding as completed for the authenticated web user."""
    await _get_current_web_user(request)
    async with async_session() as session:
        refreshed_user = await auth_service.get_user_by_session_token(
            session, _get_session_cookie_token(request) or ""
        )
        if refreshed_user is None:
            raise HTTPException(status_code=401, detail="Sessao web invalida ou expirada.")
        state = await onboarding_service.mark_completed(session, refreshed_user)
        return await _build_onboarding_payload(session, refreshed_user, state)


@app.post("/onboarding/whatsapp/prepare")
async def onboarding_whatsapp_prepare(request: Request):
    """Prepare a dedicated WhatsApp onboarding session for the authenticated user."""
    await _get_current_web_user(request)
    async with async_session() as session:
        refreshed_user, _state = await _get_onboarding_user_and_state(session, request)
        if not refreshed_user.accepted_terms:
            raise HTTPException(
                status_code=400,
                detail="Aceite os termos antes de conectar o WhatsApp.",
            )
        return await whatsapp_onboarding_service.prepare_session(session, refreshed_user)


@app.get("/onboarding/whatsapp/status")
async def onboarding_whatsapp_status(request: Request):
    """Return the current WhatsApp onboarding status for the authenticated user."""
    await _get_current_web_user(request)
    async with async_session() as session:
        refreshed_user, _state = await _get_onboarding_user_and_state(session, request)
        if not refreshed_user.accepted_terms:
            raise HTTPException(
                status_code=400,
                detail="Aceite os termos antes de conectar o WhatsApp.",
            )
        return await whatsapp_onboarding_service.get_status(session, refreshed_user)


@app.post("/onboarding/whatsapp/qrcode")
async def onboarding_whatsapp_qrcode(request: Request):
    """Generate or refresh the WhatsApp QR code for the authenticated user."""
    await _get_current_web_user(request)
    async with async_session() as session:
        refreshed_user, _state = await _get_onboarding_user_and_state(session, request)
        if not refreshed_user.accepted_terms:
            raise HTTPException(
                status_code=400,
                detail="Aceite os termos antes de conectar o WhatsApp.",
            )
        return await whatsapp_onboarding_service.generate_qrcode(session, refreshed_user)


@app.post("/onboarding/whatsapp/refresh")
async def onboarding_whatsapp_refresh(request: Request):
    """Refresh the WhatsApp connection status for the authenticated user."""
    await _get_current_web_user(request)
    async with async_session() as session:
        refreshed_user, _state = await _get_onboarding_user_and_state(session, request)
        if not refreshed_user.accepted_terms:
            raise HTTPException(
                status_code=400,
                detail="Aceite os termos antes de conectar o WhatsApp.",
            )
        return await whatsapp_onboarding_service.refresh_status(session, refreshed_user)


@app.get("/web/dashboard", response_class=HTMLResponse)
async def web_dashboard_page(request: Request):
    """Render the authenticated financial dashboard after onboarding."""
    try:
        user = await _get_current_web_user(request)
    except HTTPException:
        return RedirectResponse(url="/web/login", status_code=303)
    if not getattr(user, "onboarding_completed", False):
        return RedirectResponse(url="/web/onboarding", status_code=303)

    return HTMLResponse(
        content="""
        <!DOCTYPE html>
        <html lang="pt-BR">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>FinBot • Painel Financeiro</title>
            <link rel="preconnect" href="https://fonts.googleapis.com">
            <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
            <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600;800&display=swap" rel="stylesheet">
            <style>
                :root {
                    --bg: #eef4f7;
                    --card: rgba(255, 255, 255, 0.9);
                    --line: #d9e2ec;
                    --text: #0f172a;
                    --muted: #64748b;
                    --accent: #0f766e;
                    --accent-soft: #ccfbf1;
                    --surface: #fbfdff;
                    --danger: #be123c;
                }
                * { box-sizing: border-box; }
                body {
                    margin: 0;
                    font-family: 'Outfit', sans-serif;
                    color: var(--text);
                    background:
                        radial-gradient(circle at top right, rgba(15, 118, 110, 0.10), transparent 18%),
                        radial-gradient(circle at left center, rgba(14, 116, 144, 0.06), transparent 20%),
                        linear-gradient(180deg, #f8fafc 0%, var(--bg) 100%);
                }
                .shell {
                    max-width: 1380px;
                    margin: 0 auto;
                    padding: 28px 20px 56px;
                    display: grid;
                    gap: 20px;
                }
                .hero, .card {
                    background: var(--card);
                    backdrop-filter: blur(16px);
                    border: 1px solid var(--line);
                    border-radius: 24px;
                    box-shadow: 0 18px 50px rgba(15, 23, 42, 0.06);
                }
                .hero {
                    padding: 28px;
                    display: grid;
                    gap: 16px;
                }
                .hero h1, .card h2, .card h3 { margin: 0; }
                .hero p, .card p {
                    margin: 0;
                    color: var(--muted);
                    line-height: 1.7;
                }
                .actions, .toolbar {
                    display: flex;
                    flex-wrap: wrap;
                    gap: 12px;
                    align-items: center;
                }
                .hero-top {
                    display: flex;
                    flex-wrap: wrap;
                    gap: 16px;
                    justify-content: space-between;
                    align-items: flex-start;
                }
                .stats {
                    display: grid;
                    grid-template-columns: repeat(4, minmax(0, 1fr));
                    gap: 12px;
                }
                .stat, .panel {
                    padding: 16px;
                    border-radius: 18px;
                    border: 1px solid var(--line);
                    background: var(--surface);
                }
                .stat strong {
                    display: block;
                    font-size: 1.15rem;
                    margin-bottom: 8px;
                }
                .grid {
                    display: grid;
                    grid-template-columns: repeat(2, minmax(0, 1fr));
                    gap: 20px;
                }
                .stack {
                    display: grid;
                    gap: 20px;
                }
                .wide-card {
                    grid-column: 1 / -1;
                }
                .card {
                    padding: 22px;
                    display: grid;
                    gap: 16px;
                }
                form {
                    display: grid;
                    gap: 12px;
                }
                .two-col {
                    display: grid;
                    grid-template-columns: repeat(2, minmax(0, 1fr));
                    gap: 12px;
                }
                label {
                    display: grid;
                    gap: 8px;
                    color: var(--muted);
                    font-size: 0.93rem;
                }
                input, select {
                    width: 100%;
                    border: 1px solid var(--line);
                    border-radius: 14px;
                    padding: 13px 14px;
                    font: inherit;
                    background: white;
                }
                button {
                    border: 0;
                    border-radius: 14px;
                    padding: 12px 16px;
                    font: inherit;
                    font-weight: 700;
                    cursor: pointer;
                }
                .primary { background: var(--accent); color: white; }
                .ghost { background: #e2e8f0; color: var(--text); }
                .danger { background: var(--danger); color: white; }
                .status {
                    min-height: 22px;
                    font-size: 0.92rem;
                    color: var(--muted);
                }
                .status.error { color: var(--danger); }
                .list {
                    display: grid;
                    gap: 10px;
                }
                .list-item {
                    display: flex;
                    justify-content: space-between;
                    gap: 12px;
                    align-items: center;
                    padding: 12px 14px;
                    border: 1px solid var(--line);
                    border-radius: 16px;
                    background: var(--surface);
                }
                .list-item small {
                    display: block;
                    color: var(--muted);
                    margin-top: 4px;
                }
                .table-wrap {
                    overflow-x: auto;
                    border: 1px solid var(--line);
                    border-radius: 18px;
                    background: var(--surface);
                }
                table {
                    width: 100%;
                    border-collapse: collapse;
                    min-width: 760px;
                }
                th, td {
                    padding: 12px 14px;
                    border-bottom: 1px solid var(--line);
                    text-align: left;
                    vertical-align: top;
                }
                th {
                    color: var(--muted);
                    font-size: 0.88rem;
                    text-transform: uppercase;
                    letter-spacing: 0.04em;
                }
                tbody tr:last-child td { border-bottom: 0; }
                .mono {
                    font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
                }
                .notice {
                    padding: 14px 16px;
                    border-radius: 16px;
                    border: 1px solid #99f6e4;
                    background: var(--accent-soft);
                    color: #115e59;
                }
                .dropzone {
                    display: grid;
                    gap: 12px;
                    padding: 16px;
                    border: 1px dashed #8fb9b3;
                    border-radius: 18px;
                    background: rgba(255, 255, 255, 0.72);
                }
                .dropzone.is-active {
                    border-color: var(--accent);
                    background: rgba(204, 251, 241, 0.45);
                }
                .preview {
                    max-width: 240px;
                    border-radius: 16px;
                    border: 1px solid var(--line);
                    background: white;
                }
                .empty {
                    padding: 18px;
                    border: 1px dashed var(--line);
                    border-radius: 18px;
                    text-align: center;
                    color: var(--muted);
                    background: rgba(255, 255, 255, 0.55);
                }
                .chart-grid {
                    display: grid;
                    grid-template-columns: repeat(2, minmax(0, 1fr));
                    gap: 12px;
                }
                .chart-card img {
                    width: 100%;
                    border-radius: 18px;
                    border: 1px solid var(--line);
                    background: white;
                }
                @media (max-width: 1120px) {
                    .grid, .chart-grid, .two-col, .stats { grid-template-columns: 1fr; }
                }
            </style>
        </head>
        <body>
            <div class="shell">
                <section class="hero">
                    <div class="hero-top">
                        <div>
                            <p style="letter-spacing: 0.12em; text-transform: uppercase; font-size: 0.78rem;">FinBot Dashboard</p>
                            <h1 id="hero-title">Seu painel financeiro completo.</h1>
                            <p>Acompanhe lançamentos, ajuste despesas, gerencie metas, orçamento por categoria, exportação e conversão em um só lugar.</p>
                        </div>
                        <div class="actions">
                            <button class="ghost" id="open-settings" type="button">Configurações</button>
                            <button class="ghost" id="logout" type="button">Sair</button>
                        </div>
                    </div>
                    <form id="period-form" class="toolbar">
                        <label>Mês
                            <input name="month" type="number" min="1" max="12" required>
                        </label>
                        <label>Ano
                            <input name="year" type="number" min="2000" max="2100" required>
                        </label>
                        <button class="primary" type="submit">Atualizar visão</button>
                    </form>
                    <div class="stats" id="summary-stats"></div>
                </section>

                <div class="grid">
                    <section class="card wide-card">
                        <div class="actions" style="justify-content: space-between;">
                            <div>
                                <h2>Lançamentos</h2>
                                <p>Registre novas despesas ou receitas e ajuste o que já foi salvo.</p>
                            </div>
                            <div class="notice">
                                Moeda base atual: <strong id="base-currency-label">BRL</strong>
                            </div>
                        </div>
                        <form id="expense-form">
                            <input name="expense_id" type="hidden">
                            <div class="two-col">
                                <label>Descrição
                                    <input name="description" required placeholder="Ex.: Boliche com amigos">
                                </label>
                                <label>Valor na moeda selecionada
                                    <input name="amount" type="number" step="0.01" min="0.01" required>
                                </label>
                            </div>
                            <div class="two-col">
                                <label>Categoria
                                    <select name="category" required></select>
                                </label>
                                <label>Forma de pagamento
                                    <select name="payment_method" required></select>
                                </label>
                            </div>
                            <div class="two-col">
                                <label>Data do lançamento
                                    <input name="expense_date" type="date">
                                </label>
                                <label>Moeda informada
                                    <select name="currency" required></select>
                                </label>
                            </div>
                            <div class="two-col">
                                <label style="align-content: center;">
                                    <span>Valor dividido</span>
                                    <input name="is_shared" type="checkbox">
                                </label>
                                <label>Percentual da sua parte
                                    <input name="shared_percentage" type="number" min="0.01" max="100" step="0.01" placeholder="Ex.: 50">
                                </label>
                            </div>
                            <div class="actions">
                                <button class="primary" type="submit" id="expense-submit">Salvar lançamento</button>
                                <button class="ghost" type="button" id="expense-reset">Limpar</button>
                            </div>
                        </form>
                        <section class="dropzone" id="receipt-dropzone" tabindex="0">
                            <div>
                                <strong>Ler comprovante por imagem</strong>
                                <p>Cole uma imagem nesta área ou selecione um arquivo para preencher o lançamento automaticamente.</p>
                            </div>
                            <div class="two-col">
                                <label>Selecionar imagem
                                    <input id="receipt-file" type="file" accept="image/*">
                                </label>
                                <label>Observação opcional
                                    <input id="receipt-note" placeholder="Ex.: considerar a data do dia 01/04/2026">
                                </label>
                            </div>
                            <div class="actions">
                                <button class="ghost" id="recognize-receipt" type="button">Ler comprovante</button>
                                <button class="ghost" id="clear-receipt" type="button">Remover imagem</button>
                            </div>
                            <img id="receipt-preview" class="preview" alt="Pré-visualização do comprovante" style="display: none;">
                            <div class="status" id="recognition-status"></div>
                        </section>
                        <div class="status" id="expense-status"></div>
                        <div class="table-wrap">
                            <table>
                                <thead>
                                    <tr>
                                        <th>Data</th>
                                        <th>Descrição</th>
                                        <th>Categoria</th>
                                        <th>Pagamento</th>
                                        <th>Valor</th>
                                        <th>Divisão</th>
                                        <th>Moeda original</th>
                                        <th></th>
                                    </tr>
                                </thead>
                                <tbody id="expenses-table"></tbody>
                            </table>
                        </div>
                    </section>

                    <div class="stack">
                        <section class="card">
                            <h2>Orçamentos, limites por categoria e gráficos</h2>
                            <p>Defina tetos mensais por categoria e acompanhe como os gastos se distribuem no período.</p>
                            <form id="budget-form">
                                <div class="two-col">
                                    <label>Categoria
                                        <select name="category_name">
                                            <option value="">Geral</option>
                                        </select>
                                    </label>
                                    <label>Limite mensal
                                        <input name="monthly_limit" type="number" step="0.01" min="0.01" required>
                                    </label>
                                </div>
                                <button class="primary" type="submit">Salvar orçamento</button>
                            </form>
                            <div class="status" id="budget-status"></div>
                            <div class="list" id="budgets-list"></div>
                            <div class="chart-grid">
                                <div class="chart-card">
                                    <h3>Gastos por categoria</h3>
                                    <img id="chart-categories" alt="Gastos por categoria">
                                </div>
                                <div class="chart-card">
                                    <h3>Maiores gastos</h3>
                                    <img id="chart-top-expenses" alt="Maiores gastos">
                                </div>
                                <div class="chart-card" style="grid-column: 1 / -1;">
                                    <h3>Evolução diária</h3>
                                    <img id="chart-daily" alt="Evolução diária">
                                </div>
                            </div>
                        </section>
                    </div>

                    <div class="stack">
                        <section class="card">
                            <h2>Metas</h2>
                            <p>Cadastre objetivos, faça aportes dedicados e use valores guardados sem misturar com a categorização normal de despesas.</p>
                            <div class="two-col" style="align-items: start;">
                                <div class="stack">
                                    <form id="goal-form">
                                        <label>Descrição da meta
                                            <input name="description" required placeholder="Ex.: Reserva de emergência">
                                        </label>
                                        <div class="two-col">
                                            <label>Valor alvo
                                                <input name="target_amount" type="number" step="0.01" min="0.01" required>
                                            </label>
                                            <label>Prazo
                                                <input name="deadline" type="date" required>
                                            </label>
                                        </div>
                                        <button class="primary" type="submit">Salvar meta</button>
                                    </form>
                                    <div class="status" id="goal-status"></div>
                                    <form id="goal-contribution-form">
                                        <div class="two-col">
                                            <label>Meta para aportar
                                                <select name="goal_id" required></select>
                                            </label>
                                            <label>Valor do aporte
                                                <input name="amount" type="number" step="0.01" min="0.01" required>
                                            </label>
                                        </div>
                                        <div class="two-col">
                                            <label>Data do aporte
                                                <input name="transaction_date" type="date">
                                            </label>
                                            <label>Descrição opcional
                                                <input name="description" placeholder="Ex.: transferência para reserva">
                                            </label>
                                        </div>
                                        <button class="ghost" type="submit">Adicionar aporte</button>
                                    </form>
                                    <div class="status" id="goal-contribution-status"></div>
                                    <form id="goal-withdrawal-form">
                                        <div class="two-col">
                                            <label>Meta de origem
                                                <select name="goal_id" required></select>
                                            </label>
                                            <label>Valor utilizado
                                                <input name="amount" type="number" step="0.01" min="0.01" required>
                                            </label>
                                        </div>
                                        <div class="two-col">
                                            <label>Categoria do gasto
                                                <select name="category" required></select>
                                            </label>
                                            <label>Forma de pagamento
                                                <select name="payment_method" required></select>
                                            </label>
                                        </div>
                                        <div class="two-col">
                                            <label>Data do gasto
                                                <input name="expense_date" type="date">
                                            </label>
                                            <label>Descrição do uso
                                                <input name="description" placeholder="Ex.: consulta médica urgente">
                                            </label>
                                        </div>
                                        <button class="ghost" type="submit">Usar valor da meta</button>
                                    </form>
                                    <div class="status" id="goal-withdrawal-status"></div>
                                </div>
                                <div class="stack">
                                    <div class="list" id="goals-list"></div>
                                    <div class="table-wrap">
                                        <table>
                                            <thead>
                                                <tr>
                                                    <th>Data</th>
                                                    <th>Tipo</th>
                                                    <th>Meta</th>
                                                    <th>Descrição</th>
                                                    <th>Valor</th>
                                                    <th>Vínculo</th>
                                                </tr>
                                            </thead>
                                            <tbody id="goal-transactions-list"></tbody>
                                        </table>
                                    </div>
                                </div>
                            </div>
                        </section>

                        <section class="card">
                            <h2>Conversão de moeda</h2>
                            <p>Use a moeda base definida em configurações e faça conversões rápidas sem sair do painel.</p>
                            <form id="currency-form">
                                <div class="two-col">
                                    <label>Valor
                                        <input name="amount" type="number" step="0.01" min="0.01" required>
                                    </label>
                                    <label>De
                                        <select name="from_currency" required></select>
                                    </label>
                                </div>
                                <label>Para
                                    <select name="to_currency" required></select>
                                </label>
                                <button class="ghost" type="submit">Converter valor</button>
                            </form>
                            <div class="notice" id="currency-result">A conversão aparecerá aqui.</div>
                        </section>

                        <section class="card">
                            <h2>Exportação</h2>
                            <p>Exporte o período selecionado em planilha ou PDF com resumo visual.</p>
                            <div class="actions">
                                <button class="primary" id="export-xlsx" type="button">Exportar XLSX</button>
                                <button class="ghost" id="export-pdf" type="button">Exportar PDF</button>
                            </div>
                            <div class="status" id="export-status"></div>
                        </section>
                    </div>
                </div>
            </div>

            <script>
                const appState = {
                    period: { month: new Date().getMonth() + 1, year: new Date().getFullYear() },
                    payload: null,
                    editingExpenseId: null,
                    receiptImageDataUrl: '',
                };

                async function fetchJson(url, options = {}) {
                    const response = await fetch(url, { credentials: 'same-origin', ...options });
                    const rawText = await response.text();
                    let data = null;
                    if (rawText) {
                        try {
                            data = JSON.parse(rawText);
                        } catch {
                            data = null;
                        }
                    }
                    if (!response.ok) {
                        const fallbackMessage = rawText && !data
                            ? `Erro interno ao processar a solicitação (${response.status}).`
                            : `Erro HTTP ${response.status}.`;
                        throw new Error(data?.detail || data?.message || fallbackMessage);
                    }
                    return data ?? {};
                }

                function money(value) {
                    return new Intl.NumberFormat('pt-BR', {
                        style: 'currency',
                        currency: 'BRL',
                    }).format(Number(value || 0));
                }

                function escapeHtml(value) {
                    return String(value ?? '')
                        .replaceAll('&', '&amp;')
                        .replaceAll('<', '&lt;')
                        .replaceAll('>', '&gt;')
                        .replaceAll('"', '&quot;');
                }

                function selectOptions(items, selectedValue, labelKey = 'name', valueKey = 'code') {
                    return items.map((item) => {
                        const value = item[valueKey];
                        const selected = value === selectedValue ? 'selected' : '';
                        return `<option value="${escapeHtml(value)}" ${selected}>${escapeHtml(item[labelKey])}</option>`;
                    }).join('');
                }

                function categoryOptions(categories, selectedValue, { includeMetas = false, onlyNegative = false } = {}) {
                    return (categories.active || []).filter((item) => {
                        if (!includeMetas && item.name === 'Metas') return false;
                        if (onlyNegative && item.type !== 'Negativo') return false;
                        return true;
                    }).map((item) => {
                        const value = item.name;
                        const label = `${item.name} • ${item.type === 'Negativo' ? 'Gasto' : 'Entrada'}`;
                        const selected = value === selectedValue ? 'selected' : '';
                        return `<option value="${escapeHtml(value)}" ${selected}>${escapeHtml(label)}</option>`;
                    }).join('');
                }

                function setStatus(id, message, isError = false) {
                    const element = document.getElementById(id);
                    element.textContent = message;
                    element.className = isError ? 'status error' : 'status';
                }

                function downloadBase64(filename, base64Content, mimeType) {
                    const link = document.createElement('a');
                    link.href = `data:${mimeType};base64,${base64Content}`;
                    link.download = filename;
                    document.body.appendChild(link);
                    link.click();
                    document.body.removeChild(link);
                }

                function populateExpenseForm(expense) {
                    appState.editingExpenseId = expense.id;
                    document.querySelector('#expense-form [name="expense_id"]').value = expense.id;
                    document.querySelector('#expense-form [name="description"]').value = expense.description;
                    document.querySelector('#expense-form [name="amount"]').value = expense.original_amount || expense.amount;
                    document.querySelector('#expense-form [name="category"]').value = expense.category;
                    document.querySelector('#expense-form [name="payment_method"]').value = expense.payment_method;
                    document.querySelector('#expense-form [name="expense_date"]').value = expense.date;
                    document.querySelector('#expense-form [name="currency"]').value = expense.original_currency || appState.payload.user.base_currency || 'BRL';
                    document.querySelector('#expense-form [name="is_shared"]').checked = !!expense.is_shared;
                    document.querySelector('#expense-form [name="shared_percentage"]').value = expense.shared_percentage || '';
                    document.getElementById('expense-submit').textContent = 'Atualizar lançamento';
                    setStatus('expense-status', 'Você está editando um lançamento salvo.');
                }

                function resetExpenseForm() {
                    appState.editingExpenseId = null;
                    document.getElementById('expense-form').reset();
                    document.querySelector('#expense-form [name="expense_id"]').value = '';
                    document.getElementById('expense-submit').textContent = 'Salvar lançamento';
                    if (appState.payload) {
                        document.querySelector('#expense-form [name="currency"]').value = appState.payload.user.base_currency || 'BRL';
                    }
                    document.querySelector('#expense-form [name="is_shared"]').checked = false;
                    document.querySelector('#expense-form [name="shared_percentage"]').value = '';
                    setStatus('expense-status', '');
                }

                function setReceiptPreview(dataUrl) {
                    appState.receiptImageDataUrl = dataUrl || '';
                    const preview = document.getElementById('receipt-preview');
                    if (!appState.receiptImageDataUrl) {
                        preview.style.display = 'none';
                        preview.removeAttribute('src');
                        return;
                    }
                    preview.src = appState.receiptImageDataUrl;
                    preview.style.display = 'block';
                }

                function clearReceiptImage() {
                    document.getElementById('receipt-file').value = '';
                    document.getElementById('receipt-note').value = '';
                    setReceiptPreview('');
                    setStatus('recognition-status', '');
                }

                function fillExpenseFormFromRecognition(recognized) {
                    if (recognized.description) {
                        document.querySelector('#expense-form [name="description"]').value = recognized.description;
                    }
                    if (recognized.amount) {
                        document.querySelector('#expense-form [name="amount"]').value = recognized.amount;
                    }
                    if (recognized.category) {
                        document.querySelector('#expense-form [name="category"]').value = recognized.category;
                    }
                    if (recognized.payment_method) {
                        document.querySelector('#expense-form [name="payment_method"]').value = recognized.payment_method;
                    }
                    if (recognized.expense_date) {
                        document.querySelector('#expense-form [name="expense_date"]').value = recognized.expense_date;
                    }
                    if (recognized.currency) {
                        document.querySelector('#expense-form [name="currency"]').value = recognized.currency;
                    }
                    document.querySelector('#expense-form [name="is_shared"]').checked = !!recognized.is_shared;
                    document.querySelector('#expense-form [name="shared_percentage"]').value = recognized.shared_percentage || '';
                }

                function readFileAsDataUrl(file) {
                    return new Promise((resolve, reject) => {
                        const reader = new FileReader();
                        reader.onload = () => resolve(String(reader.result || ''));
                        reader.onerror = () => reject(new Error('Nao foi possivel ler a imagem selecionada.'));
                        reader.readAsDataURL(file);
                    });
                }

                function renderBudgets(budgets) {
                    const container = document.getElementById('budgets-list');
                    if (!budgets.length) {
                        container.innerHTML = '<div class="empty">Nenhum orçamento ativo neste momento.</div>';
                        return;
                    }
                    container.innerHTML = budgets.map((item) => `
                        <div class="list-item">
                            <div>
                                <strong>${escapeHtml(item.category)}</strong>
                                <small>Limite ${money(item.limit)} • Gasto ${money(item.spent)} • Restante ${money(item.remaining)}</small>
                            </div>
                            <div class="actions">
                                <span class="mono">${Number(item.percentage).toFixed(1)}%</span>
                                <button class="ghost" type="button" data-remove-budget="${escapeHtml(item.category === 'Geral' ? '' : item.category)}">Remover</button>
                            </div>
                        </div>
                    `).join('');
                }

                function renderGoals(goals) {
                    const container = document.getElementById('goals-list');
                    if (!goals.length) {
                        container.innerHTML = '<div class="empty">Nenhuma meta ativa cadastrada.</div>';
                        return;
                    }
                    container.innerHTML = goals.map((item) => `
                        <div class="list-item">
                            <div>
                                <strong>${escapeHtml(item.description)}</strong>
                                <small>Guardado ${money(item.current_progress)} de ${money(item.target_amount)} • Prazo ${escapeHtml(item.deadline)}</small>
                            </div>
                            <div class="actions">
                                <span class="mono">${Number(item.percentage).toFixed(1)}%</span>
                                <button class="ghost" type="button" data-remove-goal="${escapeHtml(item.description)}">Encerrar</button>
                            </div>
                        </div>
                    `).join('');
                }

                function renderGoalTransactions(transactions) {
                    const container = document.getElementById('goal-transactions-list');
                    if (!transactions.length) {
                        container.innerHTML = '<tr><td colspan="6"><div class="empty">Nenhuma movimentação de meta registrada ainda.</div></td></tr>';
                        return;
                    }
                    container.innerHTML = transactions.map((item) => `
                        <tr>
                            <td>${escapeHtml(item.transaction_date_label)}</td>
                            <td><strong>${item.transaction_type === 'contribution' ? 'Aporte' : 'Uso da meta'}</strong></td>
                            <td>${escapeHtml(item.goal_description)}</td>
                            <td>${escapeHtml(item.description || '-')}</td>
                            <td class="mono">${item.transaction_type === 'contribution' ? '+' : '-'} ${money(item.amount)}</td>
                            <td>${item.related_expense_id ? `Lançamento #${item.related_expense_id}` : '-'}</td>
                        </tr>
                    `).join('');
                }

                function openChartModal(title, imageId) {
                    const modal = document.getElementById('chart-modal');
                    const modalTitle = document.getElementById('chart-modal-title');
                    const modalImage = document.getElementById('chart-modal-image');
                    const image = document.getElementById(imageId);
                    if (!image?.src) return;
                    modalTitle.textContent = title;
                    modalImage.src = image.src;
                    modalImage.alt = title;
                    modal.classList.add('is-open');
                    modal.setAttribute('aria-hidden', 'false');
                }

                function closeChartModal() {
                    const modal = document.getElementById('chart-modal');
                    modal.classList.remove('is-open');
                    modal.setAttribute('aria-hidden', 'true');
                }

                function renderExpenses(expenses) {
                    const body = document.getElementById('expenses-table');
                    if (!expenses.length) {
                        body.innerHTML = '<tr><td colspan="8"><div class="empty">Nenhum lançamento encontrado para o período.</div></td></tr>';
                        return;
                    }
                    body.innerHTML = expenses.map((expense) => `
                        <tr>
                            <td>${escapeHtml(expense.date_label)}</td>
                            <td><strong>${escapeHtml(expense.description)}</strong><br><small>${escapeHtml(expense.type)}${expense.funding_goal_description ? ` • Origem: ${escapeHtml(expense.funding_goal_description)}` : ''}${expense.goal_description ? ` • Meta legado: ${escapeHtml(expense.goal_description)}` : ''}</small></td>
                            <td>${escapeHtml(expense.category)}</td>
                            <td>${escapeHtml(expense.payment_method)}</td>
                            <td>${money(expense.amount)}</td>
                            <td>${expense.is_shared ? `Sim • ${Number(expense.shared_percentage || 0).toFixed(0)}% seu` : 'Nao'}</td>
                            <td>${expense.original_currency ? `${escapeHtml(expense.original_currency)} ${Number(expense.original_amount || 0).toFixed(2)}` : 'BRL'}</td>
                            <td><button class="ghost" type="button" data-edit-expense="${expense.id}">Editar</button></td>
                        </tr>
                    `).join('');
                }

                function renderState(payload) {
                    appState.payload = payload;
                    document.getElementById('hero-title').textContent = `Painel de ${payload.user.display_name || payload.user.name || 'sua conta'}`;
                    document.querySelector('#period-form [name="month"]').value = payload.period.month;
                    document.querySelector('#period-form [name="year"]').value = payload.period.year;
                    document.getElementById('summary-stats').innerHTML = `
                        <div class="stat"><strong>${money(payload.summary.expenses)}</strong><span>Saídas no período</span></div>
                        <div class="stat"><strong>${money(payload.summary.income)}</strong><span>Entradas no período</span></div>
                        <div class="stat"><strong>${money(payload.summary.balance)}</strong><span>Saldo do período</span></div>
                        <div class="stat"><strong>${payload.summary.expense_count}</strong><span>Lançamentos no período</span></div>
                    `;
                    document.getElementById('base-currency-label').textContent = payload.user.base_currency || 'BRL';

                    const currencyOptions = selectOptions(payload.currencies, payload.user.base_currency || 'BRL');
                    document.querySelector('#expense-form [name="currency"]').innerHTML = currencyOptions;
                    document.querySelector('#currency-form [name="from_currency"]').innerHTML = selectOptions(
                        payload.currencies,
                        payload.user.base_currency || 'BRL',
                    );
                    document.querySelector('#currency-form [name="to_currency"]').innerHTML = selectOptions(payload.currencies, 'BRL');

                    const categoryHtml = categoryOptions(payload.categories, '');
                    const negativeCategoryHtml = categoryOptions(payload.categories, '', { onlyNegative: true });
                    document.querySelector('#expense-form [name="category"]').innerHTML = categoryHtml;
                    document.querySelector('#budget-form [name="category_name"]').innerHTML = '<option value="">Geral</option>' + categoryHtml;
                    const goalOptions = '<option value="">Selecione uma meta</option>' +
                        (payload.goals || []).map((item) =>
                            `<option value="${item.goal_id}">${escapeHtml(item.description)}</option>`
                        ).join('');
                    document.querySelector('#goal-contribution-form [name="goal_id"]').innerHTML = goalOptions;
                    document.querySelector('#goal-withdrawal-form [name="goal_id"]').innerHTML = goalOptions;
                    document.querySelector('#goal-withdrawal-form [name="category"]').innerHTML = negativeCategoryHtml;

                    const paymentMethodOptions = payload.payment_methods.map((item) => `<option value="${escapeHtml(item)}">${escapeHtml(item)}</option>`).join('');
                    document.querySelector('#expense-form [name="payment_method"]').innerHTML = paymentMethodOptions;
                    document.querySelector('#goal-withdrawal-form [name="payment_method"]').innerHTML = paymentMethodOptions;

                    renderExpenses(payload.expenses || []);
                    renderBudgets(payload.budgets || []);
                    renderGoals(payload.goals || []);
                    renderGoalTransactions(payload.goal_transactions || []);
                    document.getElementById('chart-categories').src = payload.charts.categories;
                    document.getElementById('chart-top-expenses').src = payload.charts.top_expenses;
                    document.getElementById('chart-daily').src = payload.charts.daily;

                    resetExpenseForm();
                }

                async function loadState() {
                    const payload = await fetchJson(`/dashboard/state?month=${appState.period.month}&year=${appState.period.year}`);
                    renderState(payload);
                }

                document.getElementById('period-form').addEventListener('submit', async (event) => {
                    event.preventDefault();
                    appState.period.month = Number(document.querySelector('#period-form [name="month"]').value);
                    appState.period.year = Number(document.querySelector('#period-form [name="year"]').value);
                    await loadState();
                });

                document.getElementById('expense-form').addEventListener('submit', async (event) => {
                    event.preventDefault();
                    const form = event.currentTarget;
                    const data = Object.fromEntries(new FormData(form).entries());
                    const isShared = document.querySelector('#expense-form [name="is_shared"]').checked;
                    const sharedPercentageRaw = String(data.shared_percentage || '').trim();
                    const payload = {
                        description: data.description,
                        amount: Number(data.amount),
                        category: data.category,
                        payment_method: data.payment_method,
                        expense_date: data.expense_date || null,
                        currency: data.currency,
                        is_shared: isShared,
                        shared_percentage: isShared && sharedPercentageRaw ? Number(sharedPercentageRaw) : null,
                    };
                    try {
                        if (appState.editingExpenseId) {
                            await fetchJson(`/dashboard/expenses/${appState.editingExpenseId}`, {
                                method: 'PATCH',
                                headers: {'Content-Type': 'application/json'},
                                body: JSON.stringify(payload),
                            });
                            setStatus('expense-status', 'Lançamento atualizado com sucesso.');
                        } else {
                            await fetchJson('/dashboard/expenses', {
                                method: 'POST',
                                headers: {'Content-Type': 'application/json'},
                                body: JSON.stringify(payload),
                            });
                            setStatus('expense-status', 'Lançamento salvo com sucesso.');
                        }
                        await loadState();
                    } catch (error) {
                        setStatus('expense-status', error.message, true);
                    }
                });

                document.getElementById('expense-reset').addEventListener('click', resetExpenseForm);

                document.getElementById('receipt-file').addEventListener('change', async (event) => {
                    const file = event.currentTarget.files?.[0];
                    if (!file) {
                        setReceiptPreview('');
                        return;
                    }
                    try {
                        setReceiptPreview(await readFileAsDataUrl(file));
                        setStatus('recognition-status', 'Imagem pronta para reconhecimento.');
                    } catch (error) {
                        setStatus('recognition-status', error.message, true);
                    }
                });

                const dropzone = document.getElementById('receipt-dropzone');
                dropzone.addEventListener('paste', async (event) => {
                    const imageItem = Array.from(event.clipboardData?.items || []).find((item) => item.type.startsWith('image/'));
                    if (!imageItem) {
                        return;
                    }
                    event.preventDefault();
                    const file = imageItem.getAsFile();
                    if (!file) {
                        return;
                    }
                    try {
                        setReceiptPreview(await readFileAsDataUrl(file));
                        setStatus('recognition-status', 'Imagem colada com sucesso. Agora voce pode reconhecer o comprovante.');
                    } catch (error) {
                        setStatus('recognition-status', error.message, true);
                    }
                });
                dropzone.addEventListener('dragenter', () => dropzone.classList.add('is-active'));
                dropzone.addEventListener('dragleave', () => dropzone.classList.remove('is-active'));
                dropzone.addEventListener('dragover', (event) => {
                    event.preventDefault();
                    dropzone.classList.add('is-active');
                });
                dropzone.addEventListener('drop', async (event) => {
                    event.preventDefault();
                    dropzone.classList.remove('is-active');
                    const file = Array.from(event.dataTransfer?.files || []).find((item) => item.type.startsWith('image/'));
                    if (!file) {
                        setStatus('recognition-status', 'Arraste uma imagem valida para esta area.', true);
                        return;
                    }
                    try {
                        setReceiptPreview(await readFileAsDataUrl(file));
                        setStatus('recognition-status', 'Imagem pronta para reconhecimento.');
                    } catch (error) {
                        setStatus('recognition-status', error.message, true);
                    }
                });

                document.getElementById('clear-receipt').addEventListener('click', () => {
                    clearReceiptImage();
                });

                document.getElementById('recognize-receipt').addEventListener('click', async () => {
                    if (!appState.receiptImageDataUrl) {
                        setStatus('recognition-status', 'Selecione ou cole uma imagem antes de continuar.', true);
                        return;
                    }
                    try {
                        setStatus('recognition-status', 'Lendo comprovante...');
                        const payload = await fetchJson('/dashboard/expenses/recognize', {
                            method: 'POST',
                            headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify({
                                image_base64: appState.receiptImageDataUrl,
                                additional_text: document.getElementById('receipt-note').value || '',
                            }),
                        });
                        fillExpenseFormFromRecognition(payload.recognized);
                        setStatus('recognition-status', 'Comprovante lido. Revise os campos antes de salvar.');
                    } catch (error) {
                        setStatus('recognition-status', error.message, true);
                    }
                });

                document.getElementById('budget-form').addEventListener('submit', async (event) => {
                    event.preventDefault();
                    const form = event.currentTarget;
                    const data = Object.fromEntries(new FormData(form).entries());
                    try {
                        await fetchJson('/dashboard/budgets', {
                            method: 'POST',
                            headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify({
                                category_name: data.category_name || null,
                                monthly_limit: Number(data.monthly_limit),
                            }),
                        });
                        setStatus('budget-status', 'Orçamento salvo com sucesso.');
                        form.reset();
                        await loadState();
                    } catch (error) {
                        setStatus('budget-status', error.message, true);
                    }
                });

                document.getElementById('goal-form').addEventListener('submit', async (event) => {
                    event.preventDefault();
                    const form = event.currentTarget;
                    const data = Object.fromEntries(new FormData(form).entries());
                    try {
                        await fetchJson('/dashboard/goals', {
                            method: 'POST',
                            headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify({
                                description: data.description,
                                target_amount: Number(data.target_amount),
                                deadline: data.deadline,
                            }),
                        });
                        setStatus('goal-status', 'Meta salva com sucesso.');
                        form.reset();
                        applyGoalDateConstraints();
                        await loadState();
                    } catch (error) {
                        setStatus('goal-status', error.message, true);
                    }
                });

                document.getElementById('goal-contribution-form').addEventListener('submit', async (event) => {
                    event.preventDefault();
                    const form = event.currentTarget;
                    const data = Object.fromEntries(new FormData(form).entries());
                    try {
                        await fetchJson('/dashboard/goals/contribute', {
                            method: 'POST',
                            headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify({
                                goal_id: Number(data.goal_id),
                                amount: Number(data.amount),
                                description: data.description || null,
                                transaction_date: data.transaction_date || null,
                            }),
                        });
                        setStatus('goal-contribution-status', 'Aporte registrado com sucesso.');
                        form.reset();
                        await loadState();
                    } catch (error) {
                        setStatus('goal-contribution-status', error.message, true);
                    }
                });

                document.getElementById('goal-withdrawal-form').addEventListener('submit', async (event) => {
                    event.preventDefault();
                    const form = event.currentTarget;
                    const data = Object.fromEntries(new FormData(form).entries());
                    try {
                        await fetchJson('/dashboard/goals/withdraw', {
                            method: 'POST',
                            headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify({
                                goal_id: Number(data.goal_id),
                                amount: Number(data.amount),
                                category: data.category,
                                payment_method: data.payment_method,
                                expense_date: data.expense_date || null,
                                description: data.description || null,
                            }),
                        });
                        setStatus('goal-withdrawal-status', 'Uso da meta registrado com sucesso.');
                        form.reset();
                        await loadState();
                    } catch (error) {
                        setStatus('goal-withdrawal-status', error.message, true);
                    }
                });

                document.getElementById('currency-form').addEventListener('submit', async (event) => {
                    event.preventDefault();
                    const data = Object.fromEntries(new FormData(event.currentTarget).entries());
                    try {
                        const payload = await fetchJson('/dashboard/currency/convert', {
                            method: 'POST',
                            headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify({
                                amount: Number(data.amount),
                                from_currency: data.from_currency,
                                to_currency: data.to_currency,
                            }),
                        });
                        document.getElementById('currency-result').innerHTML =
                            `<strong>${Number(payload.original_amount).toFixed(2)} ${escapeHtml(payload.original_currency)}</strong> ` +
                            `equivale a <strong>${Number(payload.converted_amount).toFixed(2)} ${escapeHtml(payload.target_currency)}</strong><br>` +
                            `Taxa usada: ${Number(payload.exchange_rate).toFixed(4)}`;
                    } catch (error) {
                        document.getElementById('currency-result').textContent = error.message;
                    }
                });

                async function exportPeriod(format) {
                    try {
                        const payload = await fetchJson('/dashboard/export', {
                            method: 'POST',
                            headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify({
                                format,
                                month: appState.period.month,
                                year: appState.period.year,
                            }),
                        });
                        downloadBase64(payload.filename, payload.file_base64, payload.mimetype);
                        setStatus('export-status', `Arquivo ${payload.filename} gerado com sucesso.`);
                    } catch (error) {
                        setStatus('export-status', error.message, true);
                    }
                }

                document.getElementById('export-xlsx').addEventListener('click', () => exportPeriod('xlsx'));
                document.getElementById('export-pdf').addEventListener('click', () => exportPeriod('pdf'));

                document.body.addEventListener('click', async (event) => {
                    const target = event.target.closest('button');
                    if (!target) return;
                    if (target.dataset.editExpense) {
                        const expense = (appState.payload.expenses || []).find((item) => String(item.id) === target.dataset.editExpense);
                        if (expense) populateExpenseForm(expense);
                        return;
                    }
                    if (target.dataset.removeBudget !== undefined) {
                        try {
                            await fetchJson('/dashboard/budgets', {
                                method: 'DELETE',
                                headers: {'Content-Type': 'application/json'},
                                body: JSON.stringify({ category_name: target.dataset.removeBudget || null }),
                            });
                            setStatus('budget-status', 'Orçamento removido.');
                            await loadState();
                        } catch (error) {
                            setStatus('budget-status', error.message, true);
                        }
                        return;
                    }
                    if (target.dataset.removeGoal) {
                        try {
                            await fetchJson('/dashboard/goals', {
                                method: 'DELETE',
                                headers: {'Content-Type': 'application/json'},
                                body: JSON.stringify({ description: target.dataset.removeGoal }),
                            });
                            setStatus('goal-status', 'Meta encerrada.');
                            await loadState();
                        } catch (error) {
                            setStatus('goal-status', error.message, true);
                        }
                    }
                });

                document.getElementById('open-settings').addEventListener('click', () => {
                    window.location.href = '/web/settings';
                });

                document.getElementById('logout').addEventListener('click', async () => {
                    await fetchJson('/auth/logout', { method: 'POST' });
                    window.location.href = '/web/login';
                });

                function applyGoalDateConstraints() {
                    const deadlineInput = document.querySelector('#goal-form [name="deadline"]');
                    const tomorrow = new Date();
                    tomorrow.setDate(tomorrow.getDate() + 1);
                    const isoDate = tomorrow.toISOString().slice(0, 10);
                    deadlineInput.min = isoDate;
                    if (!deadlineInput.value || deadlineInput.value < isoDate) {
                        deadlineInput.value = isoDate;
                    }
                }

                loadState().catch((error) => {
                    document.getElementById('summary-stats').innerHTML = `<div class="empty">${escapeHtml(error.message)}</div>`;
                });
                applyGoalDateConstraints();
            </script>
        </body>
        </html>
        """
    )


@app.get("/settings/state")
async def settings_state(request: Request):
    """Return the current state for the authenticated settings panel."""
    await _get_current_web_user(request)
    async with async_session() as session:
        refreshed_user = await _get_current_web_user_in_session(session, request)
        usage_summary = await rate_limit_service.get_usage_summary(refreshed_user)
        authorized_phones = await user_service.list_authorized_phones(session, refreshed_user)
        return _build_settings_payload(
            refreshed_user, usage_summary, authorized_phones=authorized_phones
        )


@app.post("/settings/profile")
async def settings_profile(request: Request, payload: SettingsProfileRequest):
    """Update the authenticated user's profile from the settings panel."""
    await _get_current_web_user(request)
    async with async_session() as session:
        refreshed_user = await _get_current_web_user_in_session(session, request)
        try:
            updated_user = await user_service.update_web_profile(
                session,
                refreshed_user,
                name=payload.name,
                display_name=payload.display_name,
                timezone=payload.timezone,
                email=payload.email,
            )
            updated_user = await user_service.update_base_currency(
                session,
                updated_user,
                base_currency=_normalize_currency_code(payload.base_currency),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        usage_summary = await rate_limit_service.get_usage_summary(updated_user)
        authorized_phones = await user_service.list_authorized_phones(session, updated_user)
        return _build_settings_payload(
            updated_user, usage_summary, authorized_phones=authorized_phones
        )


@app.post("/settings/notifications")
async def settings_notifications(request: Request, payload: SettingsNotificationsRequest):
    """Update notification preferences for the authenticated user."""
    await _get_current_web_user(request)
    async with async_session() as session:
        refreshed_user = await _get_current_web_user_in_session(session, request)
        updated_user = await user_service.update_notification_preferences(
            session,
            refreshed_user,
            budget_alerts=payload.budget_alerts,
            recurring_reminders=payload.recurring_reminders,
            goal_updates=payload.goal_updates,
        )
        usage_summary = await rate_limit_service.get_usage_summary(updated_user)
        authorized_phones = await user_service.list_authorized_phones(session, updated_user)
        return _build_settings_payload(
            updated_user, usage_summary, authorized_phones=authorized_phones
        )


@app.post("/settings/limits")
async def settings_limits(request: Request, payload: SettingsLimitsRequest):
    """Update daily user limits from the web settings panel."""
    await _get_current_web_user(request)
    async with async_session() as session:
        refreshed_user = await _get_current_web_user_in_session(session, request)
        try:
            updated_user = await user_service.update_limits(
                session,
                refreshed_user,
                limits_enabled=payload.limits_enabled,
                daily_text_limit=payload.daily_text_limit,
                daily_media_limit=payload.daily_media_limit,
                daily_ai_limit=payload.daily_ai_limit,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        usage_summary = await rate_limit_service.get_usage_summary(updated_user)
        authorized_phones = await user_service.list_authorized_phones(session, updated_user)
        return _build_settings_payload(
            updated_user, usage_summary, authorized_phones=authorized_phones
        )


@app.post("/settings/authorized-phones")
async def settings_add_authorized_phone(
    request: Request,
    payload: SettingsAuthorizedPhoneRequest,
):
    """Add an additional authorized WhatsApp number to the authenticated account."""
    await _get_current_web_user(request)
    async with async_session() as session:
        refreshed_user = await _get_current_web_user_in_session(session, request)
        try:
            authorized_phones = await user_service.add_authorized_phone(
                session,
                refreshed_user,
                payload.phone,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        usage_summary = await rate_limit_service.get_usage_summary(refreshed_user)
        return _build_settings_payload(
            refreshed_user, usage_summary, authorized_phones=authorized_phones
        )


@app.delete("/settings/authorized-phones")
async def settings_remove_authorized_phone(
    request: Request,
    payload: SettingsAuthorizedPhoneRequest,
):
    """Remove an additional authorized WhatsApp number from the account."""
    await _get_current_web_user(request)
    async with async_session() as session:
        refreshed_user = await _get_current_web_user_in_session(session, request)
        try:
            authorized_phones = await user_service.remove_authorized_phone(
                session,
                refreshed_user,
                payload.phone,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        usage_summary = await rate_limit_service.get_usage_summary(refreshed_user)
        return _build_settings_payload(
            refreshed_user, usage_summary, authorized_phones=authorized_phones
        )


@app.post("/settings/backup/export")
async def settings_backup_export(request: Request):
    """Export the authenticated user's backup as JSON payload for the browser."""
    await _get_current_web_user(request)
    async with async_session() as session:
        refreshed_user = await _get_current_web_user_in_session(session, request)
        result = await backup_service.export_user_backup(session, refreshed_user.phone)
        if not result["success"]:
            raise HTTPException(status_code=400, detail=result["error"])
        return {
            "status": "ok",
            "filename": result["filename"],
            "backup_json": json.dumps(result["backup_data"], ensure_ascii=True, indent=2),
        }


@app.post("/settings/backup/import/preview")
async def settings_backup_import_preview(
    request: Request,
    payload: SettingsBackupImportRequest,
):
    """Preview a backup restore before applying it in the authenticated settings panel."""
    await _get_current_web_user(request)
    document_bytes = payload.backup_json.encode("utf-8")
    parsed = backup_service.parse_backup_document(document_bytes)
    if not parsed["success"]:
        raise HTTPException(status_code=400, detail=parsed["error"])

    async with async_session() as session:
        refreshed_user = await _get_current_web_user_in_session(session, request)
        summary = backup_service.summarize_backup(parsed["backup_data"])
        store_result = await backup_service.store_temporary_backup(parsed["backup_data"])
        if not store_result["success"]:
            raise HTTPException(status_code=503, detail=store_result["error"])
        return {
            "status": "ok",
            "backup_ref": store_result["backup_ref"],
            "summary": summary,
            "requires_migration_confirmation": bool(
                (
                    summary.get("source_backup_owner_id")
                    and refreshed_user.backup_owner_id
                    and str(summary.get("source_backup_owner_id")).strip()
                    != str(refreshed_user.backup_owner_id).strip()
                )
                or (
                    summary.get("source_phone")
                    and str(summary.get("source_phone")).strip() != refreshed_user.phone
                )
            ),
        }


@app.post("/settings/backup/import/apply")
async def settings_backup_import_apply(
    request: Request,
    payload: SettingsBackupApplyRequest,
):
    """Apply a previously previewed backup restore for the authenticated user."""
    await _get_current_web_user(request)
    async with async_session() as session:
        refreshed_user = await _get_current_web_user_in_session(session, request)
        load_result = await backup_service.load_temporary_backup(payload.backup_ref)
        if not load_result["success"]:
            raise HTTPException(status_code=400, detail=load_result["error"])

        backup_data = load_result["backup_data"]
        summary = backup_service.summarize_backup(backup_data)
        source_phone = str(summary.get("source_phone") or "").strip() or None
        source_backup_owner_id = str(summary.get("source_backup_owner_id") or "").strip()
        requires_migration_confirmation = bool(
            (
                source_backup_owner_id
                and refreshed_user.backup_owner_id
                and source_backup_owner_id != refreshed_user.backup_owner_id
            )
            or (source_phone and source_phone != refreshed_user.phone)
        )

        if requires_migration_confirmation and not payload.explicit_migration_confirmation:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Este backup pertence a outro numero ou perfil logico. "
                    "Confirme explicitamente a migracao antes de aplicar."
                ),
            )

        result = await backup_service.restore_user_backup(
            session, refreshed_user.phone, backup_data
        )
        await backup_service.delete_temporary_backup(payload.backup_ref)

        if not result["success"]:
            await backup_service.record_restore_audit(
                session,
                target_phone=refreshed_user.phone,
                source_phone=source_phone,
                status="failed",
                requires_migration_confirmation=requires_migration_confirmation,
                explicit_migration_confirmation=payload.explicit_migration_confirmation,
                error_message=result.get("error", "Erro desconhecido"),
            )
            raise HTTPException(status_code=400, detail=result.get("error", "Erro desconhecido"))

        if (
            payload.explicit_migration_confirmation
            and source_backup_owner_id
            and refreshed_user.backup_owner_id != source_backup_owner_id
        ):
            refreshed_user = await user_service.adopt_backup_owner_identity(
                session,
                refreshed_user,
                source_backup_owner_id,
            )

        await backup_service.record_restore_audit(
            session,
            target_phone=refreshed_user.phone,
            source_phone=source_phone,
            status="restored",
            requires_migration_confirmation=requires_migration_confirmation,
            explicit_migration_confirmation=payload.explicit_migration_confirmation,
            restored_counts=result["restored"],
        )
        usage_summary = await rate_limit_service.get_usage_summary(refreshed_user)
        return {
            "status": "ok",
            "restored": result["restored"],
            "settings": _build_settings_payload(
                refreshed_user,
                usage_summary,
                authorized_phones=await user_service.list_authorized_phones(
                    session, refreshed_user
                ),
            ),
        }


@app.get("/dashboard/state")
async def dashboard_state(
    request: Request,
    month: int | None = None,
    year: int | None = None,
):
    """Return the authenticated dashboard state."""
    await _get_current_web_user(request)
    async with async_session() as session:
        refreshed_user = await _get_current_web_user_in_session(session, request)
        return await _build_dashboard_payload(
            session,
            refreshed_user,
            month=month,
            year=year,
        )


@app.post("/dashboard/profile/base-currency")
async def dashboard_update_base_currency(
    request: Request,
    payload: DashboardBaseCurrencyRequest,
):
    """Update the preferred base currency for the authenticated user."""
    await _get_current_web_user(request)
    normalized_currency = _normalize_currency_code(payload.base_currency)
    async with async_session() as session:
        refreshed_user = await _get_current_web_user_in_session(session, request)
        try:
            updated_user = await user_service.update_base_currency(
                session,
                refreshed_user,
                base_currency=normalized_currency,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {
            "status": "ok",
            "base_currency": updated_user.base_currency,
        }


@app.post("/dashboard/expenses")
async def dashboard_create_expense(
    request: Request,
    payload: DashboardExpenseCreateRequest,
):
    """Create a new expense or income from the web dashboard."""
    await _get_current_web_user(request)
    async with async_session() as session:
        refreshed_user = await _get_current_web_user_in_session(session, request)
        currency_code = _normalize_currency_code(payload.currency, refreshed_user.base_currency)
        amount_decimal = Decimal(str(payload.amount))
        if amount_decimal <= 0:
            raise HTTPException(status_code=400, detail="Valor invalido.")
        if payload.category == "Metas":
            raise HTTPException(
                status_code=400,
                detail="Use o painel de metas para registrar aportes, em vez da categoria Metas no lancamento comum.",
            )

        expense_data: dict[str, Any] = {
            "description": payload.description,
            "amount": amount_decimal,
            "category": payload.category,
            "payment_method": payload.payment_method,
            "expense_date": payload.expense_date,
            "is_shared": payload.is_shared,
            "shared_percentage": payload.shared_percentage,
            "goal_id": None,
        }

        if currency_code != "BRL":
            conversion_result = await currency_service.convert_to_brl(
                amount_decimal,
                currency_code,
                user=refreshed_user,
            )
            if not conversion_result["success"]:
                raise HTTPException(status_code=400, detail=conversion_result["error"])
            expense_data.update(
                {
                    "amount": conversion_result["converted_amount"],
                    "original_currency": currency_code,
                    "original_amount": amount_decimal,
                    "exchange_rate": conversion_result["exchange_rate"],
                }
            )

        result = await expense_service.create_expense(session, refreshed_user.phone, expense_data)
        if not result["success"]:
            raise HTTPException(status_code=400, detail=result["error"])
        return {"status": "ok", **result}


@app.post("/dashboard/expenses/recognize")
async def dashboard_recognize_expense(
    request: Request,
    payload: DashboardExpenseRecognitionRequest,
):
    """Recognize expense fields from a pasted or uploaded image in the dashboard."""
    await _get_current_web_user(request)
    async with async_session() as session:
        refreshed_user = await _get_current_web_user_in_session(session, request)

        media_usage = await rate_limit_service.check_and_increment(
            refreshed_user,
            "daily_media_limit",
        )
        if not media_usage["allowed"]:
            raise HTTPException(
                status_code=429,
                detail=rate_limit_service.format_limit_reached_message(
                    "daily_media_limit",
                    media_usage,
                ),
            )

        ai_usage = await rate_limit_service.check_and_increment(
            refreshed_user,
            "daily_ai_limit",
        )
        if not ai_usage["allowed"]:
            raise HTTPException(
                status_code=429,
                detail=rate_limit_service.format_limit_reached_message(
                    "daily_ai_limit",
                    ai_usage,
                ),
            )

        image_bytes = _decode_base64_media_payload(payload.image_base64)
        result = await ai_service.process_image(
            image_bytes,
            payload.additional_text or "",
            user=refreshed_user,
        )
        if not result.get("success"):
            raise HTTPException(
                status_code=400,
                detail=result.get("error") or "Nao foi possivel reconhecer a imagem enviada.",
            )

        expense_data = dict(result.get("data") or {})
        if result.get("intent") != "register_expense" or not expense_data.get("amount"):
            raise HTTPException(
                status_code=400,
                detail="Nao foi possivel identificar um lancamento valido na imagem.",
            )

        return {
            "status": "ok",
            "recognized": {
                "description": expense_data.get("description") or "",
                "amount": expense_data.get("amount"),
                "category": expense_data.get("category") or "",
                "payment_method": expense_data.get("payment_method") or "",
                "expense_date": expense_data.get("expense_date") or "",
                "currency": expense_data.get("currency") or refreshed_user.base_currency,
                "installments": expense_data.get("installments"),
                "is_shared": bool(expense_data.get("is_shared")),
                "shared_percentage": expense_data.get("shared_percentage"),
            },
        }


@app.patch("/dashboard/expenses/{expense_id}")
async def dashboard_update_expense(
    request: Request,
    expense_id: int,
    payload: DashboardExpenseUpdateRequest,
):
    """Update an existing expense from the web dashboard."""
    await _get_current_web_user(request)
    async with async_session() as session:
        refreshed_user = await _get_current_web_user_in_session(session, request)
        update_data: dict[str, Any] = {}

        if payload.description is not None:
            update_data["new_description"] = payload.description
        if payload.category is not None:
            if payload.category == "Metas":
                raise HTTPException(
                    status_code=400,
                    detail="Use o painel de metas para movimentar aportes ou resgates, em vez de recategorizar um gasto para Metas.",
                )
            update_data["new_category"] = payload.category
        if payload.payment_method is not None:
            update_data["new_payment_method"] = payload.payment_method
        if payload.expense_date is not None:
            update_data["new_expense_date"] = payload.expense_date
        if payload.is_shared is not None:
            update_data["new_is_shared"] = payload.is_shared
            update_data["new_shared_percentage"] = (
                payload.shared_percentage if payload.is_shared else None
            )
        if payload.goal_id is not None or payload.category is not None:
            update_data["new_goal_id"] = None

        if payload.amount is not None:
            amount_decimal = Decimal(str(payload.amount))
            if amount_decimal <= 0:
                raise HTTPException(status_code=400, detail="Valor invalido.")
            requested_currency = payload.currency or refreshed_user.base_currency
            currency_code = _normalize_currency_code(
                requested_currency, refreshed_user.base_currency
            )
            if currency_code == "BRL":
                update_data["new_amount"] = amount_decimal
                update_data["new_original_currency"] = ""
            else:
                conversion_result = await currency_service.convert_to_brl(
                    amount_decimal,
                    currency_code,
                    user=refreshed_user,
                )
                if not conversion_result["success"]:
                    raise HTTPException(status_code=400, detail=conversion_result["error"])
                update_data.update(
                    {
                        "new_amount": conversion_result["converted_amount"],
                        "new_original_currency": currency_code,
                        "new_original_amount": amount_decimal,
                        "new_exchange_rate": conversion_result["exchange_rate"],
                    }
                )
        elif payload.currency is not None:
            currency_code = _normalize_currency_code(payload.currency, refreshed_user.base_currency)
            if currency_code == "BRL":
                update_data["new_original_currency"] = ""

        result = await expense_service.update_expense(
            session,
            refreshed_user.phone,
            expense_id,
            update_data,
        )
        if not result["success"]:
            raise HTTPException(status_code=400, detail=result["error"])
        return {"status": "ok", **result}


@app.delete("/dashboard/expenses/{expense_id}")
async def dashboard_delete_expense(
    request: Request,
    expense_id: int,
):
    """Delete an existing expense from the web dashboard."""
    await _get_current_web_user(request)
    async with async_session() as session:
        refreshed_user = await _get_current_web_user_in_session(session, request)
        result = await expense_service.delete_expense(session, refreshed_user.phone, expense_id)
        if not result["success"]:
            raise HTTPException(status_code=400, detail=result["error"])
        return {"status": "ok", **result}


@app.post("/dashboard/budgets")
async def dashboard_create_budget(
    request: Request,
    payload: DashboardBudgetRequest,
):
    """Create or update a category budget from the web dashboard."""
    await _get_current_web_user(request)
    async with async_session() as session:
        refreshed_user = await _get_current_web_user_in_session(session, request)
        result = await budget_service.create_budget(
            session,
            refreshed_user.phone,
            payload.category_name,
            Decimal(str(payload.monthly_limit)),
        )
        if not result["success"]:
            raise HTTPException(status_code=400, detail=result["error"])
        return {"status": "ok", **result}


@app.delete("/dashboard/budgets")
async def dashboard_delete_budget(
    request: Request,
    payload: DashboardBudgetDeleteRequest,
):
    """Remove a category budget from the web dashboard."""
    await _get_current_web_user(request)
    async with async_session() as session:
        refreshed_user = await _get_current_web_user_in_session(session, request)
        result = await budget_service.remove_budget(
            session,
            refreshed_user.phone,
            payload.category_name,
        )
        if not result["success"]:
            raise HTTPException(status_code=400, detail=result["error"])
        return {"status": "ok", **result}


@app.post("/dashboard/goals")
async def dashboard_create_goal(
    request: Request,
    payload: DashboardGoalRequest,
):
    """Create a savings goal from the web dashboard."""
    await _get_current_web_user(request)
    try:
        deadline = date.fromisoformat(payload.deadline)
    except ValueError:
        raise HTTPException(status_code=400, detail="Prazo invalido.")

    async with async_session() as session:
        refreshed_user = await _get_current_web_user_in_session(session, request)
        result = await goal_service.create_goal(
            session,
            refreshed_user.phone,
            payload.description,
            Decimal(str(payload.target_amount)),
            deadline,
        )
        if not result["success"]:
            raise HTTPException(status_code=400, detail=result["error"])
        return {"status": "ok", **result}


@app.post("/dashboard/goals/contribute")
async def dashboard_contribute_to_goal(
    request: Request,
    payload: DashboardGoalContributionRequest,
):
    """Add a new contribution movement to a goal."""
    await _get_current_web_user(request)
    transaction_date = None
    if payload.transaction_date:
        try:
            transaction_date = date.fromisoformat(payload.transaction_date)
        except ValueError:
            raise HTTPException(status_code=400, detail="Data do aporte invalida.")

    async with async_session() as session:
        refreshed_user = await _get_current_web_user_in_session(session, request)
        result = await goal_service.contribute_to_goal(
            session,
            refreshed_user.phone,
            payload.goal_id,
            Decimal(str(payload.amount)),
            description=payload.description,
            transaction_date=transaction_date,
        )
        if not result["success"]:
            raise HTTPException(status_code=400, detail=result["error"])
        return {"status": "ok", **result}


@app.post("/dashboard/goals/withdraw")
async def dashboard_withdraw_from_goal(
    request: Request,
    payload: DashboardGoalWithdrawalRequest,
):
    """Use money from a goal and register the corresponding expense."""
    await _get_current_web_user(request)
    amount_decimal = Decimal(str(payload.amount))
    if amount_decimal <= 0:
        raise HTTPException(status_code=400, detail="Valor invalido.")

    expense_date = None
    if payload.expense_date:
        try:
            expense_date = date.fromisoformat(payload.expense_date)
        except ValueError:
            raise HTTPException(status_code=400, detail="Data do uso da meta invalida.")

    async with async_session() as session:
        refreshed_user = await _get_current_web_user_in_session(session, request)
        goal = await goal_service.get_goal_by_id(session, refreshed_user.phone, payload.goal_id)
        if goal is None:
            raise HTTPException(status_code=400, detail="Meta selecionada nao encontrada.")
        available_balance = await goal_service.get_available_goal_balance(
            session,
            refreshed_user.phone,
            payload.goal_id,
        )
        if available_balance is None:
            raise HTTPException(status_code=400, detail="Meta selecionada nao encontrada.")
        if available_balance < amount_decimal:
            raise HTTPException(
                status_code=400,
                detail="A meta nao possui saldo suficiente para esse resgate.",
            )

        expense_result = await expense_service.create_expense(
            session,
            refreshed_user.phone,
            {
                "description": payload.description or f"Uso de valor da meta {goal.description}",
                "amount": amount_decimal,
                "category": payload.category,
                "payment_method": payload.payment_method,
                "expense_date": expense_date.isoformat() if expense_date else None,
            },
        )
        if not expense_result["success"]:
            raise HTTPException(status_code=400, detail=expense_result["error"])

        withdrawal_result = await goal_service.withdraw_from_goal(
            session,
            refreshed_user.phone,
            payload.goal_id,
            amount_decimal,
            description=payload.description
            or f"Uso da meta {goal.description} em {payload.category}",
            related_expense_id=expense_result["expense_id"],
            transaction_date=expense_date,
        )
        if not withdrawal_result["success"]:
            raise HTTPException(status_code=400, detail=withdrawal_result["error"])

        return {
            "status": "ok",
            "expense_id": expense_result["expense_id"],
            **withdrawal_result,
        }


@app.delete("/dashboard/goals")
async def dashboard_delete_goal(
    request: Request,
    payload: DashboardGoalDeleteRequest,
):
    """Remove an active goal from the web dashboard."""
    await _get_current_web_user(request)
    async with async_session() as session:
        refreshed_user = await _get_current_web_user_in_session(session, request)
        result = await goal_service.remove_goal(session, refreshed_user.phone, payload.description)
        if not result["success"]:
            raise HTTPException(status_code=400, detail=result["error"])
        return {"status": "ok", **result}


@app.post("/dashboard/export")
async def dashboard_export(
    request: Request,
    payload: DashboardExportRequest,
):
    """Export the selected period as XLSX or PDF."""
    await _get_current_web_user(request)
    normalized_format = payload.format.strip().lower()
    async with async_session() as session:
        refreshed_user = await _get_current_web_user_in_session(session, request)
        if normalized_format == "xlsx":
            result = await export_service.export_month(
                session,
                refreshed_user.phone,
                month=payload.month,
                year=payload.year,
            )
            mimetype = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        elif normalized_format == "pdf":
            result = await export_service.export_month_pdf(
                session,
                refreshed_user.phone,
                month=payload.month,
                year=payload.year,
            )
            mimetype = "application/pdf"
        else:
            raise HTTPException(status_code=400, detail="Formato de exportacao invalido.")

        if not result["success"]:
            raise HTTPException(status_code=400, detail=result.get("message", "Erro ao exportar."))
        return {
            "status": "ok",
            "filename": result["filename"],
            "file_base64": result["file_base64"],
            "mimetype": mimetype,
        }


@app.post("/dashboard/currency/convert")
async def dashboard_currency_convert(
    request: Request,
    payload: DashboardCurrencyConvertRequest,
):
    """Convert values between supported currencies in the web dashboard."""
    await _get_current_web_user(request)
    async with async_session() as session:
        refreshed_user = await _get_current_web_user_in_session(session, request)
        result = await currency_service.convert_currency(
            Decimal(str(payload.amount)),
            _normalize_currency_code(payload.from_currency),
            _normalize_currency_code(payload.to_currency),
            user=refreshed_user,
        )
        if not result["success"]:
            raise HTTPException(status_code=400, detail=result["error"])
        return {
            "status": "ok",
            "original_amount": float(result["original_amount"]),
            "original_currency": result["original_currency"],
            "converted_amount": float(result["converted_amount"]),
            "target_currency": result["target_currency"],
            "exchange_rate": float(result["exchange_rate"]),
        }


@app.get("/admin/qrcode", response_class=HTMLResponse)
async def get_qrcode(request: Request):
    """
    Get QR Code to connect WhatsApp.

    Requires ADMIN_SECRET for security.
    Returns an HTML page with the QR code image.
    """
    await _enforce_admin_rate_limit(request)
    if not _is_valid_admin_authorization(request.headers.get("Authorization")):
        raise HTTPException(status_code=401, detail="Invalid admin authorization")

    from app.services.evolution import EvolutionService

    evolution = EvolutionService()

    try:
        qrcode_data = await evolution.get_qrcode()

        status = qrcode_data.get("status", "unknown")
        _ = qrcode_data.get("message", "")  # Reserved for future use

        base_html = """
        <!DOCTYPE html>
        <html lang="pt-BR">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>FinBot - {title}</title>
            {refresh_meta}
            <link rel="preconnect" href="https://fonts.googleapis.com">
            <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
            <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600;800&display=swap" rel="stylesheet">
            <style>
                :root {{
                    --bg-dark: #0f172a;
                    --bg-darker: #020617;
                    --primary: #10b981;
                    --primary-dark: #059669;
                    --text-main: #f8fafc;
                    --text-muted: #94a3b8;
                    --glass-bg: rgba(255, 255, 255, 0.03);
                    --glass-border: rgba(255, 255, 255, 0.08);
                }}
                body {{
                    margin: 0;
                    padding: 0;
                    min-height: 100vh;
                    font-family: 'Outfit', sans-serif;
                    background: radial-gradient(circle at top right, var(--bg-dark), var(--bg-darker));
                    color: var(--text-main);
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    flex-direction: column;
                }}
                .glass-panel {{
                    background: var(--glass-bg);
                    backdrop-filter: blur(20px);
                    -webkit-backdrop-filter: blur(20px);
                    border: 1px solid var(--glass-border);
                    border-radius: 24px;
                    padding: 40px;
                    max-width: 440px;
                    width: 90%;
                    text-align: center;
                    box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.5);
                    animation: slideUp 0.6s ease-out forwards;
                    opacity: 0;
                    transform: translateY(20px);
                    box-sizing: border-box;
                }}
                @keyframes slideUp {{
                    to {{
                        opacity: 1;
                        transform: translateY(0);
                    }}
                }}
                .logo-container {{
                    margin-bottom: 24px;
                    display: inline-flex;
                    align-items: center;
                    justify-content: center;
                    width: 72px;
                    height: 72px;
                    border-radius: 50%;
                    background: linear-gradient(135deg, rgba(16, 185, 129, 0.15), rgba(5, 150, 105, 0.05));
                    border: 1px solid rgba(16, 185, 129, 0.3);
                    box-shadow: 0 0 30px rgba(16, 185, 129, 0.2);
                    animation: pulse 3s infinite alternate;
                }}
                @keyframes pulse {{
                    0% {{ box-shadow: 0 0 20px rgba(16, 185, 129, 0.1); }}
                    100% {{ box-shadow: 0 0 40px rgba(16, 185, 129, 0.3); }}
                }}
                h1 {{
                    font-size: 1.8rem;
                    font-weight: 800;
                    margin: 0 0 16px 0;
                    background: linear-gradient(to right, #34d399, #10b981);
                    -webkit-background-clip: text;
                    -webkit-text-fill-color: transparent;
                    letter-spacing: -0.5px;
                }}
                p {{
                    font-size: 1rem;
                    line-height: 1.6;
                    color: var(--text-muted);
                    margin: 0 0 28px 0;
                    font-weight: 300;
                }}
                .qrcode-box {{
                    background: white;
                    padding: 16px;
                    border-radius: 16px;
                    display: inline-block;
                    margin-bottom: 24px;
                    box-shadow: 0 10px 25px rgba(0,0,0,0.2);
                    transition: transform 0.3s ease;
                }}
                .qrcode-box:hover {{
                    transform: scale(1.02);
                }}
                .qrcode-box img {{
                    display: block;
                    width: 260px;
                    height: 260px;
                    border-radius: 8px;
                }}
                .footer-text {{
                    font-size: 0.85rem;
                    color: rgba(148, 163, 184, 0.7);
                    margin: 0;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    gap: 8px;
                }}
                .loader {{
                    width: 40px;
                    height: 40px;
                    border: 3px solid var(--glass-border);
                    border-bottom-color: var(--primary);
                    border-radius: 50%;
                    display: inline-block;
                    box-sizing: border-box;
                    animation: rotation 1s linear infinite;
                    margin-bottom: 28px;
                }}
                @keyframes rotation {{
                    0% {{ transform: rotate(0deg); }}
                    100% {{ transform: rotate(360deg); }}
                }}
            </style>
        </head>
        <body>
            <div class="glass-panel">
                <div class="logo-container">
                    <svg xmlns="http://www.w3.org/2000/svg" width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="url(#glow)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <defs>
                            <linearGradient id="glow" x1="0%" y1="0%" x2="100%" y2="100%">
                                <stop offset="0%" stop-color="#34d399" />
                                <stop offset="100%" stop-color="#10b981" />
                            </linearGradient>
                        </defs>
                        <path d="M12 8V4H8"></path>
                        <rect width="16" height="12" x="4" y="8" rx="2"></rect>
                        <path d="M2 14h2"></path>
                        <path d="M20 14h2"></path>
                        <path d="M15 13v2"></path>
                        <path d="M9 13v2"></path>
                    </svg>
                </div>
                {content}
            </div>
        </body>
        </html>
        """

        if status == "connected":
            content = """
                <h1>Tudo Certo! 🎉</h1>
                <p>Seu WhatsApp já foi conectado com sucesso. O <strong>FinBot</strong> já está de olho nas suas mensagens, então é só mandar um "Oi" por lá para começarmos!</p>
                <p class="footer-text">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"></path><polyline points="22 4 12 14.01 9 11.01"></polyline></svg>
                    Conexão estabelecida com segurança
                </p>
            """
            return HTMLResponse(
                content=base_html.format(title="Conectado", refresh_meta="", content=content)
            )

        qrcode_base64 = qrcode_data.get("qrcode", "")
        if qrcode_base64:
            content = f"""
                <h1>Conecte seu WhatsApp</h1>
                <p>Para ativarmos o FinBot, abra o WhatsApp no seu celular, vá em <strong>Aparelhos conectados</strong> e aponte a câmera para o código abaixo. É rapidinho! ✨</p>
                <div class="qrcode-box">
                    <img src="{qrcode_base64}" alt="QR Code" />
                </div>
                <p class="footer-text">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.59-10.37l5.25 5.25"/></svg>
                    A página atualiza sozinha a cada 30 segundos
                </p>
            """
            return HTMLResponse(
                content=base_html.format(
                    title="Conectar",
                    refresh_meta='<meta http-equiv="refresh" content="30">',
                    content=content,
                )
            )

        content = """
            <h1>Preparando tudo...</h1>
            <p>Estamos gerando o seu QR Code para a conexão segura. Só mais um instantezinho! ⏳</p>
            <div class="loader"></div>
            <p class="footer-text">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.59-10.37l5.25 5.25"/></svg>
                Atualizando automaticamente...
            </p>
        """
        return HTMLResponse(
            content=base_html.format(
                title="Aguarde",
                refresh_meta='<meta http-equiv="refresh" content="5">',
                content=content,
            )
        )
    except Exception as e:
        logger.error(f"Error getting QR code: {e}")
        raise HTTPException(status_code=502, detail="Nao foi possivel obter o QR code no momento.")


@app.get("/admin/status")
async def get_status(request: Request):
    """Get connection status."""
    await _enforce_admin_rate_limit(request)
    if not _is_valid_admin_authorization(request.headers.get("Authorization")):
        raise HTTPException(status_code=401, detail="Invalid admin authorization")

    from app.services.evolution import EvolutionService

    evolution = EvolutionService()

    try:
        status = await evolution.get_connection_state()
        return status
    except Exception as e:
        logger.error(f"Error getting status: {e}")
        raise HTTPException(
            status_code=502,
            detail="Nao foi possivel consultar o status da conexao no momento.",
        )


@app.post("/webhook/evolution")
async def evolution_webhook(request: Request):
    """
    Receive webhook events from Evolution API.

    This endpoint handles incoming WhatsApp messages.
    """
    if not settings.webhook_secret:
        logger.error("Webhook request rejected because WEBHOOK_SECRET is not configured")
        raise HTTPException(status_code=503, detail="Webhook authentication is not configured")

    if not _is_valid_webhook_authorization(request.headers.get("Authorization")):
        logger.warning("Webhook request rejected due to invalid authorization")
        raise HTTPException(status_code=401, detail="Invalid webhook authorization")

    try:
        body = await request.json()
        event = body.get("event", "unknown")
        logger.info(f"Webhook event: {event}")

        message_id = ""
        if _is_message_event(event):
            message_id = _extract_webhook_message_id(body)
            if not message_id:
                logger.warning("Webhook message event rejected due to missing message ID")
                return JSONResponse(
                    status_code=400,
                    content={"status": "error", "message": "Missing webhook message ID"},
                )

        idempotency_service = WebhookIdempotencyService()
        if message_id:
            reserved = await idempotency_service.reserve(message_id)
            if not reserved:
                logger.info(f"Duplicate webhook ignored: {message_id}")
                return {"status": "duplicate_ignored"}

        from app.handlers.webhook import WebhookHandler

        handler = WebhookHandler()
        await handler.handle(body)

        return {"status": "ok"}
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        if "handler" in locals() and getattr(handler, "processing_committed", False):
            logger.warning(
                "Webhook completed persistence before failing on post-commit side effects"
            )
            operational_status.record_event(
                "webhook",
                "warning",
                "Webhook finished persistence but failed on post-commit side effects.",
            )
            return {"status": "ok_committed_with_warnings"}
        if "message_id" in locals() and message_id and "idempotency_service" in locals():
            await idempotency_service.release(message_id)
        return JSONResponse(
            status_code=500,
            content={"status": "error", "message": "Erro interno ao processar o webhook."},
        )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=settings.port)
