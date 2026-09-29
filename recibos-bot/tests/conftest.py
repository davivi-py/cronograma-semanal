import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.bot import Bot  # noqa: E402
from app.config import DEFAULT_CATEGORIES, Settings  # noqa: E402
from app.db import Database  # noqa: E402
from app.extractor import CorrectionIntent, ReceiptData  # noqa: E402
from app.whatsapp import RecipientNotAllowed, WhatsAppError, phones_match  # noqa: E402

OWNER = "5511987654321"


class FakeWA:
    def __init__(self):
        self.sent = []  # (to, body, reply_to)
        self.templates = []
        self.fail_code = None
        self.media = (b"fake-jpeg", "image/jpeg")
        self._n = 0

    def _guard(self, to):
        if not phones_match(to, OWNER):
            raise RecipientNotAllowed(to)

    def send_text(self, to, body, reply_to=None):
        self._guard(to)
        if self.fail_code:
            raise WhatsAppError("fail", code=self.fail_code)
        self._n += 1
        self.sent.append((to, body, reply_to))
        return f"wamid.bot{self._n}"

    def send_template(self, to, name, lang, body_params=None):
        self._guard(to)
        self.templates.append((to, name, lang, body_params))
        return "wamid.tpl"

    def mark_read(self, message_id):
        pass

    def download_media(self, media_id):
        return self.media


class FakeExtractor:
    def __init__(self):
        self.receipt = ReceiptData(True, "Extra", 8740, "2026-09-29", "Mercado", None)
        self.intent = CorrectionIntent("none", {})
        self.calls = 0

    def extract_receipt(self, data, mime, today, caption=None):
        self.calls += 1
        return self.receipt

    def interpret_correction(self, text, current, today):
        return self.intent


@pytest.fixture
def settings():
    return Settings(
        whatsapp_token="tok",
        whatsapp_phone_number_id="PNID",
        whatsapp_app_secret="segredo",
        whatsapp_verify_token="verifica",
        owner_phone=OWNER,
        anthropic_api_key="sk-test",
        db_path=":memory:",
        categories=tuple(DEFAULT_CATEGORIES.split(",")),
        summary_template_name="",
    )


@pytest.fixture
def bot(settings):
    return Bot(settings, Database(":memory:"), FakeWA(), FakeExtractor())


def image_msg(msg_id="wamid.in1", media_id="MEDIA1", sender=OWNER, ts="1790694000", caption=None):
    img = {"id": media_id, "mime_type": "image/jpeg"}
    if caption:
        img["caption"] = caption
    return {"from": sender, "id": msg_id, "timestamp": ts, "type": "image", "image": img}


def text_msg(body, msg_id="wamid.t1", sender=OWNER, context_id=None, ts="1790694000"):
    msg = {"from": sender, "id": msg_id, "timestamp": ts, "type": "text", "text": {"body": body}}
    if context_id:
        msg["context"] = {"id": context_id}
    return msg


def payload(*messages, phone_number_id="PNID"):
    return {"object": "whatsapp_business_account", "entry": [{"id": "WABA", "changes": [{
        "field": "messages",
        "value": {"messaging_product": "whatsapp", "metadata": {"phone_number_id": phone_number_id},
                  "messages": list(messages)},
    }]}]}
