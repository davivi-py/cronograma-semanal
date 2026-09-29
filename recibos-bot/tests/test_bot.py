from app.db import Database
from app.parsing import parse_month
from app.extractor import CorrectionIntent, ReceiptData
from app.whatsapp import REENGAGEMENT_ERROR, WhatsAppError

from .conftest import OWNER, image_msg, payload, text_msg


def test_receipt_is_registered_and_confirmed(bot):
    bot.handle_payload(payload(image_msg()))
    p = bot.db.last_purchase()
    assert (p.merchant, p.total_cents, p.purchase_date, p.category, p.media_id) == (
        "Extra", 8740, "2026-09-29", "Mercado", "MEDIA1")
    to, body, reply_to = bot.wa.sent[-1]
    assert to == OWNER and reply_to == "wamid.in1"
    assert "Registrado #1: Extra - R$ 87,40 - 29/09 (Mercado)" in body
    assert p.confirm_message_id == "wamid.bot1"


def test_duplicate_webhook_is_ignored(bot):
    bot.handle_payload(payload(image_msg()))
    bot.handle_payload(payload(image_msg()))  # Meta reenviou o mesmo evento
    assert bot.extractor.calls == 1
    assert len(bot.db.recent()) == 1
    assert len(bot.wa.sent) == 1


def test_same_media_new_message_not_duplicated(bot):
    bot.handle_payload(payload(image_msg()))
    bot.handle_payload(payload(image_msg(msg_id="wamid.in2")))
    assert len(bot.db.recent()) == 1
    assert "já está registrado" in bot.wa.sent[-1][1]


def test_messages_from_others_are_ignored_and_never_answered(bot):
    bot.handle_payload(payload(image_msg(sender="5511900000000")))
    assert bot.wa.sent == [] and bot.db.recent() == []


def test_brazilian_ninth_digit_is_tolerated(bot):
    bot.handle_payload(payload(image_msg(sender="551187654321")))
    assert len(bot.db.recent()) == 1
    assert bot.wa.sent[-1][0] == "551187654321"


def test_other_phone_number_id_ignored(bot):
    bot.handle_payload(payload(image_msg(), phone_number_id="OUTRO"))
    assert bot.db.recent() == []


def test_correction_by_reply(bot):
    bot.handle_payload(payload(image_msg()))
    bot.handle_payload(payload(text_msg("valor 97,40; data 28/09", context_id="wamid.bot1")))
    p = bot.db.get(1)
    assert (p.total_cents, p.purchase_date) == (9740, "2026-09-28")
    assert "Atualizado #1" in bot.wa.sent[-1][1]


def test_correction_without_reply_applies_to_recent_last(bot):
    bot.handle_payload(payload(image_msg()))
    bot.handle_payload(payload(text_msg("categoria farmácia")))
    assert bot.db.get(1).category == "Farmácia"


def test_free_text_correction_uses_model(bot):
    bot.handle_payload(payload(image_msg()))
    bot.extractor.intent = CorrectionIntent("update", {"merchant": "Carrefour"})
    bot.handle_payload(payload(text_msg("na verdade foi no Carrefour", context_id="wamid.bot1")))
    assert bot.db.get(1).merchant == "Carrefour"


def test_delete_by_reply(bot):
    bot.handle_payload(payload(image_msg()))
    bot.handle_payload(payload(text_msg("apagar", context_id="wamid.bot1")))
    assert bot.db.get(1) is None
    assert "Apagado #1" in bot.wa.sent[-1][1]


def test_missing_total_warns(bot):
    bot.extractor.receipt = ReceiptData(True, "Padaria", None, None, "Lanche/Café", None)
    bot.handle_payload(payload(image_msg()))
    body = bot.wa.sent[-1][1]
    assert "R$ ?" in body and "Não consegui ler o valor" in body and "usei a de hoje" in body


def test_not_a_receipt(bot):
    bot.extractor.receipt = ReceiptData(False, None, None, None, "Outros", None)
    bot.handle_payload(payload(image_msg()))
    assert bot.db.recent() == []
    assert "não parece um recibo" in bot.wa.sent[-1][1]


def test_add_command(bot):
    bot.handle_payload(payload(text_msg("/add Padaria Real 12,50 28/09 #lanche")))
    p = bot.db.last_purchase()
    assert (p.merchant, p.total_cents, p.purchase_date, p.category) == ("Padaria Real", 1250, "2026-09-28", "Lanche/Café")


def test_resumo_command(bot):
    bot.db.add_purchase(purchase_date="2026-09-02", merchant="Extra", total_cents=8740, category="Mercado")
    bot.db.add_purchase(purchase_date="2026-09-15", merchant="Drogasil", total_cents=3001, category="Farmácia")
    bot.db.add_purchase(purchase_date="2026-08-30", merchant="Antigo", total_cents=100, category="Outros")
    bot.handle_payload(payload(text_msg("/resumo 09/2026")))
    summary = bot.wa.sent[-1][1]
    assert "Setembro/2026" in summary
    assert "02/09 · Extra · R$ 87,40" in summary
    assert "15/09 · Drogasil · R$ 30,01" in summary
    assert "Antigo" not in summary
    assert "*Total: R$ 117,41* (2 compras)" in summary
    assert "Metade (50%): R$ 58,71" in summary


def test_scheduled_summary_sends_once(bot):
    bot.db.add_purchase(purchase_date="2026-08-02", merchant="Extra", total_cents=1000, category="Mercado")
    assert bot.send_scheduled_summary(2026, 8) == "resumo enviado"
    n = len(bot.wa.sent)
    assert "já foi enviado" in bot.send_scheduled_summary(2026, 8)
    assert len(bot.wa.sent) == n


def test_scheduled_summary_uses_template_outside_24h_window(bot, settings):
    from dataclasses import replace
    bot.settings = replace(settings, summary_template_name="resumo_pronto")
    bot.wa.fail_code = REENGAGEMENT_ERROR
    result = bot.send_scheduled_summary(2026, 8)
    assert "template" in result
    assert bot.wa.templates == [(OWNER, "resumo_pronto", "pt_BR", ["Agosto/2026"])]


def test_scheduled_summary_without_template_raises(bot):
    bot.wa.fail_code = REENGAGEMENT_ERROR
    try:
        bot.send_scheduled_summary(2026, 8)
    except WhatsAppError as exc:
        assert "janela de 24h" in str(exc)
    else:
        raise AssertionError("deveria falhar")
