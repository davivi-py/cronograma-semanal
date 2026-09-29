"""Configuração lida do ambiente (.env)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

DEFAULT_CATEGORIES = (
    "Mercado,Farmácia,Restaurante,Lanche/Café,Transporte,Combustível,"
    "Compras online,Vestuário,Casa,Saúde,Lazer,Serviços,Outros"
)


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Variável de ambiente obrigatória ausente: {name}")
    return value


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "sim", "on"}


@dataclass(frozen=True)
class Settings:
    whatsapp_token: str
    whatsapp_phone_number_id: str
    whatsapp_app_secret: str
    whatsapp_verify_token: str
    owner_phone: str
    anthropic_api_key: str
    graph_api_version: str = "v23.0"
    anthropic_model: str = "claude-opus-5-5"
    anthropic_effort: str = "medium"
    db_path: str = "data/recibos.db"
    timezone: str = "America/Sao_Paulo"
    categories: tuple[str, ...] = field(default_factory=lambda: tuple(DEFAULT_CATEGORIES.split(",")))
    summary_title: str = "Compras no cartão"
    summary_show_split: bool = True
    summary_template_name: str = ""
    summary_template_lang: str = "pt_BR"

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @property
    def graph_base(self) -> str:
        return f"https://graph.facebook.com/{self.graph_api_version}"


def load_settings() -> Settings:
    load_dotenv()
    cats = os.environ.get("CATEGORIES", DEFAULT_CATEGORIES)
    categories = tuple(c.strip() for c in cats.split(",") if c.strip())
    if "Outros" not in categories:
        categories = categories + ("Outros",)
    return Settings(
        whatsapp_token=_required("WHATSAPP_TOKEN"),
        whatsapp_phone_number_id=_required("WHATSAPP_PHONE_NUMBER_ID"),
        whatsapp_app_secret=_required("WHATSAPP_APP_SECRET"),
        whatsapp_verify_token=_required("WHATSAPP_VERIFY_TOKEN"),
        owner_phone="".join(ch for ch in _required("OWNER_PHONE") if ch.isdigit()),
        anthropic_api_key=_required("ANTHROPIC_API_KEY"),
        graph_api_version=os.environ.get("GRAPH_API_VERSION", "v23.0").strip() or "v23.0",
        anthropic_model=os.environ.get("ANTHROPIC_MODEL", "claude-opus-5-5").strip() or "claude-opus-5-5",
        anthropic_effort=os.environ.get("ANTHROPIC_EFFORT", "medium").strip() or "medium",
        db_path=os.environ.get("DB_PATH", "data/recibos.db").strip() or "data/recibos.db",
        timezone=os.environ.get("TIMEZONE", "America/Sao_Paulo").strip() or "America/Sao_Paulo",
        categories=categories,
        summary_title=os.environ.get("SUMMARY_TITLE", "Compras no cartão").strip() or "Compras no cartão",
        summary_show_split=_bool("SUMMARY_SHOW_SPLIT", True),
        summary_template_name=os.environ.get("SUMMARY_TEMPLATE_NAME", "").strip(),
        summary_template_lang=os.environ.get("SUMMARY_TEMPLATE_LANG", "pt_BR").strip() or "pt_BR",
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return load_settings()
