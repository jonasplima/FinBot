"""FinBot - WhatsApp Financial Assistant."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse

from app.config import get_settings
from app.database.connection import async_session, init_db
from app.database.seed import seed_all

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

settings = get_settings()


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

    logger.info("FinBot started successfully!")

    yield

    # Shutdown
    logger.info("Shutting down FinBot...")


# Create FastAPI app
app = FastAPI(
    title="FinBot",
    description="WhatsApp Financial Assistant",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "app": "FinBot", "version": "1.0.0"}


@app.get("/admin/qrcode", response_class=HTMLResponse)
async def get_qrcode(secret: str = Query(..., description="Admin secret")):
    """
    Get QR Code to connect WhatsApp.

    Requires ADMIN_SECRET for security.
    Returns an HTML page with the QR code image.
    """
    if secret != settings.admin_secret:
        raise HTTPException(status_code=401, detail="Invalid admin secret")

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
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/admin/status")
async def get_status(secret: str = Query(..., description="Admin secret")):
    """Get connection status."""
    if secret != settings.admin_secret:
        raise HTTPException(status_code=401, detail="Invalid admin secret")

    from app.services.evolution import EvolutionService

    evolution = EvolutionService()

    try:
        status = await evolution.get_connection_state()
        return status
    except Exception as e:
        logger.error(f"Error getting status: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/webhook/evolution")
async def evolution_webhook(request: Request):
    """
    Receive webhook events from Evolution API.

    This endpoint handles incoming WhatsApp messages.
    """
    try:
        body = await request.json()
        event = body.get("event", "unknown")
        logger.info(f"Webhook event: {event}")

        from app.handlers.webhook import WebhookHandler

        handler = WebhookHandler()
        await handler.handle(body)

        return {"status": "ok"}
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return JSONResponse(
            status_code=200,  # Always return 200 to Evolution
            content={"status": "error", "message": str(e)},
        )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=settings.port)
