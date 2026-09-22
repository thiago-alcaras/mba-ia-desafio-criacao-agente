from __future__ import annotations

import asyncio
import logging
import uuid
from contextlib import asynccontextmanager, closing
from weakref import WeakValueDictionary

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, StrictBool

from .adk_runtime import AdkRuntime
from .database import connect, ensure_database, initial_apartments

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_database()
    if not hasattr(app.state, "runtime"):
        app.state.runtime = AdkRuntime()
    yield


app = FastAPI(title="Residencial Aurora", lifespan=lifespan)
_locks: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()


def session_lock(session_id: str) -> asyncio.Lock:
    lock = _locks.get(session_id)
    if lock is None:
        lock = asyncio.Lock()
        _locks[session_id] = lock
    return lock


class CreateSession(BaseModel):
    apartamento: str


class Message(BaseModel):
    texto: str = Field(min_length=1, max_length=10000)


class Confirmation(BaseModel):
    id: str
    confirmado: StrictBool


def apartment_for_session(session_id: str) -> str:
    with closing(connect()) as connection:
        row = connection.execute("SELECT apartment FROM sessions WHERE id=?", (session_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Sessão não encontrada")
    return row["apartment"]


@app.post("/sessoes", status_code=201)
async def create_session(payload: CreateSession):
    if payload.apartamento not in initial_apartments():
        raise HTTPException(status_code=404, detail="Apartamento não encontrado")
    session_id = f"sess-{uuid.uuid4().hex}"
    await app.state.runtime.create_session(session_id, payload.apartamento)
    with closing(connect()) as connection:
        connection.execute("INSERT INTO sessions(id, apartment) VALUES (?, ?)", (session_id, payload.apartamento))
    return {"session_id": session_id}


@app.post("/sessoes/{session_id}/mensagens")
async def send_message(session_id: str, payload: Message):
    apartment = apartment_for_session(session_id)
    async with session_lock(session_id):
        try:
            return await app.state.runtime.message(session_id, apartment, payload.texto)
        except Exception:
            logger.exception("Falha no Runner ADK")
            raise HTTPException(status_code=503, detail="Assistente indisponível. Verifique a configuração do Gemini e consulte eventos e pendências antes de repetir a operação.")


@app.post("/sessoes/{session_id}/confirmacoes")
async def confirm(session_id: str, payload: Confirmation):
    apartment = apartment_for_session(session_id)
    async with session_lock(session_id):
        if not any(p["id"] == payload.id for p in await app.state.runtime.pending(session_id, apartment)):
            raise HTTPException(status_code=409, detail="Confirmação não pendente nesta sessão")
        try:
            return await app.state.runtime.confirm(session_id, apartment, payload.id, payload.confirmado)
        except Exception:
            logger.exception("Falha na retomada ADK")
            raise HTTPException(status_code=503, detail="Falha ao retomar o assistente. Consulte os eventos e dados antes de repetir a operação.")


@app.get("/sessoes/{session_id}/eventos")
async def events(session_id: str):
    apartment = apartment_for_session(session_id)
    return await app.state.runtime.events(session_id, apartment)


@app.get("/apartamentos/{apartment}/reservas")
def reservations(apartment: str):
    with closing(connect()) as connection:
        rows = connection.execute("SELECT code AS codigo, area, day AS data FROM reservations WHERE apartment=? ORDER BY day", (apartment,)).fetchall()
    return [dict(row) for row in rows]


@app.get("/apartamentos/{apartment}/visitantes")
def visitors(apartment: str):
    with closing(connect()) as connection:
        rows = connection.execute("SELECT name AS nome, day AS data FROM visitors WHERE apartment=? ORDER BY day", (apartment,)).fetchall()
    return [dict(row) for row in rows]
