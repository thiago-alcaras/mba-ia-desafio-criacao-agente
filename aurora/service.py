"""Tools ADK: a conversa escolhe a operação; o código impõe a autorização."""
from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
import uuid
from contextlib import closing
from datetime import date

from google.adk.tools import ToolContext

from .database import DATA_DIR, areas, connect


def apartment_for(tool_context: ToolContext) -> str:
    """Valida o state contra o vínculo imutável criado pela API."""
    with closing(connect()) as db:
        row = db.execute("SELECT apartment FROM sessions WHERE id = ?", (tool_context.session.id,)).fetchone()
    if row is None or row["apartment"] != tool_context.state.get("apartamento"):
        raise ValueError("Identidade da sessão inválida")
    return row["apartment"]


def valid_date(value: str) -> bool:
    try:
        return date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def confirmed(tool_context: ToolContext, details: dict) -> bool:
    if tool_context.tool_confirmation is None:
        tool_context.request_confirmation(hint="Aprove ou negue pela rota de confirmações.", payload=details)
        tool_context.actions.skip_summarization = True
        return False
    return tool_context.tool_confirmation.confirmed


def mutate(tool_context: ToolContext, operation) -> dict:
    """Efeito e recibo da chamada são atômicos, inclusive em reexecuções."""
    with closing(connect()) as db:
        db.execute("BEGIN IMMEDIATE")
        try:
            key = (tool_context.session.id, tool_context.function_call_id)
            previous = db.execute("SELECT result FROM tool_results WHERE session_id=? AND call_id=?", key).fetchone()
            if previous:
                db.rollback()
                return json.loads(previous["result"])
            result = operation(db)
            db.execute("INSERT INTO tool_results VALUES (?, ?, ?)", (*key, json.dumps(result, ensure_ascii=False)))
            db.commit()
            return result
        except Exception:
            db.rollback()
            raise


def listar_minhas_reservas(tool_context: ToolContext) -> dict:
    """Lista apenas as reservas do apartamento autenticado, nunca de terceiros."""
    apartment = apartment_for(tool_context)
    with closing(connect()) as db:
        rows = db.execute("SELECT code AS codigo, area, day AS data FROM reservations WHERE apartment=? ORDER BY day", (apartment,)).fetchall()
    return {"reservas": [dict(row) for row in rows]}


def listar_meus_visitantes(tool_context: ToolContext) -> dict:
    """Lista apenas visitantes do apartamento autenticado."""
    apartment = apartment_for(tool_context)
    with closing(connect()) as db:
        rows = db.execute("SELECT name AS nome, day AS data FROM visitors WHERE apartment=? ORDER BY day", (apartment,)).fetchall()
    return {"visitantes": [dict(row) for row in rows]}


def consultar_disponibilidade(area: str, data: str, tool_context: ToolContext) -> dict:
    """Consulta área por id e data ISO; revela somente disponibilidade e taxa."""
    apartment_for(tool_context)
    if area not in areas() or not valid_date(data):
        return {"erro": "Informe uma área válida e data AAAA-MM-DD."}
    with closing(connect()) as db:
        occupied = db.execute("SELECT 1 FROM reservations WHERE area=? AND day=?", (area, data)).fetchone()
    return {"area": area, "data": data, "livre": not bool(occupied), "taxa": areas()[area]["taxa"]}


def reservar_area(area: str, data: str, tool_context: ToolContext) -> dict:
    """Reserva salao-de-festas, churrasqueira ou quadra na data ISO. Taxa exige confirmação externa."""
    apartment = apartment_for(tool_context)
    if area not in areas() or not valid_date(data):
        return {"erro": "Informe uma área válida e data AAAA-MM-DD."}
    fee = areas()[area]["taxa"]
    if fee > 0 and not confirmed(tool_context, {"area": area, "data": data, "taxa": fee}):
        return {"status": "negada" if tool_context.tool_confirmation else "pendente"}

    def write(db):
        code = f"RSV-{uuid.uuid4().hex.upper()}"
        while db.execute("SELECT 1 FROM issued_codes WHERE code=?", (code,)).fetchone():
            code = f"RSV-{uuid.uuid4().hex.upper()}"
        try:
            db.execute("INSERT INTO reservations VALUES (?, ?, ?, ?)", (code, apartment, area, data))
        except sqlite3.IntegrityError:
            return {"status": "indisponivel", "area": area, "data": data}
        db.execute("INSERT INTO issued_codes VALUES (?)", (code,))
        return {"status": "reservada", "codigo": code, "area": area, "data": data}

    return mutate(tool_context, write)


def cancelar_reserva(area: str, data: str, tool_context: ToolContext) -> dict:
    """Cancela reserva própria por área e data, sem confirmação. Não consulta terceiros."""
    apartment = apartment_for(tool_context)
    if area not in areas() or not valid_date(data):
        return {"erro": "Informe uma área válida e data AAAA-MM-DD."}

    def write(db):
        count = db.execute("DELETE FROM reservations WHERE apartment=? AND area=? AND day=?", (apartment, area, data)).rowcount
        return {"status": "cancelada" if count else "reserva_propria_nao_encontrada"}

    return mutate(tool_context, write)


def autorizar_visitante(nome: str, data: str, tool_context: ToolContext) -> dict:
    """Autoriza visitante para o apartamento da sessão somente após confirmação externa."""
    apartment = apartment_for(tool_context)
    nome = " ".join(nome.split())
    if not nome or len(nome) > 200 or not valid_date(data):
        return {"erro": "Informe o nome do visitante e data AAAA-MM-DD."}
    if not confirmed(tool_context, {"nome": nome, "data": data}):
        return {"status": "negada" if tool_context.tool_confirmation else "pendente"}

    def write(db):
        db.execute("INSERT INTO visitors VALUES (?, ?, ?)", (apartment, nome, data))
        return {"status": "autorizado", "nome": nome, "data": data}

    return mutate(tool_context, write)


def normalized(value: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", value.lower()) if unicodedata.category(c) != "Mn")


def consultar_regulamento(assunto: str, consulta: str) -> dict:
    """Busca artigos de UM assunto: piscina; silêncio; academia; salão de festas;
    portaria; animais; mudanças; obras; garagem; lixo; infrações; disposições gerais.
    consulta contém palavras específicas da dúvida. Nunca retorna outros capítulos.
    """
    text = (DATA_DIR / "regulamento.md").read_text(encoding="utf-8")
    chapters = re.split(r"(?m)^## ", text)[1:]
    terms = set(re.findall(r"[a-z0-9]+", normalized(assunto))) - {"de", "da", "do", "e", "dos", "das"}
    ranked = [(len(terms & set(re.findall(r"[a-z0-9]+", normalized(c.splitlines()[0])))), c) for c in chapters]
    score, chapter = max(ranked, key=lambda item: item[0], default=(0, ""))
    if score == 0:
        return {"erro": "Assunto não localizado. Especifique o tema da dúvida."}
    title, body = chapter.split("\n", 1)
    articles = [a.strip() for a in re.split(r"(?=\*\*Art\. )", body) if a.strip()]
    words = set(re.findall(r"[a-z0-9]+", normalized(consulta))) - {"a", "o", "de", "da", "do", "e", "que", "em", "no", "na", "os", "as"}
    selected = sorted(articles, key=lambda a: len(words & set(re.findall(r"[a-z0-9]+", normalized(a)))), reverse=True)[:2]
    return {"fonte": "dados/regulamento.md", "capitulo": title, "trechos": selected}
