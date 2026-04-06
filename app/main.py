"""FinBot - WhatsApp Financial Assistant."""

import logging
import secrets
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel
from redis.asyncio import Redis
from sqlalchemy import text

from app.config import get_settings
from app.database.connection import async_session, init_db
from app.database.seed import seed_all
from app.services.admin_rate_limit import AdminRateLimitService
from app.services.auth import AuthService
from app.services.onboarding import OnboardingService
from app.services.operational_status import OperationalStatusService
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

    name: str
    display_name: str | None = None
    timezone: str


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
                    const data = await response.json();
                    if (!response.ok) {
                        throw new Error(data.detail || data.message || 'Erro inesperado.');
                    }
                    return data;
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
            <title>FinBot • Onboarding</title>
            <link rel="preconnect" href="https://fonts.googleapis.com">
            <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
            <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600;800&display=swap" rel="stylesheet">
            <style>
                :root {
                    --bg: #f6f8fc;
                    --card: #ffffff;
                    --line: #dce3ef;
                    --text: #0f172a;
                    --muted: #64748b;
                    --accent: #0f766e;
                    --accent-soft: #ccfbf1;
                    --danger-soft: #ffe4e6;
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
                .brand { margin-bottom: 28px; }
                .brand h1 { margin: 0 0 8px 0; font-size: 1.9rem; }
                .brand p { color: var(--muted); line-height: 1.6; }
                .step-list { display: grid; gap: 10px; }
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
                .step small { color: var(--muted); display: block; margin-top: 4px; }
                .card {
                    background: var(--card);
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
                .controls { display: flex; flex-wrap: wrap; gap: 12px; }
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
                form { display: grid; gap: 12px; margin-top: 18px; }
                label { display: grid; gap: 8px; color: var(--muted); font-size: 0.92rem; }
                input {
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
                .qr-shell {
                    display: grid;
                    gap: 16px;
                }
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
                        <p>Seu painel inicial para finalizar o onboarding, proteger credenciais e preparar a conexão do WhatsApp.</p>
                    </div>
                    <div class="step-list" id="step-list"></div>
                </aside>
                <main>
                    <section class="card">
                        <h2 id="welcome-title">Carregando seu onboarding...</h2>
                        <p id="welcome-copy">Estamos preparando o resumo do seu progresso atual.</p>
                        <div class="grid" id="stats"></div>
                    </section>

                    <section class="card">
                        <h2>Próximas ações</h2>
                        <p>Esta tela já salva e reflete o progresso do onboarding. Os passos técnicos mais sensíveis agora podem ser feitos dentro de uma sessão autenticada.</p>
                        <div class="controls">
                            <button class="primary" id="step-next" type="button">Avançar etapa</button>
                            <button class="ghost" id="complete-onboarding" type="button">Concluir onboarding</button>
                            <button class="ghost" id="logout" type="button">Sair</button>
                        </div>
                        <div class="status" id="flow-status"></div>
                    </section>

                    <section class="card">
                        <h2>Conectar WhatsApp</h2>
                        <p>Esta etapa cria e acompanha a sua sessão dedicada do WhatsApp sem exigir headers manuais nem ferramentas externas no navegador.</p>
                        <div class="qr-shell">
                            <div class="controls">
                                <button class="primary" id="prepare-whatsapp" type="button">Preparar sessão</button>
                                <button class="ghost" id="generate-qrcode" type="button">Gerar QR Code</button>
                                <button class="ghost" id="refresh-whatsapp" type="button">Atualizar status</button>
                            </div>
                            <div class="grid">
                                <div class="stat"><strong id="whatsapp-status">pendente</strong><span>Status da conexão</span></div>
                                <div class="stat"><strong id="whatsapp-instance" class="mono">-</strong><span>Instância Evolution</span></div>
                            </div>
                            <div class="qr-frame" id="qr-frame">
                                <span style="color: var(--muted); text-align: center;">Sua sessão ainda não gerou um QR Code.</span>
                            </div>
                            <div class="status" id="whatsapp-status-message"></div>
                        </div>
                    </section>

                    <section class="card">
                        <h2>Termos e perfil</h2>
                        <div class="notice">
                            Antes da conexão do WhatsApp, o produto deve deixar claro que a instância é self-hosted e que pode existir acesso técnico/operacional do administrador aos dados trafegados pela stack.
                        </div>
                        <div class="controls" style="margin-top: 18px;">
                            <button class="primary" id="accept-terms" type="button">Aceitar termos</button>
                            <button class="ghost" id="reject-terms" type="button">Recusar termos</button>
                        </div>
                        <form id="profile-form">
                            <label>Nome
                                <input name="name" required>
                            </label>
                            <label>Nome de exibicao
                                <input name="display_name">
                            </label>
                            <label>Timezone
                                <input name="timezone" value="America/Sao_Paulo" required>
                            </label>
                            <button class="primary" type="submit">Salvar perfil</button>
                        </form>
                    </section>
                </main>
            </div>

            <script>
                const state = { steps: [], currentStep: 'welcome', user: null, whatsapp: null };
                const stepLabels = {
                    welcome: 'Boas-vindas',
                    terms: 'Termos',
                    ai_keys: 'Chaves de IA',
                    currency_keys: 'Chaves de câmbio',
                    whatsapp_prepare: 'Preparar WhatsApp',
                    whatsapp_qrcode: 'Ler QR Code',
                    profile: 'Perfil',
                    notifications: 'Notificações',
                    categories: 'Categorias',
                    review: 'Revisão',
                    completed: 'Concluído'
                };

                async function fetchJson(url, options = {}) {
                    const response = await fetch(url, { credentials: 'same-origin', ...options });
                    const data = await response.json();
                    if (!response.ok) {
                        throw new Error(data.detail || data.message || 'Erro inesperado.');
                    }
                    return data;
                }

                function renderSteps() {
                    const container = document.getElementById('step-list');
                    container.innerHTML = '';
                    for (const step of state.steps) {
                        const item = document.createElement('div');
                        item.className = 'step' + (step === state.currentStep ? ' active' : '');
                        item.innerHTML = `<strong>${stepLabels[step] || step}</strong><small>${step === state.currentStep ? 'Etapa atual' : 'Disponível no fluxo'}</small>`;
                        container.appendChild(item);
                    }
                }

                function renderStats(payload) {
                    state.user = payload.user;
                    state.steps = payload.onboarding.steps;
                    state.currentStep = payload.onboarding.current_step;
                    document.getElementById('welcome-title').textContent = `Bem-vindo, ${payload.user.name || payload.user.email || 'usuario'}!`;
                    document.getElementById('welcome-copy').textContent =
                        `Etapa atual: ${stepLabels[payload.onboarding.current_step] || payload.onboarding.current_step}. O fluxo ja esta protegido pela sua sessão web.`;
                    document.getElementById('stats').innerHTML = `
                        <div class="stat"><strong>${payload.user.accepted_terms ? 'Aceitos' : 'Pendentes'}</strong><span>Termos de uso</span></div>
                        <div class="stat"><strong>${payload.user.onboarding_completed ? 'Concluído' : 'Em andamento'}</strong><span>Status do onboarding</span></div>
                        <div class="stat"><strong>${payload.user.phone}</strong><span>Telefone vinculado</span></div>
                        <div class="stat"><strong>${payload.user.timezone || 'America/Sao_Paulo'}</strong><span>Timezone atual</span></div>
                    `;
                    document.querySelector('#profile-form [name="name"]').value = payload.user.name || '';
                    document.querySelector('#profile-form [name="display_name"]').value = payload.user.display_name || '';
                    document.querySelector('#profile-form [name="timezone"]').value = payload.user.timezone || 'America/Sao_Paulo';
                    renderSteps();
                }

                function renderWhatsApp(payload) {
                    state.whatsapp = payload.session;
                    document.getElementById('whatsapp-status').textContent =
                        payload.session.connection_status || 'pendente';
                    document.getElementById('whatsapp-instance').textContent =
                        payload.session.evolution_instance || '-';

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

                    document.getElementById('whatsapp-status-message').textContent =
                        payload.message || '';
                    document.getElementById('whatsapp-status-message').className = 'status';
                }

                async function loadState() {
                    const payload = await fetchJson('/onboarding/state');
                    renderStats(payload);
                }

                async function loadWhatsAppStatus() {
                    const payload = await fetchJson('/onboarding/whatsapp/status');
                    renderWhatsApp(payload);
                }

                async function updateStep(nextStep) {
                    const payload = await fetchJson('/onboarding/step', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ current_step: nextStep })
                    });
                    renderStats(payload);
                }

                document.getElementById('step-next').addEventListener('click', async () => {
                    const currentIndex = state.steps.indexOf(state.currentStep);
                    const nextStep = state.steps[Math.min(currentIndex + 1, state.steps.length - 1)];
                    try {
                        await updateStep(nextStep);
                        document.getElementById('flow-status').textContent = 'Etapa atualizada com sucesso.';
                        document.getElementById('flow-status').className = 'status';
                    } catch (error) {
                        document.getElementById('flow-status').textContent = error.message;
                        document.getElementById('flow-status').className = 'status error';
                    }
                });

                document.getElementById('complete-onboarding').addEventListener('click', async () => {
                    try {
                        const payload = await fetchJson('/onboarding/complete', { method: 'POST' });
                        renderStats(payload);
                        document.getElementById('flow-status').textContent = 'Onboarding concluído.';
                        document.getElementById('flow-status').className = 'status';
                    } catch (error) {
                        document.getElementById('flow-status').textContent = error.message;
                        document.getElementById('flow-status').className = 'status error';
                    }
                });

                document.getElementById('accept-terms').addEventListener('click', async () => {
                    try {
                        const payload = await fetchJson('/onboarding/terms/accept', { method: 'POST' });
                        renderStats(payload);
                    } catch (error) {
                        document.getElementById('flow-status').textContent = error.message;
                        document.getElementById('flow-status').className = 'status error';
                    }
                });

                document.getElementById('reject-terms').addEventListener('click', async () => {
                    try {
                        const payload = await fetchJson('/onboarding/terms/reject', { method: 'POST' });
                        renderStats(payload);
                    } catch (error) {
                        document.getElementById('flow-status').textContent = error.message;
                        document.getElementById('flow-status').className = 'status error';
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
                        document.getElementById('flow-status').textContent = 'Perfil atualizado.';
                        document.getElementById('flow-status').className = 'status';
                    } catch (error) {
                        document.getElementById('flow-status').textContent = error.message;
                        document.getElementById('flow-status').className = 'status error';
                    }
                });

                document.getElementById('logout').addEventListener('click', async () => {
                    await fetchJson('/auth/logout', { method: 'POST' });
                    window.location.href = '/web/login';
                });

                document.getElementById('prepare-whatsapp').addEventListener('click', async () => {
                    try {
                        const payload = await fetchJson('/onboarding/whatsapp/prepare', { method: 'POST' });
                        renderWhatsApp(payload);
                        await loadState();
                        document.getElementById('whatsapp-status-message').textContent =
                            'Sessão preparada. Agora você pode gerar o QR Code.';
                    } catch (error) {
                        document.getElementById('whatsapp-status-message').textContent = error.message;
                        document.getElementById('whatsapp-status-message').className = 'status error';
                    }
                });

                document.getElementById('generate-qrcode').addEventListener('click', async () => {
                    try {
                        const payload = await fetchJson('/onboarding/whatsapp/qrcode', { method: 'POST' });
                        renderWhatsApp(payload);
                        await loadState();
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

                loadState().catch((error) => {
                    document.getElementById('flow-status').textContent = error.message;
                    document.getElementById('flow-status').className = 'status error';
                });

                loadWhatsAppStatus().catch(() => {
                    // Keep the onboarding screen functional even if the session is not prepared yet.
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
        return onboarding_service.build_state_payload(refreshed_user, state)


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
        return onboarding_service.build_state_payload(refreshed_user, state)


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
        state = await onboarding_service.update_step(session, updated_user, "ai_keys")
        return onboarding_service.build_state_payload(updated_user, state)


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
        return onboarding_service.build_state_payload(updated_user, state)


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
        state = await onboarding_service.get_or_create_state(session, updated_user)
        return onboarding_service.build_state_payload(updated_user, state)


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
        return onboarding_service.build_state_payload(refreshed_user, state)


@app.post("/onboarding/whatsapp/prepare")
async def onboarding_whatsapp_prepare(request: Request):
    """Prepare a dedicated WhatsApp onboarding session for the authenticated user."""
    await _get_current_web_user(request)
    async with async_session() as session:
        refreshed_user = await auth_service.get_user_by_session_token(
            session, _get_session_cookie_token(request) or ""
        )
        if refreshed_user is None:
            raise HTTPException(status_code=401, detail="Sessao web invalida ou expirada.")
        return await whatsapp_onboarding_service.prepare_session(session, refreshed_user)


@app.get("/onboarding/whatsapp/status")
async def onboarding_whatsapp_status(request: Request):
    """Return the current WhatsApp onboarding status for the authenticated user."""
    await _get_current_web_user(request)
    async with async_session() as session:
        refreshed_user = await auth_service.get_user_by_session_token(
            session, _get_session_cookie_token(request) or ""
        )
        if refreshed_user is None:
            raise HTTPException(status_code=401, detail="Sessao web invalida ou expirada.")
        return await whatsapp_onboarding_service.get_status(session, refreshed_user)


@app.post("/onboarding/whatsapp/qrcode")
async def onboarding_whatsapp_qrcode(request: Request):
    """Generate or refresh the WhatsApp QR code for the authenticated user."""
    await _get_current_web_user(request)
    async with async_session() as session:
        refreshed_user = await auth_service.get_user_by_session_token(
            session, _get_session_cookie_token(request) or ""
        )
        if refreshed_user is None:
            raise HTTPException(status_code=401, detail="Sessao web invalida ou expirada.")
        return await whatsapp_onboarding_service.generate_qrcode(session, refreshed_user)


@app.post("/onboarding/whatsapp/refresh")
async def onboarding_whatsapp_refresh(request: Request):
    """Refresh the WhatsApp connection status for the authenticated user."""
    await _get_current_web_user(request)
    async with async_session() as session:
        refreshed_user = await auth_service.get_user_by_session_token(
            session, _get_session_cookie_token(request) or ""
        )
        if refreshed_user is None:
            raise HTTPException(status_code=401, detail="Sessao web invalida ou expirada.")
        return await whatsapp_onboarding_service.refresh_status(session, refreshed_user)


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
