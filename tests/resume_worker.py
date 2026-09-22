"""Processo independente usado para testar retomada de uma confirmação persistida."""
import asyncio
import json
import sys

from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from aurora.adk_agents import build_agents
from aurora.adk_runtime import AdkRuntime


class SummaryModel(BaseLlm):
    model: str = "test-summary"

    async def generate_content_async(self, llm_request, stream=False):
        # O Runner deve executar a tool ANTES de pedir este resumo ao modelo.
        responses = [p.function_response for p in llm_request.contents[-1].parts if p.function_response]
        assert responses and responses[-1].name in {"reservar_area", "autorizar_visitante"}
        yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text=json.dumps(responses[-1].response))]))


async def main():
    runtime = AdkRuntime(build_agents(SummaryModel()))
    sid, apartment, confirmation_id = sys.argv[1:]
    before = await runtime.events(sid, apartment)
    result = await runtime.confirm(sid, apartment, confirmation_id, True)
    print(json.dumps({"before": before, "result": result}))


if __name__ == "__main__":
    asyncio.run(main())
