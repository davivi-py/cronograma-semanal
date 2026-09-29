"""Lógica do bot: roteia mensagens recebidas, registra compras, aplica correções e monta resumos."""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta, timezone

from .config import Settings
from .db import Database, Purchase
from .extractor import IMAGE_TYPES, PDF_TYPE, ExtractionError, Extractor
from .parsing import (
    Correction, fold, match_category, month_label, parse_correction, parse_date, parse_money, parse_month,
)
from .summary import build_summary, format_purchase
from .whatsapp import REENGAGEMENT_ERROR, RecipientNotAllowed, WhatsAppClient, WhatsAppError, phones_match

log = logging.getLogger(__name__)

# Sem responder a uma confirmação específica, a correção vale para a última
# compra registrada — mas só se ela for recente, para não alterar algo antigo sem querer.
CORRECTION_WINDOW = timedelta(hours=24)
OWNER_WA_ID_KEY = "owner_wa_id"

HELP = """*Como usar*
📸 Mande a foto do recibo (ou o PDF) logo depois da compra. Pode escrever uma legenda com contexto.

✏️ *Corrigir*: responda à mensagem de confirmação com, por exemplo:
• valor 87,40
• data 28/09
• loja Pão de Açúcar
• categoria farmácia
• apagar
(várias de uma vez separadas por ; ou em linhas diferentes). Sem responder, vale para a última compra das últimas 24h.

*Comandos*
/resumo — resumo do mês atual
/resumo anterior — resumo do mês passado
/resumo 09/2026 — resumo de um mês específico
/ultimas — últimas 10 compras com o número (#)
/editar 12 valor 90,00 — corrige a compra #12
/apagar 12 — apaga a compra #12
/add Padaria Real 12,50 28/09 #lanche — registra sem foto
/ajuda — esta mensagem"""

CORRECTION_HINT = (
    'Algo errado? Responda esta mensagem com a correção (ex.: "valor 87,40", "data 28/09", '
    '"loja Extra", "categoria mercado" ou "apagar").'
)


