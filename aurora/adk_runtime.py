"""Runner e eventos ADK são a única origem da conversa e das confirmações."""
from __future__ import annotations

from google.adk.apps import App, ResumabilityConfig
from google.adk.agents.run_config import RunConfig
from google.adk.runners import Runner
from google.adk.sessions.sqlite_session_service import SqliteSessionService
from google.genai import types

from .adk_agents import agente_principal
from .database import RUNTIME_DIR

APP_NAME = "residencial_aurora"
CONFIRMATION_TOOL = "adk_request_confirmation"


class AdkRuntime:
    def __init__(self, agent=None):
        RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        self.session_service = SqliteSessionService(str(RUNTIME_DIR / "adk_sessions.sqlite3"))
        self.runner = Runner(
            app=App(name=APP_NAME, root_agent=agent or agente_principal,
                    resumability_config=ResumabilityConfig(is_resumable=True)),
            session_service=self.session_service,
        )

    async def create_session(self, session_id: str, apartment: str):
        await self.session_service.create_session(
            app_name=APP_NAME, user_id=apartment, session_id=session_id,
            state={"apartamento": apartment},
        )

    async def session(self, session_id: str, apartment: str):
        return await self.session_service.get_session(
            app_name=APP_NAME, user_id=apartment, session_id=session_id,
        )

    async def pending(self, session_id: str, apartment: str) -> list[dict]:
        session = await self.session(session_id, apartment)
        pending = {}
        for event in session.events:
            for call in event.get_function_calls():
                if call.name == CONFIRMATION_TOOL and event.author != "user":
                    original = call.args["originalFunctionCall"]
                    pending[call.id] = {
                        "id": call.id, "acao": original["name"],
                        "detalhes": call.args["toolConfirmation"].get("payload") or original.get("args", {}),
                    }
            if event.author == "user":
                for response in event.get_function_responses():
                    if response.name == CONFIRMATION_TOOL:
                        pending.pop(response.id, None)
        return list(pending.values())

    async def run(self, session_id: str, apartment: str, message: types.Content) -> dict:
        texts = []
        async for event in self.runner.run_async(
            user_id=apartment, session_id=session_id, new_message=message,
            run_config=RunConfig(max_llm_calls=30),
        ):
            if event.error_code:
                raise RuntimeError("Falha na execução do modelo")
            if event.is_final_response() and event.content:
                texts.extend(p.text for p in event.content.parts or [] if p.text and not p.thought)
        return {"resposta": "\n".join(texts), "confirmacoes_pendentes": await self.pending(session_id, apartment)}

    async def message(self, session_id: str, apartment: str, text: str) -> dict:
        # Enquanto uma execução aguarda decisão externa, não iniciamos outra.
        pending = await self.pending(session_id, apartment)
        if pending:
            return {"resposta": "", "confirmacoes_pendentes": pending}
        return await self.run(session_id, apartment, types.Content(role="user", parts=[types.Part(text=text)]))

    async def confirm(self, session_id: str, apartment: str, confirmation_id: str, confirmed: bool) -> dict:
        pending = await self.pending(session_id, apartment)
        if not any(p["id"] == confirmation_id for p in pending):
            raise KeyError("confirmation")
        # Apenas esta rota cria FunctionResponse. Texto do chat nunca vira aprovação.
        message = types.Content(role="user", parts=[types.Part(function_response=types.FunctionResponse(
            id=confirmation_id, name=CONFIRMATION_TOOL, response={"confirmed": confirmed},
        ))])
        return await self.run(session_id, apartment, message)

    async def events(self, session_id: str, apartment: str) -> list[dict]:
        session = await self.session(session_id, apartment)
        return [event.model_dump(mode="json", exclude_none=True) for event in session.events]
