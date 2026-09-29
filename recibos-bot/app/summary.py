"""Montagem do resumo mensal, pronto para encaminhar."""

from __future__ import annotations

from .db import Purchase
from .parsing import format_brl, format_date_short, month_label


def build_summary(purchases: list[Purchase], year: int, month: int, *, title: str, show_split: bool) -> str:
    label = month_label(year, month)
    if not purchases:
        return f"*{title} – {label}*\n\nNenhuma compra registrada neste mês."

    lines = [f"*{title} – {label}*", ""]
    pending = 0
    total = 0
    for p in purchases:
        if p.total_cents is None:
            pending += 1
        else:
            total += p.total_cents
        line = f"{format_date_short(p.purchase_date)} · {p.merchant} · {format_brl(p.total_cents)}"
        if p.notes:
            line += f" ({p.notes})"
        lines.append(line)

    count = len(purchases)
    lines += ["", f"*Total: {format_brl(total)}* ({count} compra{'s' if count != 1 else ''})"]
    if show_split:
        # arredonda a metade para cima no centavo, para não faltar 1 centavo
        half = (total + 1) // 2
        lines.append(f"Metade (50%): {format_brl(half)}")
    if pending:
        lines.append(f"⚠️ {pending} compra(s) sem valor informado — não entram no total.")
    return "\n".join(lines)


def format_purchase(p: Purchase) -> str:
    """Linha curta usada nas confirmações: 'Extra - R$ 87,40 - 29/09 (Mercado)'."""
    text = f"{p.merchant} - {format_brl(p.total_cents)} - {format_date_short(p.purchase_date)} ({p.category})"
    if p.notes:
        text += f"\nObs.: {p.notes}"
    return text
