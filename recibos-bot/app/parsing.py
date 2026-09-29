"""Parsing de valores, datas, meses e correções escritas em texto livre."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, timedelta

MONTHS_PT = [
    "janeiro", "fevereiro", "março", "abril", "maio", "junho",
    "julho", "agosto", "setembro", "outubro", "novembro", "dezembro",
]


def strip_accents(text: str) -> str:
    norm = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in norm if not unicodedata.combining(ch))


def fold(text: str) -> str:
    """Minúsculas e sem acento, para comparar palavras-chave."""
    return strip_accents(text).lower().strip()


# ---- dinheiro ----------------------------------------------------------------

_MONEY_RE = re.compile(r"^(?:r\$\s*)?(\d{1,3}(?:[.\s]\d{3})+|\d+)(?:[.,](\d{1,2}))?$", re.IGNORECASE)


def parse_money(text: str) -> int | None:
    """'87,40' / 'R$ 1.234,56' / '87.40' / '87' -> centavos. None se não for valor."""
    t = text.strip().replace(" ", " ")
    m = _MONEY_RE.match(t)
    if not m:
        return None
    whole = re.sub(r"[.\s]", "", m.group(1))
    frac = (m.group(2) or "0").ljust(2, "0")
    # "1.234" sem centavos é ambíguo; tratamos ponto+3 dígitos como milhar (padrão BR).
    return int(whole) * 100 + int(frac)


def format_brl(cents: int | None) -> str:
    if cents is None:
        return "R$ ?"
    sign = "-" if cents < 0 else ""
    cents = abs(cents)
    reais, cent = divmod(cents, 100)
    reais_str = f"{reais:,}".replace(",", ".")
    return f"{sign}R$ {reais_str},{cent:02d}"


# ---- datas ---------------------------------------------------------------------

_DATE_RE = re.compile(r"^(\d{1,2})[/\-.](\d{1,2})(?:[/\-.](\d{2}|\d{4}))?$")
_ISO_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")


def parse_date(text: str, today: date) -> date | None:
    """'29/09', '29/09/2026', '29/09/26', '2026-09-29', 'hoje', 'ontem'.

    Sem ano: usa o ano atual, a menos que isso caia no futuro (aí ano anterior).
    """
    t = fold(text)
    if t == "hoje":
        return today
    if t == "ontem":
        return today - timedelta(days=1)
    if t == "anteontem":
        return today - timedelta(days=2)
    m = _ISO_RE.match(t)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    m = _DATE_RE.match(t)
    if not m:
        return None
    day, month = int(m.group(1)), int(m.group(2))
    year_raw = m.group(3)
    try:
        if year_raw:
            year = int(year_raw)
            if year < 100:
                year += 2000
            return date(year, month, day)
        candidate = date(today.year, month, day)
        if candidate > today + timedelta(days=1):
            candidate = date(today.year - 1, month, day)
        return candidate
    except ValueError:
        return None


def format_date_short(iso: str) -> str:
    y, m, d = iso.split("-")
    return f"{d}/{m}"


def month_label(year: int, month: int) -> str:
    return f"{MONTHS_PT[month - 1].capitalize()}/{year}"


def previous_month(today: date) -> tuple[int, int]:
    first = today.replace(day=1)
    last_prev = first - timedelta(days=1)
    return last_prev.year, last_prev.month


def parse_month(arg: str, today: date) -> tuple[int, int] | None:
    """Argumento do /resumo: '', 'anterior', '09', '09/2026', '2026-09', 'setembro', 'setembro 2025'."""
    t = fold(arg)
    if t in {"", "atual", "este", "esse", "mes"}:
        return today.year, today.month
    if t in {"anterior", "passado", "mes passado", "ultimo"}:
        return previous_month(today)
    m = re.match(r"^(\d{1,2})(?:[/\-](\d{2}|\d{4}))?$", t)
    if m:
        month = int(m.group(1))
        year = int(m.group(2)) if m.group(2) else today.year
        if year < 100:
            year += 2000
        if 1 <= month <= 12:
            return year, month
        return None
    m = re.match(r"^(\d{4})-(\d{1,2})$", t)
    if m:
        month = int(m.group(2))
        return (int(m.group(1)), month) if 1 <= month <= 12 else None
    m = re.match(r"^([a-z]+)(?:\s*(?:de|/)?\s*(\d{4}))?$", t)
    if m:
        folded_months = [fold(x) for x in MONTHS_PT]
        name = m.group(1)
        for idx, full in enumerate(folded_months):
            if full == name or (len(name) >= 3 and full.startswith(name)):
                month = idx + 1
                if m.group(2):
                    year = int(m.group(2))
                else:
                    year = today.year if month <= today.month else today.year - 1
                return year, month
    return None


# ---- categorias ----------------------------------------------------------------


def match_category(text: str, categories: tuple[str, ...] | list[str]) -> str | None:
    t = fold(text)
    if not t:
        return None
    for cat in categories:
        if fold(cat) == t:
            return cat
    for cat in categories:
        # aceita prefixo ("farm" -> Farmácia) e partes ("lanche" -> Lanche/Café)
        parts = [fold(p) for p in re.split(r"[/\s]+", cat)]
        if fold(cat).startswith(t) or t in parts:
            return cat
    return None


# ---- correções ----------------------------------------------------------------


@dataclass
class Correction:
    fields: dict = field(default_factory=dict)
    delete: bool = False
    unparsed: list[str] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not self.fields and not self.delete


DELETE_WORDS = {"apagar", "apaga", "excluir", "exclui", "deletar", "deleta", "remover", "remove", "cancelar", "cancela"}
_KEYED_RE = re.compile(
    r"^(valor|total|preco|data|dia|loja|local|estabelecimento|lugar|nome|categoria|cat|obs|nota)\s*[:=]?\s*(.+)$"
)


def parse_correction(text: str, today: date, categories: tuple[str, ...]) -> Correction:
    """Interpreta correções no formato 'chave valor', uma por linha ou separadas por ';'.

    Exemplos: "valor 87,40", "data 28/09", "loja Pão de Açúcar", "categoria farmácia",
    "apagar", ou só "87,40" / "28/09". O que não for entendido vai em `unparsed`
    (aí o chamador pode tentar interpretar com o modelo).
    """
    result = Correction()
    for raw in re.split(r"[;\n]+", text):
        part = raw.strip().rstrip(".!")
        if not part:
            continue
        key_text = fold(part)
        if key_text in DELETE_WORDS:
            result.delete = True
            continue
        m = _KEYED_RE.match(key_text)
        if m:
            key = m.group(1)
            # usa o texto original (com acentos/maiúsculas) para o valor
            value = part[len(part) - len(m.group(2)):].strip()
            if key in {"valor", "total", "preco"}:
                cents = parse_money(value)
                if cents is None:
                    result.unparsed.append(part)
                else:
                    result.fields["total_cents"] = cents
            elif key in {"data", "dia"}:
                d = parse_date(value, today)
                if d is None:
                    result.unparsed.append(part)
                else:
                    result.fields["purchase_date"] = d.isoformat()
            elif key in {"loja", "local", "estabelecimento", "lugar", "nome"}:
                result.fields["merchant"] = value[:80]
            elif key in {"categoria", "cat"}:
                cat = match_category(value, categories)
                if cat is None:
                    result.unparsed.append(part)
                else:
                    result.fields["category"] = cat
            elif key in {"obs", "nota"}:
                result.fields["notes"] = value[:200]
            continue
        cents = parse_money(part)
        if cents is not None:
            result.fields["total_cents"] = cents
            continue
        d = parse_date(part, today)
        if d is not None:
            result.fields["purchase_date"] = d.isoformat()
            continue
        cat = match_category(part, categories)
        if cat is not None and fold(part) == fold(cat):
            result.fields["category"] = cat
            continue
        result.unparsed.append(part)
    return result
