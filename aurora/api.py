from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from .adk_agents import agente_principal
from .adk_runtime import ensure_adk_session, run_adk_turn
from .database import connect, ensure_database, initial_apartments
from .service import answer_confirmation, assistant_message, event, pending_confirmations


@asynccontextmanager
async def lifespan(_: FastAPI):
    ensure_database()
    yield


app = FastAPI(title="Residencial Aurora", lifespan=lifespan)
# Instancia a topologia Google ADK no processo; a camada de politica abaixo e o
# limite de autoridade para qualquer tool que o agente venha a acionar.
_agente_principal = agente_principal


class CreateSession(BaseModel):
    apartamento: str


class Message(BaseModel):
    texto: str


class Confirmation(BaseModel):
    id: str
    confirmado: bool


def session_connection(session_id: str):
    connection = connect()
    if connection.execute("SELECT 1 FROM sessions WHERE id = ?", (session_id,)).fetchone() is None:
        connection.close()
        raise HTTPException(status_code=404, detail="Sessão não encontrada")
    return connection


@app.post("/sessoes", status_code=201)
async def create_session(payload: CreateSession):
    if payload.apartamento not in initial_apartments():
        raise HTTPException(status_code=404, detail="Apartamento não encontrado")
    connection = connect()
    session_id = f"sess-{uuid.uuid4().hex}"
    connection.execute("INSERT INTO sessions(id, apartment) VALUES (?, ?)", (session_id, payload.apartamento))
    event(connection, session_id, "session_created", "sessao criada")
    connection.close()
    await ensure_adk_session(session_id, payload.apartamento)
    return {"session_id": session_id}


@app.post("/sessoes/{session_id}/mensagens")
async def send_message(session_id: str, payload: Message):
    connection = session_connection(session_id)
    try:
        apartment = connection.execute("SELECT apartment FROM sessions WHERE id = ?", (session_id,)).fetchone()["apartment"]
        adk_ran = await run_adk_turn(session_id, apartment, payload.texto)
        event(connection, session_id, "adk_runner", "executado" if adk_ran else "indisponivel_sem_chave")
        response, pending = assistant_message(connection, session_id, payload.texto)
        return {"resposta": response, "confirmacoes_pendentes": pending}
    finally:
        connection.close()


@app.post("/sessoes/{session_id}/confirmacoes")
def confirm(session_id: str, payload: Confirmation):
    connection = session_connection(session_id)
    try:
        try:
            response, pending = answer_confirmation(connection, session_id, payload.id, payload.confirmado)
        except ValueError:
            raise HTTPException(status_code=409, detail="Confirmação não pendente nesta sessão")
        return {"resposta": response, "confirmacoes_pendentes": pending}
    finally:
        connection.close()


@app.get("/sessoes/{session_id}/eventos")
def events(session_id: str):
    connection = session_connection(session_id)
    try:
        rows = connection.execute("SELECT kind, content, created_at FROM events WHERE session_id = ? ORDER BY id", (session_id,)).fetchall()
        return [{"tipo": row["kind"], "conteudo": row["content"], "criado_em": row["created_at"]} for row in rows]
    finally:
        connection.close()


@app.get("/apartamentos/{apartment}/reservas")
def reservations(apartment: str):
    connection = connect()
    rows = connection.execute("SELECT code, area, day FROM reservations WHERE apartment = ? ORDER BY day", (apartment,)).fetchall()
    connection.close()
    return [{"codigo": row["code"], "area": row["area"], "data": row["day"]} for row in rows]


@app.get("/apartamentos/{apartment}/visitantes")
def visitors(apartment: str):
    connection = connect()
    rows = connection.execute("SELECT name, day FROM visitors WHERE apartment = ? ORDER BY day", (apartment,)).fetchall()
    connection.close()
    return [{"nome": row["name"], "data": row["day"]} for row in rows]
