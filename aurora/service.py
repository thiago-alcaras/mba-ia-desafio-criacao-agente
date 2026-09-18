from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
import uuid
from dataclasses import dataclass

from .database import areas, connect, initial_apartments


def normalized(value: str) -> str:
    return "".join(char for char in unicodedata.normalize("NFD", value.lower()) if unicodedata.category(char) != "Mn")


AREA_ALIASES = {
    "salao-de-festas": ("salao", "festas"),
    "churrasqueira": ("churrasqueira",),
    "quadra": ("quadra",),
}


def event(connection: sqlite3.Connection, session_id: str, kind: str, content: str) -> None:
    connection.execute("INSERT INTO events(session_id, kind, content) VALUES (?, ?, ?)", (session_id, kind, content))


def pending_confirmations(connection: sqlite3.Connection, session_id: str) -> list[dict]:
    rows = connection.execute(
        "SELECT id, action, details FROM confirmations WHERE session_id = ? AND status = 'pending' ORDER BY rowid",
        (session_id,),
    ).fetchall()
    return [{"id": row["id"], "acao": row["action"], "detalhes": json.loads(row["details"])} for row in rows]


def area_in(text: str) -> str | None:
    text = normalized(text)
    for area, aliases in AREA_ALIASES.items():
        if all(alias in text for alias in aliases):
            return area
    return None


def day_in(text: str) -> str | None:
    match = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", text)
    return match.group(1) if match else None


def create_reservation(connection: sqlite3.Connection, apartment: str, area: str, day: str) -> tuple[bool, str]:
    code = f"RSV-{uuid.uuid4().hex[:10].upper()}"
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("INSERT INTO issued_codes(code) VALUES (?)", (code,))
        connection.execute("INSERT INTO reservations(code, apartment, area, day) VALUES (?, ?, ?, ?)", (code, apartment, area, day))
        connection.execute("COMMIT")
        return True, code
    except sqlite3.IntegrityError:
        connection.execute("ROLLBACK")
        return False, ""


def request_confirmation(connection: sqlite3.Connection, session_id: str, action: str, details: dict, payload: dict) -> dict:
    confirmation_id = f"conf-{uuid.uuid4().hex}"
    connection.execute(
        "INSERT INTO confirmations(id, session_id, action, details, payload, status) VALUES (?, ?, ?, ?, ?, 'pending')",
        (confirmation_id, session_id, action, json.dumps(details), json.dumps(payload)),
    )
    event(connection, session_id, "tool_confirmation_requested", f"{action}: {json.dumps(details, ensure_ascii=False)}")
    return {"id": confirmation_id, "acao": action, "detalhes": details}


