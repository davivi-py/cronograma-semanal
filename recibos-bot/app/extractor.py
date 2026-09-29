"""Extração dos dados do recibo e interpretação de correções usando a API da Anthropic."""

from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass
from datetime import date

import anthropic

log = logging.getLogger(__name__)

IMAGE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
PDF_TYPE = "application/pdf"
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class ExtractionError(RuntimeError):
    pass


@dataclass
class ReceiptData:
    is_receipt: bool
    merchant: str | None
    total_cents: int | None
    purchase_date: str | None  # YYYY-MM-DD
    category: str
    notes: str | None


@dataclass
class CorrectionIntent:
    action: str  # "update" | "delete" | "none"
    fields: dict


def _nullable(schema: dict) -> dict:
    return {"anyOf": [schema, {"type": "null"}]}


def _to_cents(value) -> int | None:
    if value is None:
        return None
    try:
        return int(round(float(value) * 100))
    except (TypeError, ValueError):
        return None


def _valid_iso(value) -> str | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)).isoformat()
    except ValueError:
        return None


RECEIPT_SYSTEM = """Você extrai dados de comprovantes de compra brasileiros (cupom fiscal, NFC-e, \
comprovante de maquininha de cartão, nota de restaurante, recibo de app, print de compra online).

Regras:
- merchant: nome curto e reconhecível do estabelecimento, como a pessoa falaria \
("Extra", "Drogasil", "Padaria Real"). Prefira o nome fantasia à razão social; sem "LTDA", CNPJ etc.
- total: o valor efetivamente pago/cobrado no cartão, em reais (número com ponto decimal, ex.: 87.40). \
Se houver desconto, use o valor final. Se houver gorjeta/taxa de serviço incluída no pagamento, use o total pago. \
Se não for possível ler com segurança, use null — nunca invente.
- purchase_date: data da compra no formato YYYY-MM-DD. Datas no comprovante estão em formato brasileiro \
(dia/mês/ano). Se não houver data legível, use null.
- category: escolha a categoria mais adequada da lista.
- notes: observação curta só se for relevante para quem paga a fatura (ex.: "parcelado em 3x de R$ 50,00", \
"valor em dólar"); senão null.
- is_receipt: false se a imagem claramente não for um comprovante de compra."""

CORRECTION_SYSTEM = """Você interpreta mensagens curtas em português em que o usuário corrige um registro \
de compra feito a partir de um recibo. Devolva somente os campos que o usuário quer mudar; os demais ficam null.
- total em reais (número com ponto decimal).
- purchase_date em YYYY-MM-DD; o usuário escreve datas como dia/mês. Sem ano explícito, use o ano da data de \
hoje, a menos que isso caia no futuro.
- action: "update" se houver algo para alterar, "delete" se ele quer apagar/cancelar o registro, \
"none" se a mensagem não for uma correção (ex.: agradecimento, pergunta sem relação)."""


