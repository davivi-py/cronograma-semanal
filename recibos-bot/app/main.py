"""Servidor do webhook da WhatsApp Cloud API."""

from __future__ import annotations

import hmac
import json
import logging
import os
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, FastAPI, Request, Response
from fastapi.responses import PlainTextResponse

from .bot import Bot
from .config import Settings, get_settings
from .db import Database
from .extractor import Extractor
from .whatsapp import WhatsAppClient, verify_signature

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("recibos")


def build_bot(settings: Settings) -> Bot:
    db = Database(settings.db_path)
    wa = WhatsAppClient(
        token=settings.whatsapp_token,
        phone_number_id=settings.whatsapp_phone_number_id,
        graph_base=settings.graph_base,
        owner_phone=settings.owner_phone,
    )
    extractor = Extractor(
        api_key=settings.anthropic_api_key,
        model=settings.anthropic_model,
        effort=settings.anthropic_effort,
        categories=settings.categories,
    )
    return Bot(settings, db, wa, extractor)


def create_app(bot: Bot | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if getattr(app.state, "bot", None) is None:
            app.state.bot = build_bot(get_settings())
        yield

    app = FastAPI(title="recibos-bot", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.bot = bot

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    @app.get("/webhook")
    def verify(request: Request):
        """Handshake de verificação: a Meta manda hub.mode, hub.verify_token e hub.challenge."""
        params = request.query_params
        mode = params.get("hub.mode")
        token = params.get("hub.verify_token") or ""
        challenge = params.get("hub.challenge") or ""
        expected = request.app.state.bot.settings.whatsapp_verify_token
        if mode == "subscribe" and hmac.compare_digest(token.encode(), expected.encode()):
            log.info("webhook verificado pela Meta")
            return PlainTextResponse(challenge)
        log.warning("verificação do webhook recusada (token incorreto ou modo inválido)")
        return Response(status_code=403)

    @app.post("/webhook")
    async def receive(request: Request, background: BackgroundTasks):
        bot: Bot = request.app.state.bot
        body = await request.body()
        signature = request.headers.get("X-Hub-Signature-256")
        if not verify_signature(bot.settings.whatsapp_app_secret, body, signature):
            log.warning("POST no webhook com assinatura inválida, descartado")
            return Response(status_code=401)
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            return Response(status_code=400)
        # Responde 200 na hora (a Meta reenvia se demorar) e processa em segundo plano.
        background.add_task(bot.handle_payload, payload)
        return Response(status_code=200)

    return app


app = create_app()