class Bot:
    def __init__(self, settings: Settings, db: Database, wa: WhatsAppClient, extractor: Extractor):
        self.settings = settings
        self.db = db
        self.wa = wa
        self.extractor = extractor

    # ---- utilidades -----------------------------------------------------------

    def today(self) -> date:
        return datetime.now(self.settings.tz).date()

    def _message_date(self, msg: dict) -> date:
        try:
            ts = int(msg.get("timestamp", ""))
            return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(self.settings.tz).date()
        except (TypeError, ValueError):
            return self.today()

    def owner_address(self) -> str:
        """Número para enviar mensagens: o wa_id visto por último (formato exato da Meta) ou o OWNER_PHONE."""
        return self.db.get_kv(OWNER_WA_ID_KEY) or self.settings.owner_phone

    def reply(self, to: str, text: str, reply_to: str | None = None) -> str | None:
        try:
            return self.wa.send_text(to, text, reply_to=reply_to)
        except (WhatsAppError, RecipientNotAllowed):
            log.exception("falha ao responder")
            return None

    # ---- entrada do webhook --------------------------------------------------------

    def handle_payload(self, payload: dict) -> None:
        for entry in payload.get("entry", []) or []:
            for change in entry.get("changes", []) or []:
                value = change.get("value") or {}
                if value.get("metadata", {}).get("phone_number_id") not in (None, self.settings.whatsapp_phone_number_id):
                    continue
                for msg in value.get("messages", []) or []:
                    try:
                        self.handle_message(msg)
                    except Exception:
                        log.exception("erro processando mensagem %s", msg.get("id"))
                        self.reply(msg.get("from", ""), "❌ Deu um erro inesperado processando essa mensagem. Tente de novo.")

    def handle_message(self, msg: dict) -> None:
        sender = msg.get("from", "")
        msg_id = msg.get("id", "")
        if not phones_match(sender, self.settings.owner_phone):
            # Nunca responde a terceiros.
            log.warning("mensagem de número não autorizado ignorada: %s", sender)
            return
        if not msg_id or not self.db.claim_message(msg_id):
            log.info("mensagem %s já processada (reenvio do webhook), ignorando", msg_id)
            return
        self.db.set_kv(OWNER_WA_ID_KEY, sender)
        self.wa.mark_read(msg_id)

        mtype = msg.get("type")
        if mtype == "image":
            media = msg.get("image") or {}
            self.handle_media(msg, media.get("id"), media.get("mime_type"), media.get("caption"))
        elif mtype == "document":
            media = msg.get("document") or {}
            self.handle_media(msg, media.get("id"), media.get("mime_type"), media.get("caption"))
        elif mtype == "text":
            self.handle_text(msg, (msg.get("text") or {}).get("body", ""))
        else:
            self.reply(sender, "Só entendo fotos/PDF de recibo e mensagens de texto. Mande /ajuda para ver os comandos.")

    # ---- recibos --------------------------------------------------------------

    def handle_media(self, msg: dict, media_id: str | None, mime: str | None, caption: str | None) -> None:
        sender, msg_id = msg["from"], msg["id"]
        if not media_id:
            self.reply(sender, "Não achei a mídia nessa mensagem.", reply_to=msg_id)
            return
        existing = self.db.get_by_media_id(media_id)
        if existing:
            self.reply(sender, f"Esse recibo já está registrado como #{existing.id}: {format_purchase(existing)}",
                       reply_to=msg_id)
            return
        mime = (mime or "").split(";")[0].strip()
        if mime and mime not in IMAGE_TYPES and mime != PDF_TYPE:
            self.reply(sender, "Esse tipo de arquivo eu não leio. Mande uma foto (JPG/PNG) ou PDF do recibo.",
                       reply_to=msg_id)
            return

        msg_date = self._message_date(msg)
        try:
            data, downloaded_mime = self.wa.download_media(media_id)
            receipt = self.extractor.extract_receipt(data, mime or downloaded_mime, msg_date, caption=caption)
        except (WhatsAppError, ExtractionError) as exc:
            log.warning("falha ao processar recibo %s: %s", media_id, exc)
            self.reply(sender, f"❌ Não consegui ler esse recibo ({exc}). Tente outra foto ou use /add.",
                       reply_to=msg_id)
            return

        if not receipt.is_receipt:
            self.reply(sender, "🤔 Isso não parece um recibo, então não registrei nada. "
                               "Se for uma compra, use /add (ex.: /add Padaria 12,50).", reply_to=msg_id)
            return

        warnings = []
        purchase_date = receipt.purchase_date
        if purchase_date is None:
            purchase_date = msg_date.isoformat()
            warnings.append("Não achei a data no recibo, usei a de hoje.")
        elif date.fromisoformat(purchase_date) > msg_date + timedelta(days=1) or \
                date.fromisoformat(purchase_date) < msg_date - timedelta(days=366):
            warnings.append("A data lida parece estranha, confira.")
        if receipt.total_cents is None:
            warnings.append("Não consegui ler o valor — responda com o valor (ex.: 87,40).")
        merchant = receipt.merchant or "Estabelecimento?"
        if receipt.merchant is None:
            warnings.append("Não identifiquei o estabelecimento — responda com \"loja <nome>\".")

        purchase = self.db.add_purchase(
            purchase_date=purchase_date,
            merchant=merchant,
            total_cents=receipt.total_cents,
            category=receipt.category,
            media_id=media_id,
            source_message_id=msg_id,
            notes=receipt.notes,
        )
        text = f"✅ Registrado #{purchase.id}: {format_purchase(purchase)}"
        if warnings:
            text += "\n\n⚠️ " + "\n⚠️ ".join(warnings)
        text += f"\n\n{CORRECTION_HINT}"
        wamid = self.reply(sender, text, reply_to=msg_id)
        if wamid:
            self.db.set_confirm_message(purchase.id, wamid)

    # ---- texto ---------------------------------------------------------------------

    def handle_text(self, msg: dict, body: str) -> None:
        sender, msg_id = msg["from"], msg["id"]
        text = body.strip()
        if not text:
            return
        low = fold(text)
        first, _, rest = text.partition(" ")
        command = fold(first).lstrip("/")
        is_command = text.startswith("/") or command in {"resumo", "ajuda", "ultimas"}

        if is_command:
            if command in {"resumo", "relatorio"}:
                self.cmd_summary(sender, rest.strip())
            elif command in {"ultimas", "ultimos", "lista", "listar"}:
                self.cmd_recent(sender)
            elif command in {"apagar", "excluir", "deletar"}:
                self.cmd_delete(sender, msg_id, rest.strip(), msg)
            elif command == "editar":
                self.cmd_edit(sender, msg_id, rest.strip())
            elif command in {"add", "adicionar", "nova"}:
                self.cmd_add(sender, msg_id, rest.strip(), self._message_date(msg))
            elif command in {"ajuda", "help", "start", "comandos"}:
                self.reply(sender, HELP)
            else:
                self.reply(sender, "Comando desconhecido. Mande /ajuda para ver os comandos.")
            return

        if low in {"oi", "ola", "menu", "?"}:
            self.reply(sender, HELP)
            return

        # Correção: primeiro a compra da mensagem respondida; senão a última recente.
        target: Purchase | None = None
        context_id = (msg.get("context") or {}).get("id")
        if context_id:
            target = self.db.get_by_confirm_message(context_id)
            if target is None:
                self.reply(sender, "Não achei a compra ligada a essa mensagem (talvez já tenha sido apagada). "
                                   "Use /ultimas para ver os números e /editar N.", reply_to=msg_id)
                return
        else:
            last = self.db.last_purchase()
            if last and datetime.now(timezone.utc) - datetime.fromisoformat(last.created_at) <= CORRECTION_WINDOW:
                target = last
        if target is None:
            self.reply(sender, "Não entendi. Para corrigir uma compra, responda à mensagem de confirmação dela "
                               "ou use /editar N (veja os números em /ultimas). Mande /ajuda para ver tudo.",
                       reply_to=msg_id)
            return
        self.apply_correction(sender, msg_id, target, text, self._message_date(msg))

    def apply_correction(self, sender: str, msg_id: str, target: Purchase, text: str, today: date) -> None:
        correction: Correction = parse_correction(text, today, self.settings.categories)
        if correction.unparsed:
            # Algo em texto livre ("na verdade foi 97 e pouco no Carrefour") — o modelo interpreta.
            current = {
                "merchant": target.merchant,
                "total": None if target.total_cents is None else target.total_cents / 100,
                "purchase_date": target.purchase_date,
                "category": target.category,
            }
            try:
                intent = self.extractor.interpret_correction(text, current, today)
            except ExtractionError as exc:
                log.warning("falha ao interpretar correção: %s", exc)
                intent = None
            if intent is None or intent.action == "none":
                self.reply(sender, "Não entendi a correção. Exemplos: \"valor 87,40\", \"data 28/09\", "
                                   "\"loja Extra\", \"categoria mercado\" ou \"apagar\".", reply_to=msg_id)
                return
            correction = Correction(fields=intent.fields, delete=intent.action == "delete")

        if correction.delete:
            self.db.delete_purchase(target.id)
            self.reply(sender, f"🗑️ Apagado #{target.id}: {format_purchase(target)}", reply_to=msg_id)
            return
        updated = self.db.update_purchase(target.id, **correction.fields)
        if updated is None:
            self.reply(sender, f"A compra #{target.id} não existe mais.", reply_to=msg_id)
            return
        wamid = self.reply(sender, f"✏️ Atualizado #{updated.id}: {format_purchase(updated)}", reply_to=msg_id)
        if wamid:
            # responder a esta nova confirmação também corrige a mesma compra
            self.db.set_confirm_message(updated.id, wamid)

    # ---- comandos ----------------------------------------------------------------

    def cmd_summary(self, sender: str, arg: str) -> None:
        ym = parse_month(arg, self.today())
        if ym is None:
            self.reply(sender, "Não entendi o mês. Exemplos: /resumo, /resumo anterior, /resumo 09/2026, /resumo setembro.")
            return
        text = self.summary_text(*ym)
        self.reply(sender, "Resumo abaixo 👇 é só encaminhar.")
        self.reply(sender, text)

    def summary_text(self, year: int, month: int) -> str:
        purchases = self.db.purchases_in_month(year, month)
        return build_summary(purchases, year, month, title=self.settings.summary_title,
                             show_split=self.settings.summary_show_split)

    def cmd_recent(self, sender: str) -> None:
        items = self.db.recent(10)
        if not items:
            self.reply(sender, "Nenhuma compra registrada ainda.")
            return
        lines = ["*Últimas compras*"] + [f"#{p.id} · {format_purchase(p).splitlines()[0]}" for p in items]
        self.reply(sender, "\n".join(lines))

    def _parse_id(self, arg: str) -> tuple[int | None, str]:
        m = re.match(r"^#?(\d+)\s*(.*)$", arg, re.DOTALL)
        if not m:
            return None, arg
        return int(m.group(1)), m.group(2).strip()

    def cmd_delete(self, sender: str, msg_id: str, arg: str, msg: dict) -> None:
        pid, _ = self._parse_id(arg)
        target = None
        if pid is not None:
            target = self.db.get(pid)
        elif (msg.get("context") or {}).get("id"):
            target = self.db.get_by_confirm_message(msg["context"]["id"])
        else:
            self.reply(sender, "Diga qual compra: /apagar 12 (veja os números em /ultimas).", reply_to=msg_id)
            return
        if target is None:
            self.reply(sender, "Não achei essa compra.", reply_to=msg_id)
            return
        self.db.delete_purchase(target.id)
        self.reply(sender, f"🗑️ Apagado #{target.id}: {format_purchase(target)}", reply_to=msg_id)

    def cmd_edit(self, sender: str, msg_id: str, arg: str) -> None:
        pid, rest = self._parse_id(arg)
        if pid is None or not rest:
            self.reply(sender, "Uso: /editar 12 valor 90,00 (veja os números em /ultimas).", reply_to=msg_id)
            return
        target = self.db.get(pid)
        if target is None:
            self.reply(sender, f"Não achei a compra #{pid}.", reply_to=msg_id)
            return
        self.apply_correction(sender, msg_id, target, rest, self.today())

    def cmd_add(self, sender: str, msg_id: str, arg: str, today: date) -> None:
        total = None
        when = None
        category = None
        name_parts = []
        for tok in arg.split():
            if tok.startswith("#") and category is None:
                category = match_category(tok[1:], self.settings.categories)
                if category is None:
                    self.reply(sender, f"Categoria desconhecida: {tok[1:]}. Opções: "
                                       + ", ".join(self.settings.categories), reply_to=msg_id)
                    return
                continue
            if total is None and (cents := parse_money(tok)) is not None:
                total = cents
                continue
            if when is None and (d := parse_date(tok, today)) is not None:
                when = d
                continue
            name_parts.append(tok)
        merchant = " ".join(name_parts).strip()
        if not merchant or total is None:
            self.reply(sender, "Uso: /add <loja> <valor> [data] [#categoria]\nEx.: /add Padaria Real 12,50 28/09 #lanche",
                       reply_to=msg_id)
            return
        purchase = self.db.add_purchase(
            purchase_date=(when or today).isoformat(),
            merchant=merchant[:80],
            total_cents=total,
            category=category or "Outros",
            source_message_id=msg_id,
        )
        wamid = self.reply(sender, f"✅ Registrado #{purchase.id}: {format_purchase(purchase)}\n\n{CORRECTION_HINT}",
                           reply_to=msg_id)
        if wamid:
            self.db.set_confirm_message(purchase.id, wamid)

    # ---- resumo agendado ----------------------------------------------------------

    def send_scheduled_summary(self, year: int, month: int, *, force: bool = False) -> str:
        """Envia o resumo do mês para o dono. Retorna uma descrição do que aconteceu."""
        key = f"auto_summary_{year:04d}-{month:02d}"
        if not force and self.db.get_kv(key):
            return f"resumo de {month:02d}/{year} já foi enviado antes; nada a fazer (use --force para reenviar)"
        to = self.owner_address()
        text = self.summary_text(year, month)
        try:
            self.wa.send_text(to, "📅 Fechou o mês! Resumo abaixo 👇 é só encaminhar.")
            self.wa.send_text(to, text)
            self.db.set_kv(key, datetime.now(timezone.utc).isoformat(timespec="seconds"))
            return "resumo enviado"
        except WhatsAppError as exc:
            if exc.code != REENGAGEMENT_ERROR:
                raise
            # Fora da janela de 24h a Meta só aceita template aprovado.
            if not self.settings.summary_template_name:
                raise WhatsAppError(
                    "janela de 24h fechada e SUMMARY_TEMPLATE_NAME não configurado; "
                    "mande qualquer mensagem pro bot e depois /resumo anterior", code=exc.code,
                ) from exc
            self.wa.send_template(to, self.settings.summary_template_name, self.settings.summary_template_lang,
                                  [month_label(year, month)])
            self.db.set_kv(key, datetime.now(timezone.utc).isoformat(timespec="seconds"))
            return "janela de 24h fechada: enviado template avisando para pedir /resumo anterior"