class Extractor:
    def __init__(self, *, api_key: str, model: str, effort: str, categories: tuple[str, ...],
                 client: anthropic.Anthropic | None = None):
        self.client = client or anthropic.Anthropic(api_key=api_key)
        self.model = model
        self.effort = effort
        self.categories = categories

    def _call(self, *, system: str, content: list[dict], schema: dict, max_tokens: int = 4000) -> dict:
        try:
            response = self.client.beta.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": content}],
                output_config={
                    "effort": self.effort,
                    "format": {"type": "json_schema", "schema": schema},
                },
                # Se o modelo principal recusar, a própria API tenta de novo num modelo substituto.
                betas=[FALLBACK_BETA],
                fallbacks="default",
            )
        except anthropic.RateLimitError as exc:
            raise ExtractionError("limite de requisições da Anthropic atingido, tente de novo em instantes") from exc
        except anthropic.APIStatusError as exc:
            raise ExtractionError(f"erro {exc.status_code} na API da Anthropic") from exc
        except anthropic.APIConnectionError as exc:
            raise ExtractionError("falha de conexão com a API da Anthropic") from exc

        if response.stop_reason == "refusal":
            raise ExtractionError("o modelo recusou processar esta mensagem")
        if response.stop_reason == "max_tokens":
            raise ExtractionError("resposta do modelo foi cortada (max_tokens)")
        text = next((b.text for b in response.content if b.type == "text"), None)
        if not text:
            raise ExtractionError("resposta do modelo sem conteúdo")
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise ExtractionError("resposta do modelo não é JSON válido") from exc

    def extract_receipt(self, data: bytes, mime: str, today: date, caption: str | None = None) -> ReceiptData:
        b64 = base64.standard_b64encode(data).decode("ascii")
        if mime in IMAGE_TYPES:
            media_block = {"type": "image", "source": {"type": "base64", "media_type": mime, "data": b64}}
        elif mime == PDF_TYPE:
            media_block = {"type": "document", "source": {"type": "base64", "media_type": mime, "data": b64}}
        else:
            raise ExtractionError(f"tipo de arquivo não suportado: {mime or 'desconhecido'}")

        prompt = f"Hoje é {today.isoformat()}. Extraia os dados deste comprovante."
        if caption:
            prompt += (
                "\n\nLegenda que o usuário mandou junto com a foto (pode trazer correções ou contexto; "
                f"se contradizer a imagem, a legenda vale):\n<legenda>{caption}</legenda>"
            )
        schema = {
            "type": "object",
            "properties": {
                "is_receipt": {"type": "boolean"},
                "merchant": _nullable({"type": "string"}),
                "total": _nullable({"type": "number"}),
                "purchase_date": _nullable({"type": "string", "description": "YYYY-MM-DD"}),
                "category": {"type": "string", "enum": list(self.categories)},
                "notes": _nullable({"type": "string"}),
            },
            "required": ["is_receipt", "merchant", "total", "purchase_date", "category", "notes"],
            "additionalProperties": False,
        }
        out = self._call(system=RECEIPT_SYSTEM, content=[media_block, {"type": "text", "text": prompt}], schema=schema)
        category = out.get("category") if out.get("category") in self.categories else "Outros"
        merchant = (out.get("merchant") or "").strip() or None
        return ReceiptData(
            is_receipt=bool(out.get("is_receipt", True)),
            merchant=merchant[:80] if merchant else None,
            total_cents=_to_cents(out.get("total")),
            purchase_date=_valid_iso(out.get("purchase_date")),
            category=category,
            notes=(out.get("notes") or None),
        )

    def interpret_correction(self, text: str, current: dict, today: date) -> CorrectionIntent:
        schema = {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["update", "delete", "none"]},
                "merchant": _nullable({"type": "string"}),
                "total": _nullable({"type": "number"}),
                "purchase_date": _nullable({"type": "string", "description": "YYYY-MM-DD"}),
                "category": _nullable({"type": "string", "enum": list(self.categories)}),
            },
            "required": ["action", "merchant", "total", "purchase_date", "category"],
            "additionalProperties": False,
        }
        prompt = (
            f"Hoje é {today.isoformat()}.\n"
            f"Registro atual: {json.dumps(current, ensure_ascii=False)}\n"
            f"Mensagem do usuário:\n<mensagem>{text}</mensagem>"
        )
        out = self._call(system=CORRECTION_SYSTEM, content=[{"type": "text", "text": prompt}], schema=schema,
                         max_tokens=2000)
        fields: dict = {}
        if out.get("merchant"):
            fields["merchant"] = str(out["merchant"]).strip()[:80]
        cents = _to_cents(out.get("total"))
        if cents is not None:
            fields["total_cents"] = cents
        iso = _valid_iso(out.get("purchase_date"))
        if iso:
            fields["purchase_date"] = iso
        if out.get("category") in self.categories:
            fields["category"] = out["category"]
        action = out.get("action") if out.get("action") in {"update", "delete", "none"} else "none"
        if action == "update" and not fields:
            action = "none"
        return CorrectionIntent(action=action, fields=fields)
