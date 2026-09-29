"""Persistência em SQLite.

Valores em centavos (inteiro) para evitar erro de arredondamento; datas em
ISO (YYYY-MM-DD). Apagar é "soft delete" (deleted_at), para dar pra desfazer
na mão se precisar.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS purchases (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    purchase_date      TEXT    NOT NULL,             -- YYYY-MM-DD
    merchant           TEXT    NOT NULL,
    total_cents        INTEGER,                      -- NULL = valor ainda não informado
    category           TEXT    NOT NULL DEFAULT 'Outros',
    media_id           TEXT    UNIQUE,               -- id da mídia na Meta (dedupe)
    source_message_id  TEXT,                         -- wamid da mensagem com a foto
    confirm_message_id TEXT,                         -- wamid da confirmação enviada pelo bot
    notes              TEXT,
    created_at         TEXT    NOT NULL,
    updated_at         TEXT    NOT NULL,
    deleted_at         TEXT
);
CREATE INDEX IF NOT EXISTS idx_purchases_date ON purchases(purchase_date);
CREATE INDEX IF NOT EXISTS idx_purchases_confirm ON purchases(confirm_message_id);

-- Toda mensagem recebida é registrada aqui antes de ser processada: se a Meta
-- reenviar o mesmo webhook, o INSERT falha e a mensagem é ignorada.
CREATE TABLE IF NOT EXISTS processed_messages (
    message_id  TEXT PRIMARY KEY,
    received_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS kv (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Purchase:
    id: int
    purchase_date: str
    merchant: str
    total_cents: int | None
    category: str
    media_id: str | None
    source_message_id: str | None
    confirm_message_id: str | None
    notes: str | None
    created_at: str
    updated_at: str
    deleted_at: str | None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Purchase":
        return cls(**{k: row[k] for k in row.keys()})


class Database:
    def __init__(self, path: str):
        self.path = path
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._memory_conn: sqlite3.Connection | None = None
        with self.connect() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        if self.path == ":memory:":
            # Em memória (testes) precisa ser sempre a mesma conexão.
            if self._memory_conn is None:
                self._memory_conn = sqlite3.connect(":memory:", check_same_thread=False)
                self._memory_conn.row_factory = sqlite3.Row
            yield self._memory_conn
            self._memory_conn.commit()
            return
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ---- dedupe de mensagens -------------------------------------------------

    def claim_message(self, message_id: str) -> bool:
        """Registra a mensagem; retorna False se ela já tinha sido vista."""
        with self.connect() as conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO processed_messages (message_id, received_at) VALUES (?, ?)",
                (message_id, _now()),
            )
            return cur.rowcount == 1

    # ---- kv ------------------------------------------------------------------

    def get_kv(self, key: str) -> str | None:
        with self.connect() as conn:
            row = conn.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
            return row["value"] if row else None

    def set_kv(self, key: str, value: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO kv (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    # ---- compras -------------------------------------------------------------

    def get_by_media_id(self, media_id: str) -> Purchase | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM purchases WHERE media_id = ?", (media_id,)).fetchone()
            return Purchase.from_row(row) if row else None

    def add_purchase(
        self,
        *,
        purchase_date: str,
        merchant: str,
        total_cents: int | None,
        category: str,
        media_id: str | None = None,
        source_message_id: str | None = None,
        notes: str | None = None,
    ) -> Purchase:
        now = _now()
        with self.connect() as conn:
            cur = conn.execute(
                """INSERT INTO purchases
                   (purchase_date, merchant, total_cents, category, media_id,
                    source_message_id, notes, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (purchase_date, merchant, total_cents, category, media_id,
                 source_message_id, notes, now, now),
            )
            row = conn.execute("SELECT * FROM purchases WHERE id = ?", (cur.lastrowid,)).fetchone()
            return Purchase.from_row(row)

    def get(self, purchase_id: int) -> Purchase | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM purchases WHERE id = ? AND deleted_at IS NULL", (purchase_id,)
            ).fetchone()
            return Purchase.from_row(row) if row else None

    def get_by_confirm_message(self, message_id: str) -> Purchase | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM purchases WHERE (confirm_message_id = ? OR source_message_id = ?) "
                "AND deleted_at IS NULL",
                (message_id, message_id),
            ).fetchone()
            return Purchase.from_row(row) if row else None

    def last_purchase(self) -> Purchase | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM purchases WHERE deleted_at IS NULL ORDER BY id DESC LIMIT 1"
            ).fetchone()
            return Purchase.from_row(row) if row else None

    def recent(self, limit: int = 10) -> list[Purchase]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM purchases WHERE deleted_at IS NULL ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
            return [Purchase.from_row(r) for r in rows]

    def set_confirm_message(self, purchase_id: int, message_id: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE purchases SET confirm_message_id = ? WHERE id = ?", (message_id, purchase_id)
            )

    def update_purchase(self, purchase_id: int, **fields) -> Purchase | None:
        allowed = {"purchase_date", "merchant", "total_cents", "category", "notes"}
        changes = {k: v for k, v in fields.items() if k in allowed}
        if changes:
            cols = ", ".join(f"{k} = ?" for k in changes)
            with self.connect() as conn:
                conn.execute(
                    f"UPDATE purchases SET {cols}, updated_at = ? WHERE id = ? AND deleted_at IS NULL",
                    (*changes.values(), _now(), purchase_id),
                )
        return self.get(purchase_id)

    def delete_purchase(self, purchase_id: int) -> bool:
        with self.connect() as conn:
            cur = conn.execute(
                "UPDATE purchases SET deleted_at = ?, updated_at = ? WHERE id = ? AND deleted_at IS NULL",
                (_now(), _now(), purchase_id),
            )
            return cur.rowcount == 1

    def purchases_in_month(self, year: int, month: int) -> list[Purchase]:
        prefix = f"{year:04d}-{month:02d}-"
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM purchases WHERE deleted_at IS NULL AND purchase_date LIKE ? "
                "ORDER BY purchase_date, id",
                (prefix + "%",),
            ).fetchall()
            return [Purchase.from_row(r) for r in rows]
