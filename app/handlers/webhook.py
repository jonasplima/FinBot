"""Webhook handler for Evolution API messages."""

import logging
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database.connection import async_session
from app.database.models import PendingConfirmation
from app.services.evolution import EvolutionService
from app.services.gemini import GeminiService
from app.services.expense import ExpenseService
from app.utils.validators import is_phone_allowed, normalize_phone

logger = logging.getLogger(__name__)
settings = get_settings()


class WebhookHandler:
    """Handler for incoming WhatsApp messages."""

    def __init__(self):
        self.evolution = EvolutionService()
        self.gemini = GeminiService()
        self.expense_service = ExpenseService()

    async def handle(self, webhook_data: dict) -> None:
        """Process incoming webhook event."""
        # Extract message data
        msg_data = self.evolution.extract_message_data(webhook_data)
        if not msg_data:
            logger.info("No message data extracted (might be a status update or own message)")
            return

        phone = msg_data["phone"]
        text = msg_data["text"]

        logger.info(f"Message from {phone}: {text[:100] if text else '(empty)'}...")

        # Check if phone is allowed
        logger.info(f"Checking if {phone} is in allowed list: {settings.allowed_phones}")
        if not is_phone_allowed(phone, settings.allowed_phones):
            logger.warning(f"Unauthorized phone: {phone}")
            return

        logger.info(f"Phone {phone} is authorized, processing message...")

        # Process message
        async with async_session() as session:
            await self.process_message(session, msg_data)

    async def process_message(
        self,
        session: AsyncSession,
        msg_data: dict,
    ) -> None:
        """Process a single message."""
        phone = msg_data["phone"]
        text = msg_data["text"].strip().lower()

        # Check for pending confirmation first
        pending = await self.get_pending_confirmation(session, phone)

        if pending:
            # User is responding to a confirmation
            await self.handle_confirmation_response(session, phone, text, pending)
            return

        # Check if message is an image
        if msg_data["has_image"]:
            await self.handle_image_message(session, msg_data)
            return

        # Process text message with Gemini
        await self.handle_text_message(session, msg_data)

    async def handle_text_message(
        self,
        session: AsyncSession,
        msg_data: dict,
    ) -> None:
        """Handle text message with Gemini AI."""
        phone = msg_data["phone"]
        text = msg_data["text"]

        if not text:
            return

        try:
            # Process with Gemini
            result = await self.gemini.process_message(text)

            intent = result.get("intent", "unknown")

            if intent == "register_expense":
                await self.handle_register_expense(session, phone, result)
            elif intent == "register_recurring":
                await self.handle_register_recurring(session, phone, result)
            elif intent == "cancel_recurring":
                await self.handle_cancel_recurring(session, phone, result)
            elif intent == "query_month":
                await self.handle_query_month(session, phone, result)
            elif intent == "export":
                await self.handle_export(session, phone, result)
            elif intent == "list_recurring":
                await self.handle_list_recurring(session, phone)
            else:
                # Unknown intent - ask for clarification
                await self.evolution.send_text(
                    phone,
                    "Desculpe, nao entendi. Voce pode:\n"
                    "- Registrar gasto: 'gastei 50 reais no almoco no pix'\n"
                    "- Registrar recorrente: 'netflix 55 reais todo mes dia 15'\n"
                    "- Cancelar recorrente: 'cancelar netflix'\n"
                    "- Ver resumo: 'quanto gastei esse mes?'\n"
                    "- Exportar: 'exportar meus gastos de marco'",
                )

        except Exception as e:
            logger.error(f"Error processing message: {e}")
            await self.evolution.send_text(
                phone,
                "Ocorreu um erro ao processar sua mensagem. Tente novamente.",
            )

    async def handle_image_message(
        self,
        session: AsyncSession,
        msg_data: dict,
    ) -> None:
        """Handle image message (receipt/invoice)."""
        phone = msg_data["phone"]

        try:
            # Download image
            image_data = await self.evolution.download_media(msg_data["message_key"])

            if not image_data:
                await self.evolution.send_text(
                    phone,
                    "Nao consegui baixar a imagem. Tente enviar novamente.",
                )
                return

            # Process with Gemini Vision
            result = await self.gemini.process_image(image_data, msg_data.get("text", ""))

            if result.get("success"):
                await self.handle_register_expense(session, phone, result)
            else:
                await self.evolution.send_text(
                    phone,
                    "Nao consegui ler a nota fiscal. "
                    "Tente enviar uma imagem mais nitida ou digite manualmente.",
                )

        except Exception as e:
            logger.error(f"Error processing image: {e}")
            await self.evolution.send_text(
                phone,
                "Erro ao processar a imagem. Tente novamente.",
            )

    async def handle_register_expense(
        self,
        session: AsyncSession,
        phone: str,
        data: dict,
    ) -> None:
        """Handle expense registration (with confirmation)."""
        expense_data = data.get("data", {})

        # Normalize shared_percentage - Gemini may return 0.7 instead of 70
        shared_percentage = expense_data.get("shared_percentage")
        if shared_percentage is not None and shared_percentage < 1:
            expense_data["shared_percentage"] = shared_percentage * 100

        # Check if payment method is missing
        payment_method = expense_data.get("payment_method")
        if not payment_method:
            # Ask for payment method
            amount = expense_data.get("amount", 0)
            description = expense_data.get("description", "")
            category = expense_data.get("category", "")

            msg = f"Identifiquei:\n"
            msg += f"- Valor: R$ {amount:.2f}\n"
            msg += f"- Descricao: {description}\n"
            msg += f"- Categoria: {category}\n\n"
            msg += "Qual foi a forma de pagamento?\n"
            msg += "1. Cartao de Credito\n"
            msg += "2. Cartao de Debito\n"
            msg += "3. Pix\n"
            msg += "4. Dinheiro\n"
            msg += "5. VR"

            # Save pending asking for payment method
            await self.save_pending_confirmation(
                session,
                phone,
                {
                    "type": "asking_payment_method",
                    "data": expense_data,
                },
            )

            await self.evolution.send_text(phone, msg)
            return

        # Build confirmation message
        amount = expense_data.get("amount", 0)
        description = expense_data.get("description", "")
        category = expense_data.get("category", "")
        installments = expense_data.get("installments")
        is_shared = expense_data.get("is_shared", False)
        shared_percentage = expense_data.get("shared_percentage")

        msg = f"Entendi:\n"
        msg += f"- Valor: R$ {amount:.2f}\n"
        msg += f"- Descricao: {description}\n"
        msg += f"- Categoria: {category}\n"
        msg += f"- Pagamento: {payment_method}\n"

        if installments:
            msg += f"- Parcelas: {installments}x\n"

        if is_shared and shared_percentage:
            msg += f"- Compartilhado: {int(shared_percentage)}% seu\n"

        msg += "\nEsta correto? (sim/nao)"

        # Save pending confirmation
        await self.save_pending_confirmation(
            session,
            phone,
            {
                "type": "expense",
                "data": expense_data,
            },
        )

        await self.evolution.send_text(phone, msg)

    async def handle_register_recurring(
        self,
        session: AsyncSession,
        phone: str,
        data: dict,
    ) -> None:
        """Handle recurring expense registration."""
        expense_data = data.get("data", {})

        amount = expense_data.get("amount", 0)
        description = expense_data.get("description", "")
        category = expense_data.get("category", "")
        payment_method = expense_data.get("payment_method", "")
        recurring_day = expense_data.get("recurring_day", 1)

        msg = f"Despesa recorrente:\n"
        msg += f"- Valor: R$ {amount:.2f}\n"
        msg += f"- Descricao: {description}\n"
        msg += f"- Categoria: {category}\n"
        msg += f"- Pagamento: {payment_method}\n"
        msg += f"- Todo dia: {recurring_day}\n"
        msg += "\nEsta correto? (sim/nao)"

        # Save pending confirmation
        await self.save_pending_confirmation(
            session,
            phone,
            {
                "type": "recurring",
                "data": expense_data,
            },
        )

        await self.evolution.send_text(phone, msg)

    async def handle_cancel_recurring(
        self,
        session: AsyncSession,
        phone: str,
        data: dict,
    ) -> None:
        """Handle cancellation of recurring expense."""
        expense_data = data.get("data", {})
        description = expense_data.get("description", "")

        # Find and cancel recurring expense
        result = await self.expense_service.cancel_recurring(
            session, phone, description
        )

        if result["success"]:
            await self.evolution.send_text(
                phone,
                f"Despesa recorrente '{description}' cancelada com sucesso!",
            )
        else:
            await self.evolution.send_text(
                phone,
                f"Nao encontrei despesa recorrente com '{description}'. "
                "Verifique o nome e tente novamente.",
            )

    async def handle_query_month(
        self,
        session: AsyncSession,
        phone: str,
        data: dict,
    ) -> None:
        """Handle monthly summary query."""
        expense_data = data.get("data", {})
        month = expense_data.get("month")
        year = expense_data.get("year")

        summary = await self.expense_service.get_monthly_summary(
            session, phone, month, year
        )

        await self.evolution.send_text(phone, summary)

    async def handle_export(
        self,
        session: AsyncSession,
        phone: str,
        data: dict,
    ) -> None:
        """Handle export request."""
        from app.services.export import ExportService

        expense_data = data.get("data", {})
        month = expense_data.get("month")
        year = expense_data.get("year")

        export_service = ExportService()
        result = await export_service.export_month(session, phone, month, year)

        if result["success"]:
            await self.evolution.send_document(
                phone,
                result["file_base64"],
                result["filename"],
                caption=f"Seus gastos de {result['month_name']}",
            )
        else:
            await self.evolution.send_text(phone, result["message"])

    async def handle_list_recurring(
        self,
        session: AsyncSession,
        phone: str,
    ) -> None:
        """Handle listing of recurring expenses."""
        result = await self.expense_service.list_recurring(session, phone)
        await self.evolution.send_text(phone, result)

    async def handle_confirmation_response(
        self,
        session: AsyncSession,
        phone: str,
        response: str,
        pending: PendingConfirmation,
    ) -> None:
        """Handle user response to confirmation."""
        pending_data = pending.data
        pending_type = pending_data.get("type")
        expense_data = pending_data.get("data", {})

        # Handle payment method selection
        if pending_type == "asking_payment_method":
            # Clean up pending
            await session.delete(pending)
            await session.commit()

            # Map response to payment method
            payment_map = {
                "1": "Cartao de Credito",
                "credito": "Cartao de Credito",
                "cartao de credito": "Cartao de Credito",
                "cartão de crédito": "Cartao de Credito",
                "2": "Cartao de Debito",
                "debito": "Cartao de Debito",
                "cartao de debito": "Cartao de Debito",
                "cartão de débito": "Cartao de Debito",
                "3": "Pix",
                "pix": "Pix",
                "4": "Dinheiro",
                "dinheiro": "Dinheiro",
                "5": "VR",
                "vr": "VR",
            }

            payment_method = payment_map.get(response.lower().strip())

            if not payment_method:
                await self.evolution.send_text(
                    phone,
                    "Opcao invalida. Por favor, escolha:\n"
                    "1. Cartao de Credito\n"
                    "2. Cartao de Debito\n"
                    "3. Pix\n"
                    "4. Dinheiro\n"
                    "5. VR",
                )
                # Re-save pending
                await self.save_pending_confirmation(session, phone, pending_data)
                return

            # Update expense data with payment method
            expense_data["payment_method"] = payment_method

            # Now show confirmation
            await self.handle_register_expense(
                session, phone, {"data": expense_data}
            )
            return

        # Clean up the pending confirmation
        await session.delete(pending)
        await session.commit()

        # Check response
        positive_responses = ("sim", "s", "yes", "y", "ok", "confirmo", "isso")
        negative_responses = ("nao", "n", "no", "cancelar", "cancela")

        if response in positive_responses:
            # Confirm and save
            if pending_type == "expense":
                result = await self.expense_service.create_expense(
                    session, phone, expense_data
                )
            elif pending_type == "recurring":
                expense_data["is_recurring"] = True
                result = await self.expense_service.create_expense(
                    session, phone, expense_data
                )
            else:
                result = {"success": False}

            if result.get("success"):
                await self.evolution.send_text(
                    phone,
                    "Registrado com sucesso!",
                )
            else:
                await self.evolution.send_text(
                    phone,
                    f"Erro ao registrar: {result.get('error', 'Erro desconhecido')}",
                )

        elif response in negative_responses:
            await self.evolution.send_text(
                phone,
                "Cancelado. O que voce gostaria de registrar?",
            )
        else:
            # Re-ask
            await self.evolution.send_text(
                phone,
                "Por favor, responda 'sim' para confirmar ou 'nao' para cancelar.",
            )
            # Re-save the pending confirmation
            await self.save_pending_confirmation(
                session,
                phone,
                pending.data,
            )

    async def get_pending_confirmation(
        self,
        session: AsyncSession,
        phone: str,
    ) -> Optional[PendingConfirmation]:
        """Get pending confirmation for user."""
        normalized_phone = normalize_phone(phone)

        result = await session.execute(
            select(PendingConfirmation)
            .where(PendingConfirmation.user_phone == normalized_phone)
            .where(PendingConfirmation.expires_at > datetime.now())
        )

        return result.scalar_one_or_none()

    async def save_pending_confirmation(
        self,
        session: AsyncSession,
        phone: str,
        data: dict,
    ) -> None:
        """Save pending confirmation."""
        normalized_phone = normalize_phone(phone)

        # Delete any existing pending
        await session.execute(
            delete(PendingConfirmation)
            .where(PendingConfirmation.user_phone == normalized_phone)
        )

        # Create new pending
        pending = PendingConfirmation(
            user_phone=normalized_phone,
            data=data,
            expires_at=datetime.now() + timedelta(minutes=5),
        )
        session.add(pending)
        await session.commit()
