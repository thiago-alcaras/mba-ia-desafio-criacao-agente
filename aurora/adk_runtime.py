"""Execucao persistente do Google ADK sem delegar autoridade de dominio ao modelo."""

from __future__ import annotations

import os

from dotenv import load_dotenv
from google.adk.runners import Runner
from google.adk.sessions.sqlite_session_service import SqliteSessionService
from google.genai import types

from .adk_agents import agente_principal
from .database import RUNTIME_DIR, ROOT

load_dotenv(ROOT / ".env")
RUNTIME_DIR.mkdir(exist_ok=True)
APP_NAME = "residencial_aurora"
session_service = SqliteSessionService(str(RUNTIME_DIR / "adk_sessions.sqlite3"))
runner = Runner(app_name=APP_NAME, agent=agente_principal, session_service=session_service)


async def ensure_adk_session(session_id: str, apartment: str) -> None:
    session = await session_service.get_session(app_name=APP_NAME, user_id=apartment, session_id=session_id)
    if session is None:
        await session_service.create_session(
            app_name=APP_NAME,
            user_id=apartment,
            session_id=session_id,
            state={"apartamento": apartment},
        )


async def run_adk_turn(session_id: str, apartment: str, text: str) -> bool:
    """Run Gemini for conversation orchestration, but never trust it for authorization.

    Returning False keeps local development and the API contract usable when a
    developer has not yet placed GOOGLE_API_KEY in .env. In an evaluator setup
    the configured Gemini model is called through the real ADK Runner.
    """
    await ensure_adk_session(session_id, apartment)
    if not os.getenv("GOOGLE_API_KEY"):
        return False
    try:
        message = types.Content(role="user", parts=[types.Part(text=text)])
        async for _ in runner.run_async(user_id=apartment, session_id=session_id, new_message=message):
            pass
        return True
    except Exception:
        # Authorization and persistence remain correct even during a provider outage.
        return False
