"""FinBot - WhatsApp Financial Assistant."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, HTMLResponse

from app.config import get_settings
from app.database.connection import init_db, async_session
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
        message = qrcode_data.get("message", "")

        if status == "connected":
            return HTMLResponse(content=f"""
            <!DOCTYPE html>
            <html>
            <head>
                <title>FinBot - WhatsApp Connected</title>
                <style>
                    body {{ font-family: Arial, sans-serif; text-align: center; padding: 50px; background: #e8f5e9; }}
                    .container {{ background: white; padding: 40px; border-radius: 10px; display: inline-block; }}
                    h1 {{ color: #4caf50; }}
                </style>
            </head>
            <body>
                <div class="container">
                    <h1>WhatsApp Connected!</h1>
                    <p>{message}</p>
                </div>
            </body>
            </html>
            """)

        qrcode_base64 = qrcode_data.get("qrcode", "")
        if qrcode_base64:
            return HTMLResponse(content=f"""
            <!DOCTYPE html>
            <html>
            <head>
                <title>FinBot - Scan QR Code</title>
                <meta http-equiv="refresh" content="30">
                <style>
                    body {{ font-family: Arial, sans-serif; text-align: center; padding: 50px; background: #f5f5f5; }}
                    .container {{ background: white; padding: 40px; border-radius: 10px; display: inline-block; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }}
                    h1 {{ color: #25d366; }}
                    img {{ max-width: 300px; margin: 20px 0; }}
                    .refresh {{ color: #666; font-size: 14px; }}
                </style>
            </head>
            <body>
                <div class="container">
                    <h1>FinBot - WhatsApp</h1>
                    <p>{message}</p>
                    <img src="{qrcode_base64}" alt="QR Code">
                    <p class="refresh">Page auto-refreshes every 30 seconds</p>
                </div>
            </body>
            </html>
            """)

        return HTMLResponse(content=f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>FinBot - Waiting</title>
            <meta http-equiv="refresh" content="5">
            <style>
                body {{ font-family: Arial, sans-serif; text-align: center; padding: 50px; background: #fff3e0; }}
                .container {{ background: white; padding: 40px; border-radius: 10px; display: inline-block; }}
                h1 {{ color: #ff9800; }}
            </style>
        </head>
        <body>
            <div class="container">
                <h1>Waiting for QR Code...</h1>
                <p>{message}</p>
                <p>Page will refresh automatically...</p>
            </div>
        </body>
        </html>
        """)
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
