"""Webhook handler for Evolution API messages."""

import logging
from datetime import datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database.connection import async_session
from app.database.models import PendingConfirmation
from app.services.budget import BudgetService
from app.services.evolution import EvolutionService
from app.services.expense import ExpenseService
from app.services.gemini import GeminiService
from app.utils.validators import is_phone_allowed, normalize_phone

logger = logging.getLogger(__name__)
settings = get_settings()


class WebhookHandler:
    """Handler for incoming WhatsApp messages."""

    def __init__(self):
        self.evolution = EvolutionService()
        self.gemini = GeminiService()
        self.expense_service = ExpenseService()
        self.budget_service = BudgetService()

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

        logger.info(f"Processing message: '{text}' from {phone}")

        # Check for pending confirmation first
        pending = await self.get_pending_confirmation(session, phone)
        logger.info(f"Pending confirmation found: {pending is not None}")

        if pending:
            # User is responding to a confirmation
            logger.info(
                f"Handling confirmation response for pending type: {pending.data.get('type')}"
            )
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
            elif intent == "undo_last":
                await self.handle_undo_last(session, phone)
            elif intent == "set_budget":
                await self.handle_set_budget(session, phone, result)
            elif intent == "check_budget":
                await self.handle_check_budget(session, phone, result)
            elif intent == "list_budgets":
                await self.handle_list_budgets(session, phone)
            elif intent == "remove_budget":
                await self.handle_remove_budget(session, phone, result)
            else:
                # Unknown intent - ask for clarification
                await self.evolution.send_text(
                    phone,
                    "Desculpe, nao entendi. Voce pode:\n"
                    "- Registrar gasto: 'gastei 50 reais no almoco no pix'\n"
                    "- Registrar recorrente: 'netflix 55 reais todo mes dia 15'\n"
                    "- Cancelar recorrente: 'cancelar netflix'\n"
                    "- Ver resumo: 'quanto gastei esse mes?'\n"
                    "- Exportar: 'exportar meus gastos de marco'\n"
                    "- Desfazer: 'desfaz' ou 'apaga o ultimo'\n"
                    "- Definir orcamento: 'definir limite alimentacao 500 reais'\n"
                    "- Ver orcamentos: 'meus limites de gasto'",
                )

        except Exception as e:
            logger.error(
                f"Error processing message from {phone}: {type(e).__name__}: {e}",
                exc_info=True,
            )
            # Provide more helpful error message based on error type
            if "timeout" in str(e).lower() or "deadline" in str(e).lower():
                error_msg = (
                    "O servico esta demorando para responder. Tente novamente em alguns segundos."
                )
            elif "quota" in str(e).lower() or "rate" in str(e).lower():
                error_msg = "Muitas requisicoes no momento. Aguarde um minuto e tente novamente."
            else:
                error_msg = "Ocorreu um erro ao processar sua mensagem. Tente novamente."

            await self.evolution.send_text(phone, error_msg)

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

            msg = "Identifiquei:\n"
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

        msg = "Entendi:\n"
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

        msg = "Despesa recorrente:\n"
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
        result = await self.expense_service.cancel_recurring(session, phone, description)

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

        summary = await self.expense_service.get_monthly_summary(session, phone, month, year)

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

    async def handle_undo_last(
        self,
        session: AsyncSession,
        phone: str,
    ) -> None:
        """Handle undo last expense request."""
        result = await self.expense_service.undo_last_expense(session, phone)

        if result["success"]:
            expense = result["expense"]
            msg = (
                f"Gasto removido:\n"
                f"- {expense['description']}\n"
                f"- R$ {expense['amount']:.2f}\n"
                f"- {expense['category']}"
            )
            await self.evolution.send_text(phone, msg)
        else:
            await self.evolution.send_text(phone, result["error"])

    async def handle_set_budget(
        self,
        session: AsyncSession,
        phone: str,
        data: dict,
    ) -> None:
        """Handle setting a budget limit for a category."""
        from decimal import Decimal

        budget_data = data.get("data", {})
        category = budget_data.get("category")
        budget_limit = budget_data.get("budget_limit")

        if not budget_limit:
            await self.evolution.send_text(
                phone,
                "Por favor, informe o valor do limite. Exemplo: 'definir limite alimentacao 500 reais'",
            )
            return

        result = await self.budget_service.create_budget(
            session, phone, category, Decimal(str(budget_limit))
        )

        if result["success"]:
            category_name = result.get("category") or "Geral"
            limit_value = result.get("limit", 0)
            action = "atualizado" if result.get("updated") else "criado"

            msg = (
                f"Orcamento {action}!\n\n"
                f"Categoria: {category_name}\n"
                f"Limite: R$ {limit_value:.2f}\n\n"
                f"Voce sera alertado em 50%, 80% e 100% do limite."
            )
            await self.evolution.send_text(phone, msg)
        else:
            await self.evolution.send_text(phone, result["error"])

    async def handle_check_budget(
        self,
        session: AsyncSession,
        phone: str,
        data: dict,
    ) -> None:
        """Handle checking budget status for a category."""
        budget_data = data.get("data", {})
        category = budget_data.get("category")

        result = await self.budget_service.check_budget_status(session, phone, category)

        if result["success"]:
            category_name = result.get("category", "Geral")
            limit_value = result.get("limit", 0)
            spent = result.get("spent", 0)
            remaining = result.get("remaining", 0)
            percentage = result.get("percentage", 0)

            if remaining >= 0:
                status_emoji = "✅" if percentage < 50 else "⚠️" if percentage < 80 else "🚨"
            else:
                status_emoji = "🚨"

            msg = (
                f"{status_emoji} Orcamento de {category_name}\n\n"
                f"Limite: R$ {limit_value:.2f}\n"
                f"Gasto: R$ {spent:.2f} ({percentage:.0f}%)\n"
                f"Restante: R$ {remaining:.2f}"
            )
            await self.evolution.send_text(phone, msg)
        else:
            await self.evolution.send_text(phone, result["error"])

    async def handle_list_budgets(
        self,
        session: AsyncSession,
        phone: str,
    ) -> None:
        """Handle listing all active budgets."""
        result = await self.budget_service.list_budgets(session, phone)

        if not result["success"]:
            await self.evolution.send_text(phone, result.get("error", "Erro ao listar orcamentos."))
            return

        budgets = result.get("budgets", [])

        if not budgets:
            await self.evolution.send_text(
                phone,
                "Voce nao tem orcamentos definidos.\n\n"
                "Para criar um, diga: 'definir limite alimentacao 500 reais'",
            )
            return

        msg = "Seus orcamentos:\n\n"
        for budget in budgets:
            percentage = budget["percentage"]
            if percentage < 50:
                status_emoji = "✅"
            elif percentage < 80:
                status_emoji = "⚠️"
            else:
                status_emoji = "🚨"

            msg += (
                f"{status_emoji} *{budget['category']}*\n"
                f"   Limite: R$ {budget['limit']:.2f}\n"
                f"   Gasto: R$ {budget['spent']:.2f} ({percentage:.0f}%)\n"
                f"   Restante: R$ {budget['remaining']:.2f}\n\n"
            )

        await self.evolution.send_text(phone, msg)

    async def handle_remove_budget(
        self,
        session: AsyncSession,
        phone: str,
        data: dict,
    ) -> None:
        """Handle removing a budget."""
        budget_data = data.get("data", {})
        category = budget_data.get("category")

        result = await self.budget_service.remove_budget(session, phone, category)

        if result["success"]:
            category_name = result.get("category") or "Geral"
            await self.evolution.send_text(
                phone,
                f"Orcamento de {category_name} removido com sucesso.",
            )
        else:
            await self.evolution.send_text(phone, result["error"])

    async def handle_confirmation_response(
        self,
        session: AsyncSession,
        phone: str,
        response: str,
        pending: PendingConfirmation,
    ) -> None:
        """Handle user response to confirmation using LLM evaluation."""
        pending_data = pending.data
        pending_type = pending_data.get("type")
        expense_data = pending_data.get("data", {})
        awaiting_selection = pending_data.get("awaiting_selection")

        # Handle payment method selection
        if pending_type == "asking_payment_method":
            await self._handle_payment_method_selection(
                session, phone, response, pending_data, expense_data
            )
            return

        # Handle recurring expense confirmation
        if pending_type == "recurring_confirmation":
            await self._handle_recurring_confirmation(session, phone, response, pending_data)
            return

        # Build expense summary for LLM context
        expense_summary = self._build_expense_summary(expense_data, pending_type)

        # Evaluate response using LLM
        evaluation = await self.gemini.evaluate_confirmation_response(expense_summary, response)

        action = evaluation.get("action", "unknown")
        adjustments = evaluation.get("adjustments", {})

        # If user was selecting from a list, always show summary again (unless cancel)
        if awaiting_selection and action not in (
            "cancel",
            "list_categories",
            "list_payment_methods",
        ):
            # Treat as adjustment - apply any changes and show summary again
            action = "adjust"
            # If no adjustments detected but user confirmed current value, keep expense_data as is
            if not any(adjustments.values()):
                adjustments = {}

        logger.info(f"Confirmation evaluation: action={action}, adjustments={adjustments}")

        if action == "confirm":
            # Clean up pending and save expense
            await session.delete(pending)
            await session.commit()

            if pending_type == "expense":
                result = await self.expense_service.create_expense(session, phone, expense_data)
            elif pending_type == "recurring":
                expense_data["is_recurring"] = True
                result = await self.expense_service.create_expense(session, phone, expense_data)
            else:
                result = {"success": False}

            if result.get("success"):
                await self.evolution.send_text(phone, "Registrado com sucesso!")

                # Check for budget alerts (only for expenses, not income)
                if expense_data.get("category"):
                    category_id = await self._get_category_id(session, expense_data["category"])
                    if category_id:
                        alerts = await self.budget_service.check_and_send_alerts(
                            session, phone, category_id
                        )
                        # Send alert messages
                        for alert in alerts:
                            alert_msg = self.gemini.format_budget_alert(alert)
                            await self.evolution.send_text(phone, alert_msg)
            else:
                await self.evolution.send_text(
                    phone,
                    f"Erro ao registrar: {result.get('error', 'Erro desconhecido')}",
                )

        elif action == "cancel":
            # Clean up pending
            await session.delete(pending)
            await session.commit()
            await self.evolution.send_text(
                phone,
                "Cancelado. O que voce gostaria de registrar?",
            )

        elif action == "adjust":
            # Apply adjustments to expense data
            updated_data = self._apply_adjustments(expense_data, adjustments)

            # Update pending with new data
            await session.delete(pending)
            await session.commit()

            # Show updated confirmation
            await self.handle_register_expense(session, phone, {"data": updated_data})

        elif action == "list_categories":
            # Send categories list and mark that we're awaiting category selection
            categories_list = await self.expense_service.get_categories_list(session)
            await self.evolution.send_text(phone, categories_list + "\nQual categoria deseja usar?")
            # Update pending to mark we're awaiting category selection
            await session.delete(pending)
            await session.commit()
            pending_data["awaiting_selection"] = "category"
            await self.save_pending_confirmation(session, phone, pending_data)

        elif action == "list_payment_methods":
            # Send payment methods list and mark that we're awaiting payment selection
            methods_list = await self.expense_service.get_payment_methods_list(session)
            await self.evolution.send_text(
                phone, methods_list + "\nQual forma de pagamento deseja usar?"
            )
            # Update pending to mark we're awaiting payment method selection
            await session.delete(pending)
            await session.commit()
            pending_data["awaiting_selection"] = "payment_method"
            await self.save_pending_confirmation(session, phone, pending_data)

        else:
            # Unknown action - ask for clarification
            await self.evolution.send_text(
                phone,
                "Nao entendi. Voce pode:\n"
                "- Confirmar: 'sim', 'ok', 'pode salvar'\n"
                "- Cancelar: 'nao', 'cancela'\n"
                "- Ajustar: 'muda pra 50 reais', 'categoria Lazer'\n"
                "- Ver opcoes: 'lista categorias', 'formas de pagamento'",
            )
            # Keep the pending confirmation active

    async def _handle_payment_method_selection(
        self,
        session: AsyncSession,
        phone: str,
        response: str,
        pending_data: dict,
        expense_data: dict,
    ) -> None:
        """Handle payment method selection from user."""
        # Clean up pending
        await session.execute(
            delete(PendingConfirmation).where(
                PendingConfirmation.user_phone == normalize_phone(phone)
            )
        )
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
            # Try using LLM to understand the payment method
            evaluation = await self.gemini.evaluate_confirmation_response(
                "Selecao de forma de pagamento",
                response,
            )
            if evaluation.get("action") == "list_payment_methods":
                methods_list = await self.expense_service.get_payment_methods_list(session)
                await self.evolution.send_text(phone, methods_list)
                # Re-save pending
                await self.save_pending_confirmation(session, phone, pending_data)
                return

            adjustments = evaluation.get("adjustments", {})
            payment_method = adjustments.get("payment_method")

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
        await self.handle_register_expense(session, phone, {"data": expense_data})

    def _build_expense_summary(self, expense_data: dict, pending_type: str) -> str:
        """Build a human-readable expense summary for LLM context."""
        amount = expense_data.get("amount", 0)
        description = expense_data.get("description", "")
        category = expense_data.get("category", "")
        payment_method = expense_data.get("payment_method", "")
        installments = expense_data.get("installments")
        is_shared = expense_data.get("is_shared", False)
        shared_percentage = expense_data.get("shared_percentage")
        recurring_day = expense_data.get("recurring_day")

        summary = (
            f"Tipo: {'Despesa recorrente' if pending_type == 'recurring' else 'Despesa/Entrada'}\n"
        )
        summary += f"Valor: R$ {amount:.2f}\n"
        summary += f"Descricao: {description}\n"
        summary += f"Categoria: {category}\n"
        summary += f"Pagamento: {payment_method}\n"

        if installments:
            summary += f"Parcelas: {installments}x\n"
        if is_shared and shared_percentage:
            summary += f"Compartilhado: {int(shared_percentage)}% seu\n"
        if recurring_day:
            summary += f"Dia do mes: {recurring_day}\n"

        return summary

    def _apply_adjustments(self, expense_data: dict, adjustments: dict) -> dict:
        """Apply adjustments from LLM evaluation to expense data."""
        updated = expense_data.copy()

        if adjustments.get("amount") is not None:
            updated["amount"] = adjustments["amount"]

        if adjustments.get("description"):
            updated["description"] = adjustments["description"]

        if adjustments.get("category"):
            updated["category"] = adjustments["category"]

        if adjustments.get("payment_method"):
            updated["payment_method"] = adjustments["payment_method"]

        return updated

    async def get_pending_confirmation(
        self,
        session: AsyncSession,
        phone: str,
    ) -> PendingConfirmation | None:
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
            delete(PendingConfirmation).where(PendingConfirmation.user_phone == normalized_phone)
        )

        # Create new pending
        pending = PendingConfirmation(
            user_phone=normalized_phone,
            data=data,
            expires_at=datetime.now() + timedelta(minutes=5),
        )
        session.add(pending)
        await session.commit()

    async def _get_category_id(
        self,
        session: AsyncSession,
        category_name: str,
    ) -> int | None:
        """Get category ID by name."""
        from sqlalchemy import func

        from app.database.models import Category

        result = await session.execute(
            select(Category.id).where(func.lower(Category.name) == category_name.lower())
        )
        category_id = result.scalar_one_or_none()
        return category_id

    async def _handle_recurring_confirmation(
        self,
        session: AsyncSession,
        phone: str,
        response: str,
        pending_data: dict,
    ) -> None:
        """Handle user response to recurring expense confirmation."""
        from datetime import date

        from app.database.models import Expense

        expenses = pending_data.get("expenses", [])
        total = pending_data.get("total", 0)

        # Evaluate response - simple yes/no check
        response_lower = response.lower().strip().rstrip("!.,?")
        positive_responses = (
            "sim",
            "s",
            "yes",
            "y",
            "ok",
            "pode",
            "paguei",
            "ja paguei",
            "já paguei",
            "isso",
            "confirma",
            "confirmo",
            "beleza",
            "show",
        )
        negative_responses = (
            "nao",
            "não",
            "n",
            "no",
            "ainda nao",
            "ainda não",
            "cancela",
            "ignora",
            "pula",
            "depois",
        )

        # Clean up pending
        await session.execute(
            delete(PendingConfirmation).where(
                PendingConfirmation.user_phone == normalize_phone(phone)
            )
        )
        await session.commit()

        if response_lower in positive_responses:
            # Create all expenses from recurring
            created_count = 0
            today = date.today()

            for exp_data in expenses:
                new_expense = Expense(
                    user_phone=normalize_phone(phone),
                    description=exp_data["description"],
                    amount=exp_data["amount"],
                    category_id=exp_data["category_id"],
                    payment_method_id=exp_data["payment_method_id"],
                    type="Negativo",
                    is_recurring=False,
                    date=today,
                )
                session.add(new_expense)
                created_count += 1

            await session.commit()

            # Send confirmation message
            await self.evolution.send_text(
                phone,
                f"Lancadas {created_count} despesa(s) recorrente(s) (R$ {total:.2f})",
            )

            # Check for budget alerts for each expense
            for exp_data in expenses:
                category_id = exp_data.get("category_id")
                if category_id:
                    alerts = await self.budget_service.check_and_send_alerts(
                        session, phone, category_id
                    )
                    for alert in alerts:
                        alert_msg = self.gemini.format_budget_alert(alert)
                        await self.evolution.send_text(phone, alert_msg)

        elif response_lower in negative_responses:
            await self.evolution.send_text(
                phone,
                "Despesas recorrentes ignoradas por hoje.",
            )
        else:
            # Unknown response - try LLM evaluation
            evaluation = await self.gemini.evaluate_confirmation_response(
                f"Confirmacao de {len(expenses)} despesa(s) recorrente(s) no valor de R$ {total:.2f}",
                response,
            )

            action = evaluation.get("action", "unknown")

            if action == "confirm":
                # Re-call with "sim" to create expenses
                await self._handle_recurring_confirmation(
                    session,
                    phone,
                    "sim",
                    {"expenses": expenses, "total": total, "type": "recurring_confirmation"},
                )
            elif action == "cancel":
                await self.evolution.send_text(
                    phone,
                    "Despesas recorrentes ignoradas por hoje.",
                )
            else:
                await self.evolution.send_text(
                    phone,
                    "Nao entendi. Responda *sim* para lancar as despesas ou *nao* para ignorar hoje.",
                )
                # Re-save pending for another try
                await self.save_pending_confirmation(
                    session,
                    phone,
                    {
                        "type": "recurring_confirmation",
                        "expenses": expenses,
                        "total": total,
                    },
                )