def assistant_message(connection: sqlite3.Connection, session_id: str, text: str) -> tuple[str, list[dict]]:
    session = connection.execute("SELECT apartment FROM sessions WHERE id = ?", (session_id,)).fetchone()
    if session is None:
        raise KeyError("session")
    apartment = session["apartment"]
    clean = normalized(text)
    event(connection, session_id, "user", text)

    if "piscina" in clean and ("domingo" in clean or "domingos" in clean):
        response = "A piscina fecha às 20h aos domingos e feriados."
        event(connection, session_id, "tool_regulamento", "consulta pontual: horario da piscina aos domingos")
        event(connection, session_id, "assistant", response)
        return response, pending_confirmations(connection, session_id)

    area, day = area_in(clean), day_in(clean)
    if any(word in clean for word in ("cancele", "cancelar", "cancela")) and area and day:
        deleted = connection.execute("DELETE FROM reservations WHERE apartment = ? AND area = ? AND day = ?", (apartment, area, day)).rowcount
        response = "Reserva cancelada." if deleted else "Não encontrei uma reserva sua para cancelar."
        event(connection, session_id, "tool_cancelar_reserva", f"area={area}; data={day}; cancelada={bool(deleted)}")
        event(connection, session_id, "assistant", response)
        return response, pending_confirmations(connection, session_id)

    if any(word in clean for word in ("libera", "liberar", "autoriza", "autorizar")) and ("entrada" in clean or "visitante" in clean):
        visitor = re.search(r"(?:entrada (?:do |da )?|visitante )([A-Za-zÀ-ÿ ]+?)(?: no dia| em | para )", text, re.IGNORECASE)
        if visitor and day:
            name = " ".join(visitor.group(1).split())
            confirmation = request_confirmation(connection, session_id, "autorizar_visitante", {"nome": name, "data": day}, {"nome": name, "data": day})
            return "", [confirmation]

    if any(word in clean for word in ("reserve", "reservar", "reserva")) and area and day:
        if connection.execute("SELECT 1 FROM reservations WHERE area = ? AND day = ?", (area, day)).fetchone():
            response = "Essa área já está ocupada nessa data."
            event(connection, session_id, "tool_consultar_disponibilidade", f"area={area}; data={day}; livre=false")
            event(connection, session_id, "assistant", response)
            return response, pending_confirmations(connection, session_id)
        info = areas()[area]
        if info["taxa"] > 0:
            confirmation = request_confirmation(connection, session_id, "reservar_area_com_cobranca", {"area": area, "data": day}, {"area": area, "data": day})
            return "", [confirmation]
        created, _ = create_reservation(connection, apartment, area, day)
        response = "Reserva realizada." if created else "Essa área já está ocupada nessa data."
        event(connection, session_id, "tool_reservar_area", f"area={area}; data={day}; criada={created}")
        event(connection, session_id, "assistant", response)
        return response, pending_confirmations(connection, session_id)

    if "minhas reservas" in clean or "quais reservas" in clean:
        rows = connection.execute("SELECT area, day FROM reservations WHERE apartment = ? ORDER BY day", (apartment,)).fetchall()
        response = "Você não possui reservas." if not rows else "Suas reservas: " + ", ".join(f"{row['area']} em {row['day']}" for row in rows) + "."
        event(connection, session_id, "tool_listar_minhas_reservas", "consulta do apartamento da sessao")
        event(connection, session_id, "assistant", response)
        return response, pending_confirmations(connection, session_id)

    response = "Posso ajudar com reservas, cancelamentos, visitantes e dúvidas sobre o regulamento."
    event(connection, session_id, "assistant", response)
    return response, pending_confirmations(connection, session_id)


def answer_confirmation(connection: sqlite3.Connection, session_id: str, confirmation_id: str, confirmed: bool) -> tuple[str, list[dict]]:
    connection.execute("BEGIN IMMEDIATE")
    row = connection.execute("SELECT * FROM confirmations WHERE id = ? AND session_id = ? AND status = 'pending'", (confirmation_id, session_id)).fetchone()
    if row is None:
        connection.execute("ROLLBACK")
        raise ValueError("confirmation")
    connection.execute("UPDATE confirmations SET status = ? WHERE id = ?", ("approved" if confirmed else "denied", confirmation_id))
    payload = json.loads(row["payload"])
    apartment = connection.execute("SELECT apartment FROM sessions WHERE id = ?", (session_id,)).fetchone()["apartment"]
    if not confirmed:
        connection.execute("COMMIT")
        response = "Ação não confirmada."
        event(connection, session_id, "confirmation_denied", row["action"])
        event(connection, session_id, "assistant", response)
        return response, pending_confirmations(connection, session_id)
    if row["action"] == "autorizar_visitante":
        connection.execute("INSERT INTO visitors(apartment, name, day) VALUES (?, ?, ?)", (apartment, payload["nome"], payload["data"]))
        response = "Visitante autorizado."
    else:
        try:
            code = f"RSV-{uuid.uuid4().hex[:10].upper()}"
            connection.execute("INSERT INTO issued_codes(code) VALUES (?)", (code,))
            connection.execute("INSERT INTO reservations(code, apartment, area, day) VALUES (?, ?, ?, ?)", (code, apartment, payload["area"], payload["data"]))
            response = "Reserva realizada."
        except sqlite3.IntegrityError:
            response = "Essa área já está ocupada nessa data."
    connection.execute("COMMIT")
    event(connection, session_id, "confirmation_approved", row["action"])
    event(connection, session_id, "assistant", response)
    return response, pending_confirmations(connection, session_id)
