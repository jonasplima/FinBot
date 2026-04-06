"""Webhook handler for Evolution API messages."""

import io
import logging
from datetime import datetime, timedelta

from pypdf import PdfReader
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database.connection import async_session
from app.database.models import PendingConfirmation, User
from app.services.ai import AIService
from app.services.backup import BackupService
from app.services.budget import BudgetService
from app.services.chart import ChartService
from app.services.currency import CurrencyService
from app.services.evolution import EvolutionService
from app.services.expense import ExpenseService
from app.services.goal import GoalService
from app.services.rate_limit import RateLimitService
from app.services.user import UserService
from app.utils.validators import is_phone_allowed, mask_phone, normalize_phone, sanitize_for_log

logger = logging.getLogger(__name__)
settings = get_settings()


class WebhookHandler:
    """Handler for incoming WhatsApp messages."""

    def __init__(self):
        self.processing_committed = False
        self.evolution = EvolutionService()
        self.ai = AIService()
        self.expense_service = ExpenseService()
        self.budget_service = BudgetService()
        self.chart_service = ChartService()
        self.goal_service = GoalService()
        self.currency_service = CurrencyService()
        self.backup_service = BackupService()
        self.user_service = UserService()
        self.rate_limit_service = RateLimitService()

    async def _notify_user(self, phone: str, message: str) -> None:
        """Send a best-effort user notification without breaking committed flows."""
        try:
            await self.evolution.send_text(phone, message)
        except Exception as exc:
            logger.error(f"Failed to notify user {mask_phone(phone)}: {exc}")

    def _mark_processing_committed(self) -> None:
        """Mark that the current webhook already produced a persistent side effect."""
        self.processing_committed = True

    async def handle(self, webhook_data: dict) -> None:
        """Process incoming webhook event."""
        self.processing_committed = False
        # Extract message data
        msg_data = self.evolution.extract_message_data(webhook_data)
        if not msg_data:
            logger.info("No message data extracted (might be a status update or own message)")
            return

        phone = msg_data["phone"]
        text = msg_data["text"]
        safe_phone = mask_phone(phone)

        logger.info(f"Message received from {safe_phone}: {sanitize_for_log(text)}")

        # Optional allowlist for controlled rollout
        if settings.allowed_phones and not is_phone_allowed(phone, settings.allowed_phones):
            logger.warning(f"Unauthorized phone: {safe_phone}")
            return

        logger.info(f"Phone {safe_phone} accepted for processing")

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
        user = await self.user_service.get_or_create_user(session, phone)
        safe_phone = mask_phone(phone)

        logger.info(f"Processing message for {safe_phone}")

        if not self.user_service.has_accepted_current_terms(user):
            await self.handle_user_onboarding(session, msg_data, user)
            return

        direct_limit_command = self.user_service.parse_limit_command(msg_data["text"])
        if direct_limit_command:
            await self.handle_limit_command(session, phone, user, direct_limit_command)
            return

        if msg_data["text"]:
            text_allowed = await self._check_daily_limit(session, phone, user, "daily_text_limit")
            if not text_allowed:
                return

        # Check for pending confirmation first
        pending = await self.get_pending_confirmation(session, phone)
        logger.info(f"Pending confirmation found for {safe_phone}: {pending is not None}")

        if pending:
            # User is responding to a confirmation
            logger.info(
                f"Handling confirmation response for {safe_phone}, type: {pending.data.get('type')}"
            )
            await self.handle_confirmation_response(session, phone, text, pending, user)
            return

        # Check if message is media
        if msg_data["has_image"]:
            media_allowed = await self._check_daily_limit(session, phone, user, "daily_media_limit")
            if not media_allowed:
                return
            await self.handle_image_message(session, msg_data, user)
            return
        if msg_data.get("has_document") and msg_data.get("document_mimetype") == "application/pdf":
            media_allowed = await self._check_daily_limit(session, phone, user, "daily_media_limit")
            if not media_allowed:
                return
            await self.handle_pdf_message(session, msg_data, user)
            return
        if msg_data.get("has_document") and self._is_json_document(msg_data):
            media_allowed = await self._check_daily_limit(session, phone, user, "daily_media_limit")
            if not media_allowed:
                return
            await self.handle_backup_document(session, msg_data)
            return

        # Process text message with the configured AI provider
        await self.handle_text_message(session, msg_data, user)

    async def handle_text_message(
        self,
        session: AsyncSession,
        msg_data: dict,
        user: User,
    ) -> None:
        """Handle text message with the configured AI provider."""
        phone = msg_data["phone"]
        text = msg_data["text"]
        safe_phone = mask_phone(phone)

        if not text:
            return

        try:
            ai_allowed = await self._check_daily_limit(session, phone, user, "daily_ai_limit")
            if not ai_allowed:
                return

            # Process with the configured AI provider
            result = await self.ai.process_message(text, user=user)

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
            elif intent == "show_chart":
                await self.handle_show_chart(session, phone, result)
            elif intent == "create_goal":
                await self.handle_create_goal(session, phone, result)
            elif intent == "check_goal":
                await self.handle_check_goal(session, phone, result)
            elif intent == "list_goals":
                await self.handle_list_goals(session, phone)
            elif intent == "remove_goal":
                await self.handle_remove_goal(session, phone, result)
            elif intent == "add_to_goal":
                await self.handle_add_to_goal(session, phone, result)
            elif intent == "convert_currency":
                await self.handle_convert_currency(session, phone, result)
            elif intent == "show_limits":
                await self.handle_show_limits(phone, user)
            elif intent == "set_user_limit":
                await self.handle_set_user_limit(session, phone, user, result)
            elif intent == "export_backup":
                await self.handle_export_backup(session, phone)
            elif intent == "import_backup":
                await self.evolution.send_text(
                    phone,
                    "Envie o arquivo JSON do backup para eu validar e pedir sua confirmacao.",
                )
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
                    "- Ver orcamentos: 'meus limites de gasto'\n"
                    "- Ver grafico: 'mostra grafico de pizza'\n"
                    "- Criar meta: 'quero economizar 1000 reais ate dezembro'\n"
                    "- Ver metas: 'minhas metas'\n"
                    "- Ver limites: 'meus limites'\n"
                    "- Backup: 'exporta meu backup'",
                )

        except Exception as e:
            logger.error(
                f"Error processing message from {safe_phone}: {type(e).__name__}: {e}",
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

    async def handle_user_onboarding(
        self,
        session: AsyncSession,
        msg_data: dict,
        user: User,
    ) -> None:
        """Handle onboarding flow for users who have not accepted current terms."""
        phone = msg_data["phone"]
        text = msg_data["text"]

        pending = await self.get_pending_confirmation(session, phone)
        if pending and pending.data.get("type") == "user_onboarding":
            if self.user_service.is_terms_acceptance(text):
                await session.delete(pending)
                await session.commit()
                await self.user_service.accept_terms(session, user)
                await self.evolution.send_text(
                    phone,
                    "Termos aceitos com sucesso! Agora voce pode usar o FinBot normalmente.",
                )
                return

            if self.user_service.is_terms_rejection(text):
                await session.delete(pending)
                await session.commit()
                await self.user_service.reject_terms(session, user)
                await self.evolution.send_text(
                    phone,
                    "Sem o aceite dos termos eu nao posso continuar. "
                    "Quando quiser tentar de novo, envie qualquer mensagem.",
                )
                return

            await self.evolution.send_text(
                phone,
                "Para continuar, responda *sim* para aceitar os termos ou *nao* para recusar.",
            )
            return

        if pending:
            await session.delete(pending)
            await session.commit()

        await self.save_pending_confirmation(
            session,
            phone,
            {"type": "user_onboarding", "terms_version": settings.terms_version},
        )
        await self.evolution.send_text(phone, self.user_service.build_terms_message())

    async def handle_limit_command(
        self,
        session: AsyncSession,
        phone: str,
        user: User,
        command_data: dict,
    ) -> None:
        """Handle direct local commands related to daily usage limits."""
        action = command_data.get("action")
        if action == "show":
            await self.handle_show_limits(phone, user)
            return

        if action == "set":
            try:
                updated_user = await self.user_service.update_user_limit(
                    session,
                    user,
                    command_data["limit_type"],
                    command_data["limit_value"],
                )
                await self.evolution.send_text(
                    phone,
                    self.user_service.format_updated_limit_message(
                        updated_user, command_data["limit_type"]
                    ),
                )
            except ValueError as e:
                await self.evolution.send_text(phone, str(e))

    async def handle_show_limits(
        self,
        phone: str,
        user: User,
    ) -> None:
        """Handle showing current user limits and daily usage."""
        try:
            usage = await self.rate_limit_service.get_usage_summary(user)
        except RuntimeError:
            await self.evolution.send_text(
                phone,
                "Nao consegui consultar seus limites agora porque o armazenamento compartilhado "
                "esta indisponivel. Tente novamente em instantes.",
            )
            return

        await self.evolution.send_text(phone, self.user_service.format_user_limits(user, usage))

    async def handle_set_user_limit(
        self,
        session: AsyncSession,
        phone: str,
        user: User,
        result: dict,
    ) -> None:
        """Handle AI-parsed limit updates."""
        data = result.get("data", {})
        limit_type = data.get("limit_type")
        limit_value = data.get("daily_limit")

        if limit_type not in {"daily_text_limit", "daily_media_limit", "daily_ai_limit"}:
            await self.evolution.send_text(
                phone,
                "Nao consegui identificar qual limite voce quer alterar. Use: texto, midia ou ia.",
            )
            return

        if limit_value is None:
            await self.evolution.send_text(
                phone,
                "Nao consegui identificar o novo valor do limite.",
            )
            return

        try:
            updated_user = await self.user_service.update_user_limit(
                session,
                user,
                limit_type,
                int(limit_value),
            )
            await self.evolution.send_text(
                phone,
                self.user_service.format_updated_limit_message(updated_user, limit_type),
            )
        except ValueError as e:
            await self.evolution.send_text(phone, str(e))

    async def handle_image_message(
        self,
        session: AsyncSession,
        msg_data: dict,
        user: User,
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

            ai_allowed = await self._check_daily_limit(session, phone, user, "daily_ai_limit")
            if not ai_allowed:
                return

            # Process with the configured vision-capable AI provider
            result = await self.ai.process_image(image_data, msg_data.get("text", ""), user=user)

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

    async def handle_pdf_message(
        self,
        session: AsyncSession,
        msg_data: dict,
        user: User,
    ) -> None:
        """Handle PDF receipt/invoice messages."""
        phone = msg_data["phone"]

        try:
            pdf_data = await self.evolution.download_media(msg_data["message_key"])

            if not pdf_data:
                await self.evolution.send_text(
                    phone,
                    "Nao consegui baixar o PDF. Tente enviar novamente.",
                )
                return

            pdf_validation_error = self._validate_pdf_document(pdf_data)
            if pdf_validation_error:
                await self.evolution.send_text(phone, pdf_validation_error)
                return

            pdf_text = self._extract_text_from_pdf(pdf_data)
            if not pdf_text:
                await self.evolution.send_text(
                    phone,
                    "Nao consegui extrair texto do PDF. "
                    "Se for um documento escaneado, envie como imagem ou digite manualmente.",
                )
                return

            ai_allowed = await self._check_daily_limit(session, phone, user, "daily_ai_limit")
            if not ai_allowed:
                return

            result = await self.ai.process_pdf_text(pdf_text, msg_data.get("text", ""), user=user)

            if result.get("success"):
                await self.handle_register_expense(session, phone, result)
            else:
                await self.evolution.send_text(
                    phone,
                    "Nao consegui interpretar o comprovante em PDF. "
                    "Tente enviar uma imagem nitida ou digite manualmente.",
                )

        except Exception as e:
            logger.error(f"Error processing PDF: {e}")
            await self.evolution.send_text(
                phone,
                "Erro ao processar o PDF. Tente novamente.",
            )

    async def handle_backup_document(
        self,
        session: AsyncSession,
        msg_data: dict,
    ) -> None:
        """Handle JSON backup documents sent by the user."""
        phone = msg_data["phone"]
        user = await self.user_service.get_or_create_user(session, phone)

        try:
            document_bytes = await self.evolution.download_media(msg_data["message_key"])

            if not document_bytes:
                await self.evolution.send_text(
                    phone,
                    "Nao consegui baixar o arquivo JSON. Tente enviar novamente.",
                )
                return

            parsed = self.backup_service.parse_backup_document(document_bytes)
            if not parsed["success"]:
                await self.evolution.send_text(phone, parsed["error"])
                return

            backup_data = parsed["backup_data"]
            summary = self.backup_service.summarize_backup(backup_data)
            stored = await self.backup_service.store_temporary_backup(backup_data)
            if not stored["success"]:
                await self.evolution.send_text(
                    phone,
                    stored.get("error", "Nao consegui preparar o backup para restauracao."),
                )
                return

            await self.save_pending_confirmation(
                session,
                phone,
                {
                    "type": "backup_restore",
                    "backup_ref": stored["backup_ref"],
                    "backup_hash": stored["backup_hash"],
                    "summary": summary,
                    "target_phone": normalize_phone(phone),
                },
            )

            await self.evolution.send_text(
                phone,
                self._build_backup_restore_message(summary, phone, user.backup_owner_id),
            )

        except Exception as e:
            logger.error(f"Error processing backup document: {e}")
            await self.evolution.send_text(
                phone,
                "Erro ao processar o backup JSON. Tente novamente.",
            )

    def _extract_text_from_pdf(self, pdf_data: bytes) -> str:
        """Extract plain text from a PDF document."""
        try:
            reader = PdfReader(io.BytesIO(pdf_data))
            text_parts = []
            total_chars = 0
            for page in reader.pages[: settings.effective_max_pdf_pages]:
                page_text = page.extract_text() or ""
                if page_text.strip():
                    remaining_chars = settings.effective_max_pdf_text_chars - total_chars
                    if remaining_chars <= 0:
                        break
                    trimmed = page_text.strip()[:remaining_chars]
                    text_parts.append(trimmed)
                    total_chars += len(trimmed)
            return "\n\n".join(text_parts).strip()
        except Exception as e:
            logger.error(f"Error extracting text from PDF: {e}")
            return ""

    def _validate_pdf_document(self, pdf_data: bytes) -> str | None:
        """Validate PDF size and page count before extraction."""
        if len(pdf_data) > settings.effective_max_pdf_size_bytes:
            max_mb = settings.effective_max_pdf_size_bytes / 1_000_000
            return f"Esse PDF excede o limite seguro do servidor ({max_mb:.1f} MB)."

        try:
            reader = PdfReader(io.BytesIO(pdf_data))
        except Exception:
            return "Nao consegui ler esse PDF. Tente enviar outro arquivo ou uma imagem."

        if len(reader.pages) > settings.effective_max_pdf_pages:
            return (
                "Esse PDF tem paginas demais para processamento automatico. "
                "Envie um arquivo menor ou apenas as paginas relevantes."
            )

        return None

    async def handle_register_expense(
        self,
        session: AsyncSession,
        phone: str,
        data: dict,
    ) -> None:
        """Handle expense registration (with confirmation)."""
        from decimal import Decimal

        expense_data = data.get("data", {})

        # Normalize shared_percentage - some AI providers may return 0.7 instead of 70
        shared_percentage = expense_data.get("shared_percentage")
        if shared_percentage is not None and shared_percentage < 1:
            expense_data["shared_percentage"] = shared_percentage * 100

        # Handle currency conversion if foreign currency detected
        currency = expense_data.get("currency")
        if currency and currency.upper() != "BRL":
            amount = Decimal(str(expense_data.get("amount", 0)))
            user = await self.user_service.get_or_create_user(session, phone)
            conversion = await self.currency_service.convert_to_brl(amount, currency, user=user)

            if conversion["success"]:
                # Store original currency info and converted amount
                expense_data["original_currency"] = conversion["original_currency"]
                expense_data["original_amount"] = float(conversion["original_amount"])
                expense_data["exchange_rate"] = float(conversion["exchange_rate"])
                expense_data["amount"] = float(conversion["converted_amount"])
            else:
                await self.evolution.send_text(
                    phone,
                    f"Erro na conversao de moeda: {conversion.get('error', 'Erro desconhecido')}",
                )
                return

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
            msg += "1. Cartão de Crédito\n"
            msg += "2. Cartão de Débito\n"
            msg += "3. Pix\n"
            msg += "4. Dinheiro\n"
            msg += "5. Vale Refeição\n"
            msg += "6. Vale Alimentação"

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
        original_currency = expense_data.get("original_currency")
        original_amount = expense_data.get("original_amount")
        exchange_rate = expense_data.get("exchange_rate")

        msg = "Entendi:\n"

        # Show currency conversion info if applicable
        if original_currency and original_currency != "BRL":
            msg += f"- Valor original: {original_currency} {original_amount:.2f}\n"
            msg += f"- Valor convertido: R$ {amount:.2f}\n"
            msg += f"- Cotacao: 1 {original_currency} = R$ {exchange_rate:.4f}\n"
        else:
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
            self._mark_processing_committed()
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
        export_format = expense_data.get("export_format", "xlsx")

        export_service = ExportService()
        if export_format == "pdf":
            result = await export_service.export_month_pdf(session, phone, month, year)
        else:
            result = await export_service.export_month(session, phone, month, year)

        if result["success"]:
            await self.evolution.send_document(
                phone,
                result["file_base64"],
                result["filename"],
                caption=f"Seus gastos de {result['month_name']}",
                mimetype=result.get(
                    "mimetype",
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                ),
            )
        else:
            await self.evolution.send_text(phone, result["message"])

    async def handle_export_backup(
        self,
        session: AsyncSession,
        phone: str,
    ) -> None:
        """Handle backup export request."""
        result = await self.backup_service.export_user_backup(session, phone)

        if result["success"]:
            await self.evolution.send_document(
                phone,
                result["file_base64"],
                result["filename"],
                caption="Seu backup completo do FinBot",
                mimetype=result["mimetype"],
            )
        else:
            await self.evolution.send_text(
                phone,
                result.get("error", "Nao consegui gerar seu backup agora."),
            )

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
            self._mark_processing_committed()
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
            self._mark_processing_committed()
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
            self._mark_processing_committed()
            category_name = result.get("category") or "Geral"
            await self.evolution.send_text(
                phone,
                f"Orcamento de {category_name} removido com sucesso.",
            )
        else:
            await self.evolution.send_text(phone, result["error"])

    async def handle_show_chart(
        self,
        session: AsyncSession,
        phone: str,
        data: dict,
    ) -> None:
        """Handle chart generation request."""
        from datetime import date

        from app.services.expense import MONTH_NAMES

        chart_data = data.get("data", {})
        chart_type = chart_data.get("chart_type", "pie")
        month = chart_data.get("month")
        year = chart_data.get("year")

        # Default to current month/year if not specified
        today = date.today()
        if month is None:
            month = today.month
        if year is None:
            year = today.year

        try:
            # Get data based on chart type
            if chart_type == "pie":
                expenses_data = await self.expense_service.get_expenses_by_category(
                    session, phone, month, year
                )
                if not expenses_data:
                    await self.evolution.send_text(
                        phone,
                        f"Sem gastos registrados em {MONTH_NAMES[month]} de {year} para gerar grafico.",
                    )
                    return

                title = f"Gastos por Categoria - {MONTH_NAMES[month]}/{year}"
                chart_bytes = self.chart_service.generate_pie_chart(expenses_data, title)

            elif chart_type == "bars":
                expenses_data = await self.expense_service.get_top_expenses(
                    session, phone, month, year, limit=10
                )
                if not expenses_data:
                    await self.evolution.send_text(
                        phone,
                        f"Sem gastos registrados em {MONTH_NAMES[month]} de {year} para gerar grafico.",
                    )
                    return

                title = f"Maiores Gastos - {MONTH_NAMES[month]}/{year}"
                chart_bytes = self.chart_service.generate_bar_chart(expenses_data, title)

            elif chart_type == "line":
                expenses_data = await self.expense_service.get_daily_totals(
                    session, phone, month, year
                )
                if not expenses_data:
                    await self.evolution.send_text(
                        phone,
                        f"Sem gastos registrados em {MONTH_NAMES[month]} de {year} para gerar grafico.",
                    )
                    return

                title = f"Evolucao dos Gastos - {MONTH_NAMES[month]}/{year}"
                chart_bytes = self.chart_service.generate_line_chart(expenses_data, title)

            else:
                # Default to pie chart
                expenses_data = await self.expense_service.get_expenses_by_category(
                    session, phone, month, year
                )
                if not expenses_data:
                    await self.evolution.send_text(
                        phone,
                        f"Sem gastos registrados em {MONTH_NAMES[month]} de {year} para gerar grafico.",
                    )
                    return

                title = f"Gastos por Categoria - {MONTH_NAMES[month]}/{year}"
                chart_bytes = self.chart_service.generate_pie_chart(expenses_data, title)

            # Send the chart image
            await self.evolution.send_image(
                phone,
                chart_bytes,
                filename=f"grafico_{chart_type}_{month}_{year}.png",
                caption=title,
            )

        except Exception as e:
            logger.error(f"Error generating chart: {e}", exc_info=True)
            await self.evolution.send_text(
                phone,
                "Erro ao gerar grafico. Tente novamente.",
            )

    async def handle_create_goal(
        self,
        session: AsyncSession,
        phone: str,
        data: dict,
    ) -> None:
        """Handle goal creation request with confirmation."""
        from datetime import datetime

        goal_data = data.get("data", {})
        description = goal_data.get("goal_description")
        amount = goal_data.get("goal_amount")
        deadline_str = goal_data.get("goal_deadline")

        # Validate required fields
        if not description or not amount or not deadline_str:
            await self.evolution.send_text(
                phone,
                "Por favor, informe a descricao, valor e prazo da meta.\n"
                "Exemplo: 'quero economizar 1000 reais ate dezembro'",
            )
            return

        # Parse deadline
        try:
            deadline = datetime.strptime(deadline_str, "%Y-%m-%d").date()
        except ValueError:
            await self.evolution.send_text(
                phone,
                "Data invalida. Tente novamente com uma data valida.",
            )
            return

        # Show confirmation
        msg = (
            f"🎯 *Confirme sua meta:*\n\n"
            f"Descricao: {description}\n"
            f"Valor: R$ {amount:.2f}\n"
            f"Prazo: {deadline.strftime('%d/%m/%Y')}\n\n"
            f"Esta correto? Responda *sim* para confirmar ou ajuste os dados."
        )

        # Save pending confirmation
        await self.save_pending_confirmation(
            session,
            phone,
            {
                "type": "goal_confirmation",
                "goal_data": {
                    "description": description,
                    "target_amount": float(amount),
                    "deadline": deadline_str,
                },
            },
        )

        await self.evolution.send_text(phone, msg)

    async def handle_check_goal(
        self,
        session: AsyncSession,
        phone: str,
        data: dict,
    ) -> None:
        """Handle goal progress check request."""
        goal_data = data.get("data", {})
        description = goal_data.get("goal_description")

        result = await self.goal_service.check_goal_progress(session, phone, description)

        if result["success"]:
            progress = result["progress"]
            msg = self.ai.format_goal_motivation(progress)
            await self.evolution.send_text(phone, msg)
        else:
            await self.evolution.send_text(phone, result["error"])

    async def handle_list_goals(
        self,
        session: AsyncSession,
        phone: str,
    ) -> None:
        """Handle listing all goals."""
        result = await self.goal_service.list_goals(session, phone)

        if not result["success"]:
            await self.evolution.send_text(phone, result.get("error", "Erro ao listar metas."))
            return

        goals = result.get("goals", [])

        if not goals:
            await self.evolution.send_text(
                phone,
                "Voce nao tem metas ativas.\n\n"
                "Para criar uma, diga: 'quero economizar 1000 reais ate dezembro'",
            )
            return

        msg = "📋 *Suas metas de economia:*\n\n"
        for goal in goals:
            percentage = goal["percentage"]
            if percentage >= 75:
                emoji = "🌟"
            elif percentage >= 50:
                emoji = "💪"
            elif percentage >= 25:
                emoji = "📊"
            else:
                emoji = "🎯"

            msg += (
                f"{emoji} *{goal['description']}*\n"
                f"   Progresso: {percentage:.0f}%\n"
                f"   Meta: R$ {goal['target_amount']:.2f}\n"
                f"   Economizado: R$ {goal['current_progress']:.2f}\n"
                f"   Prazo: {goal['deadline']}\n\n"
            )

        await self.evolution.send_text(phone, msg)

    async def handle_remove_goal(
        self,
        session: AsyncSession,
        phone: str,
        data: dict,
    ) -> None:
        """Handle goal removal request."""
        goal_data = data.get("data", {})
        description = goal_data.get("goal_description")

        if not description:
            await self.evolution.send_text(
                phone,
                "Qual meta voce deseja remover?",
            )
            return

        result = await self.goal_service.remove_goal(session, phone, description)

        if result["success"]:
            await self.evolution.send_text(
                phone,
                f"Meta '{description}' removida com sucesso.",
            )
        else:
            await self.evolution.send_text(phone, result["error"])

    async def handle_add_to_goal(
        self,
        session: AsyncSession,
        phone: str,
        data: dict,
    ) -> None:
        """Handle manual deposit to goal."""
        from decimal import Decimal

        goal_data = data.get("data", {})
        description = goal_data.get("goal_description")
        deposit = goal_data.get("goal_deposit")

        if not deposit:
            await self.evolution.send_text(
                phone,
                "Por favor, informe o valor a depositar.\n"
                "Exemplo: 'depositar 200 reais na meta de viagem'",
            )
            return

        # If no description, get first active goal
        if not description:
            list_result = await self.goal_service.list_goals(session, phone)
            if list_result["success"] and list_result.get("goals"):
                description = list_result["goals"][0]["description"]
            else:
                await self.evolution.send_text(
                    phone,
                    "Voce nao tem metas ativas. Crie uma primeiro.",
                )
                return

        result = await self.goal_service.add_to_goal(
            session, phone, description, Decimal(str(deposit))
        )

        if result["success"]:
            progress = result["progress"]
            msg = (
                f"💰 *Deposito registrado!*\n\n"
                f"Valor: R$ {deposit:.2f}\n"
                f"Meta: {description}\n\n"
                f"Novo progresso: {progress['percentage']:.0f}%\n"
                f"Total economizado: R$ {progress['current_progress']:.2f}"
            )

            # Check if goal was achieved
            if progress.get("is_achieved"):
                msg += "\n\n🎉 *Parabens! Voce atingiu sua meta!*"

            await self.evolution.send_text(phone, msg)
        else:
            await self.evolution.send_text(phone, result["error"])

    async def handle_convert_currency(
        self,
        session: AsyncSession,
        phone: str,
        data: dict,
    ) -> None:
        """Handle standalone currency conversion request."""
        from decimal import Decimal

        currency_data = data.get("data", {})
        amount = currency_data.get("amount")
        from_currency = currency_data.get("currency", "USD")
        to_currency = currency_data.get("target_currency", "BRL")

        if not amount:
            await self.evolution.send_text(
                phone,
                "Por favor, informe o valor a converter.\nExemplo: 'quanto e 100 dolares em reais'",
            )
            return

        user = await self.user_service.get_or_create_user(session, phone)

        result = await self.currency_service.convert_currency(
            Decimal(str(amount)),
            from_currency,
            to_currency,
            user=user,
        )

        if result["success"]:
            msg = self.currency_service.format_conversion_result(result)
            await self.evolution.send_text(phone, msg)
        else:
            await self.evolution.send_text(phone, result.get("error", "Erro na conversao"))

    async def handle_confirmation_response(
        self,
        session: AsyncSession,
        phone: str,
        response: str,
        pending: PendingConfirmation,
        user: User,
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
            await self._handle_recurring_confirmation(session, phone, response, pending_data, user)
            return

        # Handle goal confirmation
        if pending_type == "goal_confirmation":
            await self._handle_goal_confirmation(session, phone, response, pending_data, user)
            return

        if pending_type == "backup_restore":
            await self._handle_backup_restore_confirmation(
                session, phone, response, pending_data, user
            )
            return

        # Build expense summary for LLM context
        expense_summary = self._build_expense_summary(expense_data, pending_type)

        ai_allowed = await self._check_daily_limit(session, phone, user, "daily_ai_limit")
        if not ai_allowed:
            return

        # Evaluate response using LLM
        evaluation = await self.ai.evaluate_confirmation_response(
            expense_summary, response, user=user
        )

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
                self._mark_processing_committed()
                await self._notify_user(phone, "Registrado com sucesso!")

                # Check for budget alerts (only for expenses, not income)
                if expense_data.get("category"):
                    category_id = await self._get_category_id(session, expense_data["category"])
                    if category_id:
                        alerts = await self.budget_service.check_and_send_alerts(
                            session, phone, category_id
                        )
                        # Send alert messages
                        for alert in alerts:
                            alert_msg = self.ai.format_budget_alert(alert)
                            await self._notify_user(phone, alert_msg)
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
            "1": "Cartão de Crédito",
            "credito": "Cartão de Crédito",
            "cartao de credito": "Cartão de Crédito",
            "cartão de crédito": "Cartão de Crédito",
            "2": "Cartão de Débito",
            "debito": "Cartão de Débito",
            "cartao de debito": "Cartão de Débito",
            "cartão de débito": "Cartão de Débito",
            "3": "Pix",
            "pix": "Pix",
            "4": "Dinheiro",
            "dinheiro": "Dinheiro",
            "5": "Vale Refeição",
            "vr": "Vale Refeição",
            "vale refeicao": "Vale Refeição",
            "vale refeição": "Vale Refeição",
            "6": "Vale Alimentação",
            "va": "Vale Alimentação",
            "vale alimentacao": "Vale Alimentação",
            "vale alimentação": "Vale Alimentação",
        }

        payment_method = payment_map.get(response.lower().strip())

        if not payment_method:
            # Try using LLM to understand the payment method
            evaluation = await self.ai.evaluate_confirmation_response(
                "Selecao de forma de pagamento",
                response,
                user=await self.user_service.get_or_create_user(session, phone),
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
                "1. Cartão de Crédito\n"
                "2. Cartão de Débito\n"
                "3. Pix\n"
                "4. Dinheiro\n"
                "5. Vale Refeição\n"
                "6. Vale Alimentação",
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
        user: User,
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
            self._mark_processing_committed()

            # Send confirmation message
            await self._notify_user(
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
                        alert_msg = self.ai.format_budget_alert(alert)
                        await self._notify_user(phone, alert_msg)

        elif response_lower in negative_responses:
            await self.evolution.send_text(
                phone,
                "Despesas recorrentes ignoradas por hoje.",
            )
        else:
            # Unknown response - try LLM evaluation
            ai_allowed = await self._check_daily_limit(session, phone, user, "daily_ai_limit")
            if not ai_allowed:
                return

            evaluation = await self.ai.evaluate_confirmation_response(
                f"Confirmacao de {len(expenses)} despesa(s) recorrente(s) no valor de R$ {total:.2f}",
                response,
                user=user,
            )

            action = evaluation.get("action", "unknown")

            if action == "confirm":
                # Re-call with "sim" to create expenses
                await self._handle_recurring_confirmation(
                    session,
                    phone,
                    "sim",
                    {"expenses": expenses, "total": total, "type": "recurring_confirmation"},
                    user,
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

    async def _handle_goal_confirmation(
        self,
        session: AsyncSession,
        phone: str,
        response: str,
        pending_data: dict,
        user: User,
    ) -> None:
        """Handle user response to goal creation confirmation."""
        from datetime import datetime
        from decimal import Decimal

        goal_data = pending_data.get("goal_data", {})

        # Evaluate response - simple yes/no check
        response_lower = response.lower().strip().rstrip("!.,?")
        positive_responses = (
            "sim",
            "s",
            "yes",
            "y",
            "ok",
            "pode",
            "isso",
            "confirma",
            "confirmo",
            "beleza",
            "show",
            "correto",
            "certo",
        )
        negative_responses = (
            "nao",
            "não",
            "n",
            "no",
            "cancela",
            "cancelar",
            "desisto",
        )

        # Clean up pending
        await session.execute(
            delete(PendingConfirmation).where(
                PendingConfirmation.user_phone == normalize_phone(phone)
            )
        )
        await session.commit()

        if response_lower in positive_responses:
            # Create the goal
            deadline = datetime.strptime(goal_data["deadline"], "%Y-%m-%d").date()

            result = await self.goal_service.create_goal(
                session,
                phone,
                goal_data["description"],
                Decimal(str(goal_data["target_amount"])),
                deadline,
            )

            if result["success"]:
                self._mark_processing_committed()
                msg = (
                    f"✅ *Meta criada com sucesso!*\n\n"
                    f"Descricao: {result['description']}\n"
                    f"Valor: R$ {result['target_amount']:.2f}\n"
                    f"Prazo: {result['deadline']}\n\n"
                    f"Voce sera atualizado semanalmente sobre seu progresso!"
                )
                await self.evolution.send_text(phone, msg)
            else:
                await self.evolution.send_text(phone, result["error"])

        elif response_lower in negative_responses:
            await self.evolution.send_text(
                phone,
                "Meta cancelada. Quando quiser criar uma meta, e so me dizer!",
            )

        else:
            # Unknown response - try LLM evaluation
            summary = (
                f"Criacao de meta: {goal_data.get('description')}, "
                f"R$ {goal_data.get('target_amount')}, "
                f"prazo {goal_data.get('deadline')}"
            )
            ai_allowed = await self._check_daily_limit(session, phone, user, "daily_ai_limit")
            if not ai_allowed:
                return

            evaluation = await self.ai.evaluate_confirmation_response(summary, response, user=user)

            action = evaluation.get("action", "unknown")

            if action == "confirm":
                # Re-call with "sim" to create goal
                await self._handle_goal_confirmation(
                    session,
                    phone,
                    "sim",
                    {"type": "goal_confirmation", "goal_data": goal_data},
                    user,
                )
            elif action == "cancel":
                await self.evolution.send_text(
                    phone,
                    "Meta cancelada. Quando quiser criar uma meta, e so me dizer!",
                )
            else:
                await self.evolution.send_text(
                    phone,
                    "Nao entendi. Responda *sim* para criar a meta ou *nao* para cancelar.",
                )
                # Re-save pending for another try
                await self.save_pending_confirmation(
                    session,
                    phone,
                    {
                        "type": "goal_confirmation",
                        "goal_data": goal_data,
                    },
                )

    async def _handle_backup_restore_confirmation(
        self,
        session: AsyncSession,
        phone: str,
        response: str,
        pending_data: dict,
        user: User,
    ) -> None:
        """Handle user response to backup restore confirmation."""
        summary = pending_data.get("summary", {})
        backup_ref = pending_data.get("backup_ref", "")
        target_phone = normalize_phone(pending_data.get("target_phone", phone))
        source_phone = normalize_phone(summary.get("source_phone", ""))
        source_backup_owner_id = str(summary.get("source_backup_owner_id") or "").strip()
        requires_migration_confirmation = self._requires_backup_migration_confirmation(
            summary,
            target_phone,
            user.backup_owner_id,
        )
        response_lower = response.lower().strip().rstrip("!.,?")
        explicit_migration_confirmation = response_lower in {
            "sim migrar",
            "confirmo migracao",
            "confirmo migração",
            "confirmar migracao",
            "confirmar migração",
            "migrar backup",
        }

        positive_responses = (
            "sim",
            "s",
            "ok",
            "confirmo",
            "confirma",
            "pode",
            "restaurar",
            "importar",
        )
        negative_responses = (
            "nao",
            "não",
            "n",
            "cancela",
            "cancelar",
            "desisto",
        )
        migration_positive_responses = (
            "sim migrar",
            "confirmo migracao",
            "confirmo migração",
            "confirmar migracao",
            "confirmar migração",
            "migrar backup",
        )

        await session.execute(
            delete(PendingConfirmation).where(
                PendingConfirmation.user_phone == normalize_phone(phone)
            )
        )
        await session.commit()

        if requires_migration_confirmation and response_lower in positive_responses:
            await self.evolution.send_text(
                phone,
                self._build_backup_migration_warning(source_phone, target_phone),
            )
            await self.save_pending_confirmation(
                session,
                phone,
                {
                    "type": "backup_restore",
                    "backup_ref": backup_ref,
                    "summary": summary,
                    "target_phone": target_phone,
                },
            )
            return

        if response_lower in positive_responses or response_lower in migration_positive_responses:
            load_result = await self.backup_service.load_temporary_backup(backup_ref)
            if not load_result["success"]:
                await self.evolution.send_text(
                    phone,
                    load_result["error"],
                )
                return

            backup_data = load_result["backup_data"]
            result = await self.backup_service.restore_user_backup(session, phone, backup_data)
            await self.backup_service.delete_temporary_backup(backup_ref)
            if result["success"]:
                if (
                    explicit_migration_confirmation
                    and source_backup_owner_id
                    and user.backup_owner_id != source_backup_owner_id
                ):
                    user = await self.user_service.adopt_backup_owner_identity(
                        session,
                        user,
                        source_backup_owner_id,
                    )
                await self.backup_service.record_restore_audit(
                    session,
                    target_phone=target_phone,
                    source_phone=source_phone or None,
                    status="restored",
                    requires_migration_confirmation=requires_migration_confirmation,
                    explicit_migration_confirmation=explicit_migration_confirmation,
                    restored_counts=result["restored"],
                )
                self._mark_processing_committed()
                if requires_migration_confirmation:
                    logger.warning(
                        "Backup migration executed from %s to %s after explicit confirmation",
                        source_phone or "desconhecido",
                        target_phone,
                    )
                restored = result["restored"]
                await self._notify_user(
                    phone,
                    "Backup restaurado com sucesso!\n"
                    f"- Despesas: {restored['expenses']}\n"
                    f"- Orcamentos: {restored['budgets']}\n"
                    f"- Alertas: {restored['budget_alerts']}\n"
                    f"- Metas: {restored['goals']}\n"
                    f"- Atualizacoes de metas: {restored['goal_updates']}",
                )
            else:
                await self.backup_service.record_restore_audit(
                    session,
                    target_phone=target_phone,
                    source_phone=source_phone or None,
                    status="failed",
                    requires_migration_confirmation=requires_migration_confirmation,
                    explicit_migration_confirmation=explicit_migration_confirmation,
                    error_message=result.get("error", "Erro desconhecido"),
                )
                await self.evolution.send_text(
                    phone,
                    f"Erro ao restaurar backup: {result.get('error', 'Erro desconhecido')}",
                )
            return

        if response_lower in negative_responses:
            await self.backup_service.delete_temporary_backup(backup_ref)
            await self.backup_service.record_restore_audit(
                session,
                target_phone=target_phone,
                source_phone=source_phone or None,
                status="cancelled",
                requires_migration_confirmation=requires_migration_confirmation,
                explicit_migration_confirmation=False,
            )
            await self.evolution.send_text(phone, "Restauracao cancelada.")
            return

        if requires_migration_confirmation:
            await self.evolution.send_text(
                phone,
                self._build_backup_migration_warning(source_phone, target_phone),
            )
            await self.save_pending_confirmation(
                session,
                phone,
                {
                    "type": "backup_restore",
                    "backup_ref": backup_ref,
                    "summary": summary,
                    "target_phone": target_phone,
                },
            )
            return

        ai_allowed = await self._check_daily_limit(session, phone, user, "daily_ai_limit")
        if not ai_allowed:
            return

        evaluation = await self.ai.evaluate_confirmation_response(
            self._build_backup_restore_summary(summary),
            response,
            user=user,
        )
        action = evaluation.get("action", "unknown")

        if action == "confirm":
            await self._handle_backup_restore_confirmation(
                session, phone, "sim", pending_data, user
            )
        elif action == "cancel":
            await self.evolution.send_text(phone, "Restauracao cancelada.")
        else:
            await self.evolution.send_text(
                phone,
                "Nao entendi. Responda *sim* para restaurar o backup ou *nao* para cancelar.",
            )
            await self.save_pending_confirmation(
                session,
                phone,
                {
                    "type": "backup_restore",
                    "backup_ref": backup_ref,
                    "summary": summary,
                    "target_phone": target_phone,
                },
            )

    async def _check_daily_limit(
        self,
        session: AsyncSession,
        phone: str,
        user: User,
        limit_field: str,
    ) -> bool:
        """Check and increment a daily limit for the user."""
        try:
            usage = await self.rate_limit_service.check_and_increment(user, limit_field)
        except RuntimeError:
            await self.evolution.send_text(
                phone,
                "Nao consegui validar seus limites agora porque o armazenamento compartilhado "
                "esta indisponivel. Tente novamente em instantes.",
            )
            return False

        if usage["allowed"]:
            return True

        refreshed_user = await self.user_service.get_or_create_user(session, user.phone)
        user.daily_text_limit = refreshed_user.daily_text_limit
        user.daily_media_limit = refreshed_user.daily_media_limit
        user.daily_ai_limit = refreshed_user.daily_ai_limit

        await self.evolution.send_text(
            phone,
            self.rate_limit_service.format_limit_reached_message(limit_field, usage),
        )
        return False

    def _is_json_document(self, msg_data: dict) -> bool:
        """Check whether the incoming document looks like a JSON backup file."""
        mimetype = (msg_data.get("document_mimetype") or "").lower()
        filename = (msg_data.get("document_filename") or "").lower()
        return mimetype in {"application/json", "text/json"} or filename.endswith(".json")

    def _build_backup_restore_summary(self, summary: dict) -> str:
        """Build backup summary text for confirmation evaluation."""
        return (
            f"Backup do telefone {summary.get('source_phone', 'desconhecido')}\n"
            f"Despesas: {summary.get('expenses', 0)}\n"
            f"Orcamentos: {summary.get('budgets', 0)}\n"
            f"Alertas: {summary.get('budget_alerts', 0)}\n"
            f"Metas: {summary.get('goals', 0)}\n"
            f"Atualizacoes de metas: {summary.get('goal_updates', 0)}"
        )

    def _build_backup_restore_message(
        self,
        summary: dict,
        target_phone: str,
        target_backup_owner_id: str | None = None,
    ) -> str:
        """Build user-facing message before restoring a backup."""
        normalized_target = normalize_phone(target_phone)
        source_phone = normalize_phone(summary.get("source_phone", ""))
        source_backup_owner_id = str(summary.get("source_backup_owner_id") or "").strip()
        if (
            source_phone
            and source_phone != normalized_target
            and source_backup_owner_id
            and target_backup_owner_id
            and source_backup_owner_id == target_backup_owner_id
        ):
            return (
                "Encontrei um backup valido associado ao mesmo perfil de usuario.\n"
                f"Origem anterior: {source_phone}\n"
                f"Numero atual de destino: {normalized_target}\n"
                f"Despesas: {summary.get('expenses', 0)}\n"
                f"Orcamentos: {summary.get('budgets', 0)}\n"
                f"Alertas: {summary.get('budget_alerts', 0)}\n"
                f"Metas: {summary.get('goals', 0)}\n"
                f"Atualizacoes de metas: {summary.get('goal_updates', 0)}\n\n"
                "Responda *sim* para restaurar esse backup em modo append ou *nao* para cancelar."
            )

        if source_phone and source_phone != normalized_target:
            return (
                "Encontrei um backup valido de outro numero.\n"
                f"Origem do backup: {source_phone}\n"
                f"Numero atual de destino: {normalized_target}\n"
                f"Despesas: {summary.get('expenses', 0)}\n"
                f"Orcamentos: {summary.get('budgets', 0)}\n"
                f"Alertas: {summary.get('budget_alerts', 0)}\n"
                f"Metas: {summary.get('goals', 0)}\n"
                f"Atualizacoes de metas: {summary.get('goal_updates', 0)}\n\n"
                "Se voce trocou de numero e quer migrar esse historico, responda *sim migrar*.\n"
                "Se nao reconhecer esses dados, responda *nao* para cancelar."
            )

        return (
            "Encontrei um backup valido.\n"
            f"Origem: {summary.get('source_phone', 'desconhecido')}\n"
            f"Despesas: {summary.get('expenses', 0)}\n"
            f"Orcamentos: {summary.get('budgets', 0)}\n"
            f"Alertas: {summary.get('budget_alerts', 0)}\n"
            f"Metas: {summary.get('goals', 0)}\n"
            f"Atualizacoes de metas: {summary.get('goal_updates', 0)}\n\n"
            "Responda *sim* para restaurar esse backup em modo append ou *nao* para cancelar."
        )

    def _requires_backup_migration_confirmation(
        self,
        summary: dict,
        target_phone: str,
        target_backup_owner_id: str | None,
    ) -> bool:
        """Decide whether a backup restore needs explicit migration confirmation."""
        normalized_target = normalize_phone(target_phone)
        source_phone = normalize_phone(summary.get("source_phone", ""))
        source_backup_owner_id = str(summary.get("source_backup_owner_id") or "").strip()
        normalized_target_backup_owner_id = (target_backup_owner_id or "").strip()

        if source_backup_owner_id and normalized_target_backup_owner_id:
            return source_backup_owner_id != normalized_target_backup_owner_id

        return bool(source_phone and source_phone != normalized_target)

    def _build_backup_migration_warning(self, source_phone: str, target_phone: str) -> str:
        """Build the warning shown when migrating data across phone numbers."""
        return (
            "Este backup pertence a outro numero.\n"
            f"Origem do backup: {source_phone or 'desconhecido'}\n"
            f"Destino atual: {target_phone}\n\n"
            "Para confirmar a migracao do historico para este novo numero, responda *sim migrar*.\n"
            "Se nao quiser continuar, responda *nao*."
        )
