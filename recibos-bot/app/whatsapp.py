"""Cliente mínimo da WhatsApp Cloud API (oficial, Graph API da Meta)."""

from __future__ import annotations

import hashlib
import hmac
import logging

import httpx

log = logging.getLogger(__name__)

# Erro da Meta quando a janela de 24h de atendimento está fechada.
REENGAGEMENT_ERROR = 131047
MAX_TEXT_LEN = 4096


class WhatsAppError(RuntimeError):
    def __init__(self, message: str, code: int | None = None):
        super().__init__(message)
        self.code = code


class RecipientNotAllowed(RuntimeError):
    pass


def verify_signature(app_secret: str, body: bytes, header_value: str | None) -> bool:
    """Confere o X-Hub-Signature-256 ("sha256=<hex>") contra o corpo cru do POST."""
    if not header_value or not header_value.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header_value.removeprefix("sha256="))


def normalize_phone(phone: str) -> str:
    return "".join(ch for ch in phone if ch.isdigit())


def phones_match(a: str, b: str) -> bool:
    """Compara números tolerando o 9º dígito de celulares brasileiros.

    O wa_id que chega no webhook de números BR às vezes vem sem o 9
    (5511 8xxx-xxxx em vez de 5511 98xxx-xxxx).
    """
    a, b = normalize_phone(a), normalize_phone(b)
    if a == b:
        return True

    def without_nine(n: str) -> str:
        if n.startswith("55") and len(n) == 13 and n[4] == "9":
            return n[:4] + n[5:]
        return n

    return without_nine(a) == without_nine(b)


class WhatsAppClient:
    def __init__(self, *, token: str, phone_number_id: str, graph_base: str, owner_phone: str,
                 http: httpx.Client | None = None):
        self.phone_number_id = phone_number_id
        self.graph_base = graph_base
        self.owner_phone = owner_phone
        self.http = http or httpx.Client(timeout=30)
        self.headers = {"Authorization": f"Bearer {token}"}

    # ---- envio -------------------------------------------------------------

    def _guard(self, to: str) -> None:
        # Trava de segurança: o bot só fala com o dono. Nunca com a tia nem com ninguém.
        if not phones_match(to, self.owner_phone):
            raise RecipientNotAllowed(f"envio bloqueado para {to}: só o OWNER_PHONE pode receber mensagens")

    def _post_message(self, payload: dict) -> str:
        self._guard(payload["to"])
        url = f"{self.graph_base}/{self.phone_number_id}/messages"
        resp = self.http.post(url, headers=self.headers, json={"messaging_product": "whatsapp", **payload})
        data = _json(resp)
        if resp.status_code >= 400:
            err = data.get("error", {}) if isinstance(data, dict) else {}
            raise WhatsAppError(
                f"Erro {resp.status_code} ao enviar mensagem: {err.get('message') or resp.text}",
                code=err.get("code"),
            )
        return data["messages"][0]["id"]

    def send_text(self, to: str, body: str, reply_to: str | None = None) -> str:
        """Envia texto; mensagens grandes são quebradas em partes. Retorna o wamid da última."""
        wamid = ""
        for i, chunk in enumerate(split_text(body)):
            payload: dict = {
                "recipient_type": "individual",
                "to": to,
                "type": "text",
                "text": {"body": chunk, "preview_url": False},
            }
            if reply_to and i == 0:
                payload["context"] = {"message_id": reply_to}
            wamid = self._post_message(payload)
        return wamid

    def send_template(self, to: str, name: str, lang: str, body_params: list[str] | None = None) -> str:
        template: dict = {"name": name, "language": {"code": lang}}
        if body_params:
            template["components"] = [{
                "type": "body",
                "parameters": [{"type": "text", "text": p} for p in body_params],
            }]
        return self._post_message({"to": to, "type": "template", "template": template})

    def mark_read(self, message_id: str) -> None:
        url = f"{self.graph_base}/{self.phone_number_id}/messages"
        try:
            self.http.post(url, headers=self.headers, json={
                "messaging_product": "whatsapp", "status": "read", "message_id": message_id,
            })
        except httpx.HTTPError:
            log.warning("falha ao marcar mensagem como lida", exc_info=True)

    # ---- mídia -------------------------------------------------------------

    def download_media(self, media_id: str) -> tuple[bytes, str]:
        """Media API: 1) GET /{media_id} -> URL temporária; 2) GET na URL com o token."""
        meta = self.http.get(f"{self.graph_base}/{media_id}", headers=self.headers)
        info = _json(meta)
        if meta.status_code >= 400 or "url" not in info:
            raise WhatsAppError(f"Erro {meta.status_code} ao obter URL da mídia: {meta.text}")
        blob = self.http.get(info["url"], headers=self.headers, follow_redirects=True)
        if blob.status_code >= 400:
            raise WhatsAppError(f"Erro {blob.status_code} ao baixar mídia")
        mime = (info.get("mime_type") or blob.headers.get("content-type") or "").split(";")[0].strip()
        return blob.content, mime


def split_text(body: str, limit: int = MAX_TEXT_LEN) -> list[str]:
    if len(body) <= limit:
        return [body]
    chunks, current = [], ""
    for line in body.split("\n"):
        while len(line) > limit:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:limit])
            line = line[limit:]
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > limit:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def _json(resp: httpx.Response):
    try:
        return resp.json()
    except ValueError:
        return {}
