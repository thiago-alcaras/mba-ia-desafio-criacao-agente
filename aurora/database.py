from __future__ import annotations

import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "dados"
RUNTIME_DIR = ROOT / ".aurora"
DATABASE_PATH = RUNTIME_DIR / "aurora.sqlite3"


def connect() -> sqlite3.Connection:
    RUNTIME_DIR.mkdir(exist_ok=True)
    connection = sqlite3.connect(DATABASE_PATH, timeout=10, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    return connection


def reset_database() -> None:
    if DATABASE_PATH.exists():
        DATABASE_PATH.unlink()
    connection = connect()
    connection.executescript(
        """
        CREATE TABLE sessions (
          id TEXT PRIMARY KEY,
          apartment TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE events (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          session_id TEXT NOT NULL REFERENCES sessions(id),
          kind TEXT NOT NULL,
          content TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE reservations (
          code TEXT PRIMARY KEY,
          apartment TEXT NOT NULL,
          area TEXT NOT NULL,
          day TEXT NOT NULL,
          UNIQUE(area, day)
        );
        CREATE TABLE issued_codes (code TEXT PRIMARY KEY);
        CREATE TABLE visitors (
          apartment TEXT NOT NULL,
          name TEXT NOT NULL,
          day TEXT NOT NULL
        );
        CREATE TABLE confirmations (
          id TEXT PRIMARY KEY,
          session_id TEXT NOT NULL REFERENCES sessions(id),
          action TEXT NOT NULL,
          details TEXT NOT NULL,
          payload TEXT NOT NULL,
          status TEXT NOT NULL CHECK(status IN ('pending', 'approved', 'denied'))
        );
        """
    )
    for item in json.loads((DATA_DIR / "reservas.json").read_text(encoding="utf-8")):
        connection.execute(
            "INSERT INTO reservations(code, apartment, area, day) VALUES (?, ?, ?, ?)",
            (item["codigo"], item["apartamento"], item["area"], item["data"]),
        )
        connection.execute("INSERT INTO issued_codes(code) VALUES (?)", (item["codigo"],))
    for item in json.loads((DATA_DIR / "visitantes.json").read_text(encoding="utf-8")):
        connection.execute(
            "INSERT INTO visitors(apartment, name, day) VALUES (?, ?, ?)",
            (item["apartamento"], item["nome"], item["data"]),
        )
    connection.close()


def ensure_database() -> None:
    if not DATABASE_PATH.exists():
        reset_database()


def initial_apartments() -> set[str]:
    return {item["numero"] for item in json.loads((DATA_DIR / "apartamentos.json").read_text(encoding="utf-8"))}


def areas() -> dict[str, dict]:
    return {item["id"]: item for item in json.loads((DATA_DIR / "areas.json").read_text(encoding="utf-8"))}
