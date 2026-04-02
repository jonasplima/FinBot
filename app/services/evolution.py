"""Evolution API integration service."""

import base64
import logging
from datetime import datetime, timedelta

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# Track message IDs sent by the bot to avoid processing them when received via webhook
# Format: {message_id: timestamp}
_sent_message_ids: dict[str, datetime] = {}

# Track processed message IDs to avoid duplicates
_processed_message_ids: dict[str, datetime] = {}


def _cleanup_old_ids() -> None:
    """Remove IDs older than 1 hour to prevent memory leaks."""
    cutoff = datetime.now() - timedelta(hours=1)

    global _sent_message_ids, _processed_message_ids
    _sent_message_ids = {k: v for k, v in _sent_message_ids.items() if v > cutoff}
    _processed_message_ids = {k: v for k, v in _processed_message_ids.items() if v > cutoff}


class EvolutionService:
    """Service for interacting with Evolution API."""

    def __init__(self):
        self.base_url = settings.evolution_api_url
        self.api_key = settings.evolution_api_key
        self.instance = settings.evolution_instance
        self.headers = {
            "apikey": self.api_key,
            "Content-Type": "application/json",
        }

    async def _request(
        self,
        method: str,
        endpoint: str,
        json: dict | None = None,
        timeout: float = 30.0,
    ) -> dict:
        """Make HTTP request to Evolution API."""
        url = f"{self.base_url}{endpoint}"

        async with httpx.AsyncClient() as client:
            response = await client.request(
                method=method,
                url=url,
                headers=self.headers,
                json=json,
                timeout=timeout,
            )

            if response.status_code >= 400:
                logger.error(f"Evolution API error: {response.status_code} - {response.text}")
                response.raise_for_status()

            return response.json()

    async def setup_instance(self) -> dict:
        """Create or verify instance exists."""
        # Check if instance exists
        try:
            state = await self.get_connection_state()
            logger.info(f"Instance {self.instance} exists, state: {state}")
            await self.setup_webhook()
            return state
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                # Instance doesn't exist, create it
                return await self.create_instance()
            raise

    async def create_instance(self) -> dict:
        """Create a new Evolution API instance."""
        logger.info(f"Creating instance: {self.instance}")

        data = {
            "instanceName": self.instance,
            "integration": "WHATSAPP-BAILEYS",
            "number": settings.owner_phone,
            "qrcode": True,
            "token": self.api_key,
        }

        result = await self._request("POST", "/instance/create", json=data)
        logger.info(f"Instance created: {result}")

        # Setup webhook
        await self.setup_webhook()

        return result

    async def setup_webhook(self) -> dict:
        """Configure webhook for receiving messages."""
        logger.info("Setting up webhook...")

        # Get the container's internal URL
        webhook_url = "http://finbot:3003/webhook/evolution"

        data = {
            "webhook": {
                "enabled": True,
                "url": webhook_url,
                "webhookByEvents": False,
                "webhookBase64": True,
                "events": [
                    "MESSAGES_UPSERT",
                    "CONNECTION_UPDATE",
                ],
            },
        }

        result = await self._request(
            "POST",
            f"/webhook/set/{self.instance}",
            json=data,
        )
        logger.info(f"Webhook configured: {result}")
        return result

    async def get_connection_state(self) -> dict:
        """Get current connection state."""
        return await self._request(
            "GET",
            f"/instance/connectionState/{self.instance}",
        )

    async def logout_instance(self) -> dict:
        """Logout the instance to disconnect WhatsApp."""
        logger.info(f"Logging out instance: {self.instance}")
        try:
            return await self._request(
                "DELETE",
                f"/instance/logout/{self.instance}",
            )
        except Exception as e:
            logger.warning(f"Logout failed (may not be connected): {e}")
            return {}

    async def get_qrcode(self) -> dict:
        """Get QR code for connecting WhatsApp."""

        # First, check instance state
        try:
            state_result = await self.get_connection_state()
            state = state_result.get("instance", {}).get("state", "")
            logger.info(f"Current state: {state}")

            if state == "open":
                return {
                    "status": "connected",
                    "message": "WhatsApp is already connected",
                }

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                # Instance doesn't exist, create it
                logger.info("Instance not found, creating...")
                try:
                    create_result = await self.create_instance()
                    logger.info(f"Create result: {create_result}")

                    # Check if QR code is in create response
                    if "qrcode" in create_result:
                        qr_data = create_result.get("qrcode", {})
                        if "base64" in qr_data:
                            return {
                                "status": "waiting_qrcode",
                                "qrcode": qr_data.get("base64"),
                                "message": "Scan the QR code with WhatsApp",
                            }
                except httpx.HTTPStatusError as create_error:
                    # Instance might already exist (race condition or stale state)
                    if "already in use" in str(create_error.response.text):
                        logger.info("Instance already exists, continuing to get QR...")
                    else:
                        raise
            else:
                raise

        # Try to get QR code via connect endpoint
        try:
            result = await self._request(
                "GET",
                f"/instance/connect/{self.instance}",
            )
            logger.info(f"Connect result: {result}")

            # Return formatted response
            if "base64" in result:
                return {
                    "status": "waiting_qrcode",
                    "qrcode": result.get("base64"),
                    "message": "Scan the QR code with WhatsApp",
                }
            elif "code" in result:
                # Some versions return 'code' instead of 'base64'
                return {
                    "status": "waiting_qrcode",
                    "qrcode": result.get("code"),
                    "message": "Scan the QR code with WhatsApp",
                }
            elif "pairingCode" in result:
                # Pairing code mode
                return {
                    "status": "waiting_pairing",
                    "pairingCode": result.get("pairingCode"),
                    "message": "Use this pairing code in WhatsApp",
                }
            else:
                return {
                    "status": "pending",
                    "message": "Waiting for QR code. Try again in a few seconds.",
                    "data": result,
                }
        except Exception as e:
            logger.error(f"Error getting QR: {e}")
            return {
                "status": "error",
                "message": str(e),
            }

    async def send_text(self, phone: str, message: str) -> dict:
        """Send text message to phone number."""
        # Ensure phone has WhatsApp suffix
        if not phone.endswith("@s.whatsapp.net"):
            phone = f"{phone}@s.whatsapp.net"

        data = {
            "number": phone,
            "text": message,
        }

        result = await self._request(
            "POST",
            f"/message/sendText/{self.instance}",
            json=data,
        )

        # Track sent message ID to avoid processing it when received via webhook
        msg_id = result.get("key", {}).get("id")
        if msg_id:
            _sent_message_ids[msg_id] = datetime.now()
            logger.debug(f"Tracked sent message ID: {msg_id}")

        # Cleanup old IDs periodically
        _cleanup_old_ids()

        return result

    async def send_document(
        self,
        phone: str,
        document_base64: str,
        filename: str,
        caption: str | None = None,
        mimetype: str = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ) -> dict:
        """Send document (file) to phone number."""
        if not phone.endswith("@s.whatsapp.net"):
            phone = f"{phone}@s.whatsapp.net"

        data = {
            "number": phone,
            "media": document_base64,
            "fileName": filename,
            "mediatype": "document",
            "mimetype": mimetype,
        }

        if caption:
            data["caption"] = caption

        return await self._request(
            "POST",
            f"/message/sendMedia/{self.instance}",
            json=data,
        )

    async def download_media(self, message_key: dict) -> bytes | None:
        """Download media from a message."""
        try:
            # Build the request in the format expected by Evolution API
            data = {
                "message": {
                    "key": {
                        "remoteJid": message_key.get("remoteJid", ""),
                        "fromMe": message_key.get("fromMe", False),
                        "id": message_key.get("id", ""),
                    }
                }
            }

            logger.info(f"Downloading media with key: {data}")

            result = await self._request(
                "POST",
                f"/chat/getBase64FromMediaMessage/{self.instance}",
                json=data,
            )

            if "base64" in result:
                return base64.b64decode(result["base64"])
            return None
        except Exception as e:
            logger.error(f"Error downloading media: {e}")
            return None

    def extract_message_data(self, webhook_data: dict) -> dict | None:
        """Extract relevant data from webhook payload."""
        try:
            event = webhook_data.get("event", "")

            # Handle different event name formats from Evolution API
            valid_events = ("messages.upsert", "MESSAGES_UPSERT", "message")
            if event.lower() not in [e.lower() for e in valid_events]:
                logger.debug(f"Ignoring event: {event}")
                return None

            data = webhook_data.get("data", {})
            logger.info(f"Webhook data keys: {list(data.keys()) if data else 'empty'}")

            key = data.get("key", {})
            message = data.get("message", {})

            logger.info(f"Key: {key}")
            logger.info(f"Message keys: {list(message.keys()) if message else 'empty'}")

            # Get message ID
            msg_id = key.get("id", "")

            # Skip if this is a message we sent (tracked in _sent_message_ids)
            if msg_id in _sent_message_ids:
                del _sent_message_ids[msg_id]
                logger.info(f"Skipping bot's own sent message: {msg_id}")
                return None

            # Skip if already processed (duplicate webhook)
            if msg_id in _processed_message_ids:
                logger.info(f"Skipping already processed message: {msg_id}")
                return None

            # Track this message as processed
            if msg_id:
                _processed_message_ids[msg_id] = datetime.now()

            # Get sender phone
            remote_jid = key.get("remoteJid", "")
            if not remote_jid:
                logger.info("No remoteJid found in key")
                return None

            logger.info(f"remoteJid: {remote_jid}")

            # Skip group messages - only handle personal chats
            if remote_jid.endswith("@g.us"):
                logger.info("Skipping group message")
                return None

            # Extract phone number (remove WhatsApp suffixes)
            phone = remote_jid.replace("@s.whatsapp.net", "").replace("@c.us", "")

            # Get message content
            text_content = (
                message.get("conversation")
                or message.get("extendedTextMessage", {}).get("text")
                or ""
            )

            # Check for image
            image_message = message.get("imageMessage")
            has_image = image_message is not None

            # Get image caption if exists
            if has_image and not text_content:
                text_content = image_message.get("caption", "")

            return {
                "phone": phone,
                "text": text_content,
                "has_image": has_image,
                "message_key": key,
                "raw_message": message,
            }

        except Exception as e:
            logger.error(f"Error extracting message data: {e}")
            return None
