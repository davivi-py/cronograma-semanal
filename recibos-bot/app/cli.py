"""Linha de comando: resumo mensal (usado pelo timer do systemd) e utilidades.

    python -m app.cli resumo --mes-anterior --enviar   # o que o timer roda todo dia 1
    python -m app.cli resumo --mes 09/2026             # só imprime no terminal
    python -m app.cli init-db
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime

from .config import get_settings
from .db import Database
from .parsing import parse_month, previous_month


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="recibos")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_sum = sub.add_parser("resumo", help="gera o resumo do mês")
    group = p_sum.add_mutually_exclusive_group()
    group.add_argument("--mes", help="mês: 09/2026, 2026-09, setembro...")
    group.add_argument("--mes-anterior", action="store_true", help="mês anterior ao atual")
    p_sum.add_argument("--enviar", action="store_true", help="envia pelo WhatsApp (só para o OWNER_PHONE)")
    p_sum.add_argument("--force", action="store_true", help="reenvia mesmo se já enviado")

    sub.add_parser("init-db", help="cria o banco e as tabelas")

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = get_settings()

    if args.cmd == "init-db":
        Database(settings.db_path)
        print(f"banco pronto em {settings.db_path}")
        return 0

    today = datetime.now(settings.tz).date()
    if args.mes_anterior:
        year, month = previous_month(today)
    else:
        ym = parse_month(args.mes or "", today)
        if ym is None:
            print(f"mês inválido: {args.mes}", file=sys.stderr)
            return 2
        year, month = ym

    from .main import build_bot  # importa só aqui para não subir o FastAPI à toa

    bot = build_bot(settings)
    if args.enviar:
        print(bot.send_scheduled_summary(year, month, force=args.force))
    else:
        print(bot.summary_text(year, month))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
